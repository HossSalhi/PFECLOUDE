package com.orange.pfe.backend.service;

import com.orange.pfe.backend.config.RemediationProperties;
import com.orange.pfe.backend.model.action.ActionStatus;
import com.orange.pfe.backend.model.action.ActionType;
import com.orange.pfe.backend.model.action.AutomationMode;
import com.orange.pfe.backend.model.action.IncidentAction;
import com.orange.pfe.backend.model.action.RemediationTargetKind;
import com.orange.pfe.backend.model.enums.IncidentStatus;
import com.orange.pfe.backend.model.human.TaskDecision;
import com.orange.pfe.backend.model.workflow.WorkflowInstance;
import com.orange.pfe.backend.model.workflow.WorkflowOrchestrationState;
import com.orange.pfe.backend.repository.HumanTaskRepository;
import com.orange.pfe.backend.repository.IncidentActionRepository;
import com.orange.pfe.backend.service.remediation.RecoveryVerificationResult;
import com.orange.pfe.backend.service.remediation.RecoveryVerificationService;
import com.orange.pfe.backend.service.remediation.RemediationExecutionResult;
import com.orange.pfe.backend.service.remediation.RemediationExecutor;
import com.orange.pfe.backend.service.remediation.RemediationMetricsService;
import com.orange.pfe.backend.service.remediation.RemediationPlan;
import io.camunda.client.CamundaClient;
import io.camunda.client.api.response.ActivatedJob;
import io.camunda.client.api.worker.JobClient;
import io.camunda.client.api.worker.JobWorker;
import jakarta.annotation.PreDestroy;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.boot.autoconfigure.condition.ConditionalOnBean;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.boot.context.event.ApplicationReadyEvent;
import org.springframework.context.event.EventListener;
import org.springframework.stereotype.Service;

import java.time.LocalDateTime;
import java.util.HashMap;
import java.util.Map;

@Service
@ConditionalOnProperty(name = "camunda.enabled", havingValue = "true")
@ConditionalOnBean(CamundaClient.class)
public class CamundaJobWorkerLifecycle {

    private static final Logger logger = LoggerFactory.getLogger(CamundaJobWorkerLifecycle.class);
    private static final String EXECUTOR_USERNAME = "CAMUNDA_WORKER";
    private static final int CAMUNDA_COMMAND_RETRY_ATTEMPTS = 3;
    private static final long CAMUNDA_COMMAND_RETRY_DELAY_MS = 1000L;

    private final CamundaClient camundaClient;
    private final CamundaWorkflowService camundaWorkflowService;
    private final WorkflowService workflowService;
    private final IncidentActionRepository incidentActionRepository;
    private final ActionService actionService;
    private final RemediationExecutor remediationExecutor;
    private final RecoveryVerificationService recoveryVerificationService;
    private final RemediationMetricsService remediationMetricsService;
    private final HumanTaskRepository humanTaskRepository;
    private final HumanTaskService humanTaskService;
    private final RemediationProperties remediationProperties;
    private final IncidentHistoryService incidentHistoryService;

    private JobWorker executeWorker;
    private JobWorker verifyWorker;

    public CamundaJobWorkerLifecycle(
            CamundaClient camundaClient,
            CamundaWorkflowService camundaWorkflowService,
            WorkflowService workflowService,
            IncidentActionRepository incidentActionRepository,
            ActionService actionService,
            RemediationExecutor remediationExecutor,
            RecoveryVerificationService recoveryVerificationService,
            RemediationMetricsService remediationMetricsService,
            HumanTaskRepository humanTaskRepository,
            HumanTaskService humanTaskService,
            RemediationProperties remediationProperties,
            IncidentHistoryService incidentHistoryService
    ) {
        this.camundaClient = camundaClient;
        this.camundaWorkflowService = camundaWorkflowService;
        this.workflowService = workflowService;
        this.incidentActionRepository = incidentActionRepository;
        this.actionService = actionService;
        this.remediationExecutor = remediationExecutor;
        this.recoveryVerificationService = recoveryVerificationService;
        this.remediationMetricsService = remediationMetricsService;
        this.humanTaskRepository = humanTaskRepository;
        this.humanTaskService = humanTaskService;
        this.remediationProperties = remediationProperties;
        this.incidentHistoryService = incidentHistoryService;
    }

