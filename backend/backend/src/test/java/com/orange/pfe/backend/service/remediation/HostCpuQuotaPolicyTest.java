package com.orange.pfe.backend.service.remediation;

import com.orange.pfe.backend.config.RemediationProperties;
import com.orange.pfe.backend.dto.AIOpsAnalyzeRequest;
import com.orange.pfe.backend.dto.AIPredictResponse;
import com.orange.pfe.backend.model.action.ActionType;
import com.orange.pfe.backend.model.action.AutomationMode;
import com.orange.pfe.backend.model.action.RemediationTargetKind;
import com.orange.pfe.backend.repository.IncidentActionRepository;
import java.util.List;
import java.util.Set;
import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

class HostCpuQuotaPolicyTest {

    private RemediationProperties properties(boolean bridgeEnabled) {
        RemediationProperties p = new RemediationProperties();
        p.setAutomationEnabled(true);
        p.setBridgeEnabled(bridgeEnabled);
        p.setAllowedActions(Set.of(ActionType.HOST_SERVICE_CPU_QUOTA));
        p.setAllowlistedApps(Set.of("k8s-worker1"));
        p.setAllowlistedNamespaces(Set.of("k8s-nodes"));
        p.setRestartConfidenceThreshold(0.45d);
        return p;
    }

    private RemediationPlan plan(boolean bridgeEnabled, String type) {
        return plan(bridgeEnabled, type, "HIGH");
    }

    // severity = valeur telle que résolue par HostIncidentIngestionService.parseSeverity.
    // Un NODE_HIGH_CPU réel : le détecteur classe "warning" (PAS "critical") -> parseSeverity -> MEDIUM
    // (IncidentSeverity n'a pas de WARNING) ; jamais CRITICAL, contrairement au DISK.
    private RemediationPlan plan(boolean bridgeEnabled, String type, String severity) {
        IncidentActionRepository repository = mock(IncidentActionRepository.class);
        when(repository.findRecentActionsForTarget(any(), any(), any(), any()))
                .thenReturn(List.of());

        RemediationPolicyService service =
                new RemediationPolicyService(properties(bridgeEnabled), repository);

        AIOpsAnalyzeRequest request = new AIOpsAnalyzeRequest();
        request.setNamespace("k8s-nodes");
        request.setApp("k8s-worker1");

        AIPredictResponse prediction = new AIPredictResponse();
        prediction.setIncident(true);
        prediction.setIncidentType(type);
        prediction.setSeverity(severity);
        prediction.setConfidence(0.95d);
        prediction.setRecommendedAction("RESTART_OR_SCALE");

        return service.buildPlan(request, prediction);
    }

    @Test
    void highCpuNodeSelectsQuotaActionAndCarriesIncidentType() {
        RemediationPlan plan = plan(true, "NODE_HIGH_CPU");

        assertEquals(ActionType.HOST_SERVICE_CPU_QUOTA, plan.actionType());
        assertEquals("NODE_HIGH_CPU", plan.incidentType());
        assertEquals(RemediationTargetKind.NODE, plan.targetKind());
        assertEquals(AutomationMode.AUTO, plan.automationMode());
        assertTrue(plan.autoExecutable());
        assertFalse(plan.approvalRequired());
    }

    @Test
    void cpuSaturationSelectsTheSameConstrainedAction() {
        RemediationPlan plan = plan(true, "NODE_CPU_SATURATION");

        assertEquals(ActionType.HOST_SERVICE_CPU_QUOTA, plan.actionType());
        assertEquals("NODE_CPU_SATURATION", plan.incidentType());
        assertTrue(plan.autoExecutable());
    }

    @Test
    void disabledBridgePreventsAutomaticCpuExecution() {
        RemediationPlan plan = plan(false, "NODE_HIGH_CPU");

        assertEquals(ActionType.HOST_SERVICE_CPU_QUOTA, plan.actionType());
        assertFalse(plan.autoExecutable());
        assertTrue(plan.approvalRequired());
        assertTrue(plan.manualFollowUp());
        assertEquals(AutomationMode.MANUAL, plan.automationMode());
    }

    @Test
    void mediumSeverityCpuIncidentRemainsAutoExecutable() {
        // Sévérité EFFECTIVE d'un NODE_HIGH_CPU réel : détecteur "warning" -> MEDIUM (cf. plan()).
        // MEDIUM est éligible (≠ CRITICAL) -> la remédiation CPU reste auto-exécutable.
        RemediationPlan plan = plan(true, "NODE_HIGH_CPU", "MEDIUM");

        assertEquals(ActionType.HOST_SERVICE_CPU_QUOTA, plan.actionType());
        assertTrue(plan.autoExecutable());
        assertFalse(plan.approvalRequired());
        assertEquals(AutomationMode.AUTO, plan.automationMode());
    }

    @Test
    void criticalSeverityCpuIncidentIsForcedToManual() {
        // Garde-fou de sévérité : CRITICAL force le mode manuel pour le CPU — il n'existe AUCUN bypass
        // CPU (contrairement à isDiskAutoRemediationEligible pour le DISK). Épingle le contrat pour
        // qu'une sévérité CRITICAL (p. ex. si l'agent la renvoyait) ne passe jamais en auto en silence.
        RemediationPlan plan = plan(true, "NODE_HIGH_CPU", "CRITICAL");

        assertEquals(ActionType.HOST_SERVICE_CPU_QUOTA, plan.actionType());
        assertFalse(plan.autoExecutable());
        assertTrue(plan.approvalRequired());
        assertEquals(AutomationMode.MANUAL, plan.automationMode());
    }
    @Test
    void cooldownBlocksRepeatedCpuQuotaForSameNodeAndIncidentType() {
        IncidentActionRepository repository = mock(IncidentActionRepository.class);

        when(repository.findRecentActionsForTarget(any(), any(), any(), any()))
                .thenReturn(List.of(
                        mock(com.orange.pfe.backend.model.action.IncidentAction.class)));

        when(repository.findLastIncidentTypeForTarget(any(), any()))
                .thenReturn("NODE_HIGH_CPU");

        RemediationProperties p = properties(true);
        p.setCooldownMinutes(30);

        RemediationPolicyService service =
                new RemediationPolicyService(p, repository);

        AIOpsAnalyzeRequest request = new AIOpsAnalyzeRequest();
        request.setNamespace("k8s-nodes");
        request.setApp("k8s-worker1");

        AIPredictResponse prediction = new AIPredictResponse();
        prediction.setIncident(true);
        prediction.setIncidentType("NODE_HIGH_CPU");
        prediction.setSeverity("MEDIUM");
        prediction.setConfidence(0.95d);
        prediction.setRecommendedAction("RESTART_OR_SCALE");

        RemediationPlan plan = service.buildPlan(request, prediction);

        assertEquals(ActionType.HOST_SERVICE_CPU_QUOTA, plan.actionType());
        assertFalse(plan.autoExecutable());
        assertTrue(plan.cooldownBlocked());
        assertTrue(plan.rationale().toLowerCase().contains("cooldown"));
    }
}