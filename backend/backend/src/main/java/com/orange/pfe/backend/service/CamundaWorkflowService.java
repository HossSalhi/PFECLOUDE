package com.orange.pfe.backend.service;

import com.orange.pfe.backend.config.CamundaProperties;
import com.orange.pfe.backend.config.RemediationProperties;
import com.orange.pfe.backend.dto.AIPredictResponse;
import com.orange.pfe.backend.model.action.IncidentAction;
import com.orange.pfe.backend.model.human.TaskDecision;
import com.orange.pfe.backend.model.workflow.WorkflowInstance;
import com.orange.pfe.backend.model.workflow.WorkflowOrchestrationState;
import com.orange.pfe.backend.service.remediation.RemediationExecutionResult;
import com.orange.pfe.backend.service.remediation.RemediationExecutor;
import com.orange.pfe.backend.model.action.ActionType;
import com.orange.pfe.backend.service.remediation.RemediationPlan;
import io.camunda.client.CamundaClient;
import io.camunda.client.api.response.ProcessInstanceEvent;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.ObjectProvider;
import org.springframework.stereotype.Service;

import java.util.HashMap;
import java.util.Map;

@Service
public class CamundaWorkflowService {

    public static final String APPROVAL_MESSAGE_NAME = "incident-approval-decision";
    public static final String BPMN_RESOURCE_PATH = "camunda/incident-remediation.bpmn";
    // L15 — workflow spécialisé nœud (drain/cordon/escalade VM = toujours approbation humaine)
    public static final String NODE_BPMN_RESOURCE_PATH = "camunda/node-remediation.bpmn";
    public static final String NODE_PROCESS_ID = "node-remediation";
    // Process de DÉTECTION : Camunda appelle l'IA (intermédiaire monitoring -> IA)
    public static final String DETECTION_BPMN_RESOURCE_PATH = "camunda/incident-detection.bpmn";
    public static final String DETECTION_PROCESS_ID = "incident-detection";

    private static final Logger logger = LoggerFactory.getLogger(CamundaWorkflowService.class);

    private final ObjectProvider<CamundaClient> camundaClientProvider;
    private final CamundaProperties camundaProperties;
    private final RemediationProperties remediationProperties;
    private final WorkflowService workflowService;
    private final RemediationExecutor remediationExecutor;

    public CamundaWorkflowService(
            ObjectProvider<CamundaClient> camundaClientProvider,
            CamundaProperties camundaProperties,
            RemediationProperties remediationProperties,
            WorkflowService workflowService,
            RemediationExecutor remediationExecutor
    ) {
        this.camundaClientProvider = camundaClientProvider;
        this.camundaProperties = camundaProperties;
        this.remediationProperties = remediationProperties;
        this.workflowService = workflowService;
        this.remediationExecutor = remediationExecutor;
    }

    public boolean isEnabled() {
        return camundaProperties.isEnabled() && camundaClientProvider.getIfAvailable() != null;
    }

    /**
     * Disponibilité RÉELLE de Camunda/Zeebe (pas seulement le flag de config) : sonde 'topology'
     * avec timeout court. Camunda est l'ORCHESTRATEUR CENTRAL du projet -> cette porte est utilisée
     * par les schedulers de détection : si Camunda est arrêté, HELIOS ne détecte AUCUN incident.
     * NB : isEnabled() ne suffit pas — le bean CamundaClient survit à l'arrêt de la VM .161 (Zeebe),
     * donc isEnabled() resterait 'true' alors que Zeebe est injoignable. La sonde topology, elle,
     * échoue tout de suite si Zeebe ne répond pas.
     */
    public boolean isReachable() {
        if (!camundaProperties.isEnabled()) {
            return false;
        }
        CamundaClient client = camundaClientProvider.getIfAvailable();
        if (client == null) {
            return false;
        }
        try {
            client.newTopologyRequest()
                    .requestTimeout(java.time.Duration.ofSeconds(2))
                    .send()
                    .join();
            return true;
        } catch (Exception ex) {
            logger.debug("Camunda injoignable (sonde topology) : {}", ex.getMessage());
            return false;
        }
    }

