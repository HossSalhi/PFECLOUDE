package com.orange.pfe.backend.service;

import com.orange.pfe.backend.config.RemediationProperties;
import com.orange.pfe.backend.dto.AILearnRequest;
import com.orange.pfe.backend.model.action.ActionStatus;
import com.orange.pfe.backend.model.action.ActionType;
import com.orange.pfe.backend.model.action.AutomationMode;
import com.orange.pfe.backend.model.action.IncidentAction;
import com.orange.pfe.backend.model.action.RemediationTargetKind;
import com.orange.pfe.backend.model.enums.IncidentStatus;
import com.orange.pfe.backend.model.workflow.WorkflowInstance;
import com.orange.pfe.backend.model.workflow.WorkflowOrchestrationState;
import com.orange.pfe.backend.model.workflow.WorkflowStatus;
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
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Service;

import com.orange.pfe.backend.dto.WorkflowLogDTO;
import com.orange.pfe.backend.model.human.HumanTask;
import com.orange.pfe.backend.model.human.TaskDecision;

import java.time.Duration;
import java.time.LocalDateTime;
import java.util.Collections;
import java.util.HashMap;
import java.util.List;
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

    private final AIOpsClientService aiOpsClientService;
    private final NotificationService notificationService;

    private JobWorker executeWorker;
    private JobWorker verifyWorker;
    private JobWorker autoEscalateWorker;
    private JobWorker rejectionWorker;

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
            IncidentHistoryService incidentHistoryService,
            AIOpsClientService aiOpsClientService,
            NotificationService notificationService
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
        this.aiOpsClientService = aiOpsClientService;
        this.notificationService = notificationService;
    }

    @EventListener(ApplicationReadyEvent.class)
    public void startWorkers() {
        try {
            camundaWorkflowService.deployProcessIfNeeded();

            executeWorker = camundaClient.newWorker()
                    .jobType("execute-remediation")
                    .handler(this::handleExecuteRemediation)
                    .name("backend-execute-remediation")
                    // 16 concurrent demo incidents create 16 execute jobs at once. maxJobsActive(2)
                    // throttled the worker to 2 in-flight -> the rest sat ACTIVE-undrained in Zeebe ->
                    // action PENDING forever. 16 lets the worker drain a full burst in one pass.
                    .maxJobsActive(16)
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
                    // maxJobsActive(1) serialized ALL verifications: each blocks its thread with
                    // Thread.sleep for up to retries×delay (minutes), so 16 incidents queued ~ hours and
                    // most stayed IN_PROGRESS. 16 lets verifications run in parallel (24 worker threads).
                    .maxJobsActive(16)
                    .timeout(Duration.ofMinutes(6))   // > verifyDelay(25s) + 9×retryDelay(18s) = 187s
                    .fetchVariables("workflowId", "targetNamespace", "targetName", "actionType", "incidentType")
                    .open();

            autoEscalateWorker = camundaClient.newWorker()
                    .jobType("auto-escalate-incident")
                    .handler(this::handleAutoEscalateIncident)
                    .name("backend-auto-escalate")
                    .maxJobsActive(2)
                    .fetchVariables("workflowId", "incidentId", "incidentSeverity", "approvalTimeoutIso")
                    .open();

            rejectionWorker = camundaClient.newWorker()
                    .jobType("handle-incident-rejection")
                    .handler(this::handleRejectionFeedback)
                    .name("backend-rejection-feedback")
                    .maxJobsActive(2)
                    .fetchVariables("workflowId", "incidentId", "incidentSeverity", "incidentType",
                                   "targetNamespace", "targetName")
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
        closeQuietly(autoEscalateWorker);
        closeQuietly(rejectionWorker);
    }

    /**
     * Detect and recover from Camunda worker disconnections.
     * The Camunda gRPC channel may silently drop after a Camunda restart;
     * this scheduler re-opens all workers every 30 s if any are closed.
     */
    @Scheduled(fixedDelayString  = "${camunda.worker.reconnect-check-ms:30000}",
               initialDelayString = "${camunda.worker.reconnect-initial-delay-ms:90000}")
    public void checkAndReconnectWorkers() {
        boolean anyDown = (executeWorker == null || !executeWorker.isOpen())
                || (verifyWorker == null || !verifyWorker.isOpen())
                || (autoEscalateWorker == null || !autoEscalateWorker.isOpen())
                || (rejectionWorker == null || !rejectionWorker.isOpen());
        if (!anyDown) return;

        logger.warn("One or more Camunda job workers are closed — attempting reconnect...");
        closeWorkers();
        executeWorker = null;
        verifyWorker  = null;
        autoEscalateWorker = null;
        rejectionWorker    = null;
        try {
            startWorkers();
            logger.info("Camunda workers successfully reconnected");
        } catch (Exception ex) {
            logger.error("Camunda worker reconnect failed — will retry in 30s", ex);
        }
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

            // Name the SPECIFIC incident (title carries the real measured signal) so the Camunda
            // trace itself differs per test instead of showing generic boilerplate.
            if (workflow.getIncident() != null) {
                workflowService.addWorkflowLog(workflowId,
                        "🎯 Remédiation de l'incident #" + workflow.getIncident().getId()
                                + " — " + workflow.getIncident().getTitle());
            }

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
                // Trace pédagogique : on distingue une vraie action Kubernetes d'une simple escalade.
                boolean isEscalationOnly = action.getType() == ActionType.INVESTIGATION
                        || action.getType() == ActionType.ESCALATION;
                String target = action.getTargetNamespace() + "/" + action.getTargetName();
                boolean nodeTarget = "k8s-nodes".equalsIgnoreCase(action.getTargetNamespace());
                if (isEscalationOnly) {
                    if (nodeTarget) {
                        workflowService.addWorkflowLog(workflowId,
                                "🔍 Décision IA : panne nœud — aucune action kubectl ne peut réparer un nœud "
                                + "injoignable (redémarrer ses pods n'aide pas).");
                        workflowService.addWorkflowLog(workflowId,
                                "↗ Escalade : power-on de la VM " + action.getTargetName()
                                + " délégué au watchdog hôte (hors cluster) + rééquilibrage des charges.");
                        workflowService.addWorkflowLog(workflowId,
                                "$ vmrun start " + action.getTargetName()
                                + "   (équivalent — power-on VM exécuté par le watchdog hôte)");
                        workflowService.addWorkflowLog(workflowId,
                                "⏳ HELIOS surveille le retour du nœud à l'état Ready avant de conclure.");
                    } else {
                        workflowService.addWorkflowLog(workflowId,
                                "🔍 Décision IA : aucune action Kubernetes automatique sûre pour ce type de panne.");
                        workflowService.addWorkflowLog(workflowId,
                                "↗ Escalade à l'opérateur humain pour " + target + " (cause probable : configuration/applicatif).");
                        workflowService.addWorkflowLog(workflowId,
                                "⏳ HELIOS vérifie maintenant si la panne se résout d'elle-même avant de conclure.");
                    }
                    workflowService.updateOrchestrationState(
                            workflow,
                            WorkflowOrchestrationState.EXECUTION_PENDING,
                            IncidentStatus.IN_PROGRESS,
                            nodeTarget ? "Escalade nœud enregistrée ; surveillance du retour du nœud à Ready"
                                       : "Escalade humaine enregistrée ; surveillance pour confirmer si la panne persiste"
                    );
                } else {
                    workflowService.addWorkflowLog(workflowId,
                            "▶ Action automatique choisie : " + describeActionFr(action.getType()) + " sur " + target);
                    String kubectlCmd = buildKubectlEquivalent(action.getType(), action.getTargetNamespace(), action.getTargetName());
                    if (kubectlCmd != null) {
                        workflowService.addWorkflowLog(workflowId, "$ " + kubectlCmd + "   (équivalent kubectl)");
                    }
                    workflowService.addWorkflowLog(workflowId,
                            "✓ Action appliquée : " + result.message());
                    workflowService.updateOrchestrationState(
                            workflow,
                            WorkflowOrchestrationState.EXECUTION_PENDING,
                            IncidentStatus.IN_PROGRESS,
                            "Action appliquée ; vérification que le système revient à la normale"
                    );
                }
            } else {
                actionService.markActionFailed(actionId, result.message(), EXECUTOR_USERNAME, result.message());
                remediationMetricsService.remediationFailed();
                remediationMetricsService.executionDuration(System.nanoTime() - startedAtNanos);
                incidentHistoryService.record(
                        workflow.getIncident(),
                        IncidentStatus.RESOLUTION_FAILED,
                        "Automated remediation failed: " + result.message()
                );
                workflowService.addWorkflowLog(workflowId,
                        "✗ Échec: " + result.message());
                parkWorkflowAsFailed(workflow, "Automation attempt failed: " + result.message());
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

            // Guard: duplicate job activation (lock expired and re-activated by another pod)
            if (workflow.getStatus() == WorkflowStatus.FAILED
                    || workflow.getStatus() == WorkflowStatus.COMPLETED) {
                logger.warn("verify-remediation job {} is a duplicate — workflow #{} already {}; completing no-op",
                        job.getKey(), workflowId, workflow.getStatus());
                completeJobWithRetry(jobClient, job,
                        Map.of("recoveryResolved", false, "verificationMessage", "skipped-duplicate"),
                        "verify-remediation");
                return;
            }

            workflowService.updateOrchestrationState(
                    workflow,
                    WorkflowOrchestrationState.VERIFYING,
                    IncidentStatus.IN_PROGRESS,
                    "Démarrage des vérifications post-remédiation"
            );
            String actionTypeVar = stringValue(variables.get("actionType"));
            String incidentTypeVar = stringValue(variables.get("incidentType"));
            RecoveryVerificationResult verificationResult;
            if ("k8s-nodes".equalsIgnoreCase(namespace)) {
                // NODE incident: the "workload" is a NODE, not a deployment — there is no kubectl fix; the
                // host watchdog powers the VM back on (rejoin ~2-3 min). Verify by polling the node's Ready
                // status (not deployment health / AI), so the workflow concludes RESOLVED cleanly via Camunda.
                workflowService.addWorkflowLog(workflowId,
                        "⏳ Vérification du nœud " + targetName + " : HELIOS attend que le nœud redevienne "
                        + "Ready (rallumage de la VM par le watchdog hôte). Plusieurs essais espacés.");
                boolean nodeBack = false;
                int attempts = 18; // ~18 x 15s = 270s, within the 6-min verify worker timeout
                for (int i = 1; i <= attempts; i++) {
                    nodeBack = remediationExecutor.isNodeReady(targetName);
                    workflowService.addWorkflowLog(workflowId, nodeBack
                            ? "✓ Vérification " + i + "/" + attempts + " — le nœud " + targetName
                                    + " est de retour Ready → incident RÉSOLU"
                            : "⏳ Vérification " + i + "/" + attempts + " — nœud " + targetName
                                    + " encore NotReady (rallumage de la VM par le watchdog hôte en cours)...");
                    if (nodeBack || i == attempts) break;
                    Thread.sleep(15_000L);
                }
                verificationResult = new RecoveryVerificationResult(nodeBack, !nodeBack,
                        nodeBack ? "node " + targetName + " back Ready"
                                 : "node " + targetName + " still NotReady after verification window");
            } else {
                workflowService.addWorkflowLog(workflowId,
                        "⏳ Vérification du rétablissement (2 conditions : pods tous prêts + l'IA ne détecte plus "
                        + "d'anomalie). Plusieurs essais espacés pour laisser le système se stabiliser.");
                // AUTOSCALING / SCALE_DEPLOYMENT: verify in "scale mode" — success = the added replicas are
                // Ready. The AI still reading high load is expected (sustained load is why we scaled), so it
                // must not keep the incident open. Other types verify normally (workload healthy + AI clear).
                boolean scaleMode = "SCALE_DEPLOYMENT".equalsIgnoreCase(actionTypeVar)
                        || "AUTOSCALING".equalsIgnoreCase(incidentTypeVar);
                verificationResult = recoveryVerificationService.verify(
                        namespace, targetName,
                        msg -> workflowService.addWorkflowLog(workflowId, msg),
                        scaleMode
                );
            }
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
                WorkflowInstance completed = workflowService.completeWorkflow(workflowId);
                try {
                    notificationService.notifyIncidentResolved(completed.getIncident());
                } catch (Exception ex) {
                    logger.warn("Resolved notification failed for incident #{}: {}",
                            completed.getIncident().getId(), ex.getMessage());
                }
            } else {
                remediationMetricsService.remediationFailed();
                remediationMetricsService.verificationDuration(System.nanoTime() - startedAtNanos);
                // Raison lisible par un humain (sans jargon) pour la trace + l'email d'escalade.
                String humanReason = "La panne persiste après remédiation : "
                        + (verificationResult.workloadHealthy()
                            ? "les pods sont prêts mais "
                            : "le déploiement n'est toujours pas disponible et ")
                        + "l'IA détecte encore l'anomalie après toutes les tentatives. "
                        + "Cause probable : panne non corrigeable par un redémarrage (config/applicatif ou dépendance externe). "
                        + "→ Escalade à l'opérateur humain.";
                incidentHistoryService.record(
                        workflow.getIncident(),
                        IncidentStatus.RESOLUTION_FAILED,
                        humanReason
                );
                workflowService.addWorkflowLog(workflowId, "✗ " + humanReason);
                parkWorkflowAsFailed(workflow, humanReason);
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

    private void handleAutoEscalateIncident(JobClient jobClient, ActivatedJob job) {
        Map<String, Object> variables = job.getVariablesAsMap();
        try {
            Long workflowId = longValue(variables.get("workflowId"));
            String timeoutIso = stringValueOrDefault(variables.get("approvalTimeoutIso"), "PT1H");
            String severity   = stringValueOrDefault(variables.get("incidentSeverity"), "MEDIUM");

            WorkflowInstance workflow = workflowService.getWorkflowInstance(workflowId);

            parkWorkflowAsFailed(workflow,
                    "HELIOS n'a pas pu résoudre automatiquement (severity=" + severity + ", timeout=" + timeoutIso + ")");

            logger.warn("Incident #{} marked RESOLUTION_FAILED via auto-escalate (timeout={}, severity={})",
                    workflow.getIncident().getId(), timeoutIso, severity);

            completeJobWithRetry(
                    jobClient,
                    job,
                    Map.of("escalatedAutomatically", true, "escalationReason", "approval_timeout"),
                    "auto-escalate-incident"
            );
        } catch (InterruptedException ex) {
            Thread.currentThread().interrupt();
            failJob(jobClient, job, ex, variables);
        } catch (Exception ex) {
            logger.error("Auto-escalate worker failed for job {}", job.getKey(), ex);
            failJob(jobClient, job, ex, variables);
        }
    }

    /**
     * Called when an operator rejects a remediation proposal.
     * Feeds the rejection back to the AI so the model learns from it.
     */
    private void handleRejectionFeedback(JobClient jobClient, ActivatedJob job) {
        Map<String, Object> variables = job.getVariablesAsMap();
        try {
            Long workflowId = longValue(variables.get("workflowId"));
            String severity  = stringValueOrDefault(variables.get("incidentSeverity"), "MEDIUM");
            String incType   = stringValueOrDefault(variables.get("incidentType"), "UNKNOWN");
            String namespace = stringValue(variables.get("targetNamespace"));
            String name      = stringValue(variables.get("targetName"));

            WorkflowInstance workflow = workflowService.getWorkflowInstance(workflowId);
            incidentHistoryService.record(
                    workflow.getIncident(),
                    IncidentStatus.WAITING_FOR_APPROVAL,
                    "Remediation rejected by operator — AI learn feedback sent"
            );
            workflowService.updateOrchestrationState(
                    workflow,
                    WorkflowOrchestrationState.REJECTED,
                    IncidentStatus.WAITING_FOR_APPROVAL,
                    "Remediation rejected by operator"
            );

            // Teach the AI that the suggested action was not accepted
            AILearnRequest learnReq = new AILearnRequest();
            learnReq.setIncidentType(incType);
            learnReq.setSeverity(severity);
            learnReq.setSuccess(false);
            learnReq.setAction("rejected");
            learnReq.setNamespace(namespace);
            learnReq.setApp(name);
            learnReq.setMinutesToResolve(0);
            aiOpsClientService.learn(learnReq);

            logger.info("Rejection feedback sent to AI for incident #{}", workflow.getIncident().getId());

            completeJobWithRetry(
                    jobClient, job,
                    Map.of("rejectionFeedbackSent", true),
                    "handle-incident-rejection"
            );
        } catch (Exception ex) {
            logger.error("Rejection feedback worker failed for job {}", job.getKey(), ex);
            failJob(jobClient, job, ex, variables);
        }
    }

    private void parkWorkflowAsFailed(WorkflowInstance staleWorkflow, String reason) {
        // Reload fresh from DB — the stale object may have been detached for minutes
        // (verification runs 7 min); cascading a detached incident via repo.save() doesn't persist.
        WorkflowInstance workflow = workflowService.getWorkflowInstance(staleWorkflow.getId());

        // Idempotent guard — avoid double-write if called twice (BPMN route + duplicate job)
        if (workflow.getStatus() == WorkflowStatus.FAILED
                || workflow.getStatus() == WorkflowStatus.COMPLETED) {
            logger.info("parkWorkflowAsFailed: workflow #{} already {} — skipping", workflow.getId(), workflow.getStatus());
            return;
        }
        workflowService.finalizeWorkflow(
                workflow,
                WorkflowOrchestrationState.ESCALATED,
                WorkflowStatus.FAILED,
                IncidentStatus.RESOLUTION_FAILED,
                "HELIOS a épuisé toutes les tentatives de remédiation. Raison: " + reason
        );
        incidentHistoryService.record(
                workflow.getIncident(),
                IncidentStatus.RESOLUTION_FAILED,
                "HELIOS n'a pas pu résoudre l'incident automatiquement. Raison: " + reason
        );

        // Auto-close any PENDING human tasks so they no longer block the operator UI
        List<HumanTask> pendingTasks =
                humanTaskRepository.findByWorkflowInstanceIdAndDecision(workflow.getId(), TaskDecision.PENDING);
        if (!pendingTasks.isEmpty()) {
            LocalDateTime now = LocalDateTime.now();
            pendingTasks.forEach(t -> { t.setDecision(TaskDecision.CANCELLED); t.setCompletedAt(now); });
            humanTaskRepository.saveAll(pendingTasks);
            logger.info("Auto-closed {} pending human task(s) for workflow #{}", pendingTasks.size(), workflow.getId());
        }

        // Create a new PENDING task so the operator sees this in the human tasks list
        if (!humanTaskRepository.existsByWorkflowInstanceIdAndDecision(workflow.getId(), TaskDecision.PENDING)) {
            humanTaskService.createTask(
                    "Remédiation échouée — intervention requise pour l'incident #" + workflow.getIncident().getId(),
                    workflow
            );
        }

        // Load workflow logs for rich email report
        List<WorkflowLogDTO> workflowLogs = Collections.emptyList();
        try {
            workflowLogs = workflowService.getWorkflowLogsForIncident(workflow.getIncident().getId());
        } catch (Exception ex) {
            logger.warn("Could not load workflow logs for failure email: {}", ex.getMessage());
        }

        try {
            notificationService.notifyRemediationFailedWithTrace(workflow.getIncident(), reason, workflowLogs);
        } catch (Exception ex) {
            logger.warn("Failed to send escalation notification for incident #{}: {}",
                    workflow.getIncident().getId(), ex.getMessage());
        }
        logger.warn("Incident #{} marked RESOLUTION_FAILED — HELIOS could not auto-remediate. Reason: {}",
                workflow.getIncident().getId(), reason);
    }

    private void parkWorkflowForApproval(WorkflowInstance workflow, String reason) {
        // WAITING_FOR_APPROVAL supprimé — tout incident non résolu passe directement à RESOLUTION_FAILED.
        parkWorkflowAsFailed(workflow, reason);
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
        // WorkflowNotFoundException has TWO very different causes:
        //  (1) TRANSACTION RACE (the common one): the Camunda process instance is created INSIDE the same
        //      DB transaction that writes the workflow_instance row, so a fast worker can activate the
        //      execute job BEFORE that row is committed -> getWorkflowInstance() throws "not found". This
        //      is a LEGITIMATE current-run job; dropping it permanently (retries=0) strands the real
        //      remediation in PENDING forever (no kubectl command ever runs). We instead FAIL WITH BACKOFF
        //      so the creating tx commits, then the next activation finds the row and runs the real action.
        //  (2) TRUE ORPHAN (rare): the workflow row was purged by a demo reset while its PI lived on. Such
        //      a job never finds its row -> it exhausts its retries here and is then dropped for good.
        if (ex instanceof com.orange.pfe.backend.exception.WorkflowNotFoundException) {
            int remaining = Math.max(0, job.getRetries() - 1);
            try {
                if (remaining > 0) {
                    logger.warn("Workflow not visible yet for job {} ('{}') — retrying ({} left; creating tx likely still committing): {}",
                            job.getKey(), job.getType(), remaining, ex.getMessage());
                    jobClient.newFailCommand(job).retries(remaining)
                            .retryBackoff(Duration.ofSeconds(6))
                            .errorMessage("workflow not yet visible (tx committing): " + ex.getMessage())
                            .send().join();
                } else {
                    logger.warn("Dropping ORPHAN job {} ('{}') — workflow still gone after retries (purged): {}",
                            job.getKey(), job.getType(), ex.getMessage());
                    jobClient.newFailCommand(job).retries(0).errorMessage("orphan: workflow purged").send().join();
                }
            } catch (Exception e) { logger.debug("not-found fail-command error: {}", e.getMessage()); }
            return;
        }
        int retries = Math.max(0, job.getRetries() - 1);

        if (retries == 0) {
            // All retries exhausted — escalate to super admin via email (no HumanTask).
            Long workflowId = null;
            try { workflowId = longValue(variables.get("workflowId")); } catch (Exception ignored) {}
            if (workflowId != null) {
                try {
                    WorkflowInstance wf = workflowService.getWorkflowInstance(workflowId);
                    String reason = "Job '" + job.getType() + "' failed permanently after all retries. Error: " + ex.getMessage();
                    incidentHistoryService.record(
                            wf.getIncident(),
                            IncidentStatus.RESOLUTION_FAILED,
                            "HELIOS a épuisé toutes ses tentatives. Super admin notifié. " + reason
                    );
                    workflowService.updateOrchestrationState(
                            wf,
                            WorkflowOrchestrationState.ESCALATED,
                            IncidentStatus.RESOLUTION_FAILED,
                            reason
                    );
                    notificationService.notifyRemediationFailed(wf.getIncident(), reason);
                    logger.error("Job '{}' for workflow #{} failed permanently — super admin notified via email",
                            job.getType(), workflowId);
                } catch (Exception notifyEx) {
                    logger.warn("Could not escalate workflow after permanent job failure", notifyEx);
                }
            }
        }
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

    /** Returns the kubectl-equivalent command string, or {@code null} for non-K8s actions (e.g. INVESTIGATION). */
    private String buildKubectlEquivalent(ActionType type, String ns, String name) {
        if (type == null) return null;
        String n = name != null ? name : "<app>";
        String namespace = ns != null ? ns : "pfe";
        return switch (type) {
            case RESTART_POD               -> "kubectl delete pod -n " + namespace + " -l app=" + n;
            case SCALE_DEPLOYMENT          -> "kubectl scale deployment/" + n + " -n " + namespace + " --replicas=<N+1>";
            case SCALE_DOWN                -> "kubectl scale deployment/" + n + " -n " + namespace + " --replicas=1";
            case ROLLBACK_DEPLOYMENT       -> "kubectl rollout undo deployment/" + n + " -n " + namespace;
            case DELETE_CRASHLOOPING_PODS  -> "kubectl delete pods -n " + namespace + " -l app=" + n;
            case REMOVE_NETWORK_POLICY     -> "kubectl delete networkpolicy <bloquante-récente> -n " + namespace;
            case FORCE_RECREATE_DEPLOYMENT -> "kubectl rollout restart deployment/" + n + " -n " + namespace;
            case CORDON_NODE               -> "kubectl cordon " + n;
            case DRAIN_NODE                -> "kubectl drain " + n + " --ignore-daemonsets --delete-emptydir-data";
            case INVESTIGATION             -> null;  // escalation, no K8s command
            default                        -> null;
        };
    }

    /** Description en français clair de l'action, pour la trace lisible par un opérateur. */
    private String describeActionFr(ActionType type) {
        if (type == null) return "action inconnue";
        return switch (type) {
            case RESTART_POD               -> "supprimer le pod pour le redémarrer";
            case SCALE_DEPLOYMENT          -> "ajouter des répliques (scale up)";
            case SCALE_DOWN                -> "réduire les répliques (scale down)";
            case ROLLBACK_DEPLOYMENT       -> "revenir à la version précédente (rollback)";
            case DELETE_CRASHLOOPING_PODS  -> "supprimer les pods en crash pour forcer une recréation propre";
            case REMOVE_NETWORK_POLICY     -> "supprimer la NetworkPolicy bloquante (rétablir la connectivité)";
            case FORCE_RECREATE_DEPLOYMENT -> "redémarrage progressif de tous les pods (rollout restart)";
            case CORDON_NODE               -> "isoler le nœud (cordon)";
            case DRAIN_NODE                -> "vider le nœud et migrer les pods (drain)";
            case RESTART_VM                -> "redémarrer la VM du worker (watchdog)";
            case INVESTIGATION             -> "investigation / escalade humaine";
            case ESCALATION                -> "escalade à l'équipe d'astreinte";
            default                        -> type.name();
        };
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
