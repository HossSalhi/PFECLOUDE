package com.orange.pfe.backend.service;

import com.google.gson.reflect.TypeToken;
import com.orange.pfe.backend.config.AiopsPollingProperties;
import com.orange.pfe.backend.dto.AIOpsAnalyzeRequest;
import com.orange.pfe.backend.model.enums.IncidentSource;
import com.orange.pfe.backend.model.enums.IncidentStatus;
import com.orange.pfe.backend.repository.IncidentRepository;
import io.kubernetes.client.openapi.ApiClient;
import io.kubernetes.client.openapi.ApiException;
import io.kubernetes.client.openapi.apis.AppsV1Api;
import io.kubernetes.client.openapi.models.V1Deployment;
import io.kubernetes.client.openapi.models.V1DeploymentList;
import io.kubernetes.client.util.Config;
import io.kubernetes.client.util.Watch;
import jakarta.annotation.PreDestroy;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.boot.context.event.ApplicationReadyEvent;
import org.springframework.context.event.EventListener;
import org.springframework.scheduling.annotation.Async;
import org.springframework.stereotype.Component;

import java.io.IOException;
import java.time.Instant;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicReference;

/**
 * Surveille l'API Kubernetes en temps réel via Watch.
 *
 * Dès qu'un Deployment est créé (ADDED) ou mis à jour de manière significative
 * (MODIFIED — nouveau tag image, changement replicas, nouveau env), une analyse
 * AI est déclenchée après un délai configurable (pour laisser les pods démarrer).
 *
 * Complète le polling périodique (AIOpsPollingScheduler) :
 *  - Watcher  → réaction immédiate aux nouveaux déploiements
 *  - Scheduler → surveillance continue de la santé en cours d'exécution
 */
@Component
public class KubernetesDeploymentWatcher {

    private static final Logger logger = LoggerFactory.getLogger(KubernetesDeploymentWatcher.class);

    // Namespaces système : jamais surveillés automatiquement
    private static final Set<String> EXCLUDED_NAMESPACES = Set.of(
            "kube-system", "kube-public", "kube-node-lease",
            "cnpg-system", "cert-manager", "ingress-nginx",
            "monitoring", "local-path-storage"
    );

    private static final List<IncidentStatus> ACTIVE_STATUSES = List.of(
            IncidentStatus.NEW, IncidentStatus.OPEN,
            IncidentStatus.IN_PROGRESS, IncidentStatus.WAITING_FOR_APPROVAL
    );

    private final AiopsPollingProperties pollingProperties;
    private final AIOpsOrchestrationService orchestrationService;
    private final IncidentRepository incidentRepository;
    private final LeaderElectionService leaderElectionService;
    // "camunda" = détection pilotée par Camunda (process incident-detection déclenché par le polling) :
    // ce watcher direct est alors désactivé pour que TOUTE la détection passe par Camunda.
    @org.springframework.beans.factory.annotation.Value("${helios.detection.pipeline:backend}")
    private String detectionPipeline;

    /** Exécuteur pour les analyses planifiées post-délai. */
    private final ScheduledExecutorService analysisExecutor =
            Executors.newScheduledThreadPool(4, r -> {
                Thread t = new Thread(r, "aiops-watch-analysis");
                t.setDaemon(true);
                return t;
            });

    /**
     * Debounce map : évite de déclencher plusieurs analyses consécutives pour le
     * même Deployment lors de rafales de MODIFIED events (ex: rolling update).
     * Clé = "namespace/app", valeur = instant du dernier déclenchement.
     */
    private final ConcurrentHashMap<String, Instant> lastTriggered = new ConcurrentHashMap<>();

    /** Thread du watch loop (démon). */
    private volatile Thread watchThread;

    /** Dernier resourceVersion connu — permet de reprendre le watch sans re-list. */
    private final AtomicReference<String> resourceVersion = new AtomicReference<>("");

    public KubernetesDeploymentWatcher(
            AiopsPollingProperties pollingProperties,
            AIOpsOrchestrationService orchestrationService,
            IncidentRepository incidentRepository,
            LeaderElectionService leaderElectionService
    ) {
        this.pollingProperties = pollingProperties;
        this.orchestrationService = orchestrationService;
        this.incidentRepository = incidentRepository;
        this.leaderElectionService = leaderElectionService;
    }

    // ── Lifecycle ─────────────────────────────────────────────────────────────

