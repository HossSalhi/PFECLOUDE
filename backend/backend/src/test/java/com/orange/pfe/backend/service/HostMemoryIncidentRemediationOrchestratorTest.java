package com.orange.pfe.backend.service;

import com.orange.pfe.backend.config.RemediationProperties;
import com.orange.pfe.backend.dto.AIOpsAnalyzeRequest;
import com.orange.pfe.backend.dto.AIPredictResponse;
import com.orange.pfe.backend.model.action.ActionType;
import com.orange.pfe.backend.model.action.AutomationMode;
import com.orange.pfe.backend.model.action.IncidentAction;
import com.orange.pfe.backend.model.action.RemediationTargetKind;
import com.orange.pfe.backend.model.core.Incident;
import com.orange.pfe.backend.model.enums.IncidentSeverity;
import com.orange.pfe.backend.model.enums.IncidentStatus;
import com.orange.pfe.backend.model.workflow.WorkflowInstance;
import com.orange.pfe.backend.repository.IncidentRepository;
import com.orange.pfe.backend.service.remediation.RemediationPlan;
import com.orange.pfe.backend.service.remediation.RemediationPolicyService;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.test.util.ReflectionTestUtils;

import java.util.Optional;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

/**
 * RAM (Étape C+) — orchestrateur mémoire, MODÈLE CPU. Prouve :
 *  - remédiation RÉELLE (action + workflow Camunda) uniquement quand le drapeau est actif ET la politique
 *    produit un plan HOST_SERVICE_MEMORY_LIMIT auto-exécutable ;
 *  - ESCALADE déterministe fail-closed sinon (drapeau faux, cible hors allowlist, politique non satisfaite) ;
 *  - aucune action/aucun workflow fabriqué ; incident laissé OUVERT ; échec de lancement tracé.
 * Aucun appel réseau/Bridge/IA : dépendances mockées.
 */
class HostMemoryIncidentRemediationOrchestratorTest {

    private RemediationProperties properties;
    private RemediationPolicyService policy;
    private ActionService actionService;
    private WorkflowService workflowService;
    private CamundaWorkflowService camundaWorkflowService;
    private IncidentHistoryService historyService;
    private RemediationLaunchFailureService launchFailureService;
    private IncidentRepository incidentRepository;
    private HostMemoryIncidentRemediationOrchestrator orchestrator;

    @BeforeEach
    void setUp() {
        properties = new RemediationProperties();
        policy = mock(RemediationPolicyService.class);
        actionService = mock(ActionService.class);
        workflowService = mock(WorkflowService.class);
        camundaWorkflowService = mock(CamundaWorkflowService.class);
        historyService = mock(IncidentHistoryService.class);
        launchFailureService = mock(RemediationLaunchFailureService.class);
        incidentRepository = mock(IncidentRepository.class);

        orchestrator = new HostMemoryIncidentRemediationOrchestrator(
                properties, policy, actionService, workflowService, camundaWorkflowService,
                historyService, launchFailureService, incidentRepository);
    }

    private Incident memoryIncident(String type, String target) {
        Incident incident = new Incident();
        ReflectionTestUtils.setField(incident, "id", 77L);
        when(incidentRepository.findById(77L)).thenReturn(Optional.of(incident));
        incident.setNamespace("k8s-nodes");
        incident.setApp(target);
        incident.setIncidentType(type);
        incident.setSeverity(IncidentSeverity.HIGH);
        incident.setStatus(IncidentStatus.OPEN);
        return incident;
    }

    private RemediationPlan plan(boolean executable) {
        return new RemediationPlan(
                ActionType.HOST_SERVICE_MEMORY_LIMIT,
                AutomationMode.AUTO,
                !executable,
                executable,
                !executable,
                false,
                false,
                "k8s-nodes",
                RemediationTargetKind.NODE,
                "k8s-worker1",
                executable ? "policy accepted" : "bridge or policy gate blocked",
                "NODE_HIGH_MEMORY");
    }

