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

class HostDiskIncidentRemediationOrchestratorTest {

    private RemediationProperties properties;
    private RemediationPolicyService policy;
    private ActionService actionService;
    private WorkflowService workflowService;
    private CamundaWorkflowService camundaWorkflowService;
    private IncidentHistoryService historyService;
    private RemediationLaunchFailureService launchFailureService;
    private IncidentRepository incidentRepository;
    private HostDiskIncidentRemediationOrchestrator orchestrator;

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

        orchestrator = new HostDiskIncidentRemediationOrchestrator(
                properties,
                policy,
                actionService,
                workflowService,
                camundaWorkflowService,
                historyService,
                launchFailureService,
                incidentRepository);
    }

    private Incident diskIncident(String type, String target) {
        Incident incident = new Incident();
        ReflectionTestUtils.setField(incident, "id", 77L);
        incident.setNamespace("k8s-nodes");
        incident.setApp(target);
        incident.setIncidentType(type);
        incident.setSeverity(IncidentSeverity.HIGH);
        incident.setStatus(IncidentStatus.OPEN);
        when(incidentRepository.findById(77L)).thenReturn(Optional.of(incident));
        return incident;
    }

    private RemediationPlan plan(boolean executable) {
        return new RemediationPlan(
                ActionType.HOST_RECLAIM_DISK,
                AutomationMode.AUTO,
                !executable,
                executable,
                !executable,
                false,
                false,
                "k8s-nodes",
                RemediationTargetKind.NODE,
                "k8s-worker1",
                executable ? "policy accepted" : "feature or policy blocked",
                "NODE_DISK_FULL");
    }

    @Test
    void disabledFeatureNeverCreatesAnActionOrWorkflow() {
        Incident incident = diskIncident("NODE_DISK_FULL", "k8s-worker1");

        orchestrator.startIfEligible(incident);

        verifyNoInteractions(policy, actionService, workflowService, camundaWorkflowService);
        verify(historyService).record(
                eq(incident), eq(IncidentStatus.OPEN),
                contains("HOST_DISK_E2E_158_ENABLED=false"));
        assertEquals(IncidentStatus.OPEN, incident.getStatus());
    }

    @Test
    void eligibleDiskIncidentCreatesActionAndStartsCamundaWhenExplicitlyEnabled() {
        properties.setHostDiskE2e158Enabled(true);
        Incident incident = diskIncident("NODE_DISK_FULL", "k8s-worker1");
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
                eq(ActionType.HOST_RECLAIM_DISK),
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
                eq(incident), eq(ActionType.HOST_RECLAIM_DISK), anyString(),
                eq("HELIOS-DETERMINISTIC"), eq("k8s-nodes"),
                eq(RemediationTargetKind.NODE), eq("k8s-worker1"), eq(AutomationMode.AUTO));
        verify(camundaWorkflowService).startWorkflowWithOutcome(
                eq(workflow), eq(action), eq(plan), any(AIPredictResponse.class), eq(false));
        assertEquals(IncidentStatus.IN_PROGRESS, incident.getStatus());
    }

    @Test
    void policyRejectionNeverStartsWorkflowOrCreatesAction() {
        properties.setHostDiskE2e158Enabled(true);
        Incident incident = diskIncident("NODE_DISK_FULL", "k8s-worker1");

        when(policy.buildPlan(any(AIOpsAnalyzeRequest.class), any(AIPredictResponse.class)))
                .thenReturn(plan(false));

        orchestrator.startIfEligible(incident);

        verifyNoInteractions(workflowService, actionService, camundaWorkflowService);
        assertEquals(IncidentStatus.OPEN, incident.getStatus());
        verify(historyService).record(
                eq(incident), eq(IncidentStatus.OPEN),
                contains("not started by policy"));
    }

    @Test
    void nonAllowlistedTargetNeverCreatesAnAction() {
        properties.setHostDiskRemediationEnabled(true);
        Incident incident = diskIncident("NODE_DISK_FULL", "k8s-master");

        orchestrator.startIfEligible(incident);

        verifyNoInteractions(policy, workflowService, actionService, camundaWorkflowService);
        assertEquals(IncidentStatus.OPEN, incident.getStatus());
    }

    @Test
    void nonDiskIncidentIsIgnored() {
        properties.setHostDiskRemediationEnabled(true);
        Incident incident = diskIncident("NODE_HIGH_CPU", "k8s-worker1");

        orchestrator.startIfEligible(incident);

        verifyNoInteractions(policy, workflowService, actionService, camundaWorkflowService);
        assertEquals(IncidentStatus.OPEN, incident.getStatus());
    }

    @Test
    void camundaLaunchFailureIsRecordedWhenDiskRemediationIsEnabled() {
        properties.setHostDiskE2e158Enabled(true);
        Incident incident = diskIncident("NODE_DISK_FULL", "k8s-worker1");
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
                eq(incident), eq(ActionType.HOST_RECLAIM_DISK), anyString(),
                eq("HELIOS-DETERMINISTIC"), eq("k8s-nodes"),
                eq(RemediationTargetKind.NODE), eq("k8s-worker1"), eq(AutomationMode.AUTO)))
                .thenReturn(action);

        doThrow(new IllegalStateException("Camunda unavailable"))
                .when(camundaWorkflowService)
                .startWorkflowWithOutcome(eq(workflow), eq(action), eq(plan), any(AIPredictResponse.class), eq(false));

        orchestrator.startIfEligible(incident);

        verify(launchFailureService).recordCamundaLaunchFailure(
                eq("DISK"), eq(77L), eq(88L), any(RuntimeException.class));
    }
}