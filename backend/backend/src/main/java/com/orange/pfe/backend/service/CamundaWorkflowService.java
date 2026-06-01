package com.orange.pfe.backend.service;

import com.orange.pfe.backend.config.CamundaProperties;
import com.orange.pfe.backend.config.RemediationProperties;
import com.orange.pfe.backend.dto.AIPredictResponse;
import com.orange.pfe.backend.model.action.IncidentAction;
import com.orange.pfe.backend.model.human.TaskDecision;
import com.orange.pfe.backend.model.workflow.WorkflowInstance;
import com.orange.pfe.backend.model.workflow.WorkflowOrchestrationState;
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

    private static final Logger logger = LoggerFactory.getLogger(CamundaWorkflowService.class);

    private final ObjectProvider<CamundaClient> camundaClientProvider;
    private final CamundaProperties camundaProperties;
    private final RemediationProperties remediationProperties;
    private final WorkflowService workflowService;

    public CamundaWorkflowService(
            ObjectProvider<CamundaClient> camundaClientProvider,
            CamundaProperties camundaProperties,
            RemediationProperties remediationProperties,
            WorkflowService workflowService
    ) {
        this.camundaClientProvider = camundaClientProvider;
        this.camundaProperties = camundaProperties;
        this.remediationProperties = remediationProperties;
        this.workflowService = workflowService;
    }

    public boolean isEnabled() {
        return camundaProperties.isEnabled() && camundaClientProvider.getIfAvailable() != null;
    }

    public void deployProcessIfNeeded() {
        if (!isEnabled() || !camundaProperties.isAutoDeployBpmn()) {
            return;
        }

        try {
            requireClient()
                    .newDeployResourceCommand()
                    .addResourceFromClasspath(BPMN_RESOURCE_PATH)
                    .send()
                    .join();
            logger.info("Camunda BPMN deployed from {}", BPMN_RESOURCE_PATH);
        } catch (Exception ex) {
            logger.error("Unable to deploy Camunda BPMN resource {}", BPMN_RESOURCE_PATH, ex);
        }
    }

    public void startWorkflow(
            WorkflowInstance workflow,
            IncidentAction action,
            RemediationPlan plan,
            AIPredictResponse prediction
    ) {
        if (workflow == null || action == null || plan == null || plan.actionType() == null) {
            return;
        }
        if (!isEnabled()) {
            logger.info("Camunda is disabled or unavailable; workflow {} stays backend-managed only", workflow.getId());
            return;
        }

        try {
            ProcessInstanceEvent processInstanceEvent = requireClient()
                    .newCreateInstanceCommand()
                    .bpmnProcessId(camundaProperties.getProcessId())
                    .latestVersion()
                    .variables(buildVariables(workflow, action, plan, prediction))
                    .send()
                    .join();

            workflowService.attachCamundaProcess(
                    workflow,
                    processInstanceEvent.getBpmnProcessId(),
                    String.valueOf(processInstanceEvent.getProcessInstanceKey())
            );
            workflowService.updateOrchestrationState(
                    workflow,
                    plan.approvalRequired() ? WorkflowOrchestrationState.WAITING_FOR_APPROVAL : WorkflowOrchestrationState.EXECUTION_PENDING,
                    "Camunda orchestration started with process instance " + processInstanceEvent.getProcessInstanceKey()
            );
        } catch (Exception ex) {
            logger.error("Unable to start Camunda workflow for workflow {}", workflow.getId(), ex);
            workflow.addLog("Camunda start failed: " + ex.getMessage());
            workflowService.updateOrchestrationState(
                    workflow,
                    WorkflowOrchestrationState.MANUAL_FOLLOW_UP,
                    "Camunda start failed; backend will keep the workflow local"
            );
        }
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
        variables.put("approvalDecision", "PENDING");
        variables.put("executionSucceeded", false);
        variables.put("recoveryResolved", false);
        return variables;
    }
}
