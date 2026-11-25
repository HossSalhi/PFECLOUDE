
package com.orange.pfe.backend.service;

import com.orange.pfe.backend.config.RemediationProperties;
import com.orange.pfe.backend.dto.AIOpsAnalyzeRequest;
import com.orange.pfe.backend.dto.ExecutionEventRequest;
import com.orange.pfe.backend.dto.IncidentVerificationRequest;
import com.orange.pfe.backend.dto.WorkflowLogDTO;
import com.orange.pfe.backend.model.action.ActionStatus;
import com.orange.pfe.backend.model.action.ActionType;
import com.orange.pfe.backend.model.action.AutomationMode;
import com.orange.pfe.backend.model.action.IncidentAction;
import com.orange.pfe.backend.model.action.RemediationTargetKind;
import com.orange.pfe.backend.model.enums.ExecutionStage;
import com.orange.pfe.backend.model.enums.IncidentStatus;
import com.orange.pfe.backend.model.human.HumanTask;
import com.orange.pfe.backend.model.human.TaskDecision;
import com.orange.pfe.backend.model.workflow.WorkflowInstance;
import com.orange.pfe.backend.model.workflow.WorkflowOrchestrationState;
import com.orange.pfe.backend.model.workflow.WorkflowStatus;
import com.orange.pfe.backend.repository.HumanTaskRepository;
import com.orange.pfe.backend.repository.IncidentActionRepository;
import com.orange.pfe.backend.service.remediation.HostCpuVerificationService;
import com.orange.pfe.backend.service.remediation.HostDiskVerificationService;
import com.orange.pfe.backend.service.remediation.HostMemoryVerificationService;
import com.orange.pfe.backend.service.remediation.NodeReadinessObservation;
import com.orange.pfe.backend.service.vmpower.VmPowerPolicy;
import com.orange.pfe.backend.service.vmpower.VmRecoveryVerifier;
import com.orange.pfe.backend.service.vmpower.WatchdogClient;
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
import org.springframework.beans.factory.ObjectProvider;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.autoconfigure.condition.ConditionalOnBean;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.boot.context.event.ApplicationReadyEvent;
import org.springframework.context.event.EventListener;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Service;

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

    private static final Logger logger =
            LoggerFactory.getLogger(CamundaJobWorkerLifecycle.class);

    private static final String EXECUTOR_USERNAME = "CAMUNDA_WORKER";
    private static final int CAMUNDA_COMMAND_RETRY_ATTEMPTS = 3;
    private static final long CAMUNDA_COMMAND_RETRY_DELAY_MS = 1000L;

    private static final int NODE_READY_MAX_ATTEMPTS = 18;
    private static final long NODE_READY_INTERVAL_MS = 15_000L;
    // Points de contrôle package-private pour les tests unitaires.
    int nodeReadyVerificationMaxAttempts() {
        return NODE_READY_MAX_ATTEMPTS;
    }

    void waitBetweenNodeReadyObservations() throws InterruptedException {
        Thread.sleep(NODE_READY_INTERVAL_MS);
    }

    private final CamundaClient camundaClient;
    private final CamundaWorkflowService camundaWorkflowService;
    private final WorkflowService workflowService;
    private final IncidentActionRepository incidentActionRepository;
    private final ActionService actionService;
    private final RemediationExecutor remediationExecutor;
    private final RecoveryVerificationService recoveryVerificationService;
    private final HostCpuVerificationService hostCpuVerificationService;
    private final HostDiskVerificationService hostDiskVerificationService;
    private final HostMemoryVerificationService hostMemoryVerificationService;
    private final RemediationMetricsService remediationMetricsService;
    private final HumanTaskRepository humanTaskRepository;
    private final HumanTaskService humanTaskService;
    private final RemediationProperties remediationProperties;
    private final IncidentHistoryService incidentHistoryService;

    private final NotificationService notificationService;
    private final IncidentCardService incidentCardService;

    private final ObjectProvider<AIOpsOrchestrationService> orchestrationProvider;
    private final ObjectProvider<InfrastructureHealthScheduler> infraProvider;

    @Value("${helios.host-watchdog.url:http://192.168.148.1:9099}")
    private String hostWatchdogUrl;

    // VMDOWN-POLICY-20261105 : vérification de récupération des VM d'INFRASTRUCTURE (.162). Injection par champ,
    // facultative, pour ne pas modifier le constructeur ; absente => la vérification infra ÉCHOUE (fail-closed).
    @org.springframework.beans.factory.annotation.Autowired(required = false)
    private VmRecoveryVerifier vmRecoveryVerifier;
    @org.springframework.beans.factory.annotation.Autowired(required = false)
    private WatchdogClient watchdogClient;

    void setVmRecovery(VmRecoveryVerifier verifier, WatchdogClient client) {
        this.vmRecoveryVerifier = verifier;
        this.watchdogClient = client;
    }

    @Value("${aiops.polling.window-minutes:1}")
    private int detectionWindowMinutes;

    private JobWorker executeWorker;
    private JobWorker verifyWorker;
    private JobWorker autoEscalateWorker;
    private JobWorker rejectionWorker;
    private JobWorker classifyWorker;

    public CamundaJobWorkerLifecycle(
            CamundaClient camundaClient,
            CamundaWorkflowService camundaWorkflowService,
            WorkflowService workflowService,
            IncidentActionRepository incidentActionRepository,
            ActionService actionService,
            RemediationExecutor remediationExecutor,
            RecoveryVerificationService recoveryVerificationService,
            HostCpuVerificationService hostCpuVerificationService,
            HostDiskVerificationService hostDiskVerificationService,
            HostMemoryVerificationService hostMemoryVerificationService,
            RemediationMetricsService remediationMetricsService,
            HumanTaskRepository humanTaskRepository,
            HumanTaskService humanTaskService,
            RemediationProperties remediationProperties,
            IncidentHistoryService incidentHistoryService,
            NotificationService notificationService,
            ObjectProvider<AIOpsOrchestrationService> orchestrationProvider,
            ObjectProvider<InfrastructureHealthScheduler> infraProvider,
            IncidentCardService incidentCardService
    ) {
        this.orchestrationProvider = orchestrationProvider;
        this.infraProvider = infraProvider;
        this.incidentCardService = incidentCardService;
        this.camundaClient = camundaClient;
        this.camundaWorkflowService = camundaWorkflowService;
        this.workflowService = workflowService;
        this.incidentActionRepository = incidentActionRepository;
        this.actionService = actionService;
        this.remediationExecutor = remediationExecutor;
        this.recoveryVerificationService = recoveryVerificationService;
        this.hostCpuVerificationService = hostCpuVerificationService;
        this.hostDiskVerificationService = hostDiskVerificationService;
        this.hostMemoryVerificationService = hostMemoryVerificationService;
        this.remediationMetricsService = remediationMetricsService;
        this.humanTaskRepository = humanTaskRepository;
        this.humanTaskService = humanTaskService;
        this.remediationProperties = remediationProperties;
        this.incidentHistoryService = incidentHistoryService;
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
                    .maxJobsActive(16)
                    .fetchVariables(
                            "workflowId",
                            "actionId",
                            "actionType",
                            "incidentType",
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
                    .maxJobsActive(16)
                    .timeout(Duration.ofMinutes(6))
                    .fetchVariables(
                            "workflowId",
                            "targetNamespace",
                            "targetName",
                            "actionType",
                            "incidentType"
                    )
                    .open();

            autoEscalateWorker = camundaClient.newWorker()
                    .jobType("auto-escalate-incident")
                    .handler(this::handleAutoEscalateIncident)
                    .name("backend-auto-escalate")
                    .maxJobsActive(2)
                    .fetchVariables(
                            "workflowId",
                            "incidentId",
                            "incidentSeverity",
                            "approvalTimeoutIso"
                    )
                    .open();

            rejectionWorker = camundaClient.newWorker()
                    .jobType("handle-incident-rejection")
                    .handler(this::handleRejectionFeedback)
                    .name("backend-rejection-feedback")
                    .maxJobsActive(2)
                    .fetchVariables(
                            "workflowId",
                            "incidentId",
                            "incidentSeverity",
                            "incidentType",
                            "targetNamespace",
                            "targetName"
                    )
                    .open();

            classifyWorker = camundaClient.newWorker()
                    .jobType("ai-classify")
                    .handler(this::handleAiClassify)
                    .name("backend-ai-classify")
                    .maxJobsActive(8)
                    .timeout(Duration.ofSeconds(60))
                    .fetchVariables("namespace", "app", "kind", "detail")
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
        closeQuietly(classifyWorker);
    }

    @Scheduled(
            fixedDelayString = "${camunda.worker.reconnect-check-ms:30000}",
            initialDelayString = "${camunda.worker.reconnect-initial-delay-ms:90000}"
    )
    public void checkAndReconnectWorkers() {

        boolean anyDown =
                (executeWorker == null || !executeWorker.isOpen())
                        || (verifyWorker == null || !verifyWorker.isOpen())
                        || (autoEscalateWorker == null || !autoEscalateWorker.isOpen())
                        || (rejectionWorker == null || !rejectionWorker.isOpen())
                        || (classifyWorker == null || !classifyWorker.isOpen());

        if (!anyDown) {
            return;
        }

        logger.warn(
                "One or more Camunda job workers are closed — attempting reconnect..."
        );

        closeWorkers();

        executeWorker = null;
        verifyWorker = null;
        autoEscalateWorker = null;
        rejectionWorker = null;
        classifyWorker = null;

        try {
            startWorkers();
            logger.info("Camunda workers successfully reconnected");
        } catch (Exception ex) {
            logger.error("Camunda worker reconnect failed — will retry in 30s", ex);
        }
    }

    private void handleAiClassify(JobClient jobClient, ActivatedJob job) {

        Map<String, Object> variables = job.getVariablesAsMap();

        String namespace = variables.get("namespace") != null
                ? variables.get("namespace").toString()
                : null;

        String app = variables.get("app") != null
                ? variables.get("app").toString()
                : null;

        String kind = variables.get("kind") != null
                ? variables.get("kind").toString()
                : "app";

        String detail = variables.get("detail") != null
                ? variables.get("detail").toString()
                : "";

        boolean incidentCreated = false;

        try {
            if ("infra".equalsIgnoreCase(kind)) {

                logger.info(
                        "[ai-classify] Camunda routes infrastructure incident {}/{}",
                        namespace,
                        app
                );

                incidentCreated = infraProvider.getObject()
                        .openInfraIncidentFromCamunda(namespace, app, detail);

            } else {

                logger.info(
                        "[ai-classify] Deterministic mode: no AI classification for {}/{}",
                        namespace,
                        app
                );

                incidentCreated = false;
            }

        } catch (Exception ex) {
            logger.error(
                    "[ai-classify] Classification failure for {}/{}: {}",
                    namespace,
                    app,
                    ex.getMessage()
            );
        }

        try {
            completeJobWithRetry(
                    jobClient,
                    job,
                    Map.of("incidentCreated", incidentCreated),
                    "ai-classify"
            );

        } catch (InterruptedException ie) {
            Thread.currentThread().interrupt();
            logger.warn(
                    "[ai-classify] Job completion interrupted for {}/{}",
                    namespace,
                    app
            );
        }
    }

    private void handleExecuteRemediation(JobClient jobClient, ActivatedJob job) {

        Map<String, Object> variables = job.getVariablesAsMap();
        long startedAtNanos = System.nanoTime();

        try {
            Long workflowId = longValue(variables.get("workflowId"));
            Long actionId = longValue(variables.get("actionId"));

            WorkflowInstance workflow =
                    workflowService.getWorkflowInstance(workflowId);

            IncidentAction action = incidentActionRepository.findById(actionId)
                    .orElseThrow(() ->
                            new IllegalStateException("Action not found: " + actionId)
                    );

            markActionInProgress(action);

            if (workflow.getIncident() != null) {
                workflowService.addWorkflowLog(
                        workflowId,
                        "Remediation of incident #"
                                + workflow.getIncident().getId()
                                + " — "
                                + workflow.getIncident().getTitle()
                );
            }

            RemediationPlan plan = buildPlan(variables, action);

            if (plan.autoExecutable()) {
                remediationMetricsService.autoRemediationAttempted();
            }

            RemediationExecutionResult result =
                    remediationExecutor.execute(plan);

            recordExecOnCard(
                    workflow,
                    result.success()
                            ? ExecutionStage.COMPLETED
                            : ExecutionStage.FAILED,
                    action.getType() != null
                            ? action.getType().name()
                            : null,
                    result.message(),
                    result.success()
            );

            if (result.success()) {

                actionService.markActionCompleted(
                        actionId,
                        EXECUTOR_USERNAME,
                        result.message()
                );

                remediationMetricsService.executionDuration(
                        System.nanoTime() - startedAtNanos
                );

                incidentHistoryService.record(
                        workflow.getIncident(),
                        IncidentStatus.IN_PROGRESS,
                        "Automated remediation executed: " + result.message()
                );

                boolean isEscalationOnly =
                        action.getType() == ActionType.INVESTIGATION
                                || action.getType() == ActionType.ESCALATION;

                String target =
                        action.getTargetNamespace() + "/" + action.getTargetName();

                boolean nodeTarget =
                        "k8s-nodes".equalsIgnoreCase(action.getTargetNamespace());

                if (isEscalationOnly) {

                    if (nodeTarget) {

                        workflowService.addWorkflowLog(
                                workflowId,
                                "Node escalation recorded — deterministic recovery path "
                                        + "through HELIOS policy, Bridge and host watchdog."
                        );

                        workflowService.addWorkflowLog(
                                workflowId,
                                "HELIOS will verify the node Ready state before "
                                        + "allowing incident resolution."
                        );

                    } else {

                        workflowService.addWorkflowLog(
                                workflowId,
                                "No safe automatic Kubernetes action for this incident."
                        );

                        workflowService.addWorkflowLog(
                                workflowId,
                                "Human escalation recorded for " + target
                        );

                        workflowService.addWorkflowLog(
                                workflowId,
                                "HELIOS will verify whether the incident persists."
                        );
                    }

                    workflowService.updateOrchestrationState(
                            workflow,
                            WorkflowOrchestrationState.EXECUTION_PENDING,
                            IncidentStatus.IN_PROGRESS,
                            nodeTarget
                                    ? "Node escalation recorded; waiting for node Ready"
                                    : "Human escalation recorded; waiting for verification"
                    );

                } else {

                    workflowService.addWorkflowLog(
                            workflowId,
                            "Automatic action selected: "
                                    + describeActionFr(action.getType())
                                    + " on "
                                    + target
                    );

                    String kubectlCmd = buildKubectlEquivalent(
                            action.getType(),
                            action.getTargetNamespace(),
                            action.getTargetName()
                    );

                    if (kubectlCmd != null) {
                        workflowService.addWorkflowLog(
                                workflowId,
                                "$ " + kubectlCmd + " (kubectl equivalent)"
                        );
                    }

                    workflowService.addWorkflowLog(
                            workflowId,
                            "Action applied: " + result.message()
                    );

                    workflowService.updateOrchestrationState(
                            workflow,
                            WorkflowOrchestrationState.EXECUTION_PENDING,
                            IncidentStatus.IN_PROGRESS,
                            "Action applied; recovery verification is required"
                    );
                }

            } else {

                actionService.markActionFailed(
                        actionId,
                        result.message(),
                        EXECUTOR_USERNAME,
                        result.message()
                );

                remediationMetricsService.remediationFailed();

                remediationMetricsService.executionDuration(
                        System.nanoTime() - startedAtNanos
                );

                incidentHistoryService.record(
                        workflow.getIncident(),
                        IncidentStatus.RESOLUTION_FAILED,
                        "Automated remediation failed: " + result.message()
                );

                workflowService.addWorkflowLog(
                        workflowId,
                        "Remediation failed: " + result.message()
                );

                parkWorkflowAsFailed(
                        workflow,
                        "Automation attempt failed: " + result.message()
                );
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
            logger.error(
                    "Execute remediation worker failed for job {}",
                    job.getKey(),
                    ex
            );
            failJob(jobClient, job, ex, variables);
        }
    }

    void handleVerifyRemediation(JobClient jobClient, ActivatedJob job) {

        Map<String, Object> variables = job.getVariablesAsMap();
        long startedAtNanos = System.nanoTime();

        try {
            Long workflowId = longValue(variables.get("workflowId"));
            String namespace = stringValue(variables.get("targetNamespace"));
            String targetName = stringValue(variables.get("targetName"));

            WorkflowInstance workflow =
                    workflowService.getWorkflowInstance(workflowId);

            if (workflow.getStatus() == WorkflowStatus.FAILED
                    || workflow.getStatus() == WorkflowStatus.COMPLETED) {

                logger.warn(
                        "verify-remediation job {} is a duplicate — workflow #{} already {}",
                        job.getKey(),
                        workflowId,
                        workflow.getStatus()
                );

                completeJobWithRetry(
                        jobClient,
                        job,
                        Map.of(
                                "recoveryResolved", false,
                                "verificationMessage", "skipped-duplicate"
                        ),
                        "verify-remediation"
                );

                return;
            }

            workflowService.updateOrchestrationState(
                    workflow,
                    WorkflowOrchestrationState.VERIFYING,
                    IncidentStatus.IN_PROGRESS,
                    "Starting post-remediation verification"
            );

            String actionTypeVar =
                    stringValue(variables.get("actionType"));

            String incidentTypeVar =
                    stringValue(variables.get("incidentType"));

            RecoveryVerificationResult verificationResult;

            boolean vmRecoveryIncident =
                    "NODE_NOT_READY".equalsIgnoreCase(incidentTypeVar)
                            || "VM_DOWN".equalsIgnoreCase(incidentTypeVar)
                            || "VM_POWERED_OFF".equalsIgnoreCase(incidentTypeVar);

            // VMDOWN-POLICY-20261105 : VM d'infrastructure AUTO (.162) = incident VM_DOWN / ns infra. Ses preuves
            // (SSH, HAProxy, DNS, etcd, quorum) remplacent « nœud Ready ». Jamais de passage par la branche workload.
            boolean infraVmRecovery = "infra".equalsIgnoreCase(namespace)
                    && "VM_DOWN".equalsIgnoreCase(incidentTypeVar)
                    && VmPowerPolicy.isCamundaRestartable(targetName);

            if (infraVmRecovery) {
                verificationResult = verifyInfraVmRecovery(workflowId, workflow, targetName);
            } else
            /*
             * VM_DOWN / NODE_NOT_READY:
             * Two real Kubernetes observations are mandatory.
             * Each observation is persisted before another attempt.
             */
            if ("k8s-nodes".equalsIgnoreCase(namespace)
                    && vmRecoveryIncident) {

                Long incidentId = workflow.getIncident() != null
                        ? workflow.getIncident().getId()
                        : null;

                if (incidentId == null) {
                    throw new IllegalStateException(
                            "VM_DOWN verification refused: incident ID is missing"
                    );
                }

                workflowService.addWorkflowLog(
                        workflowId,
                        "VM_DOWN verification started: two consecutive "
                                + "Kubernetes Ready observations separated "
                                + "by at least 15 seconds are required."
                );

                int attempts = nodeReadyVerificationMaxAttempts();
                int consecutiveReady = 0;
                boolean nodeBack = false;

                for (int i = 1; i <= attempts; i++) {

                    LocalDateTime capturedAt = LocalDateTime.now();

                    NodeReadinessObservation observation =
                            remediationExecutor.inspectNodeReadiness(targetName);

                    if (observation == null) {
                        throw new IllegalStateException(
                                "Kubernetes readiness observation is null"
                        );
                    }

                    // Nœuds Kubernetes cibles du rallumage automatique (workers et, depuis le 2026-11-17, plan de contrôle .153) :
                    // « Ready » ne suffit pas (kubelet, SSH, etcd, CNPG, backend, + apiserver pour .153). La lecture effective porte
                    // le verdict complet : la preuve persistée ci-dessous le reflète.
                    if (vmRecoveryVerifier != null && VmPowerPolicy.isCamundaRestartable(targetName)) {
                        VmPowerPolicy.Entry workerEntry = VmPowerPolicy.find(targetName).orElse(null);
                        if (workerEntry != null && (workerEntry.vmClass() == VmPowerPolicy.VmClass.K8S_WORKER
                                || workerEntry.vmClass() == VmPowerPolicy.VmClass.K8S_CONTROL_PLANE)) {
                            observation = vmRecoveryVerifier.effectiveWorkerObservation(workerEntry, observation);
                        }
                    }

                    // Persistence is mandatory: a database error prevents
                    // the verification from becoming successful.
                    var savedEvidence =
                            incidentCardService.recordNodeReadinessEvidence(
                                    incidentId,
                                    workflowId,
                                    targetName,
                                    i,
                                    capturedAt,
                                    observation
                            );

                    if (savedEvidence == null || savedEvidence.id() == null) {
                        throw new IllegalStateException(
                                "VM_DOWN: Kubernetes observation was not persisted"
                        );
                    }

                    boolean sampleReady =
                            observation.apiAvailable()
                                    && observation.ready()
                                    && "True".equalsIgnoreCase(
                                    observation.conditionStatus()
                            );

                    if (sampleReady) {
                        consecutiveReady++;
                    } else {
                        consecutiveReady = 0;
                    }

                    workflowService.addWorkflowLog(
                            workflowId,
                            "VM_DOWN observation "
                                    + i + "/" + attempts
                                    + " — node=" + targetName
                                    + ", API available=" + observation.apiAvailable()
                                    + ", Ready=" + observation.ready()
                                    + ", condition=" + observation.conditionStatus()
                                    + ", consecutive Ready=" + consecutiveReady + "/2"
                                    + ". " + observation.message()
                    );

                    if (consecutiveReady >= 2) {

                        nodeBack =
                                incidentCardService.hasTwoConsecutiveNodeReadyEvidence(
                                        incidentId,
                                        workflowId,
                                        targetName
                                );

                        if (nodeBack) {
                            workflowService.addWorkflowLog(
                                    workflowId,
                                    "VM_DOWN: two consecutive Kubernetes Ready "
                                            + "evidence records have been persisted "
                                            + "and validated."
                            );
                            break;
                        }

                        workflowService.addWorkflowLog(
                                workflowId,
                                "VM_DOWN: persisted evidence pair is not yet valid. "
                                        + "Verification will continue."
                        );

                        // The latest Ready sample can become the first
                        // observation of a new pair.
                        consecutiveReady = 1;
                    }

                    if (i < attempts) {
                        waitBetweenNodeReadyObservations();
                    }
                }

                verificationResult = new RecoveryVerificationResult(
                        nodeBack,
                        !nodeBack,
                        nodeBack
                                ? "Node " + targetName
                                + " recovered with two persisted "
                                + "consecutive Ready observations"
                                : "Node " + targetName
                                + " recovery not confirmed: two consecutive "
                                + "persisted Ready observations missing"
                );

            } else if ("k8s-nodes".equalsIgnoreCase(namespace)
                    && ("NODE_HIGH_CPU".equalsIgnoreCase(incidentTypeVar)
                    || "NODE_CPU_SATURATION".equalsIgnoreCase(incidentTypeVar))) {

                workflowService.addWorkflowLog(
                        workflowId,
                        "CPU verification: checking measured utilization on node "
                                + targetName
                                + " using the detector's three-minute Prometheus window."
                );

                int configuredAttempts =
                        remediationProperties.getVerificationRetryAttempts();

                int attempts = Math.max(2, Math.min(8, configuredAttempts));

                int delaySeconds = Math.max(
                        15,
                        remediationProperties.getVerificationRetryDelaySeconds()
                );

                int consecutiveHealthy = 0;

                RecoveryVerificationResult cpuVerification =
                        new RecoveryVerificationResult(
                                false,
                                true,
                                "CPU verification not completed"
                        );

                for (int i = 1; i <= attempts; i++) {

                    RecoveryVerificationResult sample =
                            hostCpuVerificationService.verify(targetName);

                    if (sample.resolved()) {
                        consecutiveHealthy++;
                    } else {
                        consecutiveHealthy = 0;
                    }

                    workflowService.addWorkflowLog(
                            workflowId,
                            "CPU verification "
                                    + i + "/" + attempts
                                    + " on " + targetName
                                    + " - measured recovery=" + sample.resolved()
                                    + ", consecutive healthy samples="
                                    + consecutiveHealthy
                                    + ". " + sample.message()
                    );

                    if (consecutiveHealthy >= 2) {
                        cpuVerification = new RecoveryVerificationResult(
                                true,
                                false,
                                "CPU recovery confirmed by two consecutive "
                                        + "Prometheus samples. " + sample.message()
                        );
                        break;
                    }

                    if (i == attempts) {
                        cpuVerification = new RecoveryVerificationResult(
                                false,
                                true,
                                "CPU recovery not confirmed after "
                                        + attempts
                                        + " checks. Last result: "
                                        + sample.message()
                        );
                        break;
                    }

                    Thread.sleep(delaySeconds * 1000L);
                }

                verificationResult = cpuVerification;

            } else if ("k8s-nodes".equalsIgnoreCase(namespace)
                    && ("NODE_DISK_FULL".equalsIgnoreCase(incidentTypeVar)
                    || "NODE_DISK_PRESSURE".equalsIgnoreCase(incidentTypeVar)
                    || "NODE_INODES_FULL".equalsIgnoreCase(incidentTypeVar))) {

                verificationResult = verifyDiskRecovery(
                        workflowId,
                        targetName,
                        incidentTypeVar
                );

            } else if ("k8s-nodes".equalsIgnoreCase(namespace)
                    && ("NODE_HIGH_MEMORY".equalsIgnoreCase(incidentTypeVar)
                    || "NODE_MEMORY_PRESSURE".equalsIgnoreCase(incidentTypeVar))) {

                verificationResult = verifyMemoryRecovery(
                        workflowId,
                        targetName,
                        incidentTypeVar
                );

            } else if ("k8s-nodes".equalsIgnoreCase(namespace)) {

                verificationResult = new RecoveryVerificationResult(
                        false,
                        true,
                        "Host incident " + incidentTypeVar
                                + " requires scenario-specific verification; "
                                + "node Ready alone is insufficient"
                );

            } else {

                workflowService.addWorkflowLog(
                        workflowId,
                        "Workload recovery verification started."
                );

                boolean scaleMode =
                        "SCALE_DEPLOYMENT".equalsIgnoreCase(actionTypeVar)
                                || "AUTOSCALING".equalsIgnoreCase(incidentTypeVar);

                verificationResult = recoveryVerificationService.verify(
                        namespace,
                        targetName,
                        msg -> workflowService.addWorkflowLog(workflowId, msg),
                        scaleMode
                );
            }

            boolean resolved = verificationResult.resolved();

            remediationMetricsService.verification(resolved);

            /*
             * Strict VM recovery verification:
             * PASS is only recorded after the two persisted observations
             * have been checked again by IncidentCardService.
             */
            if (infraVmRecovery) {

                Long incidentId = workflow.getIncident() != null ? workflow.getIncident().getId() : null;
                if (incidentId == null) {
                    throw new IllegalStateException("VM_DOWN infra: incident ID missing before final verification");
                }
                if (resolved) {
                    incidentCardService.recordVmRecoveryVerification(
                            incidentId, targetName, verificationResult.message());
                } else {
                    incidentCardService.recordVerification(
                            incidentId,
                            new IncidentVerificationRequest(
                                    "Two consecutive infrastructure recovery passes required",
                                    "FAIL", null, verificationResult.message(), LocalDateTime.now(), false));
                }
                incidentCardService.recordExecutionEvent(
                        incidentId,
                        new ExecutionEventRequest(ExecutionStage.VERIFIED, null, verificationResult.message(), resolved));

            } else if ("k8s-nodes".equalsIgnoreCase(namespace)
                    && vmRecoveryIncident) {

                Long incidentId = workflow.getIncident() != null
                        ? workflow.getIncident().getId()
                        : null;

                if (incidentId == null) {
                    throw new IllegalStateException(
                            "VM_DOWN: incident ID missing before final verification"
                    );
                }

                if (resolved) {

                    incidentCardService.recordNodeRecoveryVerification(
                            incidentId,
                            workflowId,
                            targetName,
                            verificationResult.message()
                    );

                } else {

                    incidentCardService.recordVerification(
                            incidentId,
                            new IncidentVerificationRequest(
                                    "Two consecutive Kubernetes Ready observations required",
                                    "FAIL",
                                    null,
                                    verificationResult.message(),
                                    LocalDateTime.now(),
                                    false
                            )
                    );
                }

                incidentCardService.recordExecutionEvent(
                        incidentId,
                        new ExecutionEventRequest(
                                ExecutionStage.VERIFIED,
                                null,
                                verificationResult.message(),
                                resolved
                        )
                );

            } else {

                recordVerifOnCard(
                        workflow,
                        namespace,
                        targetName,
                        resolved,
                        verificationResult.message()
                );
            }

            /*
             * No path can reach this block for a successful node recovery
             * unless the dedicated verification and persistence succeeded.
             */
            if (resolved && ((infraVmRecovery) || ("k8s-nodes".equalsIgnoreCase(namespace) && vmRecoveryIncident))) {
                // VMDOWN-POLICY-20261105 (R5) : aucun RESOLVED sans PASS persisté pour la famille VM.
                requirePersistedRecoveryPass(workflow.getIncident() == null ? null : workflow.getIncident().getId());
            }

            if (resolved) {

                remediationMetricsService.remediationSucceeded();

                remediationMetricsService.verificationDuration(
                        System.nanoTime() - startedAtNanos
                );

                incidentHistoryService.record(
                        workflow.getIncident(),
                        IncidentStatus.RESOLVED,
                        "Post-remediation verification succeeded: "
                                + verificationResult.message()
                );

                WorkflowInstance completed =
                        workflowService.completeWorkflow(workflowId);

                try {
                    List<WorkflowLogDTO> resolvedLogs =
                            Collections.emptyList();

                    try {
                        resolvedLogs =
                                workflowService.getWorkflowLogsForIncident(
                                        completed.getIncident().getId()
                                );
                    } catch (Exception logEx) {
                        logger.warn(
                                "Could not load workflow logs for resolved email: {}",
                                logEx.getMessage()
                        );
                    }

                    notificationService.notifyIncidentResolvedWithTrace(
                            completed.getIncident(),
                            resolvedLogs
                    );

                } catch (Exception ex) {
                    logger.warn(
                            "Resolved notification failed for incident #{}: {}",
                            completed.getIncident().getId(),
                            ex.getMessage()
                    );
                }

                maybeStopCpuLoadAfterRecovery(
                        workflowId,
                        namespace,
                        targetName,
                        incidentTypeVar
                );

            } else {

                remediationMetricsService.remediationFailed();

                remediationMetricsService.verificationDuration(
                        System.nanoTime() - startedAtNanos
                );

                boolean diskVerificationIncident =
                        "NODE_DISK_FULL".equalsIgnoreCase(incidentTypeVar)
                                || "NODE_DISK_PRESSURE".equalsIgnoreCase(incidentTypeVar)
                                || "NODE_INODES_FULL".equalsIgnoreCase(incidentTypeVar);

                String humanReason;

                if (diskVerificationIncident) {

                    humanReason =
                            "La récupération DISK n'a pas été confirmée pour le nœud "
                                    + targetName + ". "
                                    + verificationResult.message()
                                    + " Vérifier les métriques et la cause disque avant de conclure.";

                } else if (infraVmRecovery) {

                    humanReason =
                            "La récupération de la VM d'infrastructure "
                                    + targetName + " n'a pas été confirmée. "
                                    + verificationResult.message()
                                    + " Deux passes de vérification consécutives persistées "
                                    + "sont obligatoires avant résolution.";

                } else if (vmRecoveryIncident) {

                    humanReason =
                            "La récupération VM_DOWN n'a pas été confirmée pour le nœud "
                                    + targetName + ". "
                                    + verificationResult.message()
                                    + " Deux observations Ready consécutives persistées "
                                    + "sont obligatoires avant résolution.";

                } else {

                    humanReason =
                            "La panne persiste après remédiation : "
                                    + (verificationResult.workloadHealthy()
                                    ? "les pods sont prêts mais "
                                    : "le déploiement n'est toujours pas disponible et ")
                                    + "la récupération n'est pas confirmée. "
                                    + "Escalade à l'opérateur humain.";
                }


                workflowService.addWorkflowLog(
                        workflowId,
                        "Verification failed: " + humanReason
                );

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

            logger.error(
                    "Verify remediation worker failed for job {}",
                    job.getKey(),
                    ex
            );

            failJob(jobClient, job, ex, variables);
        }
    }

    /** Garde de preuves : un RESOLVED de la famille VM exige un verdict PASS déjà PERSISTÉ pour l'incident. */
    private void requirePersistedRecoveryPass(Long incidentId) {
        if (incidentId == null || !incidentCardService.hasPassVerification(incidentId)) {
            throw new IllegalStateException(
                    "RESOLVED refusé : aucun verdict PASS de récupération persisté pour l'incident " + incidentId);
        }
    }

    /**
     * VMDOWN-POLICY-20261105 — vérification de récupération de .162 : au plus {@code nodeReadyVerificationMaxAttempts}
     * passes espacées de 15 s ; DEUX passes consécutives réussies et PERSISTÉES sont obligatoires. Chaque passe est
     * enregistrée (même en échec, pour casser la série) avant la suivante.
     */
    private RecoveryVerificationResult verifyInfraVmRecovery(
            Long workflowId, WorkflowInstance workflow, String vm) throws InterruptedException {

        Long incidentId = workflow.getIncident() != null ? workflow.getIncident().getId() : null;
        if (incidentId == null) {
            throw new IllegalStateException("VM_DOWN infra verification refused: incident ID is missing");
        }
        VmPowerPolicy.Entry entry = VmPowerPolicy.find(vm).orElse(null);
        if (vmRecoveryVerifier == null || watchdogClient == null || entry == null) {
            return new RecoveryVerificationResult(false, true,
                    "Vérification de récupération de " + vm + " indisponible (vérificateur ou watchdog absent) : "
                            + "aucun RESOLVED sans preuve");
        }
        workflowService.addWorkflowLog(workflowId,
                "VM_DOWN " + vm + " : deux passes consécutives (SSH, HAProxy, DNS, etcd, quorum), espacées d'au moins "
                        + "15 secondes, sont requises.");
        int attempts = nodeReadyVerificationMaxAttempts();
        int consecutive = 0;
        boolean back = false;
        String last = "aucune passe";
        for (int i = 1; i <= attempts; i++) {
            LocalDateTime capturedAt = LocalDateTime.now();
            WatchdogClient.Result<java.util.Map<String, WatchdogClient.VmState>> states = watchdogClient.states();
            VmRecoveryVerifier.PassResult pass = states.ok()
                    ? vmRecoveryVerifier.verifyInfra(entry, states.value())
                    : new VmRecoveryVerifier.PassResult(false,
                    new java.util.LinkedHashMap<>(java.util.Map.of("watchdog_state", false)),
                    "état d'alimentation indisponible (" + states.error() + ")");
            last = pass.summary();
            var saved = incidentCardService.recordVmRecoveryEvidence(
                    incidentId, vm, i, capturedAt, pass.passed(), pass.criteriaJson());
            if (saved == null || saved.id() == null) {
                throw new IllegalStateException("VM_DOWN infra: la passe de vérification n'a pas été persistée");
            }
            consecutive = pass.passed() ? consecutive + 1 : 0;
            workflowService.addWorkflowLog(workflowId, "Passe " + i + "/" + attempts + " — " + pass.summary()
                    + " — passes consécutives réussies=" + consecutive + "/2");
            if (consecutive >= 2 && incidentCardService.hasTwoConsecutiveVmRecoveryEvidence(incidentId, vm)) {
                back = true;
                break;
            }
            if (i < attempts) {
                waitBetweenNodeReadyObservations();
            }
        }
        return new RecoveryVerificationResult(back, !back, back
                ? vm + " récupérée : deux passes consécutives persistées (" + last + ")"
                : vm + " : récupération non confirmée (dernière passe : " + last + ")");
    }

    private void maybeStopCpuLoadAfterRecovery(
            Long workflowId,
            String namespace,
            String targetName,
            String incidentType) {

        boolean cpuNodeIncident =
                "k8s-nodes".equalsIgnoreCase(namespace)
                        && ("NODE_HIGH_CPU".equalsIgnoreCase(incidentType)
                        || "NODE_CPU_SATURATION".equalsIgnoreCase(incidentType));

        if (!cpuNodeIncident) {
            return;
        }

        try {
            RemediationExecutionResult stop =
                    remediationExecutor.stopCpuLoadServiceAfterRecovery(
                            namespace,
                            targetName,
                            incidentType
                    );

            if (stop != null && stop.success()) {

                workflowService.addWorkflowLog(
                        workflowId,
                        "Charge CPU de laboratoire arrêtée automatiquement sur "
                                + targetName + ". " + stop.message()
                );

                logger.info(
                        "Auto-stop CPU load succeeded on {} after RESOLVED: {}",
                        targetName,
                        stop.message()
                );

            } else {

                String detail = stop == null
                        ? "réponse absente"
                        : stop.message();

                workflowService.addWorkflowLog(
                        workflowId,
                        "Arrêt automatique de la charge CPU non confirmé sur "
                                + targetName + ". " + detail
                );

                logger.warn(
                        "Auto-stop CPU load NOT confirmed on {} after RESOLVED: {}",
                        targetName,
                        detail
                );
            }

        } catch (RuntimeException ex) {

            logger.warn(
                    "Auto-stop CPU load threw after RESOLVED on {}: {}",
                    targetName,
                    ex.getClass().getSimpleName()
            );
        }
    }

    RecoveryVerificationResult verifyDiskRecovery(
            Long workflowId,
            String targetName,
            String incidentType) throws InterruptedException {

        workflowService.addWorkflowLog(
                workflowId,
                "DISK verification: checking measured filesystem/inode/DiskPressure recovery on "
                        + targetName + "."
        );

        int configuredAttempts =
                remediationProperties.getVerificationRetryAttempts();

        int attempts = Math.max(2, Math.min(8, configuredAttempts));

        int configuredDelay =
                remediationProperties.getVerificationRetryDelaySeconds();

        int delaySeconds = Math.max(1, Math.min(30, configuredDelay));

        int consecutiveHealthy = 0;

        RecoveryVerificationResult diskVerification =
                new RecoveryVerificationResult(
                        false,
                        true,
                        "DISK verification not completed"
                );

        for (int i = 1; i <= attempts; i++) {

            RecoveryVerificationResult sample =
                    hostDiskVerificationService.verify(
                            targetName,
                            incidentType
                    );

            if (sample == null) {
                sample = new RecoveryVerificationResult(
                        false,
                        true,
                        "DISK verifier returned no result"
                );
            }

            if (sample.resolved()) {
                consecutiveHealthy++;
            } else {
                consecutiveHealthy = 0;
            }

            workflowService.addWorkflowLog(
                    workflowId,
                    "DISK verification " + i + "/" + attempts
                            + " on " + targetName
                            + " - measured recovery=" + sample.resolved()
                            + ", consecutive healthy samples=" + consecutiveHealthy
                            + ". " + sample.message()
            );

            if (consecutiveHealthy >= 2) {

                diskVerification = new RecoveryVerificationResult(
                        true,
                        false,
                        "DISK recovery confirmed by two consecutive "
                                + "Prometheus samples. " + sample.message()
                );

                break;
            }

            if (i == attempts) {

                diskVerification = new RecoveryVerificationResult(
                        false,
                        true,
                        "DISK recovery not confirmed after "
                                + attempts
                                + " checks. Last result: "
                                + sample.message()
                );

                break;
            }

            Thread.sleep(delaySeconds * 1000L);
        }

        return diskVerification;
    }

    RecoveryVerificationResult verifyMemoryRecovery(
            Long workflowId,
            String targetName,
            String incidentType) throws InterruptedException {

        workflowService.addWorkflowLog(
                workflowId,
                "RAM verification: checking measured node memory / MemoryPressure recovery on "
                        + targetName + "."
        );

        int configuredAttempts =
                remediationProperties.getVerificationRetryAttempts();

        int attempts = Math.max(2, Math.min(8, configuredAttempts));

        int configuredDelay =
                remediationProperties.getVerificationRetryDelaySeconds();

        int delaySeconds = Math.max(1, Math.min(30, configuredDelay));

        int consecutiveHealthy = 0;

        RecoveryVerificationResult memoryVerification =
                new RecoveryVerificationResult(
                        false,
                        true,
                        "RAM verification not completed"
                );

        for (int i = 1; i <= attempts; i++) {

            RecoveryVerificationResult sample =
                    hostMemoryVerificationService.verify(
                            targetName,
                            incidentType
                    );

            if (sample == null) {
                sample = new RecoveryVerificationResult(
                        false,
                        true,
                        "RAM verifier returned no result"
                );
            }

            if (sample.resolved()) {
                consecutiveHealthy++;
            } else {
                consecutiveHealthy = 0;
            }

            workflowService.addWorkflowLog(
                    workflowId,
                    "RAM verification " + i + "/" + attempts
                            + " on " + targetName
                            + " - measured recovery=" + sample.resolved()
                            + ", consecutive healthy samples=" + consecutiveHealthy
                            + ". " + sample.message()
            );

            if (consecutiveHealthy >= 2) {

                memoryVerification = new RecoveryVerificationResult(
                        true,
                        false,
                        "RAM recovery confirmed by two consecutive "
                                + "Prometheus samples. " + sample.message()
                );

                break;
            }

            if (i == attempts) {

                memoryVerification = new RecoveryVerificationResult(
                        false,
                        true,
                        "RAM recovery not confirmed after "
                                + attempts
                                + " checks. Last result: "
                                + sample.message()
                );

                break;
            }

            Thread.sleep(delaySeconds * 1000L);
        }

        return memoryVerification;
    }

    private void handleAutoEscalateIncident(
            JobClient jobClient,
            ActivatedJob job) {

        Map<String, Object> variables = job.getVariablesAsMap();

        try {
            Long workflowId = longValue(variables.get("workflowId"));

            String timeoutIso = stringValueOrDefault(
                    variables.get("approvalTimeoutIso"),
                    "PT1H"
            );

            String severity = stringValueOrDefault(
                    variables.get("incidentSeverity"),
                    "MEDIUM"
            );

            WorkflowInstance workflow =
                    workflowService.getWorkflowInstance(workflowId);

            parkWorkflowAsFailed(
                    workflow,
                    "HELIOS n'a pas pu résoudre automatiquement "
                            + "(severity=" + severity
                            + ", timeout=" + timeoutIso + ")"
            );

            logger.warn(
                    "Incident #{} marked RESOLUTION_FAILED via auto-escalate "
                            + "(timeout={}, severity={})",
                    workflow.getIncident().getId(),
                    timeoutIso,
                    severity
            );

            completeJobWithRetry(
                    jobClient,
                    job,
                    Map.of(
                            "escalatedAutomatically", true,
                            "escalationReason", "approval_timeout"
                    ),
                    "auto-escalate-incident"
            );

        } catch (InterruptedException ex) {

            Thread.currentThread().interrupt();
            failJob(jobClient, job, ex, variables);

        } catch (Exception ex) {

            logger.error(
                    "Auto-escalate worker failed for job {}",
                    job.getKey(),
                    ex
            );

            failJob(jobClient, job, ex, variables);
        }
    }

    private void handleRejectionFeedback(
            JobClient jobClient,
            ActivatedJob job) {

        Map<String, Object> variables = job.getVariablesAsMap();

        try {
            Long workflowId = longValue(variables.get("workflowId"));

            WorkflowInstance workflow =
                    workflowService.getWorkflowInstance(workflowId);

            incidentHistoryService.record(
                    workflow.getIncident(),
                    IncidentStatus.WAITING_FOR_APPROVAL,
                    "Remediation rejected by operator — feedback recorded"
            );

            workflowService.updateOrchestrationState(
                    workflow,
                    WorkflowOrchestrationState.REJECTED,
                    IncidentStatus.WAITING_FOR_APPROVAL,
                    "Remediation rejected by operator"
            );

            // Retrait de l'IA (11 nov. 2026) : le rejet est consigné dans l'historique de l'incident et l'état du
            // workflow (ci-dessus) ; il n'est plus transmis à un service d'apprentissage.
            logger.info(
                    "Rejection recorded for incident #{}",
                    workflow.getIncident().getId()
            );

            completeJobWithRetry(
                    jobClient,
                    job,
                    Map.of("rejectionFeedbackSent", true),
                    "handle-incident-rejection"
            );

        } catch (Exception ex) {

            logger.error(
                    "Rejection feedback worker failed for job {}",
                    job.getKey(),
                    ex
            );

            failJob(jobClient, job, ex, variables);
        }
    }

    private void parkWorkflowAsFailed(
            WorkflowInstance staleWorkflow,
            String reason) {

        WorkflowInstance workflow =
                workflowService.getWorkflowInstance(staleWorkflow.getId());

        if (workflow.getStatus() == WorkflowStatus.FAILED
                || workflow.getStatus() == WorkflowStatus.COMPLETED) {

            logger.info(
                    "parkWorkflowAsFailed: workflow #{} already {} — skipping",
                    workflow.getId(),
                    workflow.getStatus()
            );

            return;
        }

        workflowService.finalizeWorkflow(
                workflow,
                WorkflowOrchestrationState.ESCALATED,
                WorkflowStatus.FAILED,
                IncidentStatus.RESOLUTION_FAILED,
                "HELIOS a épuisé toutes les tentatives de remédiation. Raison: "
                        + reason
        );

        incidentHistoryService.record(
                workflow.getIncident(),
                IncidentStatus.RESOLUTION_FAILED,
                "HELIOS n'a pas pu résoudre l'incident automatiquement. Raison: "
                        + reason
        );

        List<HumanTask> pendingTasks =
                humanTaskRepository.findByWorkflowInstanceIdAndDecision(
                        workflow.getId(),
                        TaskDecision.PENDING
                );

        if (!pendingTasks.isEmpty()) {

            LocalDateTime now = LocalDateTime.now();

            pendingTasks.forEach(task -> {
                task.setDecision(TaskDecision.CANCELLED);
                task.setCompletedAt(now);
            });

            humanTaskRepository.saveAll(pendingTasks);

            logger.info(
                    "Auto-closed {} pending human task(s) for workflow #{}",
                    pendingTasks.size(),
                    workflow.getId()
            );
        }

        if (!humanTaskRepository.existsByWorkflowInstanceIdAndDecision(
                workflow.getId(),
                TaskDecision.PENDING
        )) {

            humanTaskService.createTask(
                    "Remédiation échouée — intervention requise pour l'incident #"
                            + workflow.getIncident().getId(),
                    workflow
            );
        }

        List<WorkflowLogDTO> workflowLogs = Collections.emptyList();

        try {
            workflowLogs =
                    workflowService.getWorkflowLogsForIncident(
                            workflow.getIncident().getId()
                    );
        } catch (Exception ex) {
            logger.warn(
                    "Could not load workflow logs for failure email: {}",
                    ex.getMessage()
            );
        }

        try {
            notificationService.notifyRemediationFailedWithTrace(
                    workflow.getIncident(),
                    reason,
                    workflowLogs
            );
        } catch (Exception ex) {
            logger.warn(
                    "Failed to send escalation notification for incident #{}: {}",
                    workflow.getIncident().getId(),
                    ex.getMessage()
            );
        }

        logger.warn(
                "Incident #{} marked RESOLUTION_FAILED — reason: {}",
                workflow.getIncident().getId(),
                reason
        );
    }

    private void parkWorkflowForApproval(
            WorkflowInstance workflow,
            String reason) {

        parkWorkflowAsFailed(workflow, reason);
    }

    private void markActionInProgress(IncidentAction action) {

        action.incrementAttemptCount();
        action.setStatus(ActionStatus.IN_PROGRESS);
        action.setExecutedBy(EXECUTOR_USERNAME);
        action.setExecutedAt(LocalDateTime.now());

        incidentActionRepository.save(action);
    }

    private RemediationPlan buildPlan(
            Map<String, Object> variables,
            IncidentAction action) {

        ActionType actionType = enumValue(
                ActionType.class,
                variables.get("actionType"),
                action.getType(),
                ActionType.REMEDIATION
        );

        AutomationMode automationMode = enumValue(
                AutomationMode.class,
                variables.get("automationMode"),
                action.getAutomationMode(),
                AutomationMode.MANUAL
        );

        RemediationTargetKind targetKind = enumValue(
                RemediationTargetKind.class,
                variables.get("targetKind"),
                action.getTargetKind(),
                RemediationTargetKind.UNKNOWN
        );

        return new RemediationPlan(
                actionType,
                automationMode,
                booleanValue(variables.get("approvalRequired")),
                booleanValue(variables.get("autoExecutable")),
                booleanValue(variables.get("manualFollowUp")),
                booleanValue(variables.get("cooldownBlocked")),
                booleanValue(variables.get("dbRelated")),
                stringValueOrDefault(
                        variables.get("targetNamespace"),
                        action.getTargetNamespace()
                ),
                targetKind,
                stringValueOrDefault(
                        variables.get("targetName"),
                        action.getTargetName()
                ),
                stringValue(variables.get("rationale")),
                stringValue(variables.get("incidentType")),
                // VMDOWN-POLICY-20261105 : incident justificatif = clé d'idempotence du power-on (X-Helios-Incident).
                action.getIncident() != null ? action.getIncident().getId() : null
        );
    }

    private void failJob(
            JobClient jobClient,
            ActivatedJob job,
            Exception ex,
            Map<String, Object> variables) {

        if (ex instanceof com.orange.pfe.backend.exception.WorkflowNotFoundException) {

            int remaining = Math.max(0, job.getRetries() - 1);

            try {
                if (remaining > 0) {

                    logger.warn(
                            "Workflow not visible yet for job {} ('{}') — retrying "
                                    + "({} left): {}",
                            job.getKey(),
                            job.getType(),
                            remaining,
                            ex.getMessage()
                    );

                    jobClient.newFailCommand(job)
                            .retries(remaining)
                            .retryBackoff(Duration.ofSeconds(6))
                            .errorMessage(
                                    "workflow not yet visible (tx committing): "
                                            + ex.getMessage()
                            )
                            .send()
                            .join();

                } else {

                    logger.warn(
                            "Dropping orphan job {} ('{}') — workflow still missing: {}",
                            job.getKey(),
                            job.getType(),
                            ex.getMessage()
                    );

                    jobClient.newFailCommand(job)
                            .retries(0)
                            .errorMessage("orphan: workflow purged")
                            .send()
                            .join();
                }

            } catch (Exception e) {
                logger.debug(
                        "not-found fail-command error: {}",
                        e.getMessage()
                );
            }

            return;
        }

        int retries = Math.max(0, job.getRetries() - 1);

        if (retries == 0) {

            Long workflowId = null;

            try {
                workflowId = longValue(variables.get("workflowId"));
            } catch (Exception ignored) {
                // No workflow identifier available.
            }

            if (workflowId != null) {
                try {
                    WorkflowInstance wf =
                            workflowService.getWorkflowInstance(workflowId);

                    String reason =
                            "Job '" + job.getType()
                                    + "' failed permanently after all retries. Error: "
                                    + ex.getMessage();

                    incidentHistoryService.record(
                            wf.getIncident(),
                            IncidentStatus.RESOLUTION_FAILED,
                            "HELIOS a épuisé toutes ses tentatives. "
                                    + "Super admin notifié. " + reason
                    );

                    workflowService.updateOrchestrationState(
                            wf,
                            WorkflowOrchestrationState.ESCALATED,
                            IncidentStatus.RESOLUTION_FAILED,
                            reason
                    );

                    notificationService.notifyRemediationFailed(
                            wf.getIncident(),
                            reason
                    );

                    logger.error(
                            "Job '{}' for workflow #{} failed permanently",
                            job.getType(),
                            workflowId
                    );

                } catch (Exception notifyEx) {
                    logger.warn(
                            "Could not escalate workflow after permanent job failure",
                            notifyEx
                    );
                }
            }
        }

        Map<String, Object> failureVariables = new HashMap<>();
        failureVariables.put("workerFailureMessage", ex.getMessage());
        failureVariables.putAll(variables);

        Exception lastFailure = null;

        for (int attempt = 1;
             attempt <= CAMUNDA_COMMAND_RETRY_ATTEMPTS;
             attempt++) {

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
                        "Failed to report Camunda job failure for job {} "
                                + "on attempt {}/{}",
                        job.getKey(),
                        attempt,
                        CAMUNDA_COMMAND_RETRY_ATTEMPTS,
                        sendEx
                );

                try {
                    sleepBeforeRetry(attempt);
                } catch (InterruptedException interruptedException) {
                    Thread.currentThread().interrupt();

                    logger.warn(
                            "Interrupted while retrying Camunda fail command "
                                    + "for job {}",
                            job.getKey()
                    );

                    return;
                }
            }
        }

        if (lastFailure != null) {
            logger.error(
                    "Unable to report Camunda job failure for job {}",
                    job.getKey(),
                    lastFailure
            );
        }
    }

    private void completeJobWithRetry(
            JobClient jobClient,
            ActivatedJob job,
            Map<String, Object> variables,
            String phase) throws InterruptedException {

        Exception lastFailure = null;

        for (int attempt = 1;
             attempt <= CAMUNDA_COMMAND_RETRY_ATTEMPTS;
             attempt++) {

            try {
                jobClient.newCompleteCommand(job)
                        .variables(variables)
                        .send()
                        .join();

                return;

            } catch (Exception sendEx) {

                lastFailure = sendEx;

                logger.warn(
                        "Failed to complete Camunda job {} during {} "
                                + "on attempt {}/{}",
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

    private void sleepBeforeRetry(int attempt)
            throws InterruptedException {

        if (attempt < CAMUNDA_COMMAND_RETRY_ATTEMPTS) {
            Thread.sleep(CAMUNDA_COMMAND_RETRY_DELAY_MS);
        }
    }

    private void closeQuietly(JobWorker worker) {

        if (worker != null) {
            try {
                worker.close();
            } catch (Exception ex) {
                logger.warn(
                        "Unable to close Camunda worker cleanly",
                        ex
                );
            }
        }
    }

    private Long longValue(Object value) {

        if (value == null) {
            throw new IllegalArgumentException(
                    "Missing numeric job variable"
            );
        }

        if (value instanceof Number number) {
            return number.longValue();
        }

        return Long.parseLong(String.valueOf(value));
    }

    private boolean booleanValue(Object value) {
        return value != null
                && Boolean.parseBoolean(String.valueOf(value));
    }

    private String stringValue(Object value) {
        return value == null ? null : String.valueOf(value);
    }

    private String stringValueOrDefault(
            Object value,
            String fallback) {

        String resolved = stringValue(value);

        return resolved == null || resolved.isBlank()
                ? fallback
                : resolved;
    }

    private void recordExecOnCard(
            WorkflowInstance workflow,
            ExecutionStage stage,
            String action,
            String detail,
            boolean success) {

        try {
            if (workflow == null || workflow.getIncident() == null) {
                return;
            }

            incidentCardService.recordExecutionEvent(
                    workflow.getIncident().getId(),
                    new ExecutionEventRequest(
                            stage,
                            action,
                            detail,
                            success
                    )
            );

        } catch (Exception ex) {
            logger.debug(
                    "recordExecOnCard: card not updated ({})",
                    ex.getMessage()
            );
        }
    }

    /**
     * Vérification générique des autres scénarios.
     *
     * Les scénarios de récupération de nœud ne passent plus par cette méthode
     * lorsqu'ils doivent produire un verdict PASS.
     */
    private void recordVerifOnCard(
            WorkflowInstance workflow,
            String namespace,
            String targetName,
            boolean resolved,
            String message) {

        try {
            if (workflow == null || workflow.getIncident() == null) {
                return;
            }

            Long id = workflow.getIncident().getId();

            String criteria =
                    "k8s-nodes".equalsIgnoreCase(namespace)
                            ? "node " + targetName + " recovered"
                            : "workload " + targetName
                            + " healthy and anomaly cleared";

            String measures =
                    "{\"observation\":\"" + safeJson(message) + "\"}";

            incidentCardService.recordVerification(
                    id,
                    new IncidentVerificationRequest(
                            criteria,
                            resolved ? "PASS" : "FAIL",
                            measures,
                            message,
                            LocalDateTime.now(),
                            true
                    )
            );

            incidentCardService.recordExecutionEvent(
                    id,
                    new ExecutionEventRequest(
                            ExecutionStage.VERIFIED,
                            null,
                            message,
                            resolved
                    )
            );

        } catch (Exception ex) {
            logger.debug(
                    "recordVerifOnCard: card not updated ({})",
                    ex.getMessage()
            );
        }
    }

    private static String safeJson(String s) {

        return s == null
                ? ""
                : s.replace("\\", "/")
                .replace("\"", "'");
    }

    private String triggerHostWatchdogRestart(String node) {

        try {
            java.net.http.HttpClient client =
                    java.net.http.HttpClient.newBuilder()
                            .connectTimeout(Duration.ofSeconds(5))
                            .build();

            java.net.http.HttpRequest req =
                    java.net.http.HttpRequest.newBuilder()
                            .uri(java.net.URI.create(
                                    hostWatchdogUrl + "/restart/" + node
                            ))
                            .timeout(Duration.ofSeconds(12))
                            .POST(
                                    java.net.http.HttpRequest.BodyPublishers.noBody()
                            )
                            .build();

            java.net.http.HttpResponse<String> resp =
                    client.send(
                            req,
                            java.net.http.HttpResponse.BodyHandlers.ofString()
                    );

            boolean ok =
                    resp.statusCode() >= 200
                            && resp.statusCode() < 300;

            logger.info(
                    "Watchdog host /restart/{} -> HTTP {} ({})",
                    node,
                    resp.statusCode(),
                    resp.body()
            );

            return ok
                    ? "déclenché (HTTP " + resp.statusCode() + ")"
                    : "ÉCHEC (HTTP " + resp.statusCode() + ")";

        } catch (Exception e) {

            logger.warn(
                    "Host watchdog /restart/{} failed: {}",
                    node,
                    e.getMessage()
            );

            return "ÉCHEC (watchdog injoignable : " + e.getMessage() + ")";
        }
    }

    private String buildKubectlEquivalent(
            ActionType type,
            String ns,
            String name) {

        if (type == null) {
            return null;
        }

        String n = name != null ? name : "<app>";
        String namespace = ns != null ? ns : "pfe";

        return switch (type) {
            case RESTART_POD ->
                    "kubectl delete pod -n " + namespace + " -l app=" + n;

            case SCALE_DEPLOYMENT ->
                    "kubectl scale deployment/" + n
                            + " -n " + namespace + " --replicas=<N+1>";

            case SCALE_DOWN ->
                    "kubectl scale deployment/" + n
                            + " -n " + namespace + " --replicas=1";

            case ROLLBACK_DEPLOYMENT ->
                    "kubectl rollout undo deployment/" + n
                            + " -n " + namespace;

            case DELETE_CRASHLOOPING_PODS ->
                    "kubectl delete pods -n " + namespace + " -l app=" + n;

            case REMOVE_NETWORK_POLICY ->
                    "kubectl delete networkpolicy <bloquante-récente> -n "
                            + namespace;

            case FORCE_RECREATE_DEPLOYMENT ->
                    "kubectl rollout restart deployment/" + n
                            + " -n " + namespace;

            case CORDON_NODE ->
                    "kubectl cordon " + n;

            case DRAIN_NODE ->
                    "kubectl drain " + n
                            + " --ignore-daemonsets --delete-emptydir-data";

            case INVESTIGATION -> null;

            default -> null;
        };
    }

    private String describeActionFr(ActionType type) {

        if (type == null) {
            return "action inconnue";
        }

        return switch (type) {
            case RESTART_POD ->
                    "supprimer le pod pour le redémarrer";

            case SCALE_DEPLOYMENT ->
                    "ajouter des répliques (scale up)";

            case SCALE_DOWN ->
                    "réduire les répliques (scale down)";

            case ROLLBACK_DEPLOYMENT ->
                    "revenir à la version précédente (rollback)";

            case DELETE_CRASHLOOPING_PODS ->
                    "supprimer les pods en crash pour forcer une recréation propre";

            case REMOVE_NETWORK_POLICY ->
                    "supprimer la NetworkPolicy bloquante";

            case FORCE_RECREATE_DEPLOYMENT ->
                    "redémarrage progressif des pods (rollout restart)";

            case CORDON_NODE ->
                    "isoler le nœud (cordon)";

            case DRAIN_NODE ->
                    "vider le nœud et migrer les pods (drain)";

            case RESTART_VM ->
                    "redémarrer la VM du worker (watchdog)";

            case INVESTIGATION ->
                    "investigation / escalade humaine";

            case ESCALATION ->
                    "escalade à l'équipe d'astreinte";

            default -> type.name();
        };
    }

    private <T extends Enum<T>> T enumValue(
            Class<T> enumType,
            Object rawValue,
            T currentValue,
            T fallback) {

        if (rawValue != null) {
            try {
                return Enum.valueOf(
                        enumType,
                        String.valueOf(rawValue)
                );
            } catch (IllegalArgumentException ignored) {
                logger.warn(
                        "Unable to parse enum {} from value {}",
                        enumType.getSimpleName(),
                        rawValue
                );
            }
        }

        if (currentValue != null) {
            return currentValue;
        }

        return fallback;
    }
}
