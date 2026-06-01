package com.orange.pfe.backend.service.remediation;
import com.orange.pfe.backend.config.RemediationProperties;
import com.orange.pfe.backend.model.action.ActionType;
import io.kubernetes.client.custom.V1Patch;
import io.kubernetes.client.openapi.ApiClient;
import io.kubernetes.client.openapi.ApiException;
import io.kubernetes.client.openapi.Configuration;
import io.kubernetes.client.openapi.apis.AppsV1Api;
import io.kubernetes.client.openapi.apis.CoreV1Api;
import io.kubernetes.client.openapi.models.V1DeleteOptions;
import io.kubernetes.client.openapi.models.V1Deployment;
import io.kubernetes.client.openapi.models.V1Pod;
import io.kubernetes.client.openapi.models.V1ReplicaSet;
import io.kubernetes.client.openapi.models.V1Scale;
import io.kubernetes.client.util.ClientBuilder;
import io.kubernetes.client.util.Config;
import io.kubernetes.client.util.KubeConfig;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;

import java.io.FileReader;
import java.io.IOException;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Duration;
import java.time.Instant;
import java.util.Comparator;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.Set;

@Service
public class KubernetesRemediationExecutor implements RemediationExecutor {

    private static final Logger logger = LoggerFactory.getLogger(KubernetesRemediationExecutor.class);

    /** Namespaces the executor will never touch regardless of allowlist configuration. */
    private static final Set<String> SYSTEM_NAMESPACES = Set.of(
            "kube-system", "kube-public", "kube-node-lease",
            "cnpg-system", "cert-manager", "monitoring"
    );

    private final RemediationProperties properties;

    private volatile CoreV1Api coreV1Api;
    private volatile AppsV1Api appsV1Api;

    public KubernetesRemediationExecutor(RemediationProperties properties) {
        this.properties = properties;
    }

    @Override
    public RemediationExecutionResult execute(RemediationPlan plan) {
        if (plan.actionType() == null) {
            return new RemediationExecutionResult(false, plan.targetName(), "No executable action was selected");
        }
        if (!isNamespaceAllowlisted(plan.targetNamespace())) {
            return new RemediationExecutionResult(
                    false,
                    plan.targetName(),
                    "Namespace " + plan.targetNamespace() + " is not allowlisted for automatic remediation"
            );
        }

        try {
            return switch (plan.actionType()) {
                case RESTART_POD             -> restartPod(plan.targetNamespace(), plan.targetName());
                case SCALE_DEPLOYMENT        -> scaleDeployment(plan.targetNamespace(), plan.targetName());
                case SCALE_DOWN              -> scaleDown(plan.targetNamespace(), plan.targetName());
                case ROLLBACK_DEPLOYMENT     -> rollbackDeployment(plan.targetNamespace(), plan.targetName());
                case DELETE_CRASHLOOPING_PODS -> deleteCrashloopingPods(plan.targetNamespace(), plan.targetName());
                case FORCE_RECREATE_DEPLOYMENT -> forceRecreateDeployment(plan.targetNamespace(), plan.targetName());
                case CORDON_NODE             -> cordonNode(plan.targetName());
                case DRAIN_NODE              -> drainNode(plan.targetNamespace(), plan.targetName());
                case RESTART_VM              -> restartVm(plan.targetName());
                default -> new RemediationExecutionResult(
                        false,
                        plan.targetName(),
                        "Action type " + plan.actionType() + " is not directly executable by the Kubernetes remediation executor"
                );
            };
        } catch (ApiException | IOException ex) {
            logger.error("Remediation execution failed for {}/{}: {}", plan.targetNamespace(), plan.targetName(), ex.getMessage());
            return new RemediationExecutionResult(false, plan.targetName(), ex.getMessage());
        }
    }

    @Override
    public boolean isWorkloadHealthy(String namespace, String deploymentName) {
        try {
            ensureApis();
            V1Deployment deployment = appsV1Api.readNamespacedDeployment(deploymentName, namespace).execute();
            Integer desired = deployment.getSpec() != null ? deployment.getSpec().getReplicas() : null;
            Integer available = deployment.getStatus() != null ? deployment.getStatus().getAvailableReplicas() : null;
            return desired != null && desired > 0 && available != null && available >= desired;
        } catch (ApiException | IOException ex) {
            logger.warn("Health verification failed for {}/{}", namespace, deploymentName, ex);
            return false;
        }
    }

