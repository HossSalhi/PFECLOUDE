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
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.time.Duration;
import java.time.LocalDateTime;
import java.util.List;
import java.util.Optional;
import java.util.concurrent.atomic.AtomicInteger;

@Service
@ConditionalOnProperty(name = "camunda.enabled", havingValue = "true")
public class CamundaWorkflowRecoveryService {

    private static final Logger logger = LoggerFactory.getLogger(CamundaWorkflowRecoveryService.class);
    private static final int EXECUTION_PENDING_GRACE_SECONDS = 45;
    private static final int APPROVED_GRACE_SECONDS = 20;
    private static final int CAMUNDA_DOWN_THRESHOLD = 3;
    private static final int CREATED_GRACE_MINUTES = 5;

    @Value("${camunda.rest-address:http://192.168.148.161:8080}")
    private String camundaRestAddress;

    private final AtomicInteger camundaConsecutiveFailures = new AtomicInteger(0);
    private final HttpClient httpClient = HttpClient.newBuilder()
            .connectTimeout(Duration.ofSeconds(5))
            .build();

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
    private final LeaderElectionService leaderElectionService;

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
            RemediationProperties remediationProperties,
            LeaderElectionService leaderElectionService
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
        this.leaderElectionService = leaderElectionService;
    }

    @Scheduled(
            fixedDelayString = "${camunda.recovery.fixed-delay-ms:30000}",
            initialDelayString = "${camunda.recovery.initial-delay-ms:45000}"
    )
    @Transactional
    public void recoverExecutionPendingWorkflows() {
        // HA: only the leader replica runs recovery, so workflows are not processed twice.
        if (!leaderElectionService.isLeader()) {
            return;
        }
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

    /**
     * Health-check Camunda every 60 s.  After CAMUNDA_DOWN_THRESHOLD consecutive
     * failures, any workflow stuck in CREATED state for more than CREATED_GRACE_MINUTES
     * is moved to MANUAL_FOLLOW_UP so operators know it will never progress automatically.
     * Resets the counter when Camunda is reachable again.
     */
    @Scheduled(fixedDelayString = "${camunda.health.check-delay-ms:60000}",
               initialDelayString = "${camunda.health.check-initial-delay-ms:60000}")
    @Transactional
    public void checkCamundaHealthAndParkStuckWorkflows() {
        if (!leaderElectionService.isLeader()) return;

        boolean reachable = isCamundaReachable();
        if (reachable) {
            int prev = camundaConsecutiveFailures.getAndSet(0);
            if (prev >= CAMUNDA_DOWN_THRESHOLD) {
                logger.info("Camunda recovered after {} consecutive health-check failures", prev);
            }
            return;
        }

        int failures = camundaConsecutiveFailures.incrementAndGet();
        logger.warn("Camunda health-check failed ({}/{})", failures, CAMUNDA_DOWN_THRESHOLD);
        if (failures < CAMUNDA_DOWN_THRESHOLD) return;

        // Camunda is down — park CREATED workflows that have been waiting too long
        List<WorkflowInstance> stuck = workflowRepository.findByStatusAndOrchestrationState(
                WorkflowStatus.RUNNING, WorkflowOrchestrationState.CREATED);
        LocalDateTime cutoff = LocalDateTime.now().minusMinutes(CREATED_GRACE_MINUTES);
        for (WorkflowInstance wf : stuck) {
            if (wf.getStartedAt() != null && wf.getStartedAt().isAfter(cutoff)) continue;
            try {
                logger.warn("Camunda DOWN: parking stuck CREATED workflow {} as MANUAL_FOLLOW_UP", wf.getId());
                workflowService.updateOrchestrationState(
                        wf,
                        WorkflowOrchestrationState.MANUAL_FOLLOW_UP,
                        IncidentStatus.WAITING_FOR_APPROVAL,
                        "Camunda unreachable for " + failures + " consecutive checks — workflow parked for manual follow-up"
                );
                if (!humanTaskRepository.existsByWorkflowInstanceIdAndDecision(wf.getId(), TaskDecision.PENDING)) {
                    humanTaskService.createTask(
                            "Camunda DOWN — manual follow-up for incident #" + wf.getIncident().getId(),
                            wf
                    );
                }
            } catch (Exception ex) {
                logger.warn("Could not park workflow {} during Camunda-down recovery", wf.getId(), ex);
            }
        }
    }

    private boolean isCamundaReachable() {
        try {
            HttpRequest req = HttpRequest.newBuilder()
                    .uri(URI.create(camundaRestAddress + "/actuator/health"))
                    .timeout(Duration.ofSeconds(4))
                    .GET()
                    .build();
            HttpResponse<Void> resp = httpClient.send(req, HttpResponse.BodyHandlers.discarding());
            return resp.statusCode() < 500;
        } catch (Exception ex) {
            logger.debug("Camunda health-check exception: {}", ex.getMessage());
            return false;
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
