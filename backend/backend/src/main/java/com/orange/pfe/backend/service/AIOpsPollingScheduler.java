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

    private static final List<IncidentStatus> ACTIVE_STATUSES = List.of(
            IncidentStatus.NEW,
            IncidentStatus.OPEN,
            IncidentStatus.IN_PROGRESS,
            IncidentStatus.WAITING_FOR_APPROVAL
    );

    private final AIOpsOrchestrationService orchestrationService;
    private final IncidentRepository incidentRepository;
    private final AiopsPollingProperties pollingProperties;
    private final RemediationProperties remediationProperties;
    private final KubernetesDiscoveryService discoveryService;
    private final LeaderElectionService leaderElectionService;
    private final AtomicBoolean running = new AtomicBoolean(false);

    public AIOpsPollingScheduler(
            AIOpsOrchestrationService orchestrationService,
            IncidentRepository incidentRepository,
            AiopsPollingProperties pollingProperties,
            RemediationProperties remediationProperties,
            KubernetesDiscoveryService discoveryService,
            LeaderElectionService leaderElectionService
    ) {
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
     * - RESOLUTION_FAILED récent (< cooldown) : bloque pour éviter le spam. L'incident RESTE ouvert
     *   (jamais auto-fermé) pour que l'ingénieur le traite ; après le cooldown, si la panne persiste,
     *   un nouvel incident peut être créé (la dédup exclut RESOLUTION_FAILED).
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