    // ── Existing actions ──────────────────────────────────────────────────────

    private RemediationExecutionResult restartVm(String nodeName) {
        String watchdogUrl = properties.getWatchdogBaseUrl();
        if (watchdogUrl == null || watchdogUrl.isBlank()) {
            return new RemediationExecutionResult(false, nodeName,
                    "VM watchdog URL not configured (remediation.watchdog-base-url)");
        }
        String url = watchdogUrl.stripTrailing() + "/restart/" + nodeName;
        logger.info("Requesting VM restart for node {} via watchdog: {}", nodeName, url);
        try {
            HttpClient client = HttpClient.newBuilder()
                    .connectTimeout(Duration.ofSeconds(10))
                    .build();
            HttpRequest req = HttpRequest.newBuilder()
                    .uri(URI.create(url))
                    .POST(HttpRequest.BodyPublishers.noBody())
                    .timeout(Duration.ofSeconds(15))
                    .build();
            HttpResponse<String> response = client.send(req, HttpResponse.BodyHandlers.ofString());
            if (response.statusCode() == 202) {
                logger.info("VM restart initiated for node {}", nodeName);
                return new RemediationExecutionResult(true, nodeName,
                        "VM restart initiated via watchdog — node will rejoin cluster in ~2min");
            }
            return new RemediationExecutionResult(false, nodeName,
                    "Watchdog returned HTTP " + response.statusCode() + ": " + response.body());
        } catch (Exception ex) {
            logger.error("VM restart request failed for node {}: {}", nodeName, ex.getMessage());
            return new RemediationExecutionResult(false, nodeName, "Watchdog call failed: " + ex.getMessage());
        }
    }

    private RemediationExecutionResult restartPod(String namespace, String appLabel) throws ApiException, IOException {
        ensureApis();
        List<V1Pod> pods = coreV1Api.listNamespacedPod(namespace)
                .labelSelector("app=" + appLabel)
                .execute()
                .getItems();

        Comparator<V1Pod> byAge = Comparator.comparing(
                pod -> pod.getMetadata() != null ? pod.getMetadata().getCreationTimestamp() : null,
                Comparator.nullsLast(Comparator.naturalOrder())
        );
        V1Pod selectedPod = pods.stream()
                .filter(this::isPodReady)
                .min(byAge)
                .orElseGet(() -> pods.stream().min(byAge).orElse(null));

        if (selectedPod == null || selectedPod.getMetadata() == null || selectedPod.getMetadata().getName() == null) {
            return new RemediationExecutionResult(false, appLabel, "No pod found for selector app=" + appLabel);
        }

        String podName = selectedPod.getMetadata().getName();
        coreV1Api.deleteNamespacedPod(podName, namespace)
                .gracePeriodSeconds(5)
                .propagationPolicy("Background")
                .body(new V1DeleteOptions())
                .execute();

        return new RemediationExecutionResult(true, podName, "Deleted pod " + podName + " to trigger restart");
    }

