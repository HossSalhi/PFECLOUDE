package com.orange.pfe.backend.service.remediation;

import com.orange.pfe.backend.config.RemediationProperties;
import com.orange.pfe.backend.dto.AIOpsAnalyzeRequest;
import com.orange.pfe.backend.dto.AIPredictResponse;
import com.orange.pfe.backend.model.action.ActionType;
import com.orange.pfe.backend.model.action.AutomationMode;
import com.orange.pfe.backend.model.action.RemediationTargetKind;
import com.orange.pfe.backend.repository.IncidentActionRepository;
import org.junit.jupiter.api.Test;

import java.util.List;
import java.util.Set;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

class RemediationPolicyServiceTest {

    @Test
    void shouldPreferRestartForRestartOrScaleAndAllowAutoExecution() {
        IncidentActionRepository repository = mock(IncidentActionRepository.class);
        when(repository.findRecentActionsForTarget(eq("pfe"), eq("backend"), eq(ActionType.RESTART_POD), any()))
                .thenReturn(List.of());

        RemediationProperties properties = new RemediationProperties();
        properties.setAutomationEnabled(true);
        properties.setAllowedActions(Set.of(ActionType.RESTART_POD));
        properties.setAllowlistedApps(Set.of("backend"));
        properties.setRestartConfidenceThreshold(0.45d);

        RemediationPolicyService service = new RemediationPolicyService(properties, repository);

        AIOpsAnalyzeRequest request = new AIOpsAnalyzeRequest();
        request.setNamespace("pfe");
        request.setApp("backend");

        AIPredictResponse prediction = new AIPredictResponse();
        prediction.setIncident(true);
        prediction.setIncidentType("MEMORY_STRESS");
        prediction.setSeverity("HIGH");
        prediction.setConfidence(0.49d);
        prediction.setRecommendedAction("RESTART_OR_SCALE");

        RemediationPlan plan = service.buildPlan(request, prediction);

        assertEquals(ActionType.RESTART_POD, plan.actionType());
        assertEquals(AutomationMode.AUTO, plan.automationMode());
        assertEquals(RemediationTargetKind.DEPLOYMENT, plan.targetKind());
        assertTrue(plan.autoExecutable());
        assertFalse(plan.approvalRequired());
        assertFalse(plan.dbRelated());
    }

    @Test
    void shouldForceDatabaseActionsToManualRestartPath() {
        IncidentActionRepository repository = mock(IncidentActionRepository.class);
        when(repository.findRecentActionsForTarget(any(), any(), any(), any())).thenReturn(List.of());

        RemediationProperties properties = new RemediationProperties();
        properties.setAutomationEnabled(true);
        properties.setAllowedActions(Set.of(ActionType.RESTART_POD));
        properties.setAllowlistedApps(Set.of("backend"));
        properties.setRestartConfidenceThreshold(0.45d);

        RemediationPolicyService service = new RemediationPolicyService(properties, repository);

        AIOpsAnalyzeRequest request = new AIOpsAnalyzeRequest();
        request.setNamespace("pfe");
        request.setApp("backend");

        AIPredictResponse prediction = new AIPredictResponse();
        prediction.setIncident(true);
        prediction.setIncidentType("DATABASE_CONNECTIVITY");
        prediction.setSeverity("HIGH");
        prediction.setConfidence(0.90d);
        prediction.setRecommendedAction("CHECK_DATABASE_CONNECTION_POOL_AND_RESTART");

        RemediationPlan plan = service.buildPlan(request, prediction);

        assertEquals(ActionType.RESTART_POD, plan.actionType());
        assertEquals(AutomationMode.MANUAL, plan.automationMode());
        assertTrue(plan.approvalRequired());
        assertTrue(plan.manualFollowUp());
        assertTrue(plan.dbRelated());
        assertFalse(plan.autoExecutable());
    }