    @EventListener(ApplicationReadyEvent.class)
    public void startWorkers() {
        try {
            camundaWorkflowService.deployProcessIfNeeded();

            executeWorker = camundaClient.newWorker()
                    .jobType("execute-remediation")
                    .handler(this::handleExecuteRemediation)
                    .name("backend-execute-remediation")
                    .maxJobsActive(2)
                    .fetchVariables(
                            "workflowId",
                            "actionId",
                            "actionType",
                            "targetNamespace",
                            "targetKind",
                            "targetName",
                            "automationMode",
                            "approvalRequired",
                            "autoExecutable",
                            "manualFollowUp",
                            "cooldownBlocked",
                            "dbRelated",
                            "rationale"
                    )
                    .open();

            verifyWorker = camundaClient.newWorker()
                    .jobType("verify-remediation")
                    .handler(this::handleVerifyRemediation)
                    .name("backend-verify-remediation")
                    .maxJobsActive(2)
                    .fetchVariables("workflowId", "targetNamespace", "targetName")
                    .open();

            logger.info("Camunda remediation workers started");
        } catch (Exception ex) {
            logger.error("Unable to start Camunda remediation workers", ex);
            closeQuietly(executeWorker);
            closeQuietly(verifyWorker);
            executeWorker = null;
            verifyWorker = null;
        }
    }

    @PreDestroy
    public void closeWorkers() {
        closeQuietly(executeWorker);
        closeQuietly(verifyWorker);
    }

    private void handleExecuteRemediation(JobClient jobClient, ActivatedJob job) {
        Map<String, Object> variables = job.getVariablesAsMap();
        long startedAtNanos = System.nanoTime();
        try {
            Long workflowId = longValue(variables.get("workflowId"));
            Long actionId = longValue(variables.get("actionId"));

            WorkflowInstance workflow = workflowService.getWorkflowInstance(workflowId);
            IncidentAction action = incidentActionRepository.findById(actionId)
                    .orElseThrow(() -> new IllegalStateException("Action not found: " + actionId));

            markActionInProgress(action);

            RemediationPlan plan = buildPlan(variables, action);
            if (plan.autoExecutable()) {
                remediationMetricsService.autoRemediationAttempted();
            }

            RemediationExecutionResult result = remediationExecutor.execute(plan);
            if (result.success()) {
                actionService.markActionCompleted(actionId, EXECUTOR_USERNAME, result.message());
                remediationMetricsService.executionDuration(System.nanoTime() - startedAtNanos);
                incidentHistoryService.record(
                        workflow.getIncident(),
                        IncidentStatus.IN_PROGRESS,
                        "Automated remediation executed: " + result.message()
                );
                workflowService.updateOrchestrationState(
                        workflow,
                        WorkflowOrchestrationState.EXECUTION_PENDING,
                        IncidentStatus.IN_PROGRESS,
                        "Remediation executed successfully; waiting before verification"
                );
            } else {
                actionService.markActionFailed(actionId, result.message(), EXECUTOR_USERNAME, result.message());
                remediationMetricsService.remediationFailed();
                remediationMetricsService.executionDuration(System.nanoTime() - startedAtNanos);
                incidentHistoryService.record(
                        workflow.getIncident(),
                        IncidentStatus.WAITING_FOR_APPROVAL,
                        "Automated remediation failed and requires review: " + result.message()
                );
                parkWorkflowForApproval(workflow, "Automation attempt failed: " + result.message());
            }

            completeJobWithRetry(
                    jobClient,
                    job,
                    Map.of(
                            "executionSucceeded", result.success(),
                            "executionMessage", result.message(),
                            "actionId", actionId
                    ),
                    "execute-remediation"
            );
        } catch (Exception ex) {
            logger.error("Execute remediation worker failed for job {}", job.getKey(), ex);
            failJob(jobClient, job, ex, variables);
        }
    }