    private RemediationExecutionResult scaleDeployment(String namespace, String deploymentName) throws ApiException, IOException {
        ensureApis();
        V1Deployment deployment = appsV1Api.readNamespacedDeployment(deploymentName, namespace).execute();
        List<V1Pod> pods = coreV1Api.listNamespacedPod(namespace)
                .labelSelector("app=" + deploymentName)
                .execute()
                .getItems();

        Comparator<V1Pod> byAge = Comparator.comparing(
                pod -> pod.getMetadata() != null ? pod.getMetadata().getCreationTimestamp() : null,
                Comparator.nullsLast(Comparator.naturalOrder())
        );
        V1Pod unreadyPod = pods.stream()
                .filter(pod -> !isPodReady(pod))
                .min(byAge)
                .orElse(null);

        if (unreadyPod != null && unreadyPod.getMetadata() != null && unreadyPod.getMetadata().getName() != null) {
            String podName = unreadyPod.getMetadata().getName();
            coreV1Api.deleteNamespacedPod(podName, namespace)
                    .gracePeriodSeconds(5)
                    .propagationPolicy("Background")
                    .body(new V1DeleteOptions())
                    .execute();
            return new RemediationExecutionResult(
                    true, deploymentName,
                    "Recycled unready pod " + podName + " before bounded scale-up to restore healthy capacity"
            );
        }

        int currentReplicas = deployment.getSpec() != null && deployment.getSpec().getReplicas() != null
                ? deployment.getSpec().getReplicas() : 1;
        int desiredReplicas = Math.min(
                currentReplicas + Math.max(1, properties.getMaxScaleStep()),
                Math.max(1, properties.getMaxAutoScaleReplicas())
        );

        if (desiredReplicas <= currentReplicas) {
            return new RemediationExecutionResult(false, deploymentName,
                    "Deployment " + deploymentName + " is already at the bounded auto-scale ceiling of "
                            + properties.getMaxAutoScaleReplicas() + " replicas");
        }

        V1Scale scale = appsV1Api.readNamespacedDeploymentScale(deploymentName, namespace).execute();
        if (scale.getSpec() != null) {
            scale.getSpec().replicas(desiredReplicas);
        }
        appsV1Api.replaceNamespacedDeploymentScale(deploymentName, namespace, scale).execute();

        return new RemediationExecutionResult(true, deploymentName,
                "Scaled deployment " + deploymentName + " from " + currentReplicas + " to " + desiredReplicas);
    }

    // ── New actions ───────────────────────────────────────────────────────────

    private RemediationExecutionResult scaleDown(String namespace, String deploymentName) throws ApiException, IOException {
        ensureApis();
        V1Deployment deployment = appsV1Api.readNamespacedDeployment(deploymentName, namespace).execute();
        int currentReplicas = deployment.getSpec() != null && deployment.getSpec().getReplicas() != null
                ? deployment.getSpec().getReplicas() : 1;
        int desiredReplicas = Math.max(1, currentReplicas - 1);

        if (desiredReplicas >= currentReplicas) {
            return new RemediationExecutionResult(false, deploymentName,
                    "Cannot scale down below 1 replica — " + deploymentName + " is already at " + currentReplicas);
        }

        V1Scale scale = appsV1Api.readNamespacedDeploymentScale(deploymentName, namespace).execute();
        if (scale.getSpec() != null) {
            scale.getSpec().replicas(desiredReplicas);
        }
        appsV1Api.replaceNamespacedDeploymentScale(deploymentName, namespace, scale).execute();

        return new RemediationExecutionResult(true, deploymentName,
                "Scaled down deployment " + deploymentName + " from " + currentReplicas + " to " + desiredReplicas);
    }

    private RemediationExecutionResult rollbackDeployment(String namespace, String deploymentName) throws ApiException, IOException {
        ensureApis();
        V1Deployment deployment = appsV1Api.readNamespacedDeployment(deploymentName, namespace).execute();

        Map<String, String> annotations = deployment.getMetadata() != null
                ? deployment.getMetadata().getAnnotations() : null;
        String currentRevisionStr = annotations != null
                ? annotations.get("deployment.kubernetes.io/revision") : null;

        if (currentRevisionStr == null) {
            return new RemediationExecutionResult(false, deploymentName,
                    "Cannot determine current revision for rollback — annotation missing");
        }

        int currentRevision;
        try {
            currentRevision = Integer.parseInt(currentRevisionStr);
        } catch (NumberFormatException ex) {
            return new RemediationExecutionResult(false, deploymentName,
                    "Invalid revision annotation: " + currentRevisionStr);
        }
        int targetRevision = currentRevision - 1;

        // Find the ReplicaSet with the previous revision
        List<V1ReplicaSet> rsList = appsV1Api.listNamespacedReplicaSet(namespace)
                .labelSelector("app=" + deploymentName)
                .execute()
                .getItems();

        V1ReplicaSet previousRS = rsList.stream()
                .filter(rs -> {
                    Map<String, String> rsAnnotations = rs.getMetadata() != null
                            ? rs.getMetadata().getAnnotations() : null;
                    String rev = rsAnnotations != null
                            ? rsAnnotations.get("deployment.kubernetes.io/revision") : null;
                    return String.valueOf(targetRevision).equals(rev);
                })
                .findFirst()
                .orElse(null);

        if (previousRS == null || previousRS.getSpec() == null || previousRS.getSpec().getTemplate() == null) {
            return new RemediationExecutionResult(false, deploymentName,
                    "No previous revision " + targetRevision + " found for rollback");
        }

        // Patch deployment pod template with the previous RS template
        deployment.getSpec().setTemplate(previousRS.getSpec().getTemplate());
        if (deployment.getMetadata() != null && deployment.getMetadata().getAnnotations() != null) {
            deployment.getMetadata().getAnnotations()
                    .put("deployment.kubernetes.io/revision", String.valueOf(currentRevision + 1));
        }

        appsV1Api.replaceNamespacedDeployment(deploymentName, namespace, deployment).execute();

        logger.info("Rolled back deployment {}/{} to revision {}", namespace, deploymentName, targetRevision);
        return new RemediationExecutionResult(true, deploymentName,
                "Rolled back deployment " + deploymentName + " from revision " + currentRevision
                        + " to revision " + targetRevision);
    }

