package com.orange.pfe.backend.service.remediation;
import com.orange.pfe.backend.config.RemediationProperties;
import com.orange.pfe.backend.config.VmPowerProperties;
import com.orange.pfe.backend.service.vmpower.VmPowerPolicy;
import com.orange.pfe.backend.service.vmpower.WatchdogClient;
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
import org.springframework.beans.factory.annotation.Autowired;
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

    // Fixed values for the controlled lab CPU cycle; the Bridge validates the same bounds.
    private static final String CPU_QUOTA_LAB_SERVICE = "helios-cpuload.service";
    private static final int CPU_QUOTA_LAB_PERCENT = 25;
    private static final int CPU_QUOTA_LAB_LEASE_SECONDS = 240;
    // Parametres bornes du service de charge RAM de laboratoire.
    private static final String MEMORY_LAB_SERVICE = "helios-memload.service";
    private static final int MEMORY_LAB_LIMIT_MB = 256;
    private static final int MEMORY_LAB_LEASE_SECONDS = 240;

    // HOST-CPU-AUTOSTOP : arrêt de la charge de LABORATOIRE après résolution CPU confirmée.
    // Action dédiée du Bridge ; la cible (helios-cpuload.service) est résolue côté serveur depuis le nœud.
    private static final String CPU_LOAD_STOP_ACTION = "STOP_CPU_LOAD_SERVICE";
    // CPU-MASTER-20261029 : k8s-master inclus (cible de laboratoire helios-cpuload.service uniquement).
    private static final Set<String> CPU_LAB_WORKERS = Set.of("k8s-worker1", "k8s-worker2", "k8s-master");

    private static final Logger logger = LoggerFactory.getLogger(KubernetesRemediationExecutor.class);

    /** Namespaces the executor will never touch regardless of allowlist configuration. */
    private static final Set<String> SYSTEM_NAMESPACES = Set.of(
            "kube-system", "kube-public", "kube-node-lease",
            "cnpg-system", "cert-manager", "monitoring"
    );

    private final RemediationProperties properties;
    private final BridgeClient bridgeClient;
    private final WatchdogClient watchdogClient;

    private volatile CoreV1Api coreV1Api;
    private volatile AppsV1Api appsV1Api;
    private volatile NetworkingV1Api networkingV1Api;

    public KubernetesRemediationExecutor(RemediationProperties properties) {
        this(properties, (action, incidentType, namespace, target, node) ->
                new BridgeClient.BridgeResponse(false, false, -1,
                        "Bridge client non injecte ; aucune requete envoyee."));
    }

    public KubernetesRemediationExecutor(RemediationProperties properties, BridgeClient bridgeClient) {
        this(properties, bridgeClient, new WatchdogClient(properties, new VmPowerProperties()));
    }

    @Autowired
    public KubernetesRemediationExecutor(
            RemediationProperties properties, BridgeClient bridgeClient, WatchdogClient watchdogClient) {
        this.properties = properties;
        this.bridgeClient = bridgeClient;
        this.watchdogClient = watchdogClient;
    }

    @Override
    public RemediationExecutionResult execute(RemediationPlan plan) {
        return execute(plan, false);
    }

    @Override
    public RemediationExecutionResult stopCpuLoadServiceAfterRecovery(
            String namespace, String node, String incidentType) {
        // Nettoyage post-résolution : arrête le générateur de charge de LABORATOIRE après une
        // récupération CPU confirmée. JAMAIS une commande arbitraire — l'action dédiée du Bridge
        // STOP_CPU_LOAD_SERVICE résout la cible (helios-cpuload.service) côté serveur depuis le nœud
        // et refuse toute autre cible. Succès uniquement si le Bridge confirme l'état EFFECTIF.
        if (!properties.isAutoStopCpuLoadEnabled()) {
            return new RemediationExecutionResult(false, node,
                    "Arret charge CPU desactive (remediation.auto-stop-cpu-load-enabled=false).");
        }
        if (!"k8s-nodes".equalsIgnoreCase(namespace)) {
            return new RemediationExecutionResult(false, node,
                    "Arret charge CPU refuse : namespace cible invalide.");
        }
        if (node == null || !CPU_LAB_WORKERS.contains(node)) {
            return new RemediationExecutionResult(false, node,
                    "Arret charge CPU refuse : noeud hors allowlist des workers CPU de laboratoire.");
        }
        boolean cpuType = "NODE_HIGH_CPU".equalsIgnoreCase(incidentType)
                || "NODE_CPU_SATURATION".equalsIgnoreCase(incidentType);
        if (!cpuType) {
            return new RemediationExecutionResult(false, node,
                    "Arret charge CPU refuse : type d'incident non CPU.");
        }
        if (!properties.isBridgeEnabled()) {
            return new RemediationExecutionResult(false, node,
                    "Bridge desactive ; aucun arret de charge envoye.");
        }
        try {
            // Action générique 5 arguments : aucun 'service' fourni -> le Bridge résout
            // helios-cpuload.service depuis l'allowlist du nœud (REJECTED_TARGET_NOT_ALLOWLISTED sinon).
            BridgeClient.BridgeResponse response = bridgeClient.sendAction(
                    CPU_LOAD_STOP_ACTION, incidentType, namespace, node, node);
            if (response == null) {
                return new RemediationExecutionResult(false, node,
                        "Reponse Bridge absente ; arret de charge non confirme.");
            }
            boolean success = response.ok() && response.execute() && response.exitCode() == 0;
            String message = response.message();
            if (message == null || message.isBlank()) {
                message = success
                        ? "Bridge a arrete helios-cpuload.service."
                        : "Bridge n'a pas confirme l'arret (ok=" + response.ok()
                          + ", execute=" + response.execute() + ", exitCode=" + response.exitCode() + ").";
            }
            return new RemediationExecutionResult(success, node, message);
        } catch (RuntimeException ex) {
            return new RemediationExecutionResult(false, node,
                    "Echec appel Bridge pour l'arret de charge : " + ex.getClass().getSimpleName());
        }
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
                        // LOT 3BG : message rendu conforme aux faits. Ce chemin n execute
                        // plus de power-on depuis le LOT 3BD ; l affirmer serait trompeur
                        // dans les journaux d incident et les rapports.
                        "Escalade nœud — aucune action kubectl applicable sur un nœud injoignable ; "
                        + "le rallumage éventuel de " + plan.targetName() + " relève du chemin "
                        + "gouverné HELIOS (Policy → PolicyGate → Bridge → hyperviseur), "
                        + "pas de ce worker");
            }
            return new RemediationExecutionResult(true, plan.targetName(),
                    "Escaladé à l'opérateur humain — aucune action Kubernetes automatique requise");
        }
        // Actions ciblant un NŒUD (RESTART_VM = power-on VM ; HOST_* = commande hôte via pod privilégié
        // créé dans `pfe`) : elles n'opèrent PAS dans le namespace cible "k8s-nodes" (virtuel) -> exemptées
        // du contrôle d'allowlist de namespace (sinon elles échouent sur "not allowlisted").
        boolean nodeTargetingAction = plan.actionType() == ActionType.RESTART_VM
                || plan.actionType() == ActionType.HOST_SYNC_TIME
                || plan.actionType() == ActionType.HOST_IMAGE_GC
                || plan.actionType() == ActionType.HOST_RECLAIM_DISK
                || plan.actionType() == ActionType.HOST_SERVICE_CPU_QUOTA
                || plan.actionType() == ActionType.HOST_SERVICE_MEMORY_LIMIT;
        if (!nodeTargetingAction && !isNamespaceAllowlisted(plan.targetNamespace())) {
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
                    case FORCE_DELETE_POD        -> forceDeleteStuckPods(plan.targetNamespace(), plan.targetName(), dryRun);
                    case REMOVE_NETWORK_POLICY   -> removeBlockingNetworkPolicy(plan.targetNamespace(), plan.targetName(), dryRun);
                    case FORCE_RECREATE_DEPLOYMENT -> forceRecreateDeployment(plan.targetNamespace(), plan.targetName(), dryRun);
                    case CORDON_NODE              -> cordonNode(plan.targetName(), dryRun);
                    case DRAIN_NODE               -> drainNode(plan.targetNamespace(), plan.targetName(), dryRun);
                    case RESTART_VM               -> restartVm(plan.targetName(), plan.incidentId());
                    case HOST_SYNC_TIME           -> runHostCommand(plan.targetName(), "sync-time",
                            "chronyc makestep || systemctl restart chronyd || systemctl restart systemd-timesyncd", dryRun);
                    case HOST_RECLAIM_DISK -> executeHostDiskReclaim(plan, dryRun);
                    case HOST_SERVICE_CPU_QUOTA -> executeCpuQuotaThroughBridge(plan, dryRun);
                    case HOST_SERVICE_MEMORY_LIMIT -> executeMemoryLimitThroughBridge(plan, dryRun);
                    case HOST_IMAGE_GC            -> new RemediationExecutionResult(
                            false,
                            plan.targetName(),
                            "HOST_IMAGE_GC disabled: destructive image pruning is prohibited on the air-gapped cluster; no image was removed"
                    );
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
        NodeReadinessObservation observation = inspectNodeReadiness(nodeName);
        return observation.apiAvailable() && observation.ready();
    }

    @Override
    public NodeReadinessObservation inspectNodeReadiness(String nodeName) {

        if (nodeName == null || nodeName.isBlank()) {
            return new NodeReadinessObservation(
                    false, false, "UNKNOWN",
                    "Node name is missing"
            );
        }

        try {
            ensureApis();

            io.kubernetes.client.openapi.models.V1Node node =
                    coreV1Api.readNode(nodeName).execute();

            if (node == null) {
                return new NodeReadinessObservation(
                        false, false, "UNKNOWN",
                        "Kubernetes API returned no node"
                );
            }

            if (node.getStatus() == null
                    || node.getStatus().getConditions() == null) {
                return new NodeReadinessObservation(
                        true, false, "MISSING",
                        "Node status or conditions are missing"
                );
            }

            var readyCondition = node.getStatus().getConditions()
                    .stream()
                    .filter(condition -> "Ready".equals(condition.getType()))
                    .findFirst()
                    .orElse(null);

            if (readyCondition == null) {
                return new NodeReadinessObservation(
                        true, false, "MISSING",
                        "Ready condition is missing"
                );
            }

            String conditionStatus = readyCondition.getStatus() == null
                    ? "UNKNOWN"
                    : readyCondition.getStatus();

            boolean ready = "True".equalsIgnoreCase(conditionStatus);
            String reason = readyCondition.getReason() == null ? "" : readyCondition.getReason();

            return new NodeReadinessObservation(
                    true,
                    ready,
                    conditionStatus,
                    "Node " + nodeName + " Ready=" + conditionStatus + (reason.isEmpty() ? "" : " (" + reason + ")"),
                    reason,
                    ready ? readNodeLeaseAgeSeconds(nodeName) : null
            );

        } catch (ApiException | IOException ex) {
            logger.warn(
                    "Kubernetes readiness API failed for node {}: {}",
                    nodeName,
                    ex.getClass().getSimpleName()
            );

            return new NodeReadinessObservation(
                    false,
                    false,
                    "UNKNOWN",
                    "Kubernetes API unavailable for node " + nodeName
            );
        }
    }

    /**
     * Âge (secondes) du dernier renouvellement du Lease {@code kube-node-lease/<nœud>} — renouvelé toutes les ~10 s par
     * un kubelet vivant. {@code null} si illisible : jamais une valeur par défaut « fraîche » (preuve fail-closed).
     */
    private Long readNodeLeaseAgeSeconds(String nodeName) {
        try {
            io.kubernetes.client.openapi.models.V1Lease lease =
                    new io.kubernetes.client.openapi.apis.CoordinationV1Api(coreV1Api.getApiClient())
                            .readNamespacedLease(nodeName, "kube-node-lease").execute();
            if (lease == null || lease.getSpec() == null || lease.getSpec().getRenewTime() == null) {
                return null;
            }
            long ageMs = System.currentTimeMillis() - lease.getSpec().getRenewTime().toInstant().toEpochMilli();
            return Math.max(0L, ageMs / 1000L);
        } catch (Exception ex) {
            logger.debug("Lease du nœud {} illisible ({})", nodeName, ex.getClass().getSimpleName());
            return null;
        }
    }

    @Override
    public PodSetObservation inspectPods(String namespace, String labelSelector) {
        if (namespace == null || namespace.isBlank() || labelSelector == null || labelSelector.isBlank()) {
            return PodSetObservation.unavailable();
        }
        try {
            ensureApis();
            List<V1Pod> items = coreV1Api.listNamespacedPod(namespace).labelSelector(labelSelector).execute()
                    .getItems();
            List<PodSetObservation.PodState> pods = new java.util.ArrayList<>();
            if (items != null) {
                for (V1Pod p : items) {
                    boolean terminating = p.getMetadata() != null && p.getMetadata().getDeletionTimestamp() != null;
                    boolean ready = p.getStatus() != null && p.getStatus().getConditions() != null
                            && p.getStatus().getConditions().stream()
                            .anyMatch(c -> "Ready".equals(c.getType()) && "True".equalsIgnoreCase(c.getStatus()));
                    pods.add(new PodSetObservation.PodState(
                            p.getMetadata() == null ? "" : p.getMetadata().getName(),
                            p.getStatus() == null ? null : p.getStatus().getPhase(),
                            ready && !terminating,
                            p.getMetadata() == null ? null : p.getMetadata().getLabels()));
                }
            }
            return new PodSetObservation(true, pods);
        } catch (ApiException | IOException ex) {
            logger.warn("Lecture des pods {}/{} impossible ({})", namespace, labelSelector, ex.getClass().getSimpleName());
            return PodSetObservation.unavailable();
        }
    }
    /**
     * Exécute une commande HÔTE sur un nœud, 100% DANS LE CLUSTER : crée un pod PRIVILÉGIÉ éphémère sur
     * le nœud cible (nodeName + hostPID) qui, via `nsenter -t 1`, entre dans l'espace hôte du nœud et
     * lance la commande whitelistée (busybox déjà en cache air-gap). Attend la fin, récupère la sortie,
     * supprime le pod. Aucun Windows / SSH / pare-feu : c'est un pod Kubernetes ordinaire — conforme à
     * « la remédiation se fait dans les VMs du cluster ». (Ne marche que si le nœud est Ready = kubelet up.)
     */
    private RemediationExecutionResult runHostCommand(String node, String label, String shellCmd, boolean dryRun)
            throws ApiException, IOException {
        ensureApis();
        if (node == null || node.isBlank()) {
            return new RemediationExecutionResult(false, "?", "host command '" + label + "': aucun nœud cible");
        }
        if (dryRun) {
            return new RemediationExecutionResult(true, node, "[DRY-RUN] commande hôte '" + label + "' sur " + node);
        }
        String ns = "pfe";
        String podName = "helios-hostexec-" + label.replaceAll("[^a-z0-9-]", "") + "-" + (System.currentTimeMillis() % 100000);
        // POST du pod en JSON BRUT (DynamicKubernetesApi) plutôt que via le modèle V1Pod : le modèle du
        // client Java sérialise un champ `overhead` parasite -> l'admission rejette ("Pod Overhead set
        // without RuntimeClass"), alors que le MÊME pod via kubectl (y compris en tant que le SA backend)
        // passe. Le JSON brut envoie exactement le spec voulu, sans overhead.
        String escaped = shellCmd.replace("\\", "\\\\").replace("\"", "\\\"");
        String podJson = "{"
                + "\"apiVersion\":\"v1\",\"kind\":\"Pod\","
                + "\"metadata\":{\"name\":\"" + podName + "\",\"namespace\":\"" + ns + "\",\"labels\":{\"app\":\"helios-hostexec\"}},"
                + "\"spec\":{\"nodeName\":\"" + node + "\",\"hostPID\":true,\"restartPolicy\":\"Never\","
                + "\"containers\":[{\"name\":\"exec\",\"image\":\"busybox:1.36\",\"securityContext\":{\"privileged\":true},"
                + "\"command\":[\"nsenter\",\"-t\",\"1\",\"-m\",\"-u\",\"-i\",\"-n\",\"-p\",\"--\",\"sh\",\"-c\",\"" + escaped + "\"]}]}}";
        try {
            io.kubernetes.client.util.generic.dynamic.DynamicKubernetesApi podApi =
                    new io.kubernetes.client.util.generic.dynamic.DynamicKubernetesApi("", "v1", "pods", coreV1Api.getApiClient());
            podApi.create(new io.kubernetes.client.util.generic.dynamic.DynamicKubernetesObject(
                    com.google.gson.JsonParser.parseString(podJson).getAsJsonObject())).throwsApiException();
            logger.info("host-exec: pod privilégié {} créé sur {} (op={})", podName, node, label);
            String phase = "Pending";
            for (int i = 0; i < 25; i++) { // ~75s max
                Thread.sleep(3000);
                try {
                    V1Pod p = coreV1Api.readNamespacedPod(podName, ns).execute();
                    phase = (p.getStatus() != null && p.getStatus().getPhase() != null) ? p.getStatus().getPhase() : "Unknown";
                } catch (ApiException transientRead) {
                    continue;
                }
                if ("Succeeded".equals(phase) || "Failed".equals(phase)) break;
            }
            String out = "";
            try { out = coreV1Api.readNamespacedPodLog(podName, ns).execute(); } catch (Exception ignore) {}
            boolean ok = "Succeeded".equals(phase);
            logger.info("host-exec: op={} sur {} -> {} ({})", label, node, phase, out.strip());
            return new RemediationExecutionResult(ok, node,
                    "Commande hôte '" + label + "' exécutée DANS LE CLUSTER sur " + node + " -> " + phase
                            + (out.isBlank() ? "" : " | " + out.strip()));
        } catch (InterruptedException ie) {
            Thread.currentThread().interrupt();
            return new RemediationExecutionResult(false, node, "commande hôte '" + label + "' interrompue");
        } finally {
            try {
                coreV1Api.deleteNamespacedPod(podName, ns).gracePeriodSeconds(0)
                        .propagationPolicy("Background").body(new V1DeleteOptions()).execute();
            } catch (Exception ignore) {}
        }
    }

    // ── Existing actions ──────────────────────────────────────────────────────

    /**
     * VMDOWN-POLICY-20261105 — mise sous tension d'une VM par le watchdog hôte, sous la politique UNIQUE
     * {@link VmPowerPolicy} (table fermée, noms EXACTS : route CAMUNDA {k8s-worker2, k8s-worker1, k8s-lb2} ; .160
     * en DÉTECTION SEULE donc refusée ici ; .153 sans aucune autorisation ; tout le reste refusé).
     *
     * <p>Ordre des gardes (chacun AVANT le suivant, aucun appel réseau tant qu'un garde refuse) :
     * <ol>
     *   <li>garde STATIQUE de la politique ({@code VmPowerPolicy.staticRefusal}) — dernier garde avant
     *       l'hyperviseur, quelle que soit la façon dont le plan a été construit (Policy, orchestration,
     *       approbation humaine) ;</li>
     *   <li>client watchdog configuré (URL + jeton), sinon échec fermé sans repli ;</li>
     *   <li>incident connu (clé d'idempotence {@code X-Helios-Incident}) ;</li>
     *   <li>UN SEUL {@code POST /restart/<nom>} — jamais rejoué : un délai dépassé n'est pas un échec de
     *       démarrage mais un état à vérifier en aval.</li>
     * </ol>
     * Un succès HTTP ne prouve JAMAIS la récupération : le retour au service est vérifié en aval.
     */
    private RemediationExecutionResult restartVm(String nodeName, Long incidentId) {
        java.util.Optional<VmPowerPolicy.Refusal> refusal = VmPowerPolicy.staticRefusal(nodeName);
        if (refusal.isPresent()) {
            logger.warn("RESTART_VM refusé par la politique VM_DOWN ({}) pour {} — aucun appel au watchdog",
                    refusal.get(), nodeName);
            return new RemediationExecutionResult(false, nodeName, switch (refusal.get()) {
                case DETECTION_ONLY -> "RESTART_VM refusé : « " + nodeName + " » est en DÉTECTION SEULE "
                        + "(VM d'infrastructure hors cluster) — redémarrage laissé à l'administrateur, "
                        + "aucune action hyperviseur";
                case NOT_AUTHORIZED -> "RESTART_VM refusé : « " + nodeName + " » (nœud control-plane) n'a aucune "
                        + "autorisation de redémarrage automatique (politique VM_DOWN) — aucune action hyperviseur";
                default -> "RESTART_VM refusé : « " + nodeName + " » n'est pas une cible connue de la politique "
                        + "VM_DOWN (ni nœud Kubernetes autorisé, ni VM d'infrastructure autorisée — hors cluster "
                        + "ou identifiant inconnu) — aucune action hyperviseur";
            });
        }
        // Une seule route par VM : ce point d'exécution est celui de la chaîne CAMUNDA. Une cible relevée par le
        // chemin DIRECT du backend (.160) n'est jamais redémarrée par un workflow Camunda (pas d'escalade Camunda).
        if (!VmPowerPolicy.isCamundaRestartable(nodeName)) {
            logger.warn("RESTART_VM refusé pour {} : route {} — hors chaîne Camunda, aucun appel au watchdog",
                    nodeName, VmPowerPolicy.routeOf(nodeName));
            return new RemediationExecutionResult(false, nodeName,
                    "RESTART_VM refusé : « " + nodeName + " » est relevée par le chemin direct du backend "
                    + "(VmPowerWatch), jamais par une remédiation Camunda — aucune action hyperviseur");
        }
        if (!watchdogClient.isConfigured()) {
            logger.warn("RESTART_VM refusé pour {} : watchdog hôte non configuré — échec fermé, aucun repli", nodeName);
            return new RemediationExecutionResult(false, nodeName,
                    "RESTART_VM refusé : watchdog hôte non configuré "
                    + "(remediation.master-watchdog-url + master-watchdog-token requis) — aucun repli");
        }
        if (incidentId == null) {
            return new RemediationExecutionResult(false, nodeName,
                    "RESTART_VM refusé : incident inconnu — l'en-tête X-Helios-Incident (clé d'idempotence) "
                    + "est obligatoire ; aucun appel au watchdog");
        }
        logger.info("Requesting VM power-on for {} (incident #{}) via host watchdog", nodeName, incidentId);
        WatchdogClient.RestartOutcome outcome = watchdogClient.restart(nodeName, incidentId, null);
        String suffix = " (requête " + outcome.requestId() + ")";
        switch (outcome.kind()) {
            case STARTED:
                return new RemediationExecutionResult(true, nodeName,
                        "VM " + nodeName + " démarrée via le watchdog hôte (vmrun start, présence confirmée dans "
                        + "vmrun list) — récupération à vérifier en aval (un 200 n'est PAS une preuve de "
                        + "récupération)" + suffix);
            case ALREADY_RUNNING:
                return new RemediationExecutionResult(true, nodeName,
                        "VM " + nodeName + " déjà allumée (watchdog : ALREADY_RUNNING) — AUCUN démarrage effectué "
                        + "par HELIOS ; récupération à vérifier en aval" + suffix);
            case ALREADY_STARTING:
                return new RemediationExecutionResult(true, nodeName,
                        "Démarrage de " + nodeName + " déjà en cours (watchdog : ALREADY_STARTING) — non relancé ; "
                        + "récupération à vérifier en aval" + suffix);
            case ALREADY_EXECUTED:
                return new RemediationExecutionResult(true, nodeName,
                        "Incident #" + incidentId + " déjà traité par le watchdog (ALREADY_EXECUTED) — non relancé ; "
                        + "récupération à vérifier en aval" + suffix);
            case ACCEPTED_STARTING:
                return new RemediationExecutionResult(true, nodeName,
                        "Démarrage de " + nodeName + " accepté par le watchdog (HTTP 202 STARTING) — en cours, non "
                        + "terminé ; récupération à vérifier en aval" + suffix);
            case TRANSPORT_ERROR:
                logger.error("RESTART_VM {} : aucune réponse exploitable du watchdog ({})", nodeName, outcome.error());
                return new RemediationExecutionResult(false, nodeName,
                        "RESTART_VM échec : aucune réponse exploitable du watchdog hôte (" + outcome.error()
                        + ") — le démarrage a pu avoir lieu ; AUCUN rejeu automatique, état à vérifier" + suffix);
            default:
                logger.error("RESTART_VM {} refusé/échoué par le watchdog : {}", nodeName, outcome.describe());
                return new RemediationExecutionResult(false, nodeName,
                        "RESTART_VM échec : watchdog hôte " + outcome.describe()
                        + (outcome.detail() != null ? " — " + outcome.detail() : "") + suffix);
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
        // SATURATION de replicas : si des pods sont Pending (le cluster ne peut pas tous les planifier),
        // on réduit DIRECTEMENT au nombre de pods qui TIENNENT (Running) au lieu de -1 (sinon il faudrait
        // N cycles). Sinon (scale-down "normal" recommandé par l'IA) on garde le comportement -1.
        int desiredReplicas = Math.max(1, currentReplicas - 1);
        try {
            List<V1Pod> pods = coreV1Api.listNamespacedPod(namespace)
                    .labelSelector("app=" + deploymentName).execute().getItems();
            long running = pods.stream().filter(p -> p.getStatus() != null
                    && "Running".equalsIgnoreCase(p.getStatus().getPhase())).count();
            long pending = pods.stream().filter(p -> p.getStatus() != null
                    && "Pending".equalsIgnoreCase(p.getStatus().getPhase())).count();
            if (pending > 0) {
                desiredReplicas = (int) Math.max(1, running); // réduire au nombre planifiable
            }
        } catch (Exception ex) {
            logger.debug("scaleDown: comptage des pods indisponible pour {}/{}: {}", namespace, deploymentName, ex.getMessage());
        }

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
     * POD BLOQUÉ EN TERMINATING : sa suppression a été demandée mais un FINALIZER le retient. On retire
     * le finalizer par read-modify-REPLACE (évite le 415 d'un raw V1Patch) — dès que la liste des
     * finalizers est vide et que deletionTimestamp est posé, l'API server supprime le pod aussitôt. Un
     * force-delete grace=0 en secours nettoie tout résidu. C'est un vrai `kubectl` exécuté (pas une escalade).
     */
    private RemediationExecutionResult forceDeleteStuckPods(String namespace, String appLabel, boolean dryRun)
            throws ApiException, IOException {
        ensureApis();
        List<V1Pod> pods = coreV1Api.listNamespacedPod(namespace)
                .labelSelector("app=" + appLabel)
                .execute()
                .getItems();
        int cleared = 0;
        for (V1Pod pod : pods) {
            if (pod.getMetadata() == null || pod.getMetadata().getName() == null) continue;
            if (pod.getMetadata().getDeletionTimestamp() == null) continue; // uniquement les pods en Terminating
            String podName = pod.getMetadata().getName();
            if (!dryRun) {
                if (pod.getMetadata().getFinalizers() != null && !pod.getMetadata().getFinalizers().isEmpty()) {
                    try {
                        pod.getMetadata().setFinalizers(new java.util.ArrayList<>());
                        coreV1Api.replaceNamespacedPod(podName, namespace, pod).execute();
                        logger.info("Removed finalizers from stuck-terminating pod {}/{}", namespace, podName);
                    } catch (ApiException ex) {
                        if (ex.getCode() != 404 && ex.getCode() != 409) throw ex;
                    }
                }
                try {
                    coreV1Api.deleteNamespacedPod(podName, namespace)
                            .gracePeriodSeconds(0)
                            .propagationPolicy("Background")
                            .body(new V1DeleteOptions())
                            .execute();
                } catch (ApiException ex) {
                    if (ex.getCode() != 404) throw ex;
                }
            }
            cleared++;
        }
        if (cleared == 0) {
            return new RemediationExecutionResult(true, appLabel,
                    "No stuck-terminating pod for " + appLabel + " (already gone)");
        }
        return new RemediationExecutionResult(true, appLabel,
                "Force-deleted " + cleared + " stuck-terminating pod(s) for " + appLabel + " (finalizer removed)");
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

    private RemediationExecutionResult executeHostDiskReclaim(
            RemediationPlan plan, boolean dryRun) throws ApiException, IOException {

        // DISK-E2E-158-02 : le scénario (k8s-worker1 + NODE_DISK_FULL) n'est gouverné QUE par son drapeau dédié ;
        // l'ancien drapeau global reste le garde-fou de TOUT le reste (chemin d'origine inchangé, même précédence).
        boolean scenario = plan != null
                && HostDiskScenario.matches(plan.targetNamespace(), plan.targetName(), plan.incidentType());

        if (plan == null || (!scenario && !properties.isHostDiskRemediationEnabled())) {
            return new RemediationExecutionResult(
                    false, plan == null ? "?" : plan.targetName(),
                    "HOST_RECLAIM_DISK refusé : fonctionnalité DISK désactivée.");
        }

        if (!"k8s-nodes".equalsIgnoreCase(plan.targetNamespace())) {
            return new RemediationExecutionResult(false, plan.targetName(),
                    "HOST_RECLAIM_DISK refusé : namespace cible invalide.");
        }

        String target = plan.targetName();
        String canonicalTarget = target == null ? null : Set.of("k8s-worker1", "k8s-worker2")
                .stream()
                .filter(candidate -> candidate.equalsIgnoreCase(target.trim()))
                .findFirst()
                .orElse(null);

        if (canonicalTarget == null) {
            return new RemediationExecutionResult(false, target,
                    "HOST_RECLAIM_DISK refusé : cible hors allowlist des workers.");
        }

        String type = plan.incidentType() == null
                ? ""
                : plan.incidentType().trim().toUpperCase(java.util.Locale.ROOT);

        if (!Set.of("NODE_DISK_FULL", "NODE_DISK_PRESSURE", "NODE_INODES_FULL").contains(type)) {
            return new RemediationExecutionResult(false, target,
                    "HOST_RECLAIM_DISK refusé : type d'incident non autorisé.");
        }

        // DISK-E2E-158-01/02 : worker1 + NODE_DISK_FULL = scenario DISK_E2E_158, gouverné par son drapeau DÉDIÉ. Le Bridge
        // est le SEUL acteur qui libère les fichiers EXACTS du scénario (jeton) puis MESURE l'occupation réelle de /.
        // Ce chemin n'appelle JAMAIS le pod hôte privilégié historique : ni en nominal, ni en repli après un échec, ni
        // lorsque le drapeau dédié est désactivé (refus net).
        if (DISK_E2E_TARGET.equals(canonicalTarget) && DISK_E2E_TYPE.equals(type)) {
            if (!properties.isHostDiskE2e158Enabled()) {
                return new RemediationExecutionResult(false, target,
                        "HOST_RECLAIM_DISK refusé : scénario DISK_E2E_158 désactivé (HOST_DISK_E2E_158_ENABLED) ; "
                                + "l'ancien drapeau global ne l'active pas et aucun repli vers le pod hôte n'existe.");
            }
            return executeDiskReclaimThroughBridge(plan, canonicalTarget, dryRun);
        }

        // NODE_DISK_FULL n'a QU'UNE voie : le scénario ci-dessus. Partout ailleurs (k8s-worker2 = .155, primaire CNPG)
        // il est refusé quels que soient les drapeaux : aucun Bridge, aucun pod hôte, aucune commande.
        if (DISK_E2E_TYPE.equals(type)) {
            return new RemediationExecutionResult(false, target,
                    "HOST_RECLAIM_DISK refusé : NODE_DISK_FULL n'a que la voie du scénario DISK_E2E_158 "
                            + "(k8s-worker1) ; k8s-worker2/.155 jamais.");
        }

        // Chemin d'origine, STRICTEMENT INCHANGÉ : NODE_DISK_PRESSURE / NODE_INODES_FULL (ancien drapeau global,
        // mêmes cibles qu'avant ; le drapeau a déjà été contrôlé en tête de méthode).
        return executeLegacyHostReclaim(canonicalTarget, dryRun);
    }

    /**
     * Chemin d'origine : commande hôte bornée dans un pod hôte privilégié. N'est JAMAIS appelé pour le scénario
     * DISK_E2E_158 ni pour NODE_DISK_FULL (verrouillé par KubernetesRemediationExecutorDiskScopeTest).
     */
    RemediationExecutionResult executeLegacyHostReclaim(
            String canonicalTarget, boolean dryRun) throws ApiException, IOException {
        return runHostCommand(
                canonicalTarget,
                "disk-reclaim",
                HOST_DISK_RECLAIM_COMMAND,
                dryRun);
    }

    // DISK-E2E-158-01 : valeurs canoniques du scenario (miroir des constantes du Bridge et du client Bridge).
    static final String DISK_E2E_SCENARIO = "DISK_E2E_158";
    static final String DISK_E2E_TARGET = "k8s-worker1";
    static final String DISK_E2E_TYPE = "NODE_DISK_FULL";

    /**
     * DISK-E2E-158-01 : HOST_RECLAIM_DISK du scenario DISK_E2E_158, execute PAR LE BRIDGE. Un succes ici signifie que le
     * Bridge a supprime les fichiers exacts du scenario ET mesure une occupation de / &lt;= 77 % ; il ne vaut JAMAIS
     * resolution : l'incident reste ouvert jusqu'a la verification independante (Prometheus, echantillons consecutifs).
     */
    private RemediationExecutionResult executeDiskReclaimThroughBridge(
            RemediationPlan plan, String target, boolean dryRun) {

        if (dryRun) {
            return new RemediationExecutionResult(true, target,
                    "Nettoyage DISK Bridge prepare ; aucune requete envoyee.");
        }

        if (!properties.isBridgeEnabled()) {
            return new RemediationExecutionResult(false, target,
                    "Bridge desactive ; aucune requete DISK envoyee.");
        }

        Long incidentId = plan.incidentId();
        if (incidentId == null || incidentId <= 0L) {
            return new RemediationExecutionResult(false, target,
                    "Nettoyage DISK refuse : incident source inconnu ; aucune requete envoyee.");
        }

        try {
            BridgeClient.BridgeResponse response = bridgeClient.sendDiskReclaimAction(
                    ActionType.HOST_RECLAIM_DISK.name(),
                    DISK_E2E_TYPE,
                    "k8s-nodes",
                    target,
                    target,
                    DISK_E2E_SCENARIO,
                    incidentId);

            if (response == null) {
                return new RemediationExecutionResult(false, target,
                        "Reponse Bridge absente ; resultat non confirme.");
            }

            boolean success = response.ok()
                    && response.execute()
                    && response.exitCode() == 0;

            String message = response.message();
            if (message == null || message.isBlank()) {
                message = success
                        ? "Bridge a termine le nettoyage DISK."
                        : "Bridge n'a pas confirme le nettoyage DISK (ok="
                                + response.ok() + ", execute=" + response.execute()
                                + ", exitCode=" + response.exitCode() + ").";
            }

            return new RemediationExecutionResult(success, target, message);
        } catch (RuntimeException ex) {
            logger.warn("Bridge DISK reclaim request failed; exception type={}",
                    ex.getClass().getSimpleName());
            return new RemediationExecutionResult(false, target,
                    "Echec de la requete DISK Bridge ; consulter les journaux Bridge.");
        }
    }

    // Action DISK BORNÉE : vacuum du journal systemd à 200 Mo (rétention), + rapports df (mesure
    // avant/après côté hôte). AUCUN `crictl rmi` (cluster air-gap : image supprimée = irrécupérable),
    // AUCUNE suppression générale de fichiers, AUCUN accès aux données applicatives/PVC.
    // Garantie verrouillée par HostDiskReclaimCommandSafetyTest.
    static final String HOST_DISK_RECLAIM_COMMAND =
            "set -eu; journalctl --vacuum-size=200M; df -hT /; df -ih /";
    private RemediationExecutionResult executeCpuQuotaThroughBridge(
            RemediationPlan plan, boolean dryRun) {

        // CPU-02 : garde a l EXECUTION (couvre les instances Camunda DEJA soumises
        // avant la bascule vers v25 — un drapeau a l ingestion ne les arreterait pas).
        // Meme convention que HOST_RECLAIM_DISK (executeHostDiskReclaim).
        if (plan == null || !properties.isHostCpuRemediationEnabled()) {
            return new RemediationExecutionResult(
                    false, plan == null ? "?" : plan.targetName(),
                    "CPU quota Bridge refuse : remediation CPU backend desactivee "
                    + "(HOST_CPU_REMEDIATION_ENABLED=false ; proprietaire = Camunda v25).");
        }

        String target = plan.targetName();
        String namespace = plan.targetNamespace();
        String type = plan.incidentType();

        if (!"k8s-nodes".equalsIgnoreCase(namespace)) {
            return new RemediationExecutionResult(false, target,
                    "CPU quota Bridge refuse : namespace cible invalide.");
        }

        // CPU-MASTER-20261029 : k8s-master ajouté. Le quota ne vise que helios-cpuload.service (résolu
        // depuis l'allowlist du Bridge) ; etcd/kubelet/apiserver restent protégés par QUOTA_SERVICES_INTERDITS.
        if (target == null || !Set.of("k8s-worker1", "k8s-worker2", "k8s-master").contains(target)) {
            return new RemediationExecutionResult(false, target,
                    "CPU quota Bridge refuse : cible hors allowlist des workers CPU de laboratoire.");
        }

        if ("NODE_HIGH_CPU".equalsIgnoreCase(type)) {
            type = "NODE_HIGH_CPU";
        } else if ("NODE_CPU_SATURATION".equalsIgnoreCase(type)) {
            type = "NODE_CPU_SATURATION";
        } else {
            return new RemediationExecutionResult(false, target,
                    "CPU quota Bridge refuse : type d'incident non autorise.");
        }

        if (dryRun) {
            return new RemediationExecutionResult(true, target,
                    "CPU quota Bridge prepare ; aucune requete envoyee.");
        }

        if (!properties.isBridgeEnabled()) {
            return new RemediationExecutionResult(false, target,
                    "Bridge desactive ; aucune requete CPU envoyee.");
        }

        try {
            BridgeClient.BridgeResponse response = bridgeClient.sendAction(
                    ActionType.HOST_SERVICE_CPU_QUOTA.name(),
                    type,
                    namespace,
                    target,
                    target,
                    CPU_QUOTA_LAB_SERVICE,
                    CPU_QUOTA_LAB_PERCENT,
                    CPU_QUOTA_LAB_LEASE_SECONDS);

            if (response == null) {
                return new RemediationExecutionResult(false, target,
                        "Reponse Bridge absente ; resultat non confirme.");
            }

            boolean success = response.ok()
                    && response.execute()
                    && response.exitCode() == 0;

            String message = response.message();
            if (message == null || message.isBlank()) {
                message = success
                        ? "Bridge a termine l'action CPU."
                        : "Bridge n'a pas confirme l'execution CPU (ok="
                                + response.ok() + ", execute=" + response.execute()
                                + ", exitCode=" + response.exitCode() + ").";
            }

            return new RemediationExecutionResult(success, target, message);
        } catch (RuntimeException ex) {
            logger.warn("Bridge CPU quota request failed; exception type={}",
                    ex.getClass().getSimpleName());
            return new RemediationExecutionResult(false, target,
                    "Echec de la requete CPU Bridge ; consulter les journaux Bridge.");
        }
    }
    private RemediationExecutionResult executeMemoryLimitThroughBridge(
            RemediationPlan plan, boolean dryRun) {
        if (plan == null || !properties.isHostMemoryRemediationEnabled()) {
            return new RemediationExecutionResult(false,
                    plan == null ? "?" : plan.targetName(),
                    "Remediation RAM refusee : flag desactive ou plan absent.");
        }

        String target = plan.targetName();
        String namespace = plan.targetNamespace();
        String type = plan.incidentType();

        if (!"k8s-nodes".equalsIgnoreCase(namespace)) {
            return new RemediationExecutionResult(false, target,
                    "Remediation RAM refusee : namespace invalide.");
        }
        if (!"k8s-worker1".equalsIgnoreCase(target)) {
            return new RemediationExecutionResult(false, target,
                    "Remediation RAM limitee au worker laboratoire k8s-worker1.");
        }
        if (!"NODE_HIGH_MEMORY".equalsIgnoreCase(type)
                && !"NODE_MEMORY_PRESSURE".equalsIgnoreCase(type)) {
            return new RemediationExecutionResult(false, target,
                    "Remediation RAM refusee : type incident non autorise.");
        }
        if (dryRun) {
            return new RemediationExecutionResult(true, target,
                    "[DRY-RUN] Action RAM preparee ; aucune requete Bridge envoyee.");
        }
        if (!properties.isBridgeEnabled()) {
            return new RemediationExecutionResult(false, target,
                    "Bridge desactive ; aucune requete RAM envoyee.");
        }

        try {
            BridgeClient.BridgeResponse response = bridgeClient.sendMemoryLimitAction(
                    ActionType.HOST_SERVICE_MEMORY_LIMIT.name(),
                    type, namespace, target, target,
                    MEMORY_LAB_SERVICE, MEMORY_LAB_LIMIT_MB, MEMORY_LAB_LEASE_SECONDS);

            if (response == null) {
                return new RemediationExecutionResult(false, target,
                        "Reponse Bridge absente ; execution RAM non confirmee.");
            }
            boolean success = response.ok() && response.execute() && response.exitCode() == 0;
            String message = response.message();
            if (message == null || message.isBlank()) {
                message = success
                        ? "Bridge a confirme l'action RAM."
                        : "Bridge n'a pas confirme l'execution RAM (ok="
                            + response.ok() + ", execute=" + response.execute()
                            + ", exitCode=" + response.exitCode() + ").";
            }
            return new RemediationExecutionResult(success, target, message);
        } catch (RuntimeException ex) {
            logger.warn("Bridge RAM request failed; exception type={}",
                    ex.getClass().getSimpleName());
            return new RemediationExecutionResult(false, target,
                    "Echec de la requete RAM Bridge ; consulter les journaux Bridge.");
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