    @Async
    @EventListener(ApplicationReadyEvent.class)
    public void start() {
        if (!pollingProperties.isWatchEnabled()) {
            logger.info("Kubernetes deployment watcher disabled (aiops.watch.enabled=false)");
            return;
        }

        watchThread = new Thread(this::watchLoop, "k8s-deployment-watcher");
        watchThread.setDaemon(true);
        watchThread.start();
        logger.info("Kubernetes deployment watcher started (initialDelay={}s, debounce={}s)",
                pollingProperties.getWatchInitialDelaySeconds(),
                pollingProperties.getWatchDebounceSeconds());
    }

    @PreDestroy
    public void stop() {
        if (watchThread != null) {
            watchThread.interrupt();
        }
        analysisExecutor.shutdownNow();
        logger.info("Kubernetes deployment watcher stopped");
    }

    // ── Watch loop with auto-reconnect ────────────────────────────────────────

    private void watchLoop() {
        long backoffMs = 5_000;

        while (!Thread.currentThread().isInterrupted()) {
            try {
                runWatch();
                backoffMs = 5_000; // reset after clean exit
            } catch (InterruptedException ex) {
                Thread.currentThread().interrupt();
                break;
            } catch (ApiException ex) {
                if (ex.getCode() == 410) {
                    // Resource version too old — re-list from scratch
                    logger.info("Watch: resourceVersion expired (410 Gone) — re-listing");
                    resourceVersion.set("");
                } else {
                    logger.debug("Watch: ApiException code={} — reconnecting in {}ms", ex.getCode(), backoffMs);
                    sleepSafe(backoffMs);
                    backoffMs = Math.min(backoffMs * 2, 60_000);
                }
            } catch (Exception ex) {
                logger.debug("Watch: connection lost ({}) — reconnecting in {}ms", ex.getMessage(), backoffMs);
                sleepSafe(backoffMs);
                backoffMs = Math.min(backoffMs * 2, 60_000);
            }
        }
    }

    @SuppressWarnings("resource")
    private void runWatch() throws Exception {
        ApiClient client = buildClient();
        // Timeout 0 = connexion watch persistante (le serveur envoie les events au fil de l'eau)
        client.setReadTimeout(0);

        AppsV1Api appsApi = new AppsV1Api(client);

        // ── Phase 1 : si pas de resourceVersion, on liste d'abord pour l'obtenir
        if (resourceVersion.get().isEmpty()) {
            V1DeploymentList list = appsApi.listDeploymentForAllNamespaces().execute();
            String rv = list.getMetadata() != null ? list.getMetadata().getResourceVersion() : null;
            if (rv != null && !rv.isBlank()) {
                resourceVersion.set(rv);
                logger.info("Watch: initial sync done — resourceVersion={}, watching new events only", rv);
            }
        }

        // ── Phase 2 : watch depuis le resourceVersion courant (ne rejoue pas les existants)
        Watch<V1Deployment> watch = Watch.createWatch(
                client,
                appsApi.listDeploymentForAllNamespaces()
                        .watch(true)
                        .resourceVersion(resourceVersion.get())
                        .allowWatchBookmarks(true)
                        .buildCall(null),
                new TypeToken<Watch.Response<V1Deployment>>() {}.getType()
        );

        logger.debug("Watch: connected, listening for Deployment events…");

        for (Watch.Response<V1Deployment> event : watch) {
            if (Thread.currentThread().isInterrupted()) break;

            // Met à jour le resourceVersion à chaque event (BOOKMARK inclus)
            updateResourceVersion(event.object);

            switch (event.type) {
                case "ADDED"    -> onDeploymentAdded(event.object);
                case "MODIFIED" -> onDeploymentModified(event.object);
                case "DELETED"  -> onDeploymentDeleted(event.object);
                case "BOOKMARK" -> { /* resourceVersion déjà mis à jour ci-dessus */ }
                default         -> logger.trace("Watch: unknown event type={}", event.type);
            }
        }
    }

    // ── Event handlers ────────────────────────────────────────────────────────

    /**
     * Nouveau Deployment détecté dans le cluster.
     * Planifie une analyse AI après watchInitialDelaySeconds (laisse les pods démarrer).
     */
    private void onDeploymentAdded(V1Deployment deployment) {
        String namespace = namespace(deployment);
        String app       = appName(deployment);

        if (EXCLUDED_NAMESPACES.contains(namespace)) return;
        if (isMonitoringDisabled(deployment)) return;

        int delay = pollingProperties.getWatchInitialDelaySeconds();
        logger.info("[WATCH] Nouveau Deployment détecté: {}/{} — analyse IA dans {}s", namespace, app, delay);

        scheduleAnalysis(namespace, app, "nouveau deployment détecté", delay);
    }