    /**
     * Démarre le process Camunda de DÉTECTION (`incident-detection`) suite à une ALARME BRUTE du
     * monitoring. C'est le point d'entrée où Camunda devient l'intermédiaire monitoring -> IA :
     * l'unique tâche `ai-classify` appellera l'IA. No-op si Camunda injoignable (=> 0 détection,
     * cohérent avec « Camunda = orchestrateur central »). Retourne true si le process a démarré.
     */
    private static String buildDetectionBusinessId(String namespace, String app) {
        if (namespace == null || namespace.isBlank() || app == null || app.isBlank()) {
            throw new IllegalArgumentException("Namespace et cible requis pour le Business ID");
        }

        String businessId = "HELIOS-DETECTION|"
                + namespace.trim().toLowerCase(java.util.Locale.ROOT)
                + "|"
                + app.trim().toLowerCase(java.util.Locale.ROOT);

        if (businessId.length() > 256) {
            throw new IllegalArgumentException("Business ID de détection supérieur à 256 caractères");
        }

        return businessId;
    }
    public boolean startDetectionWorkflow(String namespace, String app) {
        return startDetectionWorkflow(namespace, app, "app", null);
    }

    /**
     * kind="app" -> le worker ai-classify appelle l'IA (classification). kind="infra" -> le worker
     * crée l'incident infra/nœud de façon DÉTERMINISTE (un nœud/VM mort n'a pas de métriques pour l'IA).
     * Dans les deux cas, c'est CAMUNDA qui orchestre la création de l'incident (point d'entrée unique).
     */
    public boolean startDetectionWorkflow(String namespace, String app, String kind, String detail) {
        if (!isReachable()) {
            logger.warn("Détection non démarrée pour {}/{} — Camunda injoignable (orchestrateur central requis)", namespace, app);
            return false;
        }
        try {
            Map<String, Object> vars = new HashMap<>();
            vars.put("namespace", namespace);
            vars.put("app", app);
            vars.put("kind", kind == null ? "app" : kind);
            if (detail != null) {
                vars.put("detail", detail);
            }
            ProcessInstanceEvent evt = requireClient()
                    .newCreateInstanceCommand()
                    .bpmnProcessId(DETECTION_PROCESS_ID)
                    .latestVersion()
                    .businessId(buildDetectionBusinessId(namespace, app))
                    .variables(vars)
                    .send()
                    .join();
            logger.info("Camunda: process de détection démarré pour {}/{} (PI {})",
                    namespace, app, evt.getProcessInstanceKey());
            return true;
        } catch (Exception ex) {
            logger.error("Impossible de démarrer le process de détection Camunda pour {}/{}: {}",
                    namespace, app, ex.getMessage());
            return false;
        }
    }

    public void deployProcessIfNeeded() {
        if (!isEnabled() || !camundaProperties.isAutoDeployBpmn()) {
            return;
        }

        try {
            requireClient()
                    .newDeployResourceCommand()
                    .addResourceFromClasspath(BPMN_RESOURCE_PATH)
                    .addResourceFromClasspath(NODE_BPMN_RESOURCE_PATH)
                    .addResourceFromClasspath(DETECTION_BPMN_RESOURCE_PATH)
                    .send()
                    .join();
            logger.info("Camunda BPMN deployed: {}, {}, {}", BPMN_RESOURCE_PATH, NODE_BPMN_RESOURCE_PATH, DETECTION_BPMN_RESOURCE_PATH);
        } catch (Exception ex) {
            logger.error("Unable to deploy Camunda BPMN resources", ex);
        }
    }

    /**
     * L15 — Dispatch by incident type. Node/VM-level incidents use the specialized
     * node-remediation process (which forces human approval before drain/cordon/escalate).
     * Everything else uses the default app remediation process.
     */
    private String resolveProcessId(String incidentType, RemediationPlan plan) {
        if (incidentType == null) {
            return camundaProperties.getProcessId();
        }
        String t = incidentType.toUpperCase();
        boolean isNode = t.startsWith("NODE_") || t.contains("NODE")
                || t.equals("MONITORING_DOWN") || t.equals("AI_SERVICE_DOWN");
        if (isNode) {
            // The node-remediation process gates DESTRUCTIVE node-level actions (drain/cordon) behind
            // human approval. But several NODE_ incident types are remediated by a SAFE, auto-executable
            // pod/deployment action — e.g. NODE_DISK_PRESSURE (ephemeral-storage eviction) -> DELETE the
            // evicted pod, or auto VM power-on -> RESTART_VM. Routing those through the approval process
            // strands them until a 5-min timeout -> RESOLUTION_FAILED. Only DRAIN/CORDON stay gated;
            // any other auto-executable action uses the AUTO process so it runs immediately.
            boolean safeAutoAction = plan != null && plan.autoExecutable()
                    && plan.actionType() != ActionType.DRAIN_NODE
                    && plan.actionType() != ActionType.CORDON_NODE;
            if (safeAutoAction) {
                return camundaProperties.getProcessId();
            }
            return NODE_PROCESS_ID;
        }
        return camundaProperties.getProcessId();
    }

