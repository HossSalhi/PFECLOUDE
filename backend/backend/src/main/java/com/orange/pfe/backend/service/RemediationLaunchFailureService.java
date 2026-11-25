package com.orange.pfe.backend.service;

import com.orange.pfe.backend.model.core.Incident;
import com.orange.pfe.backend.model.enums.IncidentStatus;
import com.orange.pfe.backend.repository.IncidentRepository;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;

@Service
public class RemediationLaunchFailureService {

    private static final Logger log =
            LoggerFactory.getLogger(RemediationLaunchFailureService.class);

    private static final String EXECUTOR_USERNAME = "HELIOS-DETERMINISTIC";

    private final IncidentRepository incidentRepository;
    private final ActionService actionService;
    private final IncidentHistoryService historyService;

    public RemediationLaunchFailureService(
            IncidentRepository incidentRepository,
            ActionService actionService,
            IncidentHistoryService historyService) {
        this.incidentRepository = incidentRepository;
        this.actionService = actionService;
        this.historyService = historyService;
    }

    @Transactional(propagation = Propagation.REQUIRES_NEW)
    public void recordCamundaLaunchFailure(
            String scenario,
            Long incidentId,
            Long actionId,
            RuntimeException failure) {

        if (incidentId == null) {
            throw new IllegalArgumentException("Incident id is required to record a Camunda launch failure");
        }

        Incident incident = incidentRepository.findById(incidentId)
                .orElseThrow(() -> new IllegalStateException(
                        "Incident not found while recording Camunda launch failure: " + incidentId));

        String cause = failure == null ? "unknown error" : failure.getClass().getSimpleName();
        if (failure != null && failure.getMessage() != null && !failure.getMessage().isBlank()) {
            cause += ": " + failure.getMessage().trim();
        }

        String safeScenario = scenario == null || scenario.isBlank() ? "REMEDIATION" : scenario.trim();
        String reason = "[" + safeScenario + "] Camunda workflow launch failed: " + cause;

        if (actionId != null) {
            actionService.markActionFailed(actionId, reason, EXECUTOR_USERNAME, reason);
        } else {
            reason += " Action status could not be updated because the action id is missing.";
            log.error("Missing action id while recording Camunda launch failure for incident {}", incidentId);
        }

        incident.setStatus(IncidentStatus.RESOLUTION_FAILED);
        incidentRepository.save(incident);
        historyService.record(incident, IncidentStatus.RESOLUTION_FAILED, reason);

        log.error("Camunda launch failure persisted for {} incident {}", safeScenario, incidentId);
    }
}