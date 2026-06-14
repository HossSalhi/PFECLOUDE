package com.orange.pfe.backend.service;

import com.orange.pfe.backend.dto.AIOpsAnalyzeRequest;
import com.orange.pfe.backend.dto.AIOpsAnalyzeResponse;
import com.orange.pfe.backend.dto.AIPredictEvidence;
import com.orange.pfe.backend.dto.AIPredictRequest;
import com.orange.pfe.backend.dto.AIPredictResponse;
import com.orange.pfe.backend.exception.IncidentNotFoundException;
import com.orange.pfe.backend.model.action.ActionStatus;
import com.orange.pfe.backend.model.action.ActionType;
import com.orange.pfe.backend.model.action.AutomationMode;
import com.orange.pfe.backend.model.action.IncidentAction;
import com.orange.pfe.backend.model.action.RemediationTargetKind;
import com.orange.pfe.backend.repository.IncidentActionRepository;
import org.springframework.data.domain.PageRequest;
import com.orange.pfe.backend.model.core.Incident;
import com.orange.pfe.backend.model.decision.DecisionSource;
import com.orange.pfe.backend.model.decision.DecisionType;
import com.orange.pfe.backend.model.enums.IncidentSeverity;
import com.orange.pfe.backend.model.enums.IncidentSource;
import com.orange.pfe.backend.model.enums.IncidentStatus;
import com.orange.pfe.backend.model.workflow.WorkflowInstance;
import com.orange.pfe.backend.model.workflow.WorkflowOrchestrationState;
import com.orange.pfe.backend.repository.IncidentRepository;
import com.orange.pfe.backend.service.remediation.RemediationMetricsService;
import com.orange.pfe.backend.service.remediation.RemediationPlan;
import com.orange.pfe.backend.service.remediation.RemediationPolicyService;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.slf4j.MDC;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.stereotype.Service;
import org.springframework.transaction.PlatformTransactionManager;
import org.springframework.transaction.support.TransactionTemplate;

import java.util.List;
import java.util.Optional;

@Service
public class AIOpsOrchestrationService {

    private static final Logger log = LoggerFactory.getLogger(AIOpsOrchestrationService.class);

    private static final List<IncidentStatus> ACTIVE_STATUSES = List.of(
            IncidentStatus.NEW,
            IncidentStatus.OPEN,
            IncidentStatus.IN_PROGRESS,
            IncidentStatus.WAITING_FOR_APPROVAL
    );

    private final AIOpsClientService aiOpsClientService;
    private final IncidentRepository incidentRepository;
    private final IncidentActionRepository incidentActionRepository;
    private final WorkflowService workflowService;
    private final ActionService actionService;
    private final HumanTaskService humanTaskService;
    private final IncidentHistoryService historyService;
    private final IncidentDecisionService decisionService;
    private final RemediationPolicyService remediationPolicyService;
    private final CamundaWorkflowService camundaWorkflowService;
    private final RemediationMetricsService remediationMetricsService;
    private final KubernetesSignalEnrichmentService kubernetesSignalEnrichmentService;
    private final RemediationHistoryContextService remediationHistoryContextService;
    private final NotificationService notificationService;
    private final TransactionTemplate transactionTemplate;

    public AIOpsOrchestrationService(
            AIOpsClientService aiOpsClientService,
            IncidentRepository incidentRepository,
            IncidentActionRepository incidentActionRepository,
            WorkflowService workflowService,
            ActionService actionService,
            HumanTaskService humanTaskService,
            IncidentHistoryService historyService,
            IncidentDecisionService decisionService,
            RemediationPolicyService remediationPolicyService,
            CamundaWorkflowService camundaWorkflowService,
            RemediationMetricsService remediationMetricsService,
            KubernetesSignalEnrichmentService kubernetesSignalEnrichmentService,
            RemediationHistoryContextService remediationHistoryContextService,
            NotificationService notificationService,
            PlatformTransactionManager transactionManager
    ) {
        this.aiOpsClientService = aiOpsClientService;
        this.incidentRepository = incidentRepository;
        this.incidentActionRepository = incidentActionRepository;
        this.workflowService = workflowService;
        this.actionService = actionService;
        this.humanTaskService = humanTaskService;
        this.historyService = historyService;
        this.decisionService = decisionService;
        this.remediationPolicyService = remediationPolicyService;
        this.camundaWorkflowService = camundaWorkflowService;
        this.remediationMetricsService = remediationMetricsService;
        this.kubernetesSignalEnrichmentService = kubernetesSignalEnrichmentService;
        this.remediationHistoryContextService = remediationHistoryContextService;
        this.notificationService = notificationService;
        this.transactionTemplate = new TransactionTemplate(transactionManager);
    }

