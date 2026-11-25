package com.orange.pfe.backend.service;

import com.orange.pfe.backend.config.RemediationProperties;
import com.orange.pfe.backend.dto.AIOpsAnalyzeRequest;
import com.orange.pfe.backend.dto.AIPredictResponse;
import com.orange.pfe.backend.model.action.ActionType;
import com.orange.pfe.backend.model.action.AutomationMode;
import com.orange.pfe.backend.model.action.IncidentAction;
import com.orange.pfe.backend.model.action.RemediationTargetKind;
import com.orange.pfe.backend.model.core.Incident;
import com.orange.pfe.backend.model.enums.IncidentStatus;
import com.orange.pfe.backend.model.workflow.WorkflowInstance;
import com.orange.pfe.backend.repository.IncidentRepository;
import com.orange.pfe.backend.service.remediation.HostDiskScenario;
import com.orange.pfe.backend.service.remediation.RemediationPlan;
import com.orange.pfe.backend.service.remediation.RemediationPolicyService;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.transaction.support.TransactionSynchronization;
import org.springframework.transaction.support.TransactionSynchronizationManager;

import java.util.Locale;
import java.util.Set;

@Service
public class HostDiskIncidentRemediationOrchestrator {

    private static final Logger log =
            LoggerFactory.getLogger(HostDiskIncidentRemediationOrchestrator.class);

    private static final Set<String> DISK_TYPES = Set.of(
            "NODE_DISK_FULL",
            "NODE_DISK_PRESSURE",
            "NODE_INODES_FULL"
    );

    private static final Set<String> ALLOWED_TARGETS =
            Set.of("k8s-worker1", "k8s-worker2");

    private final RemediationProperties properties;
    private final RemediationPolicyService policy;
    private final ActionService actionService;
    private final WorkflowService workflowService;
    private final CamundaWorkflowService camundaWorkflowService;
    private final IncidentHistoryService historyService;
    private final RemediationLaunchFailureService launchFailureService;
    private final IncidentRepository incidentRepository;

    public HostDiskIncidentRemediationOrchestrator(
            RemediationProperties properties,
            RemediationPolicyService policy,
            ActionService actionService,
            WorkflowService workflowService,
            CamundaWorkflowService camundaWorkflowService,
            IncidentHistoryService historyService,
            RemediationLaunchFailureService launchFailureService,
            IncidentRepository incidentRepository) {
        this.properties = properties;
        this.policy = policy;
        this.actionService = actionService;
        this.workflowService = workflowService;
        this.camundaWorkflowService = camundaWorkflowService;
        this.historyService = historyService;
        this.launchFailureService = launchFailureService;
        this.incidentRepository = incidentRepository;
    }

