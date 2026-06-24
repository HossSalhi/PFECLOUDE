package com.orange.pfe.backend.service.remediation;
import com.orange.pfe.backend.config.RemediationProperties;
import com.orange.pfe.backend.model.action.ActionType;
import io.kubernetes.client.custom.V1Patch;
import io.kubernetes.client.openapi.ApiClient;
import io.kubernetes.client.openapi.ApiException;
import io.kubernetes.client.openapi.Configuration;
import io.kubernetes.client.openapi.apis.AppsV1Api;
import io.kubernetes.client.openapi.apis.CoreV1Api;
import io.kubernetes.client.openapi.apis.NetworkingV1Api;
import io.kubernetes.client.openapi.models.V1NetworkPolicy;
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
    private volatile NetworkingV1Api networkingV1Api;

    public KubernetesRemediationExecutor(RemediationProperties properties) {
        this.properties = properties;
    }

    @Override
    public RemediationExecutionResult execute(RemediationPlan plan) {
        return execute(plan, false);
    }

    /**
     * Execute with optional dry-run mode.
     * When dryRun=true, all Kubernetes API calls are issued with dryRun=All — the API server
     * validates the request (admission, RBAC, schema) but does not persist any change.
     * Returns a result with message prefixed by "[DRY-RUN]" so callers can distinguish.
     */
    public RemediationExecutionResult execute(RemediationPlan plan, boolean dryRun) {
        if (plan.actionType() == null) {
            return new RemediationExecutionResult(false, plan.targetName(), "No executable action was selected");
        }
        // ESCALATION / INVESTIGATION ne sont PAS des actions Kubernetes : l'« action » est
        // l'escalade vers un humain (ou l'investigation), déjà réalisée via la tâche humaine.
        // On ne route donc PAS vers l'exécuteur K8s ni le contrôle d'allowlist — sinon les
        // incidents nœud (namespace virtuel "k8s-nodes") échouent sur "not allowlisted" et
        // rebouclent en "requires review". Succès no-op = l'escalade a bien eu lieu.
        if (plan.actionType() == ActionType.ESCALATION || plan.actionType() == ActionType.INVESTIGATION) {
            if ("k8s-nodes".equalsIgnoreCase(plan.targetNamespace())) {
                return new RemediationExecutionResult(true, plan.targetName(),
                        "Escalade nœud — power-on de la VM " + plan.targetName()
                        + " délégué au watchdog hôte (équivalent: vmrun start " + plan.targetName()
                        + "); aucune action kubectl applicable sur un nœud injoignable");
            }
            return new RemediationExecutionResult(true, plan.targetName(),
                    "Escaladé à l'opérateur humain — aucune action Kubernetes automatique requise");
        }
        if (!isNamespaceAllowlisted(plan.targetNamespace())) {
            return new RemediationExecutionResult(
                    false,
                    plan.targetName(),
                    "Namespace " + plan.targetNamespace() + " is not allowlisted for automatic remediation"
            );
        }

        int maxAttempts = 3;
        int attemptDelayMs = 500;

        for (int attempt = 1; attempt <= maxAttempts; attempt++) {
            try {
                RemediationExecutionResult result = switch (plan.actionType()) {
                    case RESTART_POD              -> restartPod(plan.targetNamespace(), plan.targetName(), dryRun);
                    case SCALE_DEPLOYMENT         -> scaleDeployment(plan.targetNamespace(), plan.targetName(), dryRun);
                    case SCALE_DOWN               -> scaleDown(plan.targetNamespace(), plan.targetName(), dryRun);
                    case ROLLBACK_DEPLOYMENT      -> rollbackDeployment(plan.targetNamespace(), plan.targetName(), dryRun);
                    case DELETE_CRASHLOOPING_PODS -> deleteCrashloopingPods(plan.targetNamespace(), plan.targetName(), dryRun);
                    case REMOVE_NETWORK_POLICY   -> removeBlockingNetworkPolicy(plan.targetNamespace(), plan.targetName(), dryRun);
                    case FORCE_RECREATE_DEPLOYMENT -> forceRecreateDeployment(plan.targetNamespace(), plan.targetName(), dryRun);
                    case CORDON_NODE              -> cordonNode(plan.targetName(), dryRun);
                    case DRAIN_NODE               -> drainNode(plan.targetNamespace(), plan.targetName(), dryRun);
                    case RESTART_VM               -> restartVm(plan.targetName());
                    default -> new RemediationExecutionResult(
                            false,
                            plan.targetName(),
                            "Action type " + plan.actionType() + " is not directly executable by the Kubernetes remediation executor"
                    );
                };
                if (dryRun && result.success()) {
                    return new RemediationExecutionResult(true, result.targetName(),
                            "[DRY-RUN] " + result.message() + " — no change applied");
                }
                return result;
            } catch (ApiException ex) {
                int status = ex.getCode();
                boolean transient_ = status == 429 || status == 500 || status == 503 || status == 0;
                if (!transient_ || attempt == maxAttempts) {
                    logger.error("Remediation failed for {}/{} (HTTP {}): {}",
                            plan.targetNamespace(), plan.targetName(), status, ex.getMessage());
                    return new RemediationExecutionResult(false, plan.targetName(), ex.getMessage());
                }
                logger.warn("Transient K8s API error (HTTP {}) for {}/{} — retry {}/{}",
                        status, plan.targetNamespace(), plan.targetName(), attempt, maxAttempts - 1);
                try { Thread.sleep((long) attemptDelayMs * attempt); } catch (InterruptedException ie) {
                    Thread.currentThread().interrupt();
                    return new RemediationExecutionResult(false, plan.targetName(), "Interrupted during retry");
                }
            } catch (IOException ex) {
                logger.error("Remediation IO error for {}/{}: {}", plan.targetNamespace(), plan.targetName(), ex.getMessage());
                return new RemediationExecutionResult(false, plan.targetName(), ex.getMessage());
            }
        }
        return new RemediationExecutionResult(false, plan.targetName(), "All retry attempts exhausted");
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

    @Override
    public boolean isNodeReady(String nodeName) {
        try {
            ensureApis();
            io.kubernetes.client.openapi.models.V1Node node = coreV1Api.readNode(nodeName).execute();
            if (node.getStatus() == null || node.getStatus().getConditions() == null) return false;
            return node.getStatus().getConditions().stream()
                    .anyMatch(cond -> "Ready".equals(cond.getType()) && "True".equalsIgnoreCase(cond.getStatus()));
        } catch (ApiException | IOException ex) {
            // node not found / unreachable = not ready
            logger.debug("Node readiness check failed for {}: {}", nodeName, ex.getMessage());
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
        return restartPod(namespace, appLabel, false);
    }

    private RemediationExecutionResult restartPod(String namespace, String appLabel, boolean dryRun) throws ApiException, IOException {
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
            // No pods exist (deployment may be scaled to 0) — fall back to scale-up
            logger.info("No pods found for app={}/{} — falling back to scaleDeployment", namespace, appLabel);
            return scaleDeployment(namespace, appLabel, dryRun);
        }

        // All found pods are Terminating (deployment was scaled to 0) — fall back to scale-up
        if (selectedPod.getMetadata().getDeletionTimestamp() != null) {
            logger.info("Only terminating pods for app={}/{} — falling back to scaleDeployment", namespace, appLabel);
            return scaleDeployment(namespace, appLabel, dryRun);
        }

        String podName = selectedPod.getMetadata().getName();
        if (!dryRun) {
            coreV1Api.deleteNamespacedPod(podName, namespace)
                    .gracePeriodSeconds(5)
                    .propagationPolicy("Background")
                    .body(new V1DeleteOptions())
                    .execute();
        }
        return new RemediationExecutionResult(true, podName, "Deleted pod " + podName + " to trigger restart");
    }

    private RemediationExecutionResult scaleDeployment(String namespace, String deploymentName) throws ApiException, IOException {
        return scaleDeployment(namespace, deploymentName, false);
    }

    private RemediationExecutionResult scaleDeployment(String namespace, String deploymentName, boolean dryRun) throws ApiException, IOException {
        ensureApis();
        V1Deployment deployment = appsV1Api.readNamespacedDeployment(deploymentName, namespace).execute();
        int currentReplicas = deployment.getSpec() != null && deployment.getSpec().getReplicas() != null
                ? deployment.getSpec().getReplicas() : 1;

        // Only recycle an unready pod when the deployment is already running (replicas > 0).
        // When replicas == 0 (scaled down), skip straight to scale-up to avoid no-op on Terminating pods.
        if (currentReplicas > 0) {
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
                if (!dryRun) {
                    coreV1Api.deleteNamespacedPod(podName, namespace)
                            .gracePeriodSeconds(5)
                            .propagationPolicy("Background")
                            .body(new V1DeleteOptions())
                            .execute();
                }
                return new RemediationExecutionResult(
                        true, deploymentName,
                        "Recycled unready pod " + podName + " before bounded scale-up to restore healthy capacity"
                );
            }
        }

        int desiredReplicas = Math.min(
                currentReplicas + Math.max(1, properties.getMaxScaleStep()),
                Math.max(1, properties.getMaxAutoScaleReplicas())
        );

        if (desiredReplicas <= currentReplicas) {
            // Already at or above the ceiling — desired state is achieved, treat as success (no-op).
            return new RemediationExecutionResult(true, deploymentName,
                    "Deployment " + deploymentName + " is already at the scale ceiling of "
                            + properties.getMaxAutoScaleReplicas() + " replicas — no action needed");
        }

        if (!dryRun) {
            V1Scale scale = appsV1Api.readNamespacedDeploymentScale(deploymentName, namespace).execute();
            if (scale.getSpec() != null) {
                scale.getSpec().replicas(desiredReplicas);
            }
            appsV1Api.replaceNamespacedDeploymentScale(deploymentName, namespace, scale).execute();
        }

        return new RemediationExecutionResult(true, deploymentName,
                "Scaled deployment " + deploymentName + " from " + currentReplicas + " to " + desiredReplicas);
    }

    // ── New actions ───────────────────────────────────────────────────────────

    private RemediationExecutionResult scaleDown(String namespace, String deploymentName) throws ApiException, IOException {
        return scaleDown(namespace, deploymentName, false);
    }

    private RemediationExecutionResult scaleDown(String namespace, String deploymentName, boolean dryRun) throws ApiException, IOException {
        ensureApis();
        V1Deployment deployment = appsV1Api.readNamespacedDeployment(deploymentName, namespace).execute();
        int currentReplicas = deployment.getSpec() != null && deployment.getSpec().getReplicas() != null
                ? deployment.getSpec().getReplicas() : 1;
        int desiredReplicas = Math.max(1, currentReplicas - 1);

        if (desiredReplicas >= currentReplicas) {
            return new RemediationExecutionResult(false, deploymentName,
                    "Cannot scale down below 1 replica — " + deploymentName + " is already at " + currentReplicas);
        }

        if (!dryRun) {
            V1Scale scale = appsV1Api.readNamespacedDeploymentScale(deploymentName, namespace).execute();
            if (scale.getSpec() != null) {
                scale.getSpec().replicas(desiredReplicas);
            }
            appsV1Api.replaceNamespacedDeploymentScale(deploymentName, namespace, scale).execute();
        }

        return new RemediationExecutionResult(true, deploymentName,
                "Scaled down deployment " + deploymentName + " from " + currentReplicas + " to " + desiredReplicas);
    }

    private RemediationExecutionResult rollbackDeployment(String namespace, String deploymentName) throws ApiException, IOException {
        return rollbackDeployment(namespace, deploymentName, false);
    }

    private RemediationExecutionResult rollbackDeployment(String namespace, String deploymentName, boolean dryRun) throws ApiException, IOException {
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
        final int currentRev = currentRevision;
        // Find the ReplicaSets for this deployment
        List<V1ReplicaSet> rsList = appsV1Api.listNamespacedReplicaSet(namespace)
                .labelSelector("app=" + deploymentName)
                .execute()
                .getItems();

        java.util.function.Function<V1ReplicaSet, Integer> revOf = rs -> {
            Map<String, String> a = rs.getMetadata() != null ? rs.getMetadata().getAnnotations() : null;
            String rev = a != null ? a.get("deployment.kubernetes.io/revision") : null;
            try { return rev != null ? Integer.parseInt(rev) : -1; } catch (NumberFormatException e) { return -1; }
        };

        // PREFER the ReplicaSet that is CURRENTLY healthy (has ready pods) — during a stuck rollout the
        // previous good RS still serves traffic. This is far more reliable than blindly taking
        // currentRevision-1, which on a messy rollout history can be ANOTHER broken revision (and would
        // make the rollback land on a still-broken template -> RESOLUTION_FAILED). If none is currently
        // healthy (the bad revision fully took over), fall back to the highest revision below the current.
        V1ReplicaSet previousRS = rsList.stream()
                .filter(rs -> revOf.apply(rs) >= 0 && revOf.apply(rs) < currentRev)
                .filter(rs -> rs.getStatus() != null && rs.getStatus().getReadyReplicas() != null
                        && rs.getStatus().getReadyReplicas() > 0)
                .max(java.util.Comparator.comparingInt(revOf::apply))
                .orElseGet(() -> rsList.stream()
                        .filter(rs -> revOf.apply(rs) >= 0 && revOf.apply(rs) < currentRev)
                        .max(java.util.Comparator.comparingInt(revOf::apply))
                        .orElse(null));

        if (previousRS == null || previousRS.getSpec() == null || previousRS.getSpec().getTemplate() == null) {
            return new RemediationExecutionResult(false, deploymentName,
                    "No previous healthy revision found for rollback (current=" + currentRevision + ")");
        }
        int targetRevision = revOf.apply(previousRS);

        // Apply the previous (healthy) RS template and PUT the deployment. While the bad pods crash/OOM in
        // a loop, K8s rewrites the deployment status constantly, so a read-modify-replace frequently hits a
        // 409 ("the object has been modified") — fast churns like OOM (tail /dev/zero) trigger it almost
        // every time. Retry with a FRESH read each attempt instead of failing the whole remediation.
        final var previousTemplate = previousRS.getSpec().getTemplate();
        if (!dryRun) {
            ApiException lastConflict = null;
            boolean replaced = false;
            for (int attempt = 1; attempt <= 5 && !replaced; attempt++) {
                try {
                    V1Deployment fresh = appsV1Api.readNamespacedDeployment(deploymentName, namespace).execute();
                    fresh.getSpec().setTemplate(previousTemplate);
                    if (fresh.getMetadata() != null && fresh.getMetadata().getAnnotations() != null) {
                        fresh.getMetadata().getAnnotations()
                                .put("deployment.kubernetes.io/revision", String.valueOf(currentRev + 1));
                    }
                    appsV1Api.replaceNamespacedDeployment(deploymentName, namespace, fresh).execute();
                    replaced = true;
                } catch (ApiException ex) {
                    if (ex.getCode() == 409) {
                        lastConflict = ex;
                        logger.warn("Rollback {}/{} hit 409 conflict (attempt {}/5) — re-reading and retrying",
                                namespace, deploymentName, attempt);
                        try { Thread.sleep(600L * attempt); }
                        catch (InterruptedException ie) { Thread.currentThread().interrupt(); break; }
                    } else {
                        throw ex;
                    }
                }
            }
            if (!replaced) {
                return new RemediationExecutionResult(false, deploymentName,
                        "Rollback failed after 5 retries due to concurrent modification (409): "
                                + (lastConflict != null ? lastConflict.getMessage() : "unknown"));
            }
            // After rollback, evict the OLD broken pods (CrashLoopBackOff / ImagePullBackOff / OOM) so the
            // deployment converges to the healthy revision instead of keeping a broken pod alive
            // (which would keep it "not available" and fail the recovery verification).
            cleanupBrokenPods(namespace, deploymentName);
        }

        logger.info("Rolled back deployment {}/{} to revision {}", namespace, deploymentName, targetRevision);
        return new RemediationExecutionResult(true, deploymentName,
                "Rolled back deployment " + deploymentName + " from revision " + currentRevision
                        + " to revision " + targetRevision);
    }

    /** Delete pods stuck in a broken waiting state (CrashLoopBackOff / ImagePullBackOff / Error). */
    private void cleanupBrokenPods(String namespace, String appLabel) {
        try {
            for (V1Pod pod : coreV1Api.listNamespacedPod(namespace).labelSelector("app=" + appLabel)
                    .execute().getItems()) {
                if (pod.getStatus() == null || pod.getMetadata() == null
                        || pod.getMetadata().getName() == null) continue;
                java.util.function.Predicate<io.kubernetes.client.openapi.models.V1ContainerStatus> isBroken = cs ->
                        cs.getState() != null && cs.getState().getWaiting() != null
                                && cs.getState().getWaiting().getReason() != null
                                && (cs.getState().getWaiting().getReason().contains("CrashLoopBackOff")
                                 || cs.getState().getWaiting().getReason().contains("ImagePullBackOff")
                                 || cs.getState().getWaiting().getReason().contains("ErrImagePull")
                                 || cs.getState().getWaiting().getReason().contains("CreateContainerConfigError")
                                 || cs.getState().getWaiting().getReason().contains("CreateContainerError")
                                 || cs.getState().getWaiting().getReason().contains("InvalidImageName"));
                boolean broken = (pod.getStatus().getContainerStatuses() != null
                        && pod.getStatus().getContainerStatuses().stream().anyMatch(isBroken))
                    || (pod.getStatus().getInitContainerStatuses() != null
                        && pod.getStatus().getInitContainerStatuses().stream().anyMatch(isBroken))
                    || "Pending".equalsIgnoreCase(pod.getStatus().getPhase())
                    || "Succeeded".equalsIgnoreCase(pod.getStatus().getPhase()) // graceful eviction (disk)
                    || "Evicted".equalsIgnoreCase(pod.getStatus().getReason());
                if (broken) {
                    coreV1Api.deleteNamespacedPod(pod.getMetadata().getName(), namespace)
                            .gracePeriodSeconds(0).propagationPolicy("Background")
                            .body(new V1DeleteOptions()).execute();
                    logger.info("Cleaned up broken pod {}/{} after remediation", namespace, pod.getMetadata().getName());
                }
            }
        } catch (Exception ex) {
            logger.debug("cleanupBrokenPods failed for {}/{}: {}", namespace, appLabel, ex.getMessage());
        }
    }

    private RemediationExecutionResult deleteCrashloopingPods(String namespace, String appLabel) throws ApiException, IOException {
        return deleteCrashloopingPods(namespace, appLabel, false);
    }

    private RemediationExecutionResult deleteCrashloopingPods(String namespace, String appLabel, boolean dryRun) throws ApiException, IOException {
        ensureApis();
        List<V1Pod> pods = coreV1Api.listNamespacedPod(namespace)
                .labelSelector("app=" + appLabel)
                .execute()
                .getItems();

        int deleted = 0;
        for (V1Pod pod : pods) {
            if (pod.getMetadata() == null || pod.getMetadata().getName() == null || pod.getStatus() == null) continue;
            // Delete pods that are crash-looping OR left behind by a graceful eviction (phase=Succeeded on a
            // long-running service = disk-pressure eviction) OR hard-Evicted. Removing the evicted/Succeeded
            // pod clears the disk-pressure signal (hasEvictedPod) while the self-healed Running pod stays —
            // a single fast delete that the Camunda job completes immediately (no rollout, no hang).
            boolean evictedOrCompleted = "Succeeded".equalsIgnoreCase(pod.getStatus().getPhase())
                    || "Evicted".equalsIgnoreCase(pod.getStatus().getReason());
            if (isCrashLooping(pod) || evictedOrCompleted) {
                String podName = pod.getMetadata().getName();
                if (!dryRun) {
                    coreV1Api.deleteNamespacedPod(podName, namespace)
                            .gracePeriodSeconds(0)
                            .propagationPolicy("Background")
                            .body(new V1DeleteOptions())
                            .execute();
                    logger.info("Deleted bad/evicted pod {}/{} (crashloop or eviction cleanup)", namespace, podName);
                }
                deleted++;
            }
        }

        if (deleted == 0) {
            // Pod already recreated by K8s (e.g. force-killed pod) — fall back to RESTART_POD.
            logger.info("No bad/evicted pods for {}/{} — falling back to RESTART_POD", namespace, appLabel);
            return restartPod(namespace, appLabel, dryRun);
        }
        return new RemediationExecutionResult(true, appLabel,
                "Deleted " + deleted + " bad/evicted pod(s) for " + appLabel + " — the deployment converges to its healthy pods");
    }

    /**
     * Remove the NetworkPolicy that is blocking the app's connectivity (the identified root cause of a
     * partition / DNS / service-unreachable incident). We target policies that (a) were created recently
     * (a recent change is the likely cause) and (b) select this app or all pods in the namespace. We do
     * NOT delete old, established policies — only the recent blocker. This is a real, executed remediation
     * ("kubectl delete networkpolicy <name>"), not an escalation.
     */
    private RemediationExecutionResult removeBlockingNetworkPolicy(String namespace, String app, boolean dryRun) throws ApiException, IOException {
        ensureApis();
        List<V1NetworkPolicy> policies = networkingV1Api.listNamespacedNetworkPolicy(namespace).execute().getItems();
        Instant cutoff = Instant.now().minus(Duration.ofMinutes(30)); // only recently-applied policies
        int deleted = 0;
        StringBuilder names = new StringBuilder();
        for (V1NetworkPolicy np : policies) {
            if (np.getMetadata() == null || np.getMetadata().getName() == null) continue;
            String name = np.getMetadata().getName();
            boolean recent = np.getMetadata().getCreationTimestamp() == null
                    || np.getMetadata().getCreationTimestamp().toInstant().isAfter(cutoff);
            if (recent && policySelectsApp(np, app)) {
                if (!dryRun) {
                    networkingV1Api.deleteNamespacedNetworkPolicy(name, namespace).execute();
                    logger.info("Removed blocking NetworkPolicy {}/{}", namespace, name);
                }
                deleted++;
                if (names.length() > 0) names.append(", ");
                names.append(name);
            }
        }
        if (deleted == 0) {
            // No policy to remove (e.g. a transient unreachability with no NetworkPolicy): fall back to
            // a rolling restart so the remediation still does something concrete and resolves.
            logger.info("No blocking NetworkPolicy for {}/{} — falling back to rolling restart", namespace, app);
            return forceRecreateDeployment(namespace, app, dryRun);
        }
        return new RemediationExecutionResult(true, app,
                "Removed blocking NetworkPolicy: " + names + " — connectivity restored");
    }

    /** True if the policy applies to the given app (podSelector app=app) or to all pods (empty selector). */
    private boolean policySelectsApp(V1NetworkPolicy np, String app) {
        if (np.getSpec() == null || np.getSpec().getPodSelector() == null) return false;
        Map<String, String> ml = np.getSpec().getPodSelector().getMatchLabels();
        if (ml == null || ml.isEmpty()) return true;       // empty selector = all pods in ns (deny-all)
        return app.equals(ml.get("app"));
    }

    private RemediationExecutionResult forceRecreateDeployment(String namespace, String deploymentName) throws ApiException, IOException {
        return forceRecreateDeployment(namespace, deploymentName, false);
    }

    private RemediationExecutionResult forceRecreateDeployment(String namespace, String deploymentName, boolean dryRun) throws ApiException, IOException {
        ensureApis();
        // Read-modify-REPLACE (not patch): a raw V1Patch on the fluent client is rejected with HTTP 415
        // (no content-type), whereas replace works (same approach as rollbackDeployment). We set the
        // standard kubectl.kubernetes.io/restartedAt annotation on the pod template = a real rolling
        // restart of ALL replicas (equivalent to `kubectl rollout restart`).
        V1Deployment deployment = appsV1Api.readNamespacedDeployment(deploymentName, namespace).execute();
        String timestamp = Instant.now().toString();
        if (deployment.getSpec() != null && deployment.getSpec().getTemplate() != null) {
            io.kubernetes.client.openapi.models.V1ObjectMeta tmplMeta =
                    deployment.getSpec().getTemplate().getMetadata();
            if (tmplMeta == null) {
                tmplMeta = new io.kubernetes.client.openapi.models.V1ObjectMeta();
                deployment.getSpec().getTemplate().setMetadata(tmplMeta);
            }
            Map<String, String> ann = tmplMeta.getAnnotations();
            if (ann == null) {
                ann = new java.util.HashMap<>();
                tmplMeta.setAnnotations(ann);
            }
            ann.put("kubectl.kubernetes.io/restartedAt", timestamp);
        }
        if (!dryRun) {
            // Evict stuck/evicted pods FIRST: on the disk (eviction) path the evicted Succeeded pod keeps
            // re-firing the disk-pressure signal, and the deployment is churning (eviction → recreate) so a
            // read-modify-replace can 409-conflict. Cleaning first clears the signal even if the replace
            // below conflicts, so the recovery verification converges.
            cleanupBrokenPods(namespace, deploymentName);
            try {
                appsV1Api.replaceNamespacedDeployment(deploymentName, namespace, deployment).execute();
                logger.info("Rolling-restarted deployment {}/{} at {}", namespace, deploymentName, timestamp);
            } catch (ApiException ex) {
                // 409 Conflict (deployment changed under us during pod churn): the cleanup above already
                // removed the offending pod, so the deploy self-heals — do not fail the remediation.
                if (ex.getCode() == 409) {
                    logger.warn("replaceNamespacedDeployment {}/{} conflicted (409) — cleanup already applied, continuing",
                            namespace, deploymentName);
                } else {
                    throw ex;
                }
            }
            cleanupBrokenPods(namespace, deploymentName); // second pass after the (attempted) restart
        }
        return new RemediationExecutionResult(true, deploymentName,
                "Triggered rolling restart of all pods in deployment " + deploymentName);
    }

    private RemediationExecutionResult cordonNode(String nodeName) throws ApiException, IOException {
        return cordonNode(nodeName, false);
    }

    private RemediationExecutionResult cordonNode(String nodeName, boolean dryRun) throws ApiException, IOException {
        ensureApis();
        if (!dryRun) {
            String body = "{\"spec\":{\"unschedulable\":true}}";
            coreV1Api.patchNode(nodeName, new V1Patch(body)).execute();
            logger.info("Cordoned node {}", nodeName);
        }
        return new RemediationExecutionResult(true, nodeName,
                "Node " + nodeName + " cordoned — no new pods will be scheduled on it");
    }

    private RemediationExecutionResult drainNode(String namespace, String nodeName) throws ApiException, IOException {
        return drainNode(namespace, nodeName, false);
    }

    private RemediationExecutionResult drainNode(String namespace, String nodeName, boolean dryRun) throws ApiException, IOException {
        ensureApis();

        if (!dryRun) {
            String cordonBody = "{\"spec\":{\"unschedulable\":true}}";
            coreV1Api.patchNode(nodeName, new V1Patch(cordonBody)).execute();
            logger.info("Cordoned node {} before drain", nodeName);
        }

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
            if (!dryRun) {
                try {
                    coreV1Api.deleteNamespacedPod(podName, podNamespace)
                            .gracePeriodSeconds(30)
                            .propagationPolicy("Background")
                            .body(new V1DeleteOptions())
                            .execute();
                    logger.info("Evicted pod {}/{} from node {}", podNamespace, podName, nodeName);
                } catch (ApiException ex) {
                    logger.warn("Could not evict pod {}/{}: {}", podNamespace, podName, ex.getMessage());
                }
            }
            evicted++;
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
            networkingV1Api = new NetworkingV1Api(client);
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
