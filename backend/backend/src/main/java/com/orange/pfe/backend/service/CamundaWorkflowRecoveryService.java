package com.orange.pfe.backend.service;

import com.orange.pfe.backend.config.RemediationProperties;
import com.orange.pfe.backend.model.action.ActionStatus;
import com.orange.pfe.backend.model.action.IncidentAction;
import com.orange.pfe.backend.model.enums.IncidentStatus;
import com.orange.pfe.backend.model.human.TaskDecision;
import com.orange.pfe.backend.model.workflow.WorkflowInstance;
import com.orange.pfe.backend.model.workflow.WorkflowOrchestrationState;
import com.orange.pfe.backend.model.workflow.WorkflowStatus;
import com.orange.pfe.backend.repository.HumanTaskRepository;
import com.orange.pfe.backend.repository.IncidentActionRepository;
import com.orange.pfe.backend.repository.WorkflowRepository;
import com.orange.pfe.backend.service.remediation.RecoveryVerificationResult;
import com.orange.pfe.backend.service.remediation.RecoveryVerificationService;
import com.orange.pfe.backend.service.remediation.RemediationMetricsService;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.LocalDateTime;
import java.util.List;
import java.util.Optional;

@Service
@ConditionalOnProperty(name = "camunda.enabled", havingValue = "true")
public class CamundaWorkflowRecoveryService {

    private static final Logger logger = LoggerFactory.getLogger(CamundaWorkflowRecoveryService.class);
    private static final int EXECUTION_PENDING_GRACE_SECONDS = 45;
    private static final int APPROVED_GRACE_SECONDS = 20;

    private final WorkflowRepository workflowRepository;
    private final IncidentActionRepository incidentActionRepository;
    private final WorkflowService workflowService;
    private final CamundaWorkflowService camundaWorkflowService;
    private final RecoveryVerificationService recoveryVerificationService;
    private final HumanTaskRepository humanTaskRepository;
    private final HumanTaskService humanTaskService;
    private final IncidentHistoryService incidentHistoryService;
    private final RemediationMetricsService remediationMetricsService;
    private final RemediationProperties remediationProperties;

    public CamundaWorkflowRecoveryService(
            WorkflowRepository workflowRepository,
            IncidentActionRepository incidentActionRepository,
            WorkflowService workflowService,
            CamundaWorkflowService camundaWorkflowService,
            RecoveryVerificationService recoveryVerificationService,
            HumanTaskRepository humanTaskRepository,
            HumanTaskService humanTaskService,
            IncidentHistoryService incidentHistoryService,
            RemediationMetricsService remediationMetricsService,
            RemediationProperties remediationProperties
    ) {
        this.workflowRepository = workflowRepository;
        this.incidentActionRepository = incidentActionRepository;
        this.workflowService = workflowService;
        this.camundaWorkflowService = camundaWorkflowService;
        this.recoveryVerificationService = recoveryVerificationService;
        this.humanTaskRepository = humanTaskRepository;
        this.humanTaskService = humanTaskService;
        this.incidentHistoryService = incidentHistoryService;
        this.remediationMetricsService = remediationMetricsService;
        this.remediationProperties = remediationProperties;
    }

    @Scheduled(
            fixedDelayString = "${camunda.recovery.fixed-delay-ms:30000}",
            initialDelayString = "${camunda.recovery.initial-delay-ms:45000}"
    )
    @Transactional
    public void recoverExecutionPendingWorkflows() {
        List<WorkflowInstance> executionPendingWorkflows = workflowRepository.findByStatusAndOrchestrationState(
                WorkflowStatus.RUNNING,
                WorkflowOrchestrationState.EXECUTION_PENDING
        );

        for (WorkflowInstance workflow : executionPendingWorkflows) {
            try {
                recoverWorkflowIfStuck(workflow);
            } catch (Exception ex) {
                logger.warn("Unable to recover workflow {}", workflow.getId(), ex);
            }
        }

        List<WorkflowInstance> approvedWorkflows = workflowRepository.findByStatusAndOrchestrationState(
                WorkflowStatus.RUNNING,
                WorkflowOrchestrationState.APPROVED
        );

        for (WorkflowInstance workflow : approvedWorkflows) {
            try {
                recoverApprovedWorkflowIfStuck(workflow);
            } catch (Exception ex) {
                logger.warn("Unable to recover approved workflow {}", workflow.getId(), ex);
            }
        }
    }

    private void recoverWorkflowIfStuck(WorkflowInstance workflow) throws InterruptedException {
        Optional<IncidentAction> latestActionOptional =
                incidentActionRepository.findTopByIncidentIdOrderByCreatedAtDesc(workflow.getIncident().getId());

        if (latestActionOptional.isEmpty()) {
            return;
        }

        IncidentAction latestAction = latestActionOptional.get();
        LocalDateTime executedAt = latestAction.getExecutedAt();
        if (executedAt == null) {
            return;
        }

        LocalDateTime cutoff = LocalDateTime.now()
                .minusSeconds(remediationProperties.getVerifyDelaySeconds() + EXECUTION_PENDING_GRACE_SECONDS);
        if (executedAt.isAfter(cutoff)) {
            return;
        }

        if (latestAction.getStatus() == ActionStatus.COMPLETED) {
            runRecoveryVerification(workflow, latestAction);
            return;
        }

        if (latestAction.getStatus() == ActionStatus.FAILED) {
            logger.warn(
                    "Workflow {} is stuck in EXECUTION_PENDING after failed action {}; moving it back to approval",
                    workflow.getId(),
                    latestAction.getId()
            );
            parkWorkflowForApproval(
                    workflow,
                    "Recovered stuck execution state after failed remediation action: "
                            + safe(latestAction.getFailedReason())
            );
        }
    }

