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
import org.springframework.test.util.ReflectionTestUtils;
import com.orange.pfe.backend.service.remediation.RemediationPlan;
import com.orange.pfe.backend.service.remediation.RemediationPolicyService;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

class HostCpuIncidentRemediationOrchestratorTest {

    private RemediationProperties properties;
    private RemediationPolicyService policy;
    private ActionService actionService;
    private WorkflowService workflowService;
    private CamundaWorkflowService camundaWorkflowService;
    private IncidentHistoryService historyService;
    private RemediationLaunchFailureService launchFailureService;
    private IncidentRepository incidentRepository;
    private HostCpuIncidentRemediationOrchestrator orchestrator;

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

        orchestrator = new HostCpuIncidentRemediationOrchestrator(
                properties, policy, actionService, workflowService, camundaWorkflowService,
                historyService, launchFailureService, incidentRepository);
    }

    private Incident cpuIncident(String type, String target) {
        Incident incident = new Incident();
        ReflectionTestUtils.setField(incident, "id", 77L);
        when(incidentRepository.findById(77L)).thenReturn(java.util.Optional.of(incident));
        incident.setNamespace("k8s-nodes");
        incident.setApp(target);
        incident.setIncidentType(type);
        incident.setSeverity(IncidentSeverity.HIGH);
        incident.setStatus(IncidentStatus.OPEN);
        return incident;
    }

    private RemediationPlan plan(boolean executable) {
        return new RemediationPlan(
                ActionType.HOST_SERVICE_CPU_QUOTA,
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
                "NODE_HIGH_CPU");
    }

    @Test
    void eligibleCpuIncidentCreatesActionAndStartsCamunda() {
        Incident incident = cpuIncident("NODE_HIGH_CPU", "k8s-worker1");
        RemediationPlan plan = plan(true);
        WorkflowInstance workflow = mock(WorkflowInstance.class);
        IncidentAction action = mock(IncidentAction.class);

        when(policy.buildPlan(any(AIOpsAnalyzeRequest.class), any(AIPredictResponse.class)))
                .thenReturn(plan);
        when(workflowService.startWorkflow(
                incident, "Incident-Remediation-Workflow", "k8s-nodes", "k8s-worker1"))
                .thenReturn(workflow);
        when(actionService.createAction(
                eq(incident),
                eq(ActionType.HOST_SERVICE_CPU_QUOTA),
                anyString(),
                eq("HELIOS-DETERMINISTIC"),
                eq("k8s-nodes"),
                eq(RemediationTargetKind.NODE),
                eq("k8s-worker1"),
                eq(AutomationMode.AUTO)))
                .thenReturn(action);

        when(camundaWorkflowService.startWorkflowWithOutcome(
                eq(workflow), eq(action), eq(plan), any(AIPredictResponse.class), eq(false)))
                .thenReturn(CamundaLaunchOutcome.CAMUNDA_STARTED);

        orchestrator.startIfEligible(incident);

        verify(actionService).createAction(
                eq(incident), eq(ActionType.HOST_SERVICE_CPU_QUOTA), anyString(),
                eq("HELIOS-DETERMINISTIC"), eq("k8s-nodes"),
                eq(RemediationTargetKind.NODE), eq("k8s-worker1"), eq(AutomationMode.AUTO));
        verify(camundaWorkflowService).startWorkflowWithOutcome(
                eq(workflow), eq(action), eq(plan), any(AIPredictResponse.class), eq(false));
        assertEquals(IncidentStatus.IN_PROGRESS, incident.getStatus());
    }

    @Test
    void policyRejectionNeverStartsWorkflowOrCreatesAction() {
        Incident incident = cpuIncident("NODE_HIGH_CPU", "k8s-worker1");

        when(policy.buildPlan(any(AIOpsAnalyzeRequest.class), any(AIPredictResponse.class)))
                .thenReturn(plan(false));

        orchestrator.startIfEligible(incident);

        verifyNoInteractions(workflowService, actionService, camundaWorkflowService);
        assertEquals(IncidentStatus.OPEN, incident.getStatus());
        verify(historyService).record(
                eq(incident), eq(IncidentStatus.OPEN), contains("non demarree par la politique"));
    }

    @Test
    void targetOutsideCpuAllowlistIsRejected() {
        // k8s-master est désormais autorisé (CPU-MASTER-20261029) ; on prouve ici le refus d'une
        // cible RÉELLEMENT hors allowlist (ni worker1/2 ni master).
        Incident incident = cpuIncident("NODE_HIGH_CPU", "k8s-worker999");

        orchestrator.startIfEligible(incident);

        verifyNoInteractions(policy, workflowService, actionService, camundaWorkflowService);
        assertEquals(IncidentStatus.OPEN, incident.getStatus());
    }

    @Test
    void masterCpuIncidentNowCreatesActionAndStartsCamunda() {
        // CPU-MASTER-20261029 : k8s-master est une cible CPU autorisée (le quota ne vise que
        // helios-cpuload.service côté Bridge ; etcd/kubelet/apiserver protégés). Le cycle démarre.
        Incident incident = cpuIncident("NODE_HIGH_CPU", "k8s-master");
        RemediationPlan plan = plan(true);
        WorkflowInstance workflow = mock(WorkflowInstance.class);
        IncidentAction action = mock(IncidentAction.class);

        when(policy.buildPlan(any(AIOpsAnalyzeRequest.class), any(AIPredictResponse.class)))
                .thenReturn(plan);
        when(workflowService.startWorkflow(
                incident, "Incident-Remediation-Workflow", "k8s-nodes", "k8s-master"))
                .thenReturn(workflow);
        when(actionService.createAction(
                eq(incident), eq(ActionType.HOST_SERVICE_CPU_QUOTA), anyString(),
                eq("HELIOS-DETERMINISTIC"), eq("k8s-nodes"),
                eq(RemediationTargetKind.NODE), eq("k8s-master"), eq(AutomationMode.AUTO)))
                .thenReturn(action);
        when(camundaWorkflowService.startWorkflowWithOutcome(
                eq(workflow), eq(action), eq(plan), any(AIPredictResponse.class), eq(false)))
                .thenReturn(CamundaLaunchOutcome.CAMUNDA_STARTED);

        orchestrator.startIfEligible(incident);

        verify(actionService).createAction(
                eq(incident), eq(ActionType.HOST_SERVICE_CPU_QUOTA), anyString(),
                eq("HELIOS-DETERMINISTIC"), eq("k8s-nodes"),
                eq(RemediationTargetKind.NODE), eq("k8s-master"), eq(AutomationMode.AUTO));
        assertEquals(IncidentStatus.IN_PROGRESS, incident.getStatus());
    }

    @Test
    void camundaLaunchFailureIsRecorded() {
        Incident incident = cpuIncident("NODE_HIGH_CPU", "k8s-worker1");
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
                eq(incident), eq(ActionType.HOST_SERVICE_CPU_QUOTA), anyString(),
                eq("HELIOS-DETERMINISTIC"), eq("k8s-nodes"),
                eq(RemediationTargetKind.NODE), eq("k8s-worker1"), eq(AutomationMode.AUTO)))
                .thenReturn(action);

        doThrow(new IllegalStateException("Camunda unavailable"))
                .when(camundaWorkflowService)
                .startWorkflowWithOutcome(eq(workflow), eq(action), eq(plan), any(AIPredictResponse.class), eq(false));

        orchestrator.startIfEligible(incident);

        verify(launchFailureService).recordCamundaLaunchFailure(
                eq("CPU"), eq(77L), eq(88L), any(RuntimeException.class));
    }

    @Test
    void disabledFlagNeverCreatesActionOrWorkflow() {
        // CPU-02 : HOST_CPU_REMEDIATION_ENABLED=false => le backend ne cree ni action
        // ni workflow concurrent (proprietaire = Camunda v25). Les tests ci-dessus,
        // au defaut true, prouvent que le comportement historique reste preserve.
        properties.setHostCpuRemediationEnabled(false);
        Incident incident = cpuIncident("NODE_HIGH_CPU", "k8s-worker1");

        orchestrator.startIfEligible(incident);

        verifyNoInteractions(policy, actionService, workflowService, camundaWorkflowService);
        assertEquals(IncidentStatus.OPEN, incident.getStatus());
        verify(historyService).record(
                eq(incident), eq(IncidentStatus.OPEN),
                contains("HOST_CPU_REMEDIATION_ENABLED=false"));
    }
}