    public AIOpsAnalyzeResponse analyze(AIOpsAnalyzeRequest request) {
        MDC.put("namespace", request.getNamespace());
        MDC.put("app", request.getApp());
        try {
            log.info("AIOps analyze started ns={} app={}", request.getNamespace(), request.getApp());

            AIPredictRequest predictRequest = new AIPredictRequest();
            predictRequest.setNamespace(request.getNamespace());
            predictRequest.setApp(request.getApp());
            predictRequest.setWindowMinutes(request.getWindowMinutes());
            predictRequest.setEndTime(request.getEndTime());
            predictRequest.setFeatureOverrides(
                    kubernetesSignalEnrichmentService.collectFeatureOverrides(request.getNamespace(), request.getApp())
            );
            predictRequest.setRemediationContext(
                    remediationHistoryContextService.buildContext(request.getNamespace(), request.getApp())
            );

            AIPredictResponse prediction = aiOpsClientService.predict(predictRequest);
            log.info("AI prediction: incident={} type={} severity={} confidence={} action={}",
                    prediction.isIncident(), prediction.getIncidentType(), prediction.getSeverity(),
                    prediction.getConfidence(), prediction.getRecommendedAction());

            AIOpsAnalyzeResponse response = new AIOpsAnalyzeResponse();
            response.setPrediction(prediction);

            if (!request.isCreateIncident()) {
                response.setIncidentCreated(false);
                response.setMessage("AI prediction computed only (incident creation disabled)");
                return response;
            }

            if (!prediction.isIncident()) {
                log.info("AI indicates no incident — skipping creation");
                response.setIncidentCreated(false);
                response.setMessage("AI indicates no incident");
                return response;
            }

            AIOpsAnalyzeResponse result =
                    transactionTemplate.execute(status -> analyzeWithIncidentCreation(request, prediction, response));
            // Email "incident détecté" AFTER commit (avoids holding the DB tx during SMTP).
            if (result != null && result.isIncidentCreated() && result.getIncidentId() != null) {
                try {
                    incidentRepository.findById(result.getIncidentId())
                            .ifPresent(notificationService::notifyNewIncident);
                } catch (Exception ex) {
                    log.warn("Incident-detected notification failed: {}", ex.getMessage());
                }
            }
            return result;
        } finally {
            MDC.remove("namespace");
            MDC.remove("app");
            MDC.remove("incidentId");
        }
    }