    public void startWorkflow(
            WorkflowInstance workflow,
            IncidentAction action,
            RemediationPlan plan,
            AIPredictResponse prediction
    ) {
        startWorkflowWithOutcome(workflow, action, plan, prediction, true);
    }

    /**
     * Retourne un resultat explicite afin que les orchestrateurs distinguent
     * le lancement confirme, le fallback et le suivi manuel.
     *
     * Pour les remediations host CPU/DISK, allowDirectFallback doit rester false :
     * ces actions doivent passer par le workflow gouverne et sa verification.
     */
    public CamundaLaunchOutcome startWorkflowWithOutcome(
            WorkflowInstance workflow,
            IncidentAction action,
            RemediationPlan plan,
            AIPredictResponse prediction,
            boolean allowDirectFallback
    ) {
        if (workflow == null || action == null || plan == null
                || plan.actionType() == null || prediction == null) {
            return CamundaLaunchOutcome.NOT_STARTED;
        }

        String existingProcessKey = workflow.getCamundaProcessInstanceKey();
        if (existingProcessKey != null && !existingProcessKey.isBlank()) {
            logger.warn(
                    "Camunda process already attached to workflow {}; refusing to create another process",
                    workflow.getId()
            );
            return CamundaLaunchOutcome.PROCESS_ALREADY_ATTACHED;
        }

        if (!isReachable()) {
            String reason = "Camunda unavailable before process creation";

            if (!isEnabled()) {
                if (allowDirectFallback) {
                    logger.warn(
                            "Camunda disabled or client unavailable; using legacy direct fallback for workflow {}",
                            workflow.getId()
                    );
                    return executeWithoutCamunda(workflow, plan, reason);
                }

                return markManualFollowUp(
                        workflow,
                        reason + "; direct fallback disabled for this remediation",
                        CamundaLaunchOutcome.LAUNCH_FAILED
                );
            }

            logger.warn(
                    "Camunda is enabled but unreachable for workflow {}; direct fallback suppressed",
                    workflow.getId()
            );
            return markManualFollowUp(
                    workflow,
                    reason + "; Camunda is enabled but unreachable; direct fallback suppressed",
                    CamundaLaunchOutcome.LAUNCH_FAILED
            );
        }

        final String processId;
        final Map<String, Object> variables;

        try {
            processId = resolveProcessId(prediction.getIncidentType(), plan);
            variables = buildVariables(workflow, action, plan, prediction);
        } catch (Exception ex) {
            logger.error(
                    "Unable to prepare Camunda process before submission for workflow {}",
                    workflow.getId(),
                    ex
            );
            String reason = "Camunda preparation failed before submission";
            if (allowDirectFallback) {
                return executeWithoutCamunda(workflow, plan, reason);
            }
            return markManualFollowUp(
                    workflow,
                    reason + "; direct fallback disabled for host remediation",
                    CamundaLaunchOutcome.LAUNCH_FAILED
            );
        }

        final ProcessInstanceEvent processInstanceEvent;
        try {
            processInstanceEvent = requireClient()
                    .newCreateInstanceCommand()
                    .bpmnProcessId(processId)
                    .latestVersion()
                    .businessId("HELIOS-WORKFLOW-" + workflow.getId())
                    .variables(variables)
                    .send()
                    .join();
        } catch (Exception ex) {
            logger.error(
                    "Camunda process creation outcome is uncertain for workflow {}; direct fallback suppressed",
                    workflow.getId(),
                    ex
            );

            try {
                workflowService.addWorkflowLog(
                        workflow.getId(),
                        "Camunda creation outcome uncertain: "
                                + ex.getClass().getSimpleName()
                                + "; direct fallback suppressed to prevent duplicate remediation"
                );
            } catch (Exception logEx) {
                logger.error(
                        "Unable to persist uncertain Camunda creation log for workflow {}",
                        workflow.getId(),
                        logEx
                );
            }

            try {
                workflowService.updateOrchestrationState(
                        workflow,
                        WorkflowOrchestrationState.MANUAL_FOLLOW_UP,
                        "Camunda creation outcome uncertain; automatic fallback suppressed to prevent duplicate remediation"
                );
            } catch (Exception stateEx) {
                logger.error(
                        "Unable to persist uncertain Camunda creation state for workflow {}",
                        workflow.getId(),
                        stateEx
                );
            }
            return CamundaLaunchOutcome.MANUAL_FOLLOW_UP_REQUIRED;
        }

        try {
            workflowService.attachCamundaProcess(
                    workflow,
                    processInstanceEvent.getBpmnProcessId(),
                    String.valueOf(processInstanceEvent.getProcessInstanceKey())
            );

            workflowService.updateOrchestrationState(
                    workflow,
                    plan.approvalRequired()
                            ? WorkflowOrchestrationState.WAITING_FOR_APPROVAL
                            : WorkflowOrchestrationState.EXECUTION_PENDING,
                    "Camunda orchestration started with process instance "
                            + processInstanceEvent.getProcessInstanceKey()
            );
            return CamundaLaunchOutcome.CAMUNDA_STARTED;
        } catch (Exception ex) {
            logger.error(
                    "Camunda process {} was created for workflow {}, but local persistence failed; direct fallback suppressed",
                    processInstanceEvent.getProcessInstanceKey(),
                    workflow.getId(),
                    ex
            );

            try {
                workflowService.addWorkflowLog(
                        workflow.getId(),
                        "Camunda process created with key "
                                + processInstanceEvent.getProcessInstanceKey()
                                + "; local state persistence failed; no direct fallback attempted"
                );
            } catch (Exception logEx) {
                logger.error(
                        "Unable to persist Camunda post-creation diagnostic for workflow {}",
                        workflow.getId(),
                        logEx
                );
            }

            try {
                workflowService.updateOrchestrationState(
                        workflow,
                        WorkflowOrchestrationState.MANUAL_FOLLOW_UP,
                        "Camunda process was created with key "
                                + processInstanceEvent.getProcessInstanceKey()
                                + "; local state persistence failed; direct fallback suppressed"
                );
            } catch (Exception stateEx) {
                logger.error(
                        "Unable to persist manual follow-up state for workflow {}",
                        workflow.getId(),
                        stateEx
                );
            }
            return CamundaLaunchOutcome.MANUAL_FOLLOW_UP_REQUIRED;
        }
    }
    /**
     * L17 — Camunda fallback. When the orchestrator is unreachable, the backend executes the
     * remediation DIRECTLY (instead of leaving the workflow stuck) — but only for auto-executable
     * plans that do not require human approval. Approval-gated plans stay manual, since without
     * Camunda there is no approval channel and a human must decide.
     */
    private CamundaLaunchOutcome executeWithoutCamunda(
            WorkflowInstance workflow, RemediationPlan plan, String reason) {
        if (plan.approvalRequired() || !plan.autoExecutable()) {
            workflowService.updateOrchestrationState(
                    workflow,
                    WorkflowOrchestrationState.MANUAL_FOLLOW_UP,
                    reason + " — approval required, kept for manual handling"
            );
            return CamundaLaunchOutcome.MANUAL_FOLLOW_UP_REQUIRED;
        }

        try {
            RemediationExecutionResult result = remediationExecutor.execute(plan);
            workflowService.addWorkflowLog(
                    workflow.getId(),
                    "Direct remediation (Camunda fallback): " + result.message()
            );
            workflowService.updateOrchestrationState(
                    workflow,
                    result.success()
                            ? WorkflowOrchestrationState.EXECUTION_PENDING
                            : WorkflowOrchestrationState.MANUAL_FOLLOW_UP,
                    reason + " — executed directly via Kubernetes executor: " + result.message()
            );
            logger.warn(
                    "L17 fallback executed remediation for workflow {} (success={})",
                    workflow.getId(), result.success()
            );
            return result.success()
                    ? CamundaLaunchOutcome.DIRECT_FALLBACK_EXECUTED
                    : CamundaLaunchOutcome.DIRECT_FALLBACK_FAILED;
        } catch (Exception ex) {
            logger.error("L17 fallback execution failed for workflow {}", workflow.getId(), ex);
            try {
                workflowService.updateOrchestrationState(
                        workflow,
                        WorkflowOrchestrationState.MANUAL_FOLLOW_UP,
                        reason + " — direct execution failed: " + ex.getMessage()
                );
            } catch (Exception stateEx) {
                logger.error(
                        "Unable to persist fallback failure state for workflow {}",
                        workflow.getId(),
                        stateEx
                );
            }
            return CamundaLaunchOutcome.DIRECT_FALLBACK_FAILED;
        }
    }