    private RemediationExecutionResult deleteCrashloopingPods(String namespace, String appLabel) throws ApiException, IOException {
        ensureApis();
        List<V1Pod> pods = coreV1Api.listNamespacedPod(namespace)
                .labelSelector("app=" + appLabel)
                .execute()
                .getItems();

        int deleted = 0;
        for (V1Pod pod : pods) {
            if (isCrashLooping(pod) && pod.getMetadata() != null && pod.getMetadata().getName() != null) {
                String podName = pod.getMetadata().getName();
                coreV1Api.deleteNamespacedPod(podName, namespace)
                        .gracePeriodSeconds(0)
                        .propagationPolicy("Background")
                        .body(new V1DeleteOptions())
                        .execute();
                logger.info("Deleted crash-looping pod {}/{}", namespace, podName);
                deleted++;
            }
        }

        if (deleted == 0) {
            return new RemediationExecutionResult(false, appLabel,
                    "No CrashLoopBackOff pods found for selector app=" + appLabel);
        }
        return new RemediationExecutionResult(true, appLabel,
                "Deleted " + deleted + " crash-looping pod(s) for " + appLabel + " — they will restart fresh");
    }

    private RemediationExecutionResult forceRecreateDeployment(String namespace, String deploymentName) throws ApiException, IOException {
        ensureApis();
        // Trigger a rolling restart by patching the restart annotation (equivalent to kubectl rollout restart)
        String timestamp = Instant.now().toString();
        String body = String.format(
                "{\"spec\":{\"template\":{\"metadata\":{\"annotations\":{\"kubectl.kubernetes.io/restartedAt\":\"%s\"}}}}}",
                timestamp
        );
        appsV1Api.patchNamespacedDeployment(deploymentName, namespace, new V1Patch(body)).execute();

        logger.info("Force-recreated deployment {}/{} at {}", namespace, deploymentName, timestamp);
        return new RemediationExecutionResult(true, deploymentName,
                "Triggered rolling restart of all pods in deployment " + deploymentName);
    }

    private RemediationExecutionResult cordonNode(String nodeName) throws ApiException, IOException {
        ensureApis();
        String body = "{\"spec\":{\"unschedulable\":true}}";
        coreV1Api.patchNode(nodeName, new V1Patch(body)).execute();

        logger.info("Cordoned node {}", nodeName);
        return new RemediationExecutionResult(true, nodeName,
                "Node " + nodeName + " cordoned — no new pods will be scheduled on it");
    }