    private void handleVerifyRemediation(JobClient jobClient, ActivatedJob job) {
        Map<String, Object> variables = job.getVariablesAsMap();
        long startedAtNanos = System.nanoTime();
        try {
            Long workflowId = longValue(variables.get("workflowId"));
            String namespace = stringValue(variables.get("targetNamespace"));
            String targetName = stringValue(variables.get("targetName"));

            WorkflowInstance workflow = workflowService.getWorkflowInstance(workflowId);
            workflowService.updateOrchestrationState(
                    workflow,
                    WorkflowOrchestrationState.VERIFYING,
                    IncidentStatus.IN_PROGRESS,
                    "Starting post-remediation recovery checks"
            );

            RecoveryVerificationResult verificationResult = recoveryVerificationService.verify(namespace, targetName);
            boolean resolved = verificationResult.resolved();
            remediationMetricsService.verification(resolved);

            if (resolved) {
                remediationMetricsService.remediationSucceeded();
                remediationMetricsService.verificationDuration(System.nanoTime() - startedAtNanos);
                incidentHistoryService.record(
                        workflow.getIncident(),
                        IncidentStatus.RESOLVED,
                        "Post-remediation verification succeeded: " + verificationResult.message()
                );
                workflowService.completeWorkflow(workflowId);
            } else {
                remediationMetricsService.remediationFailed();
                remediationMetricsService.verificationDuration(System.nanoTime() - startedAtNanos);
                incidentHistoryService.record(
                        workflow.getIncident(),
                        IncidentStatus.WAITING_FOR_APPROVAL,
                        "Post-remediation verification failed: " + verificationResult.message()
                );
                parkWorkflowForApproval(workflow, "Post-remediation verification failed: " + verificationResult.message());
            }

            completeJobWithRetry(
                    jobClient,
                    job,
                    Map.of(
                            "recoveryResolved", resolved,
                            "verificationMessage", verificationResult.message()
                    ),
                    "verify-remediation"
            );
        } catch (InterruptedException ex) {
            Thread.currentThread().interrupt();
            failJob(jobClient, job, ex, variables);
        } catch (Exception ex) {
            logger.error("Verify remediation worker failed for job {}", job.getKey(), ex);
            failJob(jobClient, job, ex, variables);
        }
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

    private void markActionInProgress(IncidentAction action) {
        action.incrementAttemptCount();
        action.setStatus(ActionStatus.IN_PROGRESS);
        action.setExecutedBy(EXECUTOR_USERNAME);
        action.setExecutedAt(LocalDateTime.now());
        incidentActionRepository.save(action);
    }

    private RemediationPlan buildPlan(Map<String, Object> variables, IncidentAction action) {
        ActionType actionType = enumValue(ActionType.class, variables.get("actionType"), action.getType(), ActionType.REMEDIATION);
        AutomationMode automationMode = enumValue(AutomationMode.class, variables.get("automationMode"), action.getAutomationMode(), AutomationMode.MANUAL);
        RemediationTargetKind targetKind = enumValue(RemediationTargetKind.class, variables.get("targetKind"), action.getTargetKind(), RemediationTargetKind.UNKNOWN);

        return new RemediationPlan(
                actionType,
                automationMode,
                booleanValue(variables.get("approvalRequired")),
                booleanValue(variables.get("autoExecutable")),
                booleanValue(variables.get("manualFollowUp")),
                booleanValue(variables.get("cooldownBlocked")),
                booleanValue(variables.get("dbRelated")),
                stringValueOrDefault(variables.get("targetNamespace"), action.getTargetNamespace()),
                targetKind,
                stringValueOrDefault(variables.get("targetName"), action.getTargetName()),
                stringValue(variables.get("rationale"))
        );
    }

    private void failJob(JobClient jobClient, ActivatedJob job, Exception ex, Map<String, Object> variables) {
        int retries = Math.max(0, job.getRetries() - 1);
        Map<String, Object> failureVariables = new HashMap<>();
        failureVariables.put("workerFailureMessage", ex.getMessage());
        failureVariables.putAll(variables);

        Exception lastFailure = null;
        for (int attempt = 1; attempt <= CAMUNDA_COMMAND_RETRY_ATTEMPTS; attempt++) {
            try {
                jobClient.newFailCommand(job)
                        .retries(retries)
                        .errorMessage(ex.getMessage())
                        .variables(failureVariables)
                        .send()
                        .join();
                return;
            } catch (Exception sendEx) {
                lastFailure = sendEx;
                logger.warn(
                        "Failed to report Camunda job failure for job {} on attempt {}/{}",
                        job.getKey(),
                        attempt,
                        CAMUNDA_COMMAND_RETRY_ATTEMPTS,
                        sendEx
                );
                try {
                    sleepBeforeRetry(attempt);
                } catch (InterruptedException interruptedException) {
                    Thread.currentThread().interrupt();
                    logger.warn("Interrupted while retrying Camunda fail command for job {}", job.getKey());
                    return;
                }
            }
        }

        if (lastFailure != null) {
            logger.error("Unable to report Camunda job failure for job {}", job.getKey(), lastFailure);
        }
    }

    private void completeJobWithRetry(
            JobClient jobClient,
            ActivatedJob job,
            Map<String, Object> variables,
            String phase
    ) throws InterruptedException {
        Exception lastFailure = null;
        for (int attempt = 1; attempt <= CAMUNDA_COMMAND_RETRY_ATTEMPTS; attempt++) {
            try {
                jobClient.newCompleteCommand(job)
                        .variables(variables)
                        .send()
                        .join();
                return;
            } catch (Exception sendEx) {
                lastFailure = sendEx;
                logger.warn(
                        "Failed to complete Camunda job {} during {} on attempt {}/{}",
                        job.getKey(),
                        phase,
                        attempt,
                        CAMUNDA_COMMAND_RETRY_ATTEMPTS,
                        sendEx
                );
                sleepBeforeRetry(attempt);
            }
        }

        throw new IllegalStateException(
                "Unable to complete Camunda job after retries during " + phase,
                lastFailure
        );
    }

    private void sleepBeforeRetry(int attempt) throws InterruptedException {
        if (attempt < CAMUNDA_COMMAND_RETRY_ATTEMPTS) {
            Thread.sleep(CAMUNDA_COMMAND_RETRY_DELAY_MS);
        }
    }

    private void closeQuietly(JobWorker worker) {
        if (worker != null) {
            try {
                worker.close();
            } catch (Exception ex) {
                logger.warn("Unable to close Camunda worker cleanly", ex);
            }
        }
    }

    private Long longValue(Object value) {
        if (value == null) {
            throw new IllegalArgumentException("Missing numeric job variable");
        }
        if (value instanceof Number number) {
            return number.longValue();
        }
        return Long.parseLong(String.valueOf(value));
    }

    private boolean booleanValue(Object value) {
        return value != null && Boolean.parseBoolean(String.valueOf(value));
    }

    private String stringValue(Object value) {
        return value == null ? null : String.valueOf(value);
    }

    private String stringValueOrDefault(Object value, String fallback) {
        String resolved = stringValue(value);
        return resolved == null || resolved.isBlank() ? fallback : resolved;
    }

    private <T extends Enum<T>> T enumValue(Class<T> enumType, Object rawValue, T currentValue, T fallback) {
        if (rawValue != null) {
            try {
                return Enum.valueOf(enumType, String.valueOf(rawValue));
            } catch (IllegalArgumentException ignored) {
                logger.warn("Unable to parse enum {} from value {}", enumType.getSimpleName(), rawValue);
            }
        }
        if (currentValue != null) {
            return currentValue;
        }
        return fallback;
    }
}
