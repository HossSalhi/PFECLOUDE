package com.orange.pfe.backend.service;

import com.orange.pfe.backend.config.AiopsPollingProperties;
import com.orange.pfe.backend.config.AiopsPollingProperties.Target;
import com.orange.pfe.backend.dto.AIOpsAnalyzeRequest;
import com.orange.pfe.backend.model.enums.IncidentSource;
import com.orange.pfe.backend.model.enums.IncidentStatus;
import com.orange.pfe.backend.repository.IncidentRepository;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Service;

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
    private final KubernetesDiscoveryService discoveryService;
    private final LeaderElectionService leaderElectionService;
    private final AtomicBoolean running = new AtomicBoolean(false);

    public AIOpsPollingScheduler(
            AIOpsOrchestrationService orchestrationService,
            IncidentRepository incidentRepository,
            AiopsPollingProperties pollingProperties,
            KubernetesDiscoveryService discoveryService,
            LeaderElectionService leaderElectionService
    ) {
        this.orchestrationService = orchestrationService;
        this.incidentRepository = incidentRepository;
        this.pollingProperties = pollingProperties;
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
     * Vérifie si un incident AI actif existe pour ce namespace/app spécifique.
     * Évite de bloquer la surveillance des autres apps si l'une d'elles a un incident.
     */
    private boolean hasActiveAiIncidentFor(String namespace, String app) {
        return incidentRepository.existsByStatusInAndSourceAndNamespaceAndApp(
                ACTIVE_STATUSES, IncidentSource.AI, namespace, app
        );
    }
}