    private RemediationExecutionResult drainNode(String namespace, String nodeName) throws ApiException, IOException {
        ensureApis();

        // Step 1: cordon the node
        String cordonBody = "{\"spec\":{\"unschedulable\":true}}";
        coreV1Api.patchNode(nodeName, new V1Patch(cordonBody)).execute();
        logger.info("Cordoned node {} before drain", nodeName);

        // Step 2: delete all evictable pods on this node (excluding DaemonSet and static pods)
        List<V1Pod> allPods = coreV1Api.listPodForAllNamespaces()
                .fieldSelector("spec.nodeName=" + nodeName)
                .execute()
                .getItems();

        int evicted = 0;
        for (V1Pod pod : allPods) {
            if (isDaemonSetPod(pod) || isStaticPod(pod)) {
                continue;
            }
            String podNamespace = pod.getMetadata() != null ? pod.getMetadata().getNamespace() : null;
            String podName = pod.getMetadata() != null ? pod.getMetadata().getName() : null;
            if (podNamespace == null || podName == null) {
                continue;
            }
            try {
                coreV1Api.deleteNamespacedPod(podName, podNamespace)
                        .gracePeriodSeconds(30)
                        .propagationPolicy("Background")
                        .body(new V1DeleteOptions())
                        .execute();
                logger.info("Evicted pod {}/{} from node {}", podNamespace, podName, nodeName);
                evicted++;
            } catch (ApiException ex) {
                logger.warn("Could not evict pod {}/{}: {}", podNamespace, podName, ex.getMessage());
            }
        }

        return new RemediationExecutionResult(true, nodeName,
                "Drained node " + nodeName + ": cordoned + evicted " + evicted + " pod(s)");
    }

    // ── Helpers ───────────────────────────────────────────────────────────────

    private boolean isPodReady(V1Pod pod) {
        if (pod.getStatus() == null || pod.getStatus().getConditions() == null) {
            return false;
        }
        return pod.getStatus().getConditions().stream()
                .anyMatch(c -> "Ready".equals(c.getType()) && "True".equalsIgnoreCase(c.getStatus()));
    }

    private boolean isCrashLooping(V1Pod pod) {
        if (pod.getStatus() == null || pod.getStatus().getContainerStatuses() == null) {
            return false;
        }
        return pod.getStatus().getContainerStatuses().stream()
                .anyMatch(cs -> cs.getState() != null
                        && cs.getState().getWaiting() != null
                        && "CrashLoopBackOff".equals(cs.getState().getWaiting().getReason()));
    }

    private boolean isDaemonSetPod(V1Pod pod) {
        if (pod.getMetadata() == null || pod.getMetadata().getOwnerReferences() == null) {
            return false;
        }
        return pod.getMetadata().getOwnerReferences().stream()
                .anyMatch(ref -> "DaemonSet".equals(ref.getKind()));
    }

    private boolean isStaticPod(V1Pod pod) {
        if (pod.getMetadata() == null || pod.getMetadata().getAnnotations() == null) {
            return false;
        }
        return pod.getMetadata().getAnnotations().containsKey("kubernetes.io/config.source");
    }

    private boolean isNamespaceAllowlisted(String namespace) {
        if (namespace == null || namespace.isBlank()) {
            return false;
        }
        if (SYSTEM_NAMESPACES.contains(namespace)) {
            return false;
        }
        Set<String> allowlist = properties.getAllowlistedNamespaces();
        // "*" wildcard allows all non-system namespaces
        if (allowlist.contains("*")) {
            return true;
        }
        return allowlist.stream().anyMatch(v -> v.equalsIgnoreCase(namespace));
    }

    private void ensureApis() throws IOException {
        if (coreV1Api != null && appsV1Api != null) {
            return;
        }
        synchronized (this) {
            if (coreV1Api != null && appsV1Api != null) {
                return;
            }
            ApiClient client = buildClient();
            Configuration.setDefaultApiClient(client);
            coreV1Api = new CoreV1Api(client);
            appsV1Api = new AppsV1Api(client);
        }
    }

    private ApiClient buildClient() throws IOException {
        String kubeconfigPath = properties.getKubeconfigPath();
        if (kubeconfigPath != null && !kubeconfigPath.isBlank() && Files.exists(Path.of(kubeconfigPath))) {
            try (FileReader fileReader = new FileReader(kubeconfigPath)) {
                KubeConfig kubeConfig = KubeConfig.loadKubeConfig(fileReader);
                return ClientBuilder.kubeconfig(kubeConfig).build();
            }
        }
        try {
            return ClientBuilder.cluster().build();
        } catch (RuntimeException ex) {
            logger.info("In-cluster Kubernetes client configuration unavailable, falling back to default client discovery");
        }
        return Config.defaultClient();
    }
}