    @Transactional(propagation = Propagation.REQUIRES_NEW)
    public void startIfEligible(Incident sourceIncident) {
        if (sourceIncident == null
                || sourceIncident.getId() == null
                || sourceIncident.getIncidentType() == null) {
            return;
        }

        Incident incident =
                incidentRepository.findById(sourceIncident.getId()).orElse(null);

        if (incident == null) {
            log.warn("DISK remediation skipped: persisted incident not found");
            return;
        }

        String type = incident.getIncidentType().trim().toUpperCase(Locale.ROOT);
        if (!DISK_TYPES.contains(type)) {
            return;
        }

        if (!"k8s-nodes".equalsIgnoreCase(incident.getNamespace())) {
            historyService.record(
                    incident,
                    IncidentStatus.OPEN,
                    "DISK remediation refused: namespace outside the approved scope.");
            return;
        }

        String rawTarget = incident.getApp();
        String target = rawTarget == null ? null : ALLOWED_TARGETS.stream()
                .filter(candidate -> candidate.equalsIgnoreCase(rawTarget.trim()))
                .findFirst()
                .orElse(null);

        if (target == null) {
            historyService.record(
                    incident,
                    IncidentStatus.OPEN,
                    "DISK remediation refused: target outside the worker allowlist.");
            log.warn("DISK remediation refused: target outside allowlist");
            return;
        }

        // DISK-E2E-158-02 : NODE_DISK_FULL n'a QU'UNE voie, le scenario DISK_E2E_158 (k8s-worker1). Partout ailleurs
        // (k8s-worker2 = .155, primaire CNPG) il est refuse quels que soient les drapeaux. NODE_DISK_PRESSURE et
        // NODE_INODES_FULL ne sont PAS modifies : meme allowlist, meme ancien drapeau global qu'avant.
        boolean scenario = HostDiskScenario.matches(incident.getNamespace(), target, type);
        if (!scenario && "NODE_DISK_FULL".equals(type)) {
            historyService.record(
                    incident,
                    IncidentStatus.OPEN,
                    "DISK remediation refused: NODE_DISK_FULL is eligible only for the DISK_E2E_158 scenario "
                            + "(k8s-worker1); k8s-worker2 never.");
            log.warn("DISK remediation refused: NODE_DISK_FULL outside the DISK_E2E_158 scenario");
            return;
        }

        // Independent enforcement: the feature flag must be enabled before
        // creating an action or starting a Camunda workflow. The scenario has its OWN dedicated flag
        // (the legacy global flag does not enable it); the other DISK types keep the legacy flag.
        boolean enabled = scenario
                ? properties.isHostDiskE2e158Enabled()
                : properties.isHostDiskRemediationEnabled();
        if (!enabled) {
            historyService.record(
                    incident,
                    IncidentStatus.OPEN,
                    scenario
                            ? "DISK remediation not started: HOST_DISK_E2E_158_ENABLED=false "
                                    + "(HOST_DISK_REMEDIATION_ENABLED does not enable the DISK_E2E_158 scenario)."
                            : "DISK remediation not started: HOST_DISK_REMEDIATION_ENABLED=false.");
            log.info("DISK remediation remains disabled for incident {}", incident.getId());
            return;
        }

        // Temporary internal contract shared with the existing deterministic CPU path.
        // No AI service request is performed here.
        AIOpsAnalyzeRequest request = new AIOpsAnalyzeRequest();
        request.setNamespace("k8s-nodes");
        request.setApp(target);
        request.setCreateIncident(false);

        AIPredictResponse decision = new AIPredictResponse();
        decision.setIncident(true);
        decision.setIncidentType(type);
        decision.setSeverity(
                incident.getSeverity() == null
                        ? "MEDIUM"
                        : incident.getSeverity().name());
        decision.setConfidence(1.0d);
        decision.setRecommendedAction(ActionType.HOST_RECLAIM_DISK.name());
        decision.setFinalExecutableAction(ActionType.HOST_RECLAIM_DISK.name());
        decision.setExecutionMode("AUTO");
        decision.setDecisionExplanation(
                "Deterministic host DISK incident; evaluated by the existing remediation policy.");

        RemediationPlan plan = policy.buildPlan(request, decision);

        if (plan == null
                || plan.actionType() != ActionType.HOST_RECLAIM_DISK
                || !plan.autoExecutable()
                || plan.approvalRequired()
                || plan.manualFollowUp()) {
            String reason = plan == null
                    ? "remediation plan absent"
                    : plan.rationale();

            historyService.record(
                    incident,
                    IncidentStatus.OPEN,
                    "DISK remediation not started by policy: " + reason);
            log.info("DISK remediation not started: policy requirements not met");
            return;
        }

        WorkflowInstance workflow = workflowService.startWorkflow(
                incident,
                "Incident-Remediation-Workflow",
                "k8s-nodes",
                target);

        IncidentAction action = actionService.createAction(
                incident,
                ActionType.HOST_RECLAIM_DISK,
                "Deterministic DISK remediation; execution remains guarded by the executor",
                "HELIOS-DETERMINISTIC",
                plan.targetNamespace(),
                RemediationTargetKind.NODE,
                target,
                AutomationMode.AUTO);

        incident.setStatus(IncidentStatus.IN_PROGRESS);
        incidentRepository.save(incident);
        historyService.record(
                incident,
                IncidentStatus.IN_PROGRESS,
                "DISK remediation prepared; Camunda launch and measured verification pending.");

        Runnable launchWorkflow = () -> {
            final CamundaLaunchOutcome outcome;
            try {
                outcome = camundaWorkflowService.startWorkflowWithOutcome(
                        workflow, action, plan, decision, false);
            } catch (RuntimeException ex) {
                log.error("DISK remediation Camunda launch failed for incident {}",
                        incident.getId(), ex);
                try {
                    launchFailureService.recordCamundaLaunchFailure(
                            "DISK", incident.getId(), action.getId(), ex);
                } catch (RuntimeException persistenceEx) {
                    log.error("Could not persist DISK Camunda launch failure for incident {}",
                            incident.getId(), persistenceEx);
                }
                return;
            }

            switch (outcome) {
                case CAMUNDA_STARTED ->
                        log.info("DISK remediation workflow confirmed by Camunda for incident {}",
                                incident.getId());
                case LAUNCH_FAILED, NOT_STARTED -> {
                    RuntimeException failure = new IllegalStateException(
                            "Camunda did not start the DISK workflow; direct fallback was disabled. Outcome="
                                    + outcome);
                    try {
                        launchFailureService.recordCamundaLaunchFailure(
                                "DISK", incident.getId(), action.getId(), failure);
                    } catch (RuntimeException persistenceEx) {
                        log.error("Could not persist DISK launch failure for incident {}",
                                incident.getId(), persistenceEx);
                    }
                }
                case PROCESS_ALREADY_ATTACHED, DIRECT_FALLBACK_EXECUTED,
                        DIRECT_FALLBACK_FAILED, MANUAL_FOLLOW_UP_REQUIRED -> {
                    String message = "DISK Camunda outcome requires reconciliation: " + outcome
                            + ". No automatic retry was started to avoid duplicate remediation.";
                    log.warn("DISK remediation requires reconciliation for incident {}: {}",
                            incident.getId(), outcome);
                    try {
                        historyService.record(incident, IncidentStatus.IN_PROGRESS, message);
                    } catch (RuntimeException historyEx) {
                        log.error("Could not persist DISK reconciliation history for incident {}",
                                incident.getId(), historyEx);
                    }
                }
            }
        };

        if (TransactionSynchronizationManager.isSynchronizationActive()) {
            TransactionSynchronizationManager.registerSynchronization(
                    new TransactionSynchronization() {
                        @Override
                        public void afterCommit() {
                            launchWorkflow.run();
                        }
                    });
        } else {
            // Supports direct unit-test invocation without a Spring transaction proxy.
            launchWorkflow.run();
        }
    }
}