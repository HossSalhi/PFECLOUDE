package com.orange.pfe.backend.service;

import com.orange.pfe.backend.config.AiopsPollingProperties;
import com.orange.pfe.backend.config.AiopsPollingProperties.Target;
import com.orange.pfe.backend.config.RemediationProperties;
import com.orange.pfe.backend.dto.AIOpsAnalyzeRequest;
import com.orange.pfe.backend.model.enums.IncidentSource;
import com.orange.pfe.backend.model.enums.IncidentStatus;
import com.orange.pfe.backend.repository.IncidentRepository;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Service;

import java.time.LocalDateTime;
import java.util.ArrayList;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Set;
import java.util.concurrent.atomic.AtomicBoolean;

@Service
public class AIOpsPollingScheduler {

    private static final Logger logger = LoggerFactory.getLogger(AIOpsPollingScheduler.class);

    // RESOLUTION_FAILED inclus : tant qu'un incident en echec reste ouvert pour cette app, on NE
    // recree PAS un nouvel incident du meme type a chaque fenetre de dedup (= plus de BOUCLE). A la
    // place, pollTarget appelle autoResolveIfCleared qui le CLOT automatiquement des que l'app est saine.
    private static final List<IncidentStatus> ACTIVE_STATUSES = List.of(
            IncidentStatus.NEW,
            IncidentStatus.OPEN,
            IncidentStatus.IN_PROGRESS,
            IncidentStatus.WAITING_FOR_APPROVAL,
            IncidentStatus.RESOLUTION_FAILED
    );

    private final AIOpsOrchestrationService orchestrationService;
    private final IncidentRepository incidentRepository;
    private final AiopsPollingProperties pollingProperties;
    private final RemediationProperties remediationProperties;
    private final KubernetesDiscoveryService discoveryService;
    private final LeaderElectionService leaderElectionService;
    private final CamundaWorkflowService camundaWorkflowService;
    private final AIOpsClientService aiOpsClientService;
    // "camunda" = Camunda orchestre monitoring -> IA (le backend lève l'alarme brute, Camunda appelle
    // l'IA via le process incident-detection). "backend" = historique (le backend appelle l'IA direct).
    @org.springframework.beans.factory.annotation.Value("${helios.detection.pipeline:backend}")
    private String detectionPipeline;
    private final AtomicBoolean running = new AtomicBoolean(false);

    public AIOpsPollingScheduler(
            AIOpsOrchestrationService orchestrationService,
            IncidentRepository incidentRepository,
            AiopsPollingProperties pollingProperties,
            RemediationProperties remediationProperties,
            KubernetesDiscoveryService discoveryService,
            LeaderElectionService leaderElectionService,
            CamundaWorkflowService camundaWorkflowService,
            AIOpsClientService aiOpsClientService
    ) {
        this.camundaWorkflowService = camundaWorkflowService;
        this.aiOpsClientService = aiOpsClientService;
        this.orchestrationService = orchestrationService;
        this.incidentRepository = incidentRepository;
        this.pollingProperties = pollingProperties;
        this.remediationProperties = remediationProperties;
        this.discoveryService = discoveryService;
        this.leaderElectionService = leaderElectionService;
    }

    @Scheduled(fixedDelayString = "${aiops.polling.interval-millis:120000}")
    public void poll() {
        if (!pollingProperties.isEnabled()) {
            return;
        }
        // HA: only the leader replica polls, so incidents are not created in duplicate.
        if (!leaderElectionService.isLeader()) {
            logger.debug("AIOps polling: not the leader replica, skipping this cycle");
            return;
        }
        // Camunda = orchestrateur CENTRAL : si Camunda/Zeebe est injoignable, HELIOS ne détecte
        // AUCUN incident (pas d'appel IA, pas de création). Camunda arrêté => 0 détection.
        if (!camundaWorkflowService.isReachable()) {
            logger.warn("AIOps polling: Camunda injoignable — détection SUSPENDUE (0 incident tant que Camunda est arrêté)");
            return;
        }
        // L'IA = CERVEAU : si le service IA est injoignable, on ne démarre même pas le process de
        // détection (le cerveau absent => rien à classer). IA arrêtée => 0 détection applicative.
        if (!aiOpsClientService.isReachable()) {
            logger.warn("AIOps polling: service IA injoignable — détection SUSPENDUE (0 incident tant que l'IA est arrêtée)");
            return;
        }
        if (!running.compareAndSet(false, true)) {
            logger.debug("AIOps polling still running from previous cycle, skipping");
            return;
        }
        try {
            List<Target> allTargets = resolveTargets();
            if (allTargets.isEmpty()) {
                logger.debug("AIOps polling: no targets configured or discovered");
                return;
            }
            logger.info("AIOps polling: {} target(s) to analyze: {}", allTargets.size(), allTargets);
            for (Target target : allTargets) {
                pollTarget(target);
            }
        } finally {
            running.set(false);
        }
    }