    @Transactional
    protected AIOpsAnalyzeResponse analyzeWithIncidentCreation(
            AIOpsAnalyzeRequest request,
            AIPredictResponse prediction,
            AIOpsAnalyzeResponse response
    ) {
        Incident incident = createIncidentFromPrediction(prediction, request.getNamespace(), request.getApp());
        MDC.put("incidentId", String.valueOf(incident.getId()));
        log.info("Incident created id={} severity={} type={}", incident.getId(),
                incident.getSeverity(), incident.getIncidentType());

        historyService.record(incident, IncidentStatus.NEW, "Incident created from AI prediction");

        decisionService.recordDecision(
                incident,
                DecisionType.DETECT_INCIDENT,
                DecisionSource.SYSTEM,
                buildDetectionComment(prediction)
        );

        WorkflowInstance workflow = workflowService.startWorkflow(
                incident,
                "Incident-Remediation-Workflow",
                request.getNamespace(),
                request.getApp()
        );
        remediationMetricsService.workflowStarted();

        RemediationPlan plan = remediationPolicyService.buildPlan(request, prediction);
        ActionType actionType = plan.actionType();
        log.info("Remediation plan: action={} approvalRequired={} rationale={}",
                actionType, plan.approvalRequired(), plan.rationale());

        decisionService.recordDecision(
                incident,
                DecisionType.RECOMMEND_ACTION,
                DecisionSource.SYSTEM,
                "AI recommended action: " + safe(prediction.getRecommendedAction()) + " | " + plan.rationale()
        );

        var action = actionType == null
                ? null
                : actionService.createAction(
                        incident,
                        actionType,
                        buildActionDescription(prediction, plan),
                        "AI-SYSTEM",
                        plan.targetNamespace(),
                        plan.targetKind(),
                        plan.targetName(),
                        plan.automationMode()
                );

        if (actionType == null) {
            incident.setStatus(IncidentStatus.OPEN);
            workflowService.updateOrchestrationState(workflow, WorkflowOrchestrationState.MANUAL_FOLLOW_UP,
                    "AI returned recommendation-only guidance");
            historyService.record(incident, IncidentStatus.OPEN, "Incident opened with recommendation-only AI guidance");
            log.info("Incident {} set to OPEN — recommendation-only path", incident.getId());
        } else if (actionType == ActionType.ESCALATION) {
            incident.setStatus(IncidentStatus.OPEN);
            workflowService.updateOrchestrationState(workflow, WorkflowOrchestrationState.MANUAL_FOLLOW_UP,
                    "ESCALATION — critical incident requires immediate human attention");
            historyService.record(incident, IncidentStatus.OPEN, "ESCALATION — forwarded to on-call team");
            notificationService.notifyEscalation(incident);
            log.warn("Incident {} ESCALATED — webhook + email triggered for on-call team", incident.getId());
        } else if (plan.approvalRequired()) {
            camundaWorkflowService.startWorkflow(workflow, action, plan, prediction);
            humanTaskService.createTask("Validate AI remediation for incident #" + incident.getId(), workflow);
            remediationMetricsService.approvalPending();
            incident.setStatus(IncidentStatus.WAITING_FOR_APPROVAL);
            workflowService.updateOrchestrationState(workflow, WorkflowOrchestrationState.WAITING_FOR_APPROVAL,
                    "Waiting for human approval before remediation");
            historyService.record(incident, IncidentStatus.WAITING_FOR_APPROVAL, "Waiting for human approval (AI path)");
            log.info("Incident {} WAITING_FOR_APPROVAL", incident.getId());
        } else {
            camundaWorkflowService.startWorkflow(workflow, action, plan, prediction);
            incident.setStatus(IncidentStatus.IN_PROGRESS);
            workflowService.updateOrchestrationState(workflow, WorkflowOrchestrationState.EXECUTION_PENDING,
                    "Remediation approved for automatic execution");
            historyService.record(incident, IncidentStatus.IN_PROGRESS, "AI remediation path initiated");
            log.info("Incident {} IN_PROGRESS — auto-remediation path", incident.getId());
        }

        incident = incidentRepository.save(incident);

        response.setIncidentCreated(true);
        response.setIncidentId(incident.getId());
        response.setWorkflowId(workflow.getId());
        response.setIncidentStatus(incident.getStatus());
        response.setMessage("Incident created from AI prediction");
        return response;
    }

    private Incident createIncidentFromPrediction(AIPredictResponse prediction, String namespace, String app) {
        Incident incident = new Incident();
        incident.setTitle("AI Incident: " + safe(prediction.getIncidentType()));
        incident.setDescription(buildIncidentDescription(prediction));
        incident.setExplanation(prediction.getDecisionExplanation() != null
                ? prediction.getDecisionExplanation()
                : prediction.getSummary());
        incident.setSeverity(mapSeverity(prediction.getSeverity()));
        incident.setStatus(IncidentStatus.NEW);
        incident.setSource(IncidentSource.AI);
        incident.setIncidentType(prediction.getIncidentType());
        incident.setNamespace(namespace);
        incident.setApp(app);
        return incidentRepository.save(incident);
    }

    private String buildIncidentDescription(AIPredictResponse prediction) {
        // Clean, structured at-a-glance summary. The full cause + Why prose lives in the
        // incident's explanation field ("Explication IA"), so we do NOT repeat it here.
        double conf = prediction.getConfidence() != null ? prediction.getConfidence() : 0.0;
        return String.format(
                "Type: %s | Severity: %s (confidence %.0f%%) | Recommended action: %s | Mode: %s",
                safe(prediction.getIncidentType()),
                safe(prediction.getSeverity()),
                conf * 100.0,
                safe(prediction.getRecommendedAction()),
                safe(prediction.getExecutionMode())
        );
    }