    @Test
    void eligibleMemoryIncidentCreatesActionAndStartsCamunda_whenEnabled() {
        properties.setHostMemoryRemediationEnabled(true);
        Incident incident = memoryIncident("NODE_HIGH_MEMORY", "k8s-worker1");
        RemediationPlan plan = plan(true);
        WorkflowInstance workflow = mock(WorkflowInstance.class);
        IncidentAction action = mock(IncidentAction.class);

        when(policy.buildPlan(any(AIOpsAnalyzeRequest.class), any(AIPredictResponse.class)))
                .thenReturn(plan);
        when(workflowService.startWorkflow(
                incident, "Incident-Remediation-Workflow", "k8s-nodes", "k8s-worker1"))
                .thenReturn(workflow);
        when(actionService.createAction(
                eq(incident), eq(ActionType.HOST_SERVICE_MEMORY_LIMIT), anyString(),
                eq("HELIOS-DETERMINISTIC"), eq("k8s-nodes"),
                eq(RemediationTargetKind.NODE), eq("k8s-worker1"), eq(AutomationMode.AUTO)))
                .thenReturn(action);
        when(camundaWorkflowService.startWorkflowWithOutcome(
                eq(workflow), eq(action), eq(plan), any(AIPredictResponse.class), eq(false)))
                .thenReturn(CamundaLaunchOutcome.CAMUNDA_STARTED);

        orchestrator.startIfEligible(incident);

        verify(actionService).createAction(
                eq(incident), eq(ActionType.HOST_SERVICE_MEMORY_LIMIT), anyString(),
                eq("HELIOS-DETERMINISTIC"), eq("k8s-nodes"),
                eq(RemediationTargetKind.NODE), eq("k8s-worker1"), eq(AutomationMode.AUTO));
        verify(camundaWorkflowService).startWorkflowWithOutcome(
                eq(workflow), eq(action), eq(plan), any(AIPredictResponse.class), eq(false));
        assertEquals(IncidentStatus.IN_PROGRESS, incident.getStatus());
    }

    @Test
    void disabledFlagEscalatesWithoutActionOrWorkflow() {
        // Défaut false => fail-closed : escalade, aucune action/aucun workflow.
        Incident incident = memoryIncident("NODE_HIGH_MEMORY", "k8s-worker1");

        orchestrator.startIfEligible(incident);

        verifyNoInteractions(policy, actionService, workflowService, camundaWorkflowService);
        assertEquals(IncidentStatus.OPEN, incident.getStatus());
        verify(historyService).record(
                eq(incident), eq(IncidentStatus.OPEN),
                contains("HOST_MEMORY_REMEDIATION_ENABLED=false"));
    }

    @Test
    void policyRejectionEscalatesWithoutActionOrWorkflow() {
        properties.setHostMemoryRemediationEnabled(true);
        Incident incident = memoryIncident("NODE_HIGH_MEMORY", "k8s-worker1");
        when(policy.buildPlan(any(AIOpsAnalyzeRequest.class), any(AIPredictResponse.class)))
                .thenReturn(plan(false));

        orchestrator.startIfEligible(incident);

        verifyNoInteractions(workflowService, actionService, camundaWorkflowService);
        assertEquals(IncidentStatus.OPEN, incident.getStatus());
        verify(historyService).record(
                eq(incident), eq(IncidentStatus.OPEN), contains("politique non satisfaite"));
    }

    @Test
    void targetOutsideAllowlistEscalates() {
        properties.setHostMemoryRemediationEnabled(true);
        Incident incident = memoryIncident("NODE_HIGH_MEMORY", "k8s-master");

        orchestrator.startIfEligible(incident);

        verifyNoInteractions(policy, workflowService, actionService, camundaWorkflowService);
        assertEquals(IncidentStatus.OPEN, incident.getStatus());
        verify(historyService).record(
                eq(incident), eq(IncidentStatus.OPEN), contains("allowlist"));
    }

    @Test
    void nonMemoryIncidentIsIgnored() {
        properties.setHostMemoryRemediationEnabled(true);
        Incident incident = memoryIncident("NODE_HIGH_CPU", "k8s-worker1");

        orchestrator.startIfEligible(incident);

        verifyNoInteractions(
                policy, workflowService, actionService, camundaWorkflowService, historyService);
        assertEquals(IncidentStatus.OPEN, incident.getStatus());
    }

    @Test
    void camundaLaunchFailureIsRecorded() {
        properties.setHostMemoryRemediationEnabled(true);
        Incident incident = memoryIncident("NODE_HIGH_MEMORY", "k8s-worker1");
        RemediationPlan plan = plan(true);
        WorkflowInstance workflow = mock(WorkflowInstance.class);
        IncidentAction action = mock(IncidentAction.class);
        when(action.getId()).thenReturn(88L);

        when(policy.buildPlan(any(AIOpsAnalyzeRequest.class), any(AIPredictResponse.class)))
                .thenReturn(plan);
        when(workflowService.startWorkflow(
                incident, "Incident-Remediation-Workflow", "k8s-nodes", "k8s-worker1"))
                .thenReturn(workflow);
        when(actionService.createAction(
                eq(incident), eq(ActionType.HOST_SERVICE_MEMORY_LIMIT), anyString(),
                eq("HELIOS-DETERMINISTIC"), eq("k8s-nodes"),
                eq(RemediationTargetKind.NODE), eq("k8s-worker1"), eq(AutomationMode.AUTO)))
                .thenReturn(action);
        doThrow(new IllegalStateException("Camunda unavailable"))
                .when(camundaWorkflowService)
                .startWorkflowWithOutcome(eq(workflow), eq(action), eq(plan), any(AIPredictResponse.class), eq(false));

        orchestrator.startIfEligible(incident);

        verify(launchFailureService).recordCamundaLaunchFailure(
                eq("RAM"), eq(77L), eq(88L), any(RuntimeException.class));
    }
}