    /**
     * Fusionne les cibles statiques (config) et les cibles découvertes (K8s labels),
     * sans doublons.
     */
    private List<Target> resolveTargets() {
        Set<Target> merged = new LinkedHashSet<>();
        merged.addAll(pollingProperties.getEffectiveTargets());
        merged.addAll(discoveryService.discoverTargets());
        return new ArrayList<>(merged);
    }

    private void pollTarget(Target target) {
        String namespace = target.getNamespace();
        String app = target.getApp();
        try {
            if (hasActiveAiIncidentFor(namespace, app)) {
                boolean resolved = orchestrationService.autoResolveIfCleared(
                        namespace, app, pollingProperties.getWindowMinutes());
                if (!resolved) {
                    logger.debug("Active AI incident still valid for {}/{}, condition persists", namespace, app);
                }
                return;
            }

            // Pipeline « Camunda orchestre monitoring -> IA » : le backend lève l'ALARME BRUTE
            // (déterministe, sans IA) puis DÉMARRE le process Camunda incident-detection, dont la
            // tâche ai-classify appelle l'IA. Camunda devient l'intermédiaire monitoring -> IA.
            if ("camunda".equalsIgnoreCase(detectionPipeline)) {
                if (orchestrationService.hasRawAlarm(namespace, app)) {
                    logger.info("AIOps polling: alarme brute {}/{} -> démarrage du process de détection Camunda", namespace, app);
                    camundaWorkflowService.startDetectionWorkflow(namespace, app);
                } else {
                    logger.debug("AIOps polling: pas d'alarme brute pour {}/{}", namespace, app);
                }
                return;
            }

            logger.info("AIOps polling: analyzing namespace={} app={}", namespace, app);
            AIOpsAnalyzeRequest request = new AIOpsAnalyzeRequest();
            request.setNamespace(namespace);
            request.setApp(app);
            request.setWindowMinutes(pollingProperties.getWindowMinutes());
            request.setCreateIncident(true);

            var response = orchestrationService.analyze(request);

            if (response.isIncidentCreated()) {
                logger.info("AIOps polling: incident created id={} status={} for {}/{}",
                        response.getIncidentId(), response.getIncidentStatus(), namespace, app);
            } else {
                logger.debug("AIOps polling: no incident for {}/{} — {}", namespace, app, response.getMessage());
            }
        } catch (Exception ex) {
            logger.warn("AIOps polling failed for {}/{}: {}", namespace, app, ex.getMessage());
        }
    }

    /**
     * Vérifie si un incident AI actif existe pour ce namespace/app.
     * - Statuts actifs (NEW/OPEN/IN_PROGRESS) : bloque toujours.
     * - RESOLVED récent (< fenêtre + 1 min) : bloque pour éviter de re-détecter les
     *   métriques de panne encore visibles dans la fenêtre Prometheus.
     * - RESOLUTION_FAILED (ouvert) : bloque TOUJOURS la creation d'un nouvel incident pour cette app
     *   (il fait partie d'ACTIVE_STATUSES) -> JAMAIS de boucle de fausses resolutions. Il est auto-clos
     *   par autoResolveIfCleared des que l'app redevient saine (donc pas d'incident bloque a vie).
     */
    private boolean hasActiveAiIncidentFor(String namespace, String app) {
        if (incidentRepository.existsByStatusInAndSourceAndNamespaceAndApp(
                ACTIVE_STATUSES, IncidentSource.AI, namespace, app)) {
            return true;
        }
        // Anti-churn: after ANY incident on this app, the fault metrics linger in the Prometheus/
        // cAdvisor window for a few minutes (and the verification itself perturbs the pods). A new
        // detection during that tail is a DUPLICATE, not a new fault. Block any new incident for the
        // app for a generous window after the last one was created — this guarantees one specific,
        // actionable incident per app per fault (no churn duplicates, no post-resolution noise).
        long dedupMinutes = Math.max(pollingProperties.getWindowMinutes() + 6L,
                remediationProperties.getCooldownMinutes() + 1L);
        LocalDateTime dedupCutoff = LocalDateTime.now().minusMinutes(dedupMinutes);
        return incidentRepository.existsBySourceAndNamespaceAndAppAndCreatedAtAfter(
                IncidentSource.AI, namespace, app, dedupCutoff);
    }
}