    private String buildDetectionComment(AIPredictResponse prediction) {
        AIPredictEvidence evidence = prediction.getEvidence();
        String evidenceText = evidence == null
                ? "no-evidence"
                : String.format(
                "log=%.3f metric=%.3f rule=%.3f model=%s",
                valueOrZero(evidence.getLogScore()),
                valueOrZero(evidence.getMetricScore()),
                valueOrZero(evidence.getRuleScore()),
                evidence.getModelScore() == null ? "null" : String.format("%.3f", evidence.getModelScore())
        );

        return String.format(
                "AI detection: incident=%s type=%s severity=%s confidence=%.3f action=%s executionMode=%s chosenPlan=%s explanation=%s evidence=[%s]",
                prediction.isIncident(),
                safe(prediction.getIncidentType()),
                safe(prediction.getSeverity()),
                prediction.getConfidence() != null ? prediction.getConfidence() : 0.0,
                safe(prediction.getRecommendedAction()),
                safe(prediction.getExecutionMode()),
                safe(prediction.getChosenPlanId()),
                safe(prediction.getDecisionExplanation()),
                evidenceText
        );
    }

    private String buildActionDescription(AIPredictResponse prediction, RemediationPlan plan) {
        return String.format(
                "AI recommended action: %s | executionMode=%s | chosenPlan=%s | explanation=%s | %s",
                safe(prediction.getRecommendedAction()),
                safe(prediction.getExecutionMode()),
                safe(prediction.getChosenPlanId()),
                safe(prediction.getDecisionExplanation()),
                plan.rationale()
        );
    }

    private double valueOrZero(Double value) {
        return value == null ? 0.0 : value;
    }

    private IncidentSeverity mapSeverity(String severity) {
        if (severity == null || severity.isBlank()) {
            return IncidentSeverity.MEDIUM;
        }
        try {
            return IncidentSeverity.valueOf(severity.toUpperCase());
        } catch (Exception ex) {
            return IncidentSeverity.MEDIUM;
        }
    }

    private String safe(String value) {
        return value == null ? "unknown" : value;
    }

    @Transactional
    public boolean autoResolveIfCleared(String namespace, String app, int windowMinutes) {
        Optional<Incident> openIncidentOpt = incidentRepository
                .findFirstByStatusInAndSourceAndNamespaceAndAppOrderByCreatedAtDesc(
                        ACTIVE_STATUSES, IncidentSource.AI, namespace, app);
        if (openIncidentOpt.isEmpty()) {
            return false;
        }

        AIPredictRequest predictRequest = new AIPredictRequest();
        predictRequest.setNamespace(namespace);
        predictRequest.setApp(app);
        predictRequest.setWindowMinutes(windowMinutes);
        predictRequest.setSkipLlm(true);
        predictRequest.setFeatureOverrides(
                kubernetesSignalEnrichmentService.collectFeatureOverrides(namespace, app));

        AIPredictResponse prediction = aiOpsClientService.predict(predictRequest);

        if (prediction.isIncident()) {
            log.debug("Auto-resolve check: condition still present for {}/{}", namespace, app);
            return false;
        }

        Incident incident = openIncidentOpt.get();
        incident.setStatus(IncidentStatus.RESOLVED);
        historyService.record(incident, IncidentStatus.RESOLVED,
                "Auto-resolved: AI monitoring detects condition has cleared");
        incidentRepository.save(incident);
        log.info("Auto-resolved incident id={} for {}/{} — condition cleared", incident.getId(), namespace, app);
        try {
            notificationService.notifyIncidentResolved(incident);
        } catch (Exception ex) {
            log.warn("Incident-resolved notification failed: {}", ex.getMessage());
        }
        return true;
    }