    private CamundaLaunchOutcome markManualFollowUp(
            WorkflowInstance workflow, String reason, CamundaLaunchOutcome outcome) {
        try {
            workflowService.updateOrchestrationState(
                    workflow,
                    WorkflowOrchestrationState.MANUAL_FOLLOW_UP,
                    reason
            );
        } catch (Exception stateEx) {
            logger.error(
                    "Unable to persist manual follow-up state for workflow {}",
                    workflow.getId(),
                    stateEx
            );
        }
        return outcome;
    }

    public void publishDecision(WorkflowInstance workflow, TaskDecision decision, Long actionId) {
        if (workflow == null || decision == null) {
            return;
        }
        if (!isEnabled() || workflow.getCorrelationKey() == null || workflow.getCorrelationKey().isBlank()) {
            return;
        }

        try {
            Map<String, Object> variables = new HashMap<>();
            variables.put("approvalDecision", decision.name());
            if (actionId != null) {
                variables.put("actionId", actionId);
            }

            requireClient()
                    .newPublishMessageCommand()
                    .messageName(APPROVAL_MESSAGE_NAME)
                    .correlationKey(workflow.getCorrelationKey())
                    .variables(variables)
                    .send()
                    .join();

            logger.info("Published approval decision {} for workflow {}", decision, workflow.getId());
        } catch (Exception ex) {
            logger.error("Unable to publish approval decision for workflow {}", workflow.getId(), ex);
            throw new IllegalStateException("Camunda approval correlation failed", ex);
        }
    }