    /**
     * Deployment modifié (rolling update, scale, changement de config).
     * Déclenche une analyse uniquement si la modification est significative
     * et si le debounce le permet.
     */
    private void onDeploymentModified(V1Deployment deployment) {
        String namespace = namespace(deployment);
        String app       = appName(deployment);

        if (EXCLUDED_NAMESPACES.contains(namespace)) return;
        if (isMonitoringDisabled(deployment))        return;
        if (!isMeaningfulModification(deployment))   return;
        if (isDebounced(namespace, app))             return;

        int delay = pollingProperties.getWatchInitialDelaySeconds();
        logger.info("[WATCH] Deployment mis à jour: {}/{} — re-analyse IA dans {}s", namespace, app, delay);

        scheduleAnalysis(namespace, app, "deployment mis à jour (image/replicas/config)", delay);
    }

    private void onDeploymentDeleted(V1Deployment deployment) {
        String key = key(namespace(deployment), appName(deployment));
        lastTriggered.remove(key);
        logger.info("[WATCH] Deployment supprimé: {} — surveillance arrêtée", key);
    }

    // ── Analysis trigger ──────────────────────────────────────────────────────

    private void scheduleAnalysis(String namespace, String app, String reason, int delaySec) {
        String key = key(namespace, app);
        lastTriggered.put(key, Instant.now());

        analysisExecutor.schedule(
                () -> triggerAnalysis(namespace, app, reason),
                delaySec,
                TimeUnit.SECONDS
        );
    }

    private void triggerAnalysis(String namespace, String app, String reason) {
        try {
            // HA: only the leader replica analyses, so incidents are not created in duplicate.
            if (!leaderElectionService.isLeader()) {
                logger.debug("[WATCH] Not the leader replica — skipping analysis for {}/{}", namespace, app);
                return;
            }
            // Ne lance pas d'analyse si un incident AI est déjà actif pour ce target
            if (hasActiveAiIncident(namespace, app)) {
                logger.debug("[WATCH] Incident AI actif pour {}/{} — analyse ignorée", namespace, app);
                return;
            }
            // Pipeline « Camunda orchestre » : la détection passe par le process Camunda (déclenché par
            // le polling sur alarme brute). On désactive donc l'appel IA direct du watcher.
            if ("camunda".equalsIgnoreCase(detectionPipeline)) {
                logger.debug("[WATCH] pipeline=camunda -> détection via Camunda (polling), watcher direct ignoré pour {}/{}", namespace, app);
                return;
            }

            logger.info("[WATCH] Déclenchement analyse IA: namespace={} app={} reason={}",
                    namespace, app, reason);

            AIOpsAnalyzeRequest request = new AIOpsAnalyzeRequest();
            request.setNamespace(namespace);
            request.setApp(app);
            request.setWindowMinutes(pollingProperties.getWindowMinutes());
            request.setCreateIncident(true);

            var response = orchestrationService.analyze(request);

            if (response.isIncidentCreated()) {
                logger.info("[WATCH] Incident créé id={} status={} pour {}/{}",
                        response.getIncidentId(), response.getIncidentStatus(), namespace, app);
            } else {
                logger.info("[WATCH] Aucun incident détecté pour {}/{} — {}", namespace, app, response.getMessage());
            }

        } catch (Exception ex) {
            logger.warn("[WATCH] Analyse IA échouée pour {}/{}: {}", namespace, app, ex.getMessage());
        }
    }

    // ── Meaningful modification detection ────────────────────────────────────