    @Transactional
    public void executeRecommendation(Long incidentId) {
        Incident incident = incidentRepository.findById(incidentId)
                .orElseThrow(() -> new IncidentNotFoundException(incidentId));

        // Garde : ne pas (re)executer un incident deja traite/en cours.
        if (incident.getStatus() == IncidentStatus.RESOLVED
                || incident.getStatus() == IncidentStatus.CLOSED
                || incident.getStatus() == IncidentStatus.ARCHIVED
                || incident.getStatus() == IncidentStatus.IN_PROGRESS) {
            log.info("executeRecommendation: incident {} deja {} - ignore", incidentId, incident.getStatus());
            return;
        }

        String namespace = incident.getNamespace() != null ? incident.getNamespace() : "pfe";
        String app = incident.getApp() != null ? incident.getApp() : "backend";
        String rawAction = parseFieldFromDescription(incident.getDescription(), "action");
        ActionType actionType = remediationPolicyService.mapActionType(rawAction, incident.getIncidentType());
        if (actionType == null || actionType == ActionType.INVESTIGATION || actionType == ActionType.ESCALATION) {
            // Incident noeud -> RESTART_VM (rallumer la VM via l'agent hote) ; sinon RESTART_POD.
            boolean isNode = (incident.getIncidentType() != null
                        && incident.getIncidentType().toUpperCase(java.util.Locale.ROOT).contains("NODE"))
                    || (app != null && app.toLowerCase(java.util.Locale.ROOT).startsWith("k8s-worker"));
            actionType = isNode ? ActionType.RESTART_VM : ActionType.RESTART_POD;
        }

        // Reutiliser l'action PENDING deja recommandee (evite un doublon) ; sinon en creer
        // une nouvelle en mode MANUEL (c'est l'operateur qui declenche via "Demarrer").
        var pendingPage = incidentActionRepository.findByIncidentIdAndStatus(
                incident.getId(), ActionStatus.PENDING, PageRequest.of(0, 1));
        IncidentAction action;
        RemediationTargetKind targetKind;
        if (!pendingPage.isEmpty()) {
            action = pendingPage.getContent().get(0);
            actionType = action.getType();
            targetKind = action.getTargetKind() != null ? action.getTargetKind() : RemediationTargetKind.POD;
            if (action.getTargetNamespace() != null) { namespace = action.getTargetNamespace(); }
            if (action.getTargetName() != null) { app = action.getTargetName(); }
        } else {
            targetKind = (actionType == ActionType.RESTART_POD
                    || actionType == ActionType.DELETE_CRASHLOOPING_PODS)
                    ? RemediationTargetKind.POD : RemediationTargetKind.DEPLOYMENT;
            action = actionService.createAction(
                    incident, actionType,
                    "Execution declenchee par l'operateur (Demarrer): " + safe(rawAction),
                    "HUMAN-OPERATOR",
                    namespace, targetKind, app, AutomationMode.MANUAL
            );
        }

        WorkflowInstance workflow = workflowService.startWorkflow(
                incident, "Incident-Remediation-Workflow", namespace, app);

        // approvalRequired=false + autoExecutable=true : le clic "Demarrer" de l'operateur
        // VAUT approbation -> Camunda execute directement, sans Human Task d'attente.
        // Le mode de l'action reste celui d'origine (MANUEL = declenche par l'operateur).
        RemediationPlan plan = new RemediationPlan(
                actionType,
                action.getAutomationMode() != null ? action.getAutomationMode() : AutomationMode.MANUAL,
                false, true, false, false, false,
                namespace, targetKind, app, "Execution directe demandee par l'operateur (Demarrer)"
        );

        AIPredictResponse prediction = new AIPredictResponse();
        prediction.setIncident(true);
        prediction.setIncidentType(incident.getIncidentType());
        prediction.setSeverity(incident.getSeverity() != null ? incident.getSeverity().name() : "MEDIUM");
        prediction.setRecommendedAction(rawAction);
        prediction.setFinalExecutableAction(rawAction);
        prediction.setExecutionMode("AUTO");
        prediction.setConfidence(1.0);
        prediction.setDecisionExplanation(incident.getExplanation());

        // Camunda execute la remediation directement (le clic Demarrer = approbation).
        camundaWorkflowService.startWorkflow(workflow, action, plan, prediction);

        incident.setStatus(IncidentStatus.IN_PROGRESS);
        workflowService.updateOrchestrationState(workflow, WorkflowOrchestrationState.EXECUTION_PENDING,
                "Execution directe declenchee par l'operateur");
        historyService.record(incident, IncidentStatus.IN_PROGRESS,
                "Remediation declenchee par l'operateur (Demarrer) - Camunda execute directement");
        incidentRepository.save(incident);

        log.info("executeRecommendation: incident={} action={} workflow={}", incidentId, actionType, workflow.getId());
    }

    private String parseFieldFromDescription(String description, String field) {
        if (description == null || field == null) return null;
        String marker = "| " + field + "=";
        int start = description.indexOf(marker);
        if (start < 0) return null;
        start += marker.length();
        int end = description.indexOf(" |", start);
        return end < 0 ? description.substring(start).trim() : description.substring(start, end).trim();
    }
}