    private CamundaClient requireClient() {
        CamundaClient client = camundaClientProvider.getIfAvailable();
        if (client == null) {
            throw new IllegalStateException("Camunda client is not available");
        }
        return client;
    }

    private Map<String, Object> buildVariables(
            WorkflowInstance workflow,
            IncidentAction action,
            RemediationPlan plan,
            AIPredictResponse prediction
    ) {
        Map<String, Object> variables = new HashMap<>();
        variables.put("workflowId", workflow.getId());
        variables.put("workflowCorrelationKey", workflow.getCorrelationKey());
        variables.put("incidentId", workflow.getIncident().getId());
        variables.put("actionId", action.getId());
        variables.put("actionType", action.getType().name());
        variables.put("targetNamespace", plan.targetNamespace());
        variables.put("targetKind", plan.targetKind().name());
        variables.put("targetName", plan.targetName());
        variables.put("automationMode", plan.automationMode().name());
        variables.put("approvalRequired", plan.approvalRequired());
        variables.put("autoExecutable", plan.autoExecutable());
        variables.put("manualFollowUp", plan.manualFollowUp());
        variables.put("cooldownBlocked", plan.cooldownBlocked());
        variables.put("dbRelated", plan.dbRelated());
        variables.put("rationale", plan.rationale());
        variables.put("incidentSeverity", workflow.getIncident().getSeverity().name());
        variables.put("recommendedAction", prediction.getRecommendedAction());
        variables.put("finalExecutableAction", prediction.getFinalExecutableAction());
        variables.put("executionMode", prediction.getExecutionMode());
        variables.put("recommendationOnlyReason", prediction.getRecommendationOnlyReason());
        variables.put("decisionExplanation", prediction.getDecisionExplanation());
        variables.put("chosenPlanId", prediction.getChosenPlanId());
        variables.put("incidentType", prediction.getIncidentType());
        variables.put("confidence", prediction.getConfidence() == null ? 0.0d : prediction.getConfidence());
        variables.put("verificationDelayIso", "PT" + remediationProperties.getVerifyDelaySeconds() + "S");
        // Safety-net timer: if code path stalls, Camunda auto-escalates after 5 min max.
        // Normal path resolves in ~50s (initial delay + retries); timer is last resort.
        String severity = workflow.getIncident().getSeverity() != null
                ? workflow.getIncident().getSeverity().name()
                : "MEDIUM";
        String approvalTimeoutIso = switch (severity) {
            case "CRITICAL" -> "PT3M";
            case "HIGH"     -> "PT5M";
            default         -> "PT5M";
        };
        variables.put("approvalTimeoutIso", approvalTimeoutIso);
        variables.put("approvalDecision", "PENDING");
        variables.put("executionSucceeded", false);
        variables.put("recoveryResolved", false);
        return variables;
    }
}