    /**
     * Détermine si une modification du Deployment justifie une re-analyse IA.
     *
     * Déclenche si :
     *  - Le tag image d'un container a changé (nouveau déploiement applicatif)
     *  - Le nombre de replicas a changé (scale manuel inattendu)
     *  - Une variable d'environnement a été ajoutée/modifiée (changement de config)
     *
     * Ne déclenche PAS sur les mises à jour de statut pures (pods ready/unready)
     * pour éviter le bruit pendant les rolling updates normaux.
     */
    private boolean isMeaningfulModification(V1Deployment deployment) {
        if (deployment.getSpec() == null) return false;

        var spec = deployment.getSpec();

        // Changement de replicas (scale manuel)
        if (spec.getReplicas() != null) {
            Integer desiredReplicas  = spec.getReplicas();
            Integer availableReplicas = deployment.getStatus() != null
                    ? deployment.getStatus().getAvailableReplicas() : null;
            // Déclenchement si replicas désirés < replicas disponibles (scale-down inattendu)
            if (availableReplicas != null && desiredReplicas < availableReplicas) {
                return true;
            }
        }

        // Vérification des annotations de mise à jour (kubectl rollout restart, kubectl set image)
        if (deployment.getMetadata() != null && deployment.getMetadata().getAnnotations() != null) {
            Map<String, String> annotations = deployment.getMetadata().getAnnotations();
            // kubectl rollout restart ajoute cette annotation
            if (annotations.containsKey("kubectl.kubernetes.io/restartedAt")) {
                return true;
            }
        }

        // Changement de génération (toute modification de spec)
        if (deployment.getMetadata() != null && deployment.getStatus() != null) {
            Long generation         = deployment.getMetadata().getGeneration();
            Long observedGeneration = deployment.getStatus().getObservedGeneration();
            // Nouvelle génération non encore observée = modification de spec en cours
            if (generation != null && observedGeneration != null
                    && generation > observedGeneration) {
                return true;
            }
        }

        return false;
    }

    // ── Debounce ──────────────────────────────────────────────────────────────

    private boolean isDebounced(String namespace, String app) {
        Instant last = lastTriggered.get(key(namespace, app));
        if (last == null) return false;
        long elapsed = Instant.now().getEpochSecond() - last.getEpochSecond();
        return elapsed < pollingProperties.getWatchDebounceSeconds();
    }

    // ── Helpers ───────────────────────────────────────────────────────────────

    private boolean hasActiveAiIncident(String namespace, String app) {
        if (incidentRepository.existsByStatusInAndSourceAndNamespaceAndApp(
                ACTIVE_STATUSES, IncidentSource.AI, namespace, app)) {
            return true;
        }
        // Same anti-churn window as the poller: after ANY incident on this app, the fault metrics linger
        // and remediation perturbs the pods — a watch-triggered re-analysis during that tail is a
        // DUPLICATE, not a new fault. Block it so we keep ONE specific incident per app per fault.
        long dedupMinutes = pollingProperties.getWindowMinutes() + 6L;
        return incidentRepository.existsBySourceAndNamespaceAndAppAndCreatedAtAfter(
                IncidentSource.AI, namespace, app, java.time.LocalDateTime.now().minusMinutes(dedupMinutes));
    }

    private void updateResourceVersion(V1Deployment deployment) {
        if (deployment == null || deployment.getMetadata() == null) return;
        String rv = deployment.getMetadata().getResourceVersion();
        if (rv != null && !rv.isBlank()) {
            resourceVersion.set(rv);
        }
    }

    /**
     * Résout le nom d'application depuis les labels Kubernetes.
     * Priorité : aiops.io/app > app.kubernetes.io/name > app > metadata.name
     */
    private String appName(V1Deployment deployment) {
        if (deployment.getMetadata() == null) return "unknown";
        Map<String, String> labels = deployment.getMetadata().getLabels();
        if (labels != null && labels.containsKey("aiops.io/app"))             return labels.get("aiops.io/app");
        if (labels != null && labels.containsKey("app.kubernetes.io/name"))   return labels.get("app.kubernetes.io/name");
        if (labels != null && labels.containsKey("app"))                      return labels.get("app");
        return deployment.getMetadata().getName() != null ? deployment.getMetadata().getName() : "unknown";
    }

    private boolean isMonitoringDisabled(V1Deployment deployment) {
        if (deployment.getMetadata() == null) return false;
        Map<String, String> labels = deployment.getMetadata().getLabels();
        return labels != null && "false".equalsIgnoreCase(labels.get("helios.io/monitor"));
    }

    private String namespace(V1Deployment deployment) {
        return (deployment.getMetadata() != null && deployment.getMetadata().getNamespace() != null)
                ? deployment.getMetadata().getNamespace() : "default";
    }

    private String key(String namespace, String app) {
        return namespace + "/" + app;
    }

    private ApiClient buildClient() throws IOException {
        try {
            return Config.fromCluster();
        } catch (Exception ex) {
            logger.debug("In-cluster config unavailable, using default client: {}", ex.getMessage());
            return Config.defaultClient();
        }
    }

    private void sleepSafe(long ms) {
        try {
            Thread.sleep(ms);
        } catch (InterruptedException ex) {
            Thread.currentThread().interrupt();
        }
    }
}