    private void runRecoveryVerification(WorkflowInstance workflow, IncidentAction latestAction)
            throws InterruptedException {
        logger.warn(
                "Workflow {} appears stuck after completed action {}; running backend recovery verification",
                workflow.getId(),
                latestAction.getId()
        );

        workflowService.updateOrchestrationState(
                workflow,
                WorkflowOrchestrationState.VERIFYING,
                IncidentStatus.IN_PROGRESS,
                "Recovered stuck Camunda execution; running backend verification"
        );

        RecoveryVerificationResult verificationResult = recoveryVerificationService.verify(
                latestAction.getTargetNamespace(),
                latestAction.getTargetName()
        );
        boolean resolved = verificationResult.resolved();
        remediationMetricsService.verification(resolved);

        if (resolved) {
            remediationMetricsService.remediationSucceeded();
            incidentHistoryService.record(
                    workflow.getIncident(),
                    IncidentStatus.RESOLVED,
                    "Recovered stuck workflow after successful verification: " + verificationResult.message()
            );
            workflowService.completeWorkflow(workflow.getId());
            return;
        }

        remediationMetricsService.remediationFailed();
        incidentHistoryService.record(
                workflow.getIncident(),
                IncidentStatus.WAITING_FOR_APPROVAL,
                "Recovered stuck workflow but verification failed: " + verificationResult.message()
        );
        parkWorkflowForApproval(
                workflow,
                "Recovered stuck workflow but verification failed: " + verificationResult.message()
        );
    }

    private void recoverApprovedWorkflowIfStuck(WorkflowInstance workflow) {
        if (workflow.getStartedAt() == null) {
            return;
        }
        LocalDateTime cutoff = LocalDateTime.now().minusSeconds(APPROVED_GRACE_SECONDS);
        if (workflow.getStartedAt().isAfter(cutoff)) {
            return;
        }
        if (humanTaskRepository.existsByWorkflowInstanceIdAndDecision(workflow.getId(), TaskDecision.PENDING)) {
            return;
        }

        Optional<IncidentAction> latestActionOptional =
                incidentActionRepository.findTopByIncidentIdOrderByCreatedAtDesc(workflow.getIncident().getId());
        if (latestActionOptional.isEmpty()) {
            return;
        }

        IncidentAction latestAction = latestActionOptional.get();
        if (latestAction.getStatus() == ActionStatus.COMPLETED) {
            try {
                logger.warn(
                        "Workflow {} is stuck in APPROVED after completed action {}; running backend recovery verification",
                        workflow.getId(),
                        latestAction.getId()
                );
                runRecoveryVerification(workflow, latestAction);
            } catch (InterruptedException ex) {
                Thread.currentThread().interrupt();
                logger.warn(
                        "Interrupted while recovering approved workflow {} after completed action {}",
                        workflow.getId(),
                        latestAction.getId()
                );
            }
            return;
        }

        if (latestAction.getStatus() == ActionStatus.FAILED) {
            logger.warn(
                    "Workflow {} is stuck in APPROVED after failed action {}; moving it back to approval",
                    workflow.getId(),
                    latestAction.getId()
            );
            parkWorkflowForApproval(
                    workflow,
                    "Recovered stuck approved workflow after failed remediation action: "
                            + safe(latestAction.getFailedReason())
            );
            return;
        }

        if (latestAction.getStatus() != ActionStatus.PENDING && latestAction.getStatus() != ActionStatus.IN_PROGRESS) {
            return;
        }

        logger.warn(
                "Workflow {} is stuck in APPROVED; republishing approval to Camunda with action {}",
                workflow.getId(),
                latestAction.getId()
        );
        workflowService.updateOrchestrationState(
                workflow,
                WorkflowOrchestrationState.EXECUTION_PENDING,
                IncidentStatus.IN_PROGRESS,
                "Recovered stuck approved workflow; republishing approval to Camunda"
        );
        camundaWorkflowService.publishDecision(workflow, TaskDecision.APPROVED, latestAction.getId());
    }

    private void parkWorkflowForApproval(WorkflowInstance workflow, String reason) {
        workflowService.updateOrchestrationState(
                workflow,
                WorkflowOrchestrationState.WAITING_FOR_APPROVAL,
                IncidentStatus.WAITING_FOR_APPROVAL,
                reason
        );

        if (!humanTaskRepository.existsByWorkflowInstanceIdAndDecision(workflow.getId(), TaskDecision.PENDING)) {
            humanTaskService.createTask(
                    "Review remediation follow-up for incident #" + workflow.getIncident().getId(),
                    workflow
            );
            remediationMetricsService.approvalPending();
        }
    }

    private String safe(String value) {
        return value == null || value.isBlank() ? "unknown reason" : value;
    }
}
