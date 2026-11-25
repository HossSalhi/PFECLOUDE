package com.orange.pfe.backend.service;

import com.orange.pfe.backend.config.CamundaProperties;
import com.orange.pfe.backend.config.RemediationProperties;
import com.orange.pfe.backend.dto.AIPredictResponse;
import com.orange.pfe.backend.model.action.ActionType;
import com.orange.pfe.backend.model.action.AutomationMode;
import com.orange.pfe.backend.model.action.IncidentAction;
import com.orange.pfe.backend.model.action.RemediationTargetKind;
import com.orange.pfe.backend.model.core.Incident;
import com.orange.pfe.backend.model.enums.IncidentSeverity;
import com.orange.pfe.backend.model.workflow.WorkflowInstance;
import com.orange.pfe.backend.model.workflow.WorkflowOrchestrationState;
import com.orange.pfe.backend.service.remediation.RemediationExecutor;
import com.orange.pfe.backend.service.remediation.RemediationPlan;
import io.camunda.client.CamundaClient;
import io.camunda.client.api.response.ProcessInstanceEvent;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.ObjectProvider;

import java.util.concurrent.CompletionException;

import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

class CamundaWorkflowServiceNoDuplicateRemediationTest {

    private ObjectProvider<CamundaClient> clientProvider;
    private CamundaClient client;
    private WorkflowService workflowService;
    private RemediationExecutor remediationExecutor;
    private CamundaProperties camundaProperties;
    private RemediationProperties remediationProperties;
    private CamundaWorkflowService service;

    private WorkflowInstance workflow;
    private IncidentAction action;
    private RemediationPlan plan;
    private AIPredictResponse prediction;

    @BeforeEach
    void setUp() {
        clientProvider = mock(ObjectProvider.class);
        client = mock(CamundaClient.class, RETURNS_DEEP_STUBS);
        workflowService = mock(WorkflowService.class);
        remediationExecutor = mock(RemediationExecutor.class);
        camundaProperties = mock(CamundaProperties.class);
        remediationProperties = mock(RemediationProperties.class);

        when(clientProvider.getIfAvailable()).thenReturn(client);
        when(camundaProperties.isEnabled()).thenReturn(true);
        when(camundaProperties.getProcessId()).thenReturn("incident-remediation");

        service = new CamundaWorkflowService(
                clientProvider,
                camundaProperties,
                remediationProperties,
                workflowService,
                remediationExecutor
        );

        Incident incident = mock(Incident.class);
        when(incident.getId()).thenReturn(501L);
        when(incident.getSeverity()).thenReturn(IncidentSeverity.HIGH);

        workflow = mock(WorkflowInstance.class);
        when(workflow.getId()).thenReturn(601L);
        when(workflow.getCorrelationKey()).thenReturn("601");
        when(workflow.getIncident()).thenReturn(incident);
        when(workflow.getCamundaProcessInstanceKey()).thenReturn(null);

        action = mock(IncidentAction.class);
        when(action.getId()).thenReturn(701L);
        when(action.getType()).thenReturn(ActionType.HOST_SERVICE_CPU_QUOTA);

        plan = new RemediationPlan(
                ActionType.HOST_SERVICE_CPU_QUOTA,
                AutomationMode.AUTO,
                false,
                true,
                false,
                false,
                false,
                "k8s-nodes",
                RemediationTargetKind.NODE,
                "k8s-worker1",
                "test plan",
                "NODE_HIGH_CPU"
        );

        prediction = new AIPredictResponse();
        prediction.setIncidentType("NODE_HIGH_CPU");
    }

    @Test
    void persistenceFailureAfterConfirmedCamundaCreationNeverRunsDirectRemediation() {
        ProcessInstanceEvent event = mock(ProcessInstanceEvent.class);
        when(event.getBpmnProcessId()).thenReturn("incident-remediation");
        when(event.getProcessInstanceKey()).thenReturn(987L);

        when(client.newCreateInstanceCommand()
                .bpmnProcessId(anyString())
                .latestVersion()
                .businessId(anyString())
                .variables(anyMap())
                .send()
                .join()
        ).thenReturn(event);

        doThrow(new IllegalStateException("simulated persistence failure"))
                .when(workflowService)
                .attachCamundaProcess(eq(workflow), anyString(), eq("987"));

        org.junit.jupiter.api.Assertions.assertEquals(
                CamundaLaunchOutcome.MANUAL_FOLLOW_UP_REQUIRED,
                service.startWorkflowWithOutcome(workflow, action, plan, prediction, true));

        verify(workflowService).attachCamundaProcess(
                eq(workflow), anyString(), eq("987"));
        verify(workflowService).addWorkflowLog(
                eq(601L), contains("no direct fallback attempted"));
        verify(workflowService).updateOrchestrationState(
                eq(workflow),
                eq(WorkflowOrchestrationState.MANUAL_FOLLOW_UP),
                contains("local state persistence failed"));
        verify(remediationExecutor, never()).execute(any(RemediationPlan.class));
    }

    @Test
    void uncertainCamundaCreationNeverRunsDirectRemediation() {
        when(client.newCreateInstanceCommand()
                .bpmnProcessId(anyString())
                .latestVersion()
                .businessId(anyString())
                .variables(anyMap())
                .send()
                .join()
        ).thenThrow(new CompletionException(
                "simulated uncertain creation outcome",
                new IllegalStateException("simulated communication failure")
        ));

        org.junit.jupiter.api.Assertions.assertEquals(
                CamundaLaunchOutcome.MANUAL_FOLLOW_UP_REQUIRED,
                service.startWorkflowWithOutcome(workflow, action, plan, prediction, true));

        verify(remediationExecutor, never()).execute(any(RemediationPlan.class));
        verify(workflowService).addWorkflowLog(
                eq(601L), contains("direct fallback suppressed"));
        verify(workflowService).updateOrchestrationState(
                eq(workflow),
                eq(WorkflowOrchestrationState.MANUAL_FOLLOW_UP),
                contains("outcome uncertain")
        );
        verify(workflowService, never())
                .attachCamundaProcess(any(), anyString(), anyString());
    }

@Test
    void enabledButUnreachableCamundaNeverRunsDirectFallback() {
        when(client.newTopologyRequest()
                .requestTimeout(any(java.time.Duration.class))
                .send()
                .join()
        ).thenThrow(new CompletionException(
                "simulated topology failure",
                new IllegalStateException("simulated Zeebe unavailable")
        ));

        org.junit.jupiter.api.Assertions.assertEquals(
                CamundaLaunchOutcome.LAUNCH_FAILED,
                service.startWorkflowWithOutcome(workflow, action, plan, prediction, true)
        );

        verify(remediationExecutor, never()).execute(any(RemediationPlan.class));
        verify(client, never()).newCreateInstanceCommand();
        verify(workflowService).updateOrchestrationState(
                eq(workflow),
                eq(WorkflowOrchestrationState.MANUAL_FOLLOW_UP),
                contains("direct fallback suppressed")
        );
    }
}