    @Test
    void shouldEscalateVmOutageWithoutAutoExecution() {
        IncidentActionRepository repository = mock(IncidentActionRepository.class);
        when(repository.findRecentActionsForTarget(any(), any(), any(), any())).thenReturn(List.of());

        RemediationProperties properties = new RemediationProperties();
        properties.setAutomationEnabled(true);
        properties.setAllowedActions(Set.of(ActionType.RESTART_POD));
        properties.setAllowlistedApps(Set.of("backend"));
        properties.setRestartConfidenceThreshold(0.45d);

        RemediationPolicyService service = new RemediationPolicyService(properties, repository);

        AIOpsAnalyzeRequest request = new AIOpsAnalyzeRequest();
        request.setNamespace("pfe");
        request.setApp("backend");

        AIPredictResponse prediction = new AIPredictResponse();
        prediction.setIncident(true);
        prediction.setIncidentType("VM_DOWN");
        prediction.setSeverity("HIGH");
        prediction.setConfidence(0.91d);
        prediction.setRecommendedAction("ESCALATE_VM_OUTAGE_AND_FAILOVER");

        RemediationPlan plan = service.buildPlan(request, prediction);

        assertEquals(ActionType.ESCALATION, plan.actionType());
        assertEquals(AutomationMode.MANUAL, plan.automationMode());
        assertFalse(plan.autoExecutable());
        assertTrue(plan.approvalRequired());
        assertTrue(plan.manualFollowUp());
    }

    @Test
    void shouldMapSpecificScaleAndRollbackRecommendations() {
        IncidentActionRepository repository = mock(IncidentActionRepository.class);
        when(repository.findRecentActionsForTarget(any(), any(), any(), any())).thenReturn(List.of());

        RemediationProperties properties = new RemediationProperties();
        properties.setAutomationEnabled(true);
        properties.setAllowedActions(Set.of(ActionType.RESTART_POD, ActionType.SCALE_DEPLOYMENT));
        properties.setAllowlistedApps(Set.of("backend"));
        properties.setAllowlistedNamespaces(Set.of("pfe"));
        properties.setRestartConfidenceThreshold(0.45d);
        properties.setScaleConfidenceThreshold(0.60d);

        RemediationPolicyService service = new RemediationPolicyService(properties, repository);

        AIOpsAnalyzeRequest request = new AIOpsAnalyzeRequest();
        request.setNamespace("pfe");
        request.setApp("backend");

        AIPredictResponse scalePrediction = new AIPredictResponse();
        scalePrediction.setIncident(true);
        scalePrediction.setIncidentType("AVAILABILITY_DROP");
        scalePrediction.setSeverity("HIGH");
        scalePrediction.setConfidence(0.71d);
        scalePrediction.setRecommendedAction("SCALE_DEPLOYMENT_AND_REBALANCE");

        RemediationPlan scalePlan = service.buildPlan(request, scalePrediction);
        assertEquals(ActionType.SCALE_DEPLOYMENT, scalePlan.actionType());
        assertTrue(scalePlan.autoExecutable());
        assertFalse(scalePlan.approvalRequired());

        AIPredictResponse rollbackPrediction = new AIPredictResponse();
        rollbackPrediction.setIncident(true);
        rollbackPrediction.setIncidentType("RESTART_LOOP");
        rollbackPrediction.setSeverity("HIGH");
        rollbackPrediction.setConfidence(0.66d);
        rollbackPrediction.setRecommendedAction("SHIFT_TRAFFIC_TO_STABLE_REVISION_AND_INVESTIGATE");

        RemediationPlan rollbackPlan = service.buildPlan(request, rollbackPrediction);
        assertEquals(ActionType.RESTART_POD, rollbackPlan.actionType());
        assertFalse(rollbackPlan.manualFollowUp());
        assertTrue(rollbackPlan.autoExecutable());
    }

    @Test
    void shouldMapProfessionalAiActionsExplicitly() {
        IncidentActionRepository repository = mock(IncidentActionRepository.class);
        when(repository.findRecentActionsForTarget(any(), any(), any(), any())).thenReturn(List.of());

        RemediationProperties properties = new RemediationProperties();
        properties.setAutomationEnabled(true);
        properties.setAllowedActions(Set.of(ActionType.RESTART_POD, ActionType.SCALE_DEPLOYMENT));
        properties.setAllowlistedApps(Set.of("backend"));
        properties.setAllowlistedNamespaces(Set.of("pfe"));
        properties.setRestartConfidenceThreshold(0.45d);
        properties.setScaleConfidenceThreshold(0.60d);

        RemediationPolicyService service = new RemediationPolicyService(properties, repository);

        AIOpsAnalyzeRequest request = new AIOpsAnalyzeRequest();
        request.setNamespace("pfe");
        request.setApp("backend");

        AIPredictResponse vmPrediction = new AIPredictResponse();
        vmPrediction.setIncident(true);
        vmPrediction.setIncidentType("VM_DOWN");
        vmPrediction.setSeverity("CRITICAL");
        vmPrediction.setConfidence(0.82d);
        vmPrediction.setRecommendedAction("ESCALATE_NODE_OUTAGE_AND_FAILOVER_WORKLOAD");

        RemediationPlan vmPlan = service.buildPlan(request, vmPrediction);
        assertEquals(ActionType.ESCALATION, vmPlan.actionType());
        assertEquals(AutomationMode.MANUAL, vmPlan.automationMode());

        AIPredictResponse appPrediction = new AIPredictResponse();
        appPrediction.setIncident(true);
        appPrediction.setIncidentType("APPLICATION_ERROR_STORM");
        appPrediction.setSeverity("HIGH");
        appPrediction.setConfidence(0.72d);
        appPrediction.setRecommendedAction("SHIFT_TRAFFIC_TO_STABLE_REVISION_AND_INVESTIGATE");

        RemediationPlan appPlan = service.buildPlan(request, appPrediction);
        assertEquals(ActionType.RESTART_POD, appPlan.actionType());
        assertFalse(appPlan.manualFollowUp());
        assertTrue(appPlan.autoExecutable());

        AIPredictResponse degradationPrediction = new AIPredictResponse();
        degradationPrediction.setIncident(true);
        degradationPrediction.setIncidentType("GENERAL_DEGRADATION");
        degradationPrediction.setSeverity("MEDIUM");
        degradationPrediction.setConfidence(0.51d);
        degradationPrediction.setRecommendedAction("TRACE_DEPENDENCY_LATENCY_AND_INVESTIGATE");

        RemediationPlan degradationPlan = service.buildPlan(request, degradationPrediction);
        assertEquals(ActionType.RESTART_POD, degradationPlan.actionType());
        assertTrue(degradationPlan.manualFollowUp() || degradationPlan.autoExecutable());
    }

    @Test
    void shouldBlockAutomationOutsideAllowlistedNamespace() {
        IncidentActionRepository repository = mock(IncidentActionRepository.class);
        when(repository.findRecentActionsForTarget(any(), any(), any(), any())).thenReturn(List.of());

        RemediationProperties properties = new RemediationProperties();
        properties.setAutomationEnabled(true);
        properties.setAllowedActions(Set.of(ActionType.RESTART_POD, ActionType.SCALE_DEPLOYMENT));
        properties.setAllowlistedApps(Set.of("backend"));
        properties.setAllowlistedNamespaces(Set.of("pfe"));
        properties.setRestartConfidenceThreshold(0.45d);

        RemediationPolicyService service = new RemediationPolicyService(properties, repository);

        AIOpsAnalyzeRequest request = new AIOpsAnalyzeRequest();
        request.setNamespace("demo");
        request.setApp("backend");

        AIPredictResponse prediction = new AIPredictResponse();
        prediction.setIncident(true);
        prediction.setIncidentType("CPU_STRESS");
        prediction.setSeverity("HIGH");
        prediction.setConfidence(0.70d);
        prediction.setRecommendedAction("RESTART_POD_AND_CAPTURE_CPU_PROFILE");

        RemediationPlan plan = service.buildPlan(request, prediction);

        assertEquals(ActionType.RESTART_POD, plan.actionType());
        assertFalse(plan.autoExecutable());
        assertTrue(plan.approvalRequired());
        assertTrue(plan.rationale().contains("namespace"));
    }

    @Test
    void shouldBlockAutoExecutionWhenCooldownActive() {
        IncidentActionRepository repository = mock(IncidentActionRepository.class);
        // Simulate a recent action still within cooldown window
        var fakeAction = mock(com.orange.pfe.backend.model.action.IncidentAction.class);
        when(repository.findRecentActionsForTarget(eq("pfe"), eq("backend"), eq(ActionType.RESTART_POD), any()))
                .thenReturn(List.of(fakeAction));

        RemediationProperties properties = new RemediationProperties();
        properties.setAutomationEnabled(true);
        properties.setAllowedActions(Set.of(ActionType.RESTART_POD));
        properties.setAllowlistedApps(Set.of("backend"));
        properties.setAllowlistedNamespaces(Set.of("pfe"));
        properties.setRestartConfidenceThreshold(0.45d);
        properties.setCooldownMinutes(30);

        RemediationPolicyService service = new RemediationPolicyService(properties, repository);

        AIOpsAnalyzeRequest request = new AIOpsAnalyzeRequest();
        request.setNamespace("pfe");
        request.setApp("backend");

        AIPredictResponse prediction = new AIPredictResponse();
        prediction.setIncident(true);
        prediction.setIncidentType("CPU_STRESS");
        prediction.setSeverity("HIGH");
        prediction.setConfidence(0.80d);
        prediction.setRecommendedAction("RESTART_POD_AND_CAPTURE_CPU_PROFILE");

        RemediationPlan plan = service.buildPlan(request, prediction);

        assertEquals(ActionType.RESTART_POD, plan.actionType());
        assertFalse(plan.autoExecutable());
        assertTrue(plan.cooldownBlocked());
        assertTrue(plan.rationale().toLowerCase().contains("cooldown"));
    }

    @Test
    void shouldRequireApprovalWhenConfidenceBelowThreshold() {
        IncidentActionRepository repository = mock(IncidentActionRepository.class);
        when(repository.findRecentActionsForTarget(any(), any(), any(), any())).thenReturn(List.of());

        RemediationProperties properties = new RemediationProperties();
        properties.setAutomationEnabled(true);
        properties.setAllowedActions(Set.of(ActionType.RESTART_POD));
        properties.setAllowlistedApps(Set.of("backend"));
        properties.setAllowlistedNamespaces(Set.of("pfe"));
        properties.setRestartConfidenceThreshold(0.45d);

        RemediationPolicyService service = new RemediationPolicyService(properties, repository);

        AIOpsAnalyzeRequest request = new AIOpsAnalyzeRequest();
        request.setNamespace("pfe");
        request.setApp("backend");

        AIPredictResponse prediction = new AIPredictResponse();
        prediction.setIncident(true);
        prediction.setIncidentType("CPU_STRESS");
        prediction.setSeverity("HIGH");
        prediction.setConfidence(0.30d);  // below threshold of 0.45
        prediction.setRecommendedAction("RESTART_POD_AND_INVESTIGATE");

        RemediationPlan plan = service.buildPlan(request, prediction);

        assertFalse(plan.autoExecutable());
        assertTrue(plan.approvalRequired());
        assertTrue(plan.rationale().toLowerCase().contains("confidence"));
    }

    @Test
    void shouldKeepUnsafeIdeasAsRecommendationOnlyWithoutApprovalFlow() {
        IncidentActionRepository repository = mock(IncidentActionRepository.class);
        when(repository.findRecentActionsForTarget(any(), any(), any(), any())).thenReturn(List.of());

        RemediationProperties properties = new RemediationProperties();
        properties.setAutomationEnabled(true);
        properties.setAllowedActions(Set.of(ActionType.RESTART_POD));
        properties.setAllowlistedApps(Set.of("backend"));
        properties.setRestartConfidenceThreshold(0.45d);

        RemediationPolicyService service = new RemediationPolicyService(properties, repository);

        AIOpsAnalyzeRequest request = new AIOpsAnalyzeRequest();
        request.setNamespace("pfe");
        request.setApp("backend");

        AIPredictResponse prediction = new AIPredictResponse();
        prediction.setIncident(true);
        prediction.setIncidentType("VM_DOWN");
        prediction.setSeverity("CRITICAL");
        prediction.setConfidence(0.74d);
        prediction.setRecommendedAction("ESCALATE_MULTI_VM_OUTAGE_AND_FREEZE_AUTOMATION");
        prediction.setExecutionMode("RECOMMEND_ONLY");
        prediction.setRecommendationOnlyReason("Recommendation only: critical situations stay under human control.");

        RemediationPlan plan = service.buildPlan(request, prediction);

        assertNull(plan.actionType());
        assertEquals(AutomationMode.MANUAL, plan.automationMode());
        assertTrue(plan.manualFollowUp());
        assertFalse(plan.approvalRequired());
        assertFalse(plan.autoExecutable());
        assertTrue(plan.rationale().contains("Recommendation only"));
    }
}
