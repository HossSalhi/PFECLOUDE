package com.orange.pfe.backend.service;

import com.orange.pfe.backend.config.RemediationProperties;
import com.orange.pfe.backend.dto.ExecutionEventRequest;
import com.orange.pfe.backend.dto.TerminalEvidenceResponse;
import com.orange.pfe.backend.dto.IncidentVerificationRequest;
import com.orange.pfe.backend.model.core.Incident;
import com.orange.pfe.backend.model.enums.ExecutionStage;
import com.orange.pfe.backend.model.enums.IncidentStatus;
import com.orange.pfe.backend.model.enums.TerminalEvidencePhase;
import com.orange.pfe.backend.model.workflow.WorkflowInstance;
import com.orange.pfe.backend.model.workflow.WorkflowOrchestrationState;
import com.orange.pfe.backend.model.workflow.WorkflowStatus;
import com.orange.pfe.backend.repository.HumanTaskRepository;
import com.orange.pfe.backend.repository.IncidentActionRepository;
import com.orange.pfe.backend.service.remediation.HostCpuVerificationService;
import com.orange.pfe.backend.service.remediation.HostDiskVerificationService;
import com.orange.pfe.backend.service.remediation.HostMemoryVerificationService;
import com.orange.pfe.backend.service.remediation.NodeReadinessObservation;
import com.orange.pfe.backend.service.remediation.RecoveryVerificationService;
import com.orange.pfe.backend.service.remediation.RemediationExecutor;
import com.orange.pfe.backend.service.remediation.RemediationMetricsService;
import com.orange.pfe.backend.service.vmpower.VmRecoveryVerifier;
import io.camunda.client.CamundaClient;
import io.camunda.client.api.response.ActivatedJob;
import io.camunda.client.api.worker.JobClient;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.mockito.InOrder;
import org.springframework.beans.factory.ObjectProvider;

import java.time.LocalDateTime;
import java.util.Map;

import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

class CamundaJobWorkerVmDownVerificationTest {

    private static final Long WORKFLOW_ID = 77L;
    private static final Long INCIDENT_ID = 42L;
    private static final String NODE = "k8s-worker1";

    private CamundaJobWorkerLifecycle worker;

    private WorkflowService workflowService;
    private IncidentHistoryService incidentHistoryService;
    private IncidentCardService incidentCardService;
    private RemediationExecutor remediationExecutor;
    private RemediationMetricsService metricsService;
    private NotificationService notificationService;

    private WorkflowInstance workflow;
    private Incident incident;

    private JobClient jobClient;
    private ActivatedJob job;

    @SuppressWarnings("unchecked")
    @BeforeEach
    void setUp() {

        workflowService = mock(WorkflowService.class);
        incidentHistoryService = mock(IncidentHistoryService.class);
        incidentCardService = mock(IncidentCardService.class);
        remediationExecutor = mock(RemediationExecutor.class);
        metricsService = mock(RemediationMetricsService.class);
        notificationService = mock(NotificationService.class);

        workflow = mock(WorkflowInstance.class);
        incident = mock(Incident.class);

        jobClient = mock(JobClient.class, RETURNS_DEEP_STUBS);
        job = mock(ActivatedJob.class);

        when(job.getVariablesAsMap()).thenReturn(Map.of(
                "workflowId", WORKFLOW_ID,
                "targetNamespace", "k8s-nodes",
                "targetName", NODE,
                "actionType", "RESTART_VM",
                "incidentType", "VM_DOWN"
        ));

        when(job.getKey()).thenReturn(12345L);
        when(job.getType()).thenReturn("verify-remediation");
        when(job.getRetries()).thenReturn(3);

        when(workflow.getId()).thenReturn(WORKFLOW_ID);
        when(workflow.getStatus()).thenReturn(WorkflowStatus.RUNNING);
        when(workflow.getIncident()).thenReturn(incident);
        when(incident.getId()).thenReturn(INCIDENT_ID);

        when(workflowService.getWorkflowInstance(WORKFLOW_ID))
                .thenReturn(workflow);

        when(workflowService.completeWorkflow(WORKFLOW_ID))
                .thenReturn(workflow);

        ObjectProvider<AIOpsOrchestrationService> orchestrationProvider =
                (ObjectProvider<AIOpsOrchestrationService>) mock(ObjectProvider.class);

        ObjectProvider<InfrastructureHealthScheduler> infraProvider =
                (ObjectProvider<InfrastructureHealthScheduler>) mock(ObjectProvider.class);

        worker = new CamundaJobWorkerLifecycle(
                mock(CamundaClient.class),
                mock(CamundaWorkflowService.class),
                workflowService,
                mock(IncidentActionRepository.class),
                mock(ActionService.class),
                remediationExecutor,
                mock(RecoveryVerificationService.class),
                mock(HostCpuVerificationService.class),
                mock(HostDiskVerificationService.class),
                mock(HostMemoryVerificationService.class),
                metricsService,
                mock(HumanTaskRepository.class),
                mock(HumanTaskService.class),
                mock(RemediationProperties.class),
                incidentHistoryService,
                notificationService,
                orchestrationProvider,
                infraProvider,
                incidentCardService
        ) {
            @Override
            int nodeReadyVerificationMaxAttempts() {
                return 2;
            }

            @Override
            void waitBetweenNodeReadyObservations() {
                // Pas d'attente réelle dans les tests.
                // La valeur de production reste 15 secondes.
            }
        };
    }

    private static NodeReadinessObservation readyObservation() {
        return new NodeReadinessObservation(
                true,
                true,
                "True",
                "Node is Ready"
        );
    }

    private static NodeReadinessObservation notReadyObservation() {
        return new NodeReadinessObservation(
                true,
                false,
                "False",
                "Node is not Ready"
        );
    }

    private static TerminalEvidenceResponse persistedEvidence(
            Long evidenceId,
            int sampleNumber) {

        String measures =
                "{\"workflowId\":77"
                        + ",\"sample\":" + sampleNumber
                        + ",\"apiAvailable\":true"
                        + ",\"ready\":true"
                        + ",\"conditionStatus\":\"True\"}";

        return new TerminalEvidenceResponse(
                evidenceId,
                INCIDENT_ID,
                NODE,
                TerminalEvidencePhase.AFTER,
                LocalDateTime.now(),
                "GET /api/v1/nodes/" + NODE
                        + " [sample=" + sampleNumber + "]",
                200,
                "True",
                null,
                measures,
                true,
                "K8S_API_WF_77"
        );
    }

    private void stubEvidencePersistence() {

        when(incidentCardService.recordNodeReadinessEvidence(
                eq(INCIDENT_ID),
                eq(WORKFLOW_ID),
                eq(NODE),
                anyInt(),
                any(LocalDateTime.class),
                any(NodeReadinessObservation.class)
        )).thenReturn(
                persistedEvidence(101L, 1),
                persistedEvidence(102L, 2)
        );
    }

    @Test
    void twoReadyObservations_recordDedicatedPass_beforeResolved() {

        when(remediationExecutor.inspectNodeReadiness(NODE))
                .thenReturn(readyObservation(), readyObservation());

        stubEvidencePersistence();

        when(incidentCardService.hasTwoConsecutiveNodeReadyEvidence(
                INCIDENT_ID,
                WORKFLOW_ID,
                NODE
        )).thenReturn(true);
        // VMDOWN-POLICY-20261105 (R5) : le PASS enregistré ci-dessous est relu en base avant tout RESOLVED.
        when(incidentCardService.hasPassVerification(INCIDENT_ID)).thenReturn(true);

        worker.handleVerifyRemediation(jobClient, job);

        verify(remediationExecutor, times(2))
                .inspectNodeReadiness(NODE);

        verify(incidentCardService, times(2))
                .recordNodeReadinessEvidence(
                        eq(INCIDENT_ID),
                        eq(WORKFLOW_ID),
                        eq(NODE),
                        anyInt(),
                        any(LocalDateTime.class),
                        any(NodeReadinessObservation.class)
                );

        InOrder order = inOrder(
                incidentCardService,
                incidentHistoryService,
                workflowService
        );

        order.verify(incidentCardService)
                .recordNodeRecoveryVerification(
                        eq(INCIDENT_ID),
                        eq(WORKFLOW_ID),
                        eq(NODE),
                        anyString()

                );

        order.verify(incidentCardService)
                .recordExecutionEvent(
                        eq(INCIDENT_ID),
                        any(ExecutionEventRequest.class)
                );

        order.verify(incidentHistoryService)
                .record(
                        eq(incident),
                        eq(IncidentStatus.RESOLVED),
                        anyString()
                );

        order.verify(workflowService)
                .completeWorkflow(WORKFLOW_ID);

        verify(jobClient).newCompleteCommand(job);
    }

    /**
     * VMDOWN-POLICY-20261105 (R5) — garde de preuves : même avec deux observations Ready, un RESOLVED est REFUSÉ si
     * le verdict PASS n'est pas retrouvé en base (hasPassVerification=false par défaut du mock).
     */
    @Test
    void resolvedRefused_whenThePassVerdictIsNotPersisted() {

        when(remediationExecutor.inspectNodeReadiness(NODE))
                .thenReturn(readyObservation(), readyObservation());
        stubEvidencePersistence();
        when(incidentCardService.hasTwoConsecutiveNodeReadyEvidence(INCIDENT_ID, WORKFLOW_ID, NODE))
                .thenReturn(true);

        worker.handleVerifyRemediation(jobClient, job);

        verify(workflowService, never()).completeWorkflow(WORKFLOW_ID);
        verify(notificationService, never()).notifyIncidentResolvedWithTrace(any(), anyList());
    }

    @Test
    void notReadyObservation_preventsPass_andResolved() {

        when(remediationExecutor.inspectNodeReadiness(NODE))
                .thenReturn(
                        readyObservation(),
                        notReadyObservation()
                );

        stubEvidencePersistence();

        worker.handleVerifyRemediation(jobClient, job);

        verify(remediationExecutor, times(2))
                .inspectNodeReadiness(NODE);

        verify(incidentCardService, never())
                .recordNodeRecoveryVerification(
                        anyLong(),
                        anyLong(),
                        anyString(),
                        anyString()
                );

        verify(incidentCardService)
                .recordVerification(
                        eq(INCIDENT_ID),
                        any(IncidentVerificationRequest.class)
                );

        verify(incidentCardService)
                .recordExecutionEvent(
                        eq(INCIDENT_ID),
                        any(ExecutionEventRequest.class)
                );

        verify(incidentHistoryService)
                .record(
                        eq(incident),
                        eq(IncidentStatus.RESOLUTION_FAILED),
                        anyString()
                );

        verify(workflowService, never())
                .completeWorkflow(WORKFLOW_ID);

        verify(jobClient).newCompleteCommand(job);
    }

    /**
     * Rallumage automatique d'un worker : « Ready » ne suffit pas. Si une preuve complémentaire échoue (kubelet, SSH,
     * etcd, CNPG, backend), la lecture EFFECTIVE du nœud est « non prête » : la preuve persistée reflète ce verdict
     * complet, aucun PASS n'est produit, l'incident passe RESOLUTION_FAILED et le workflow n'est jamais complété.
     */
    @Test
    void readyButComplementaryProofsFail_preventsPass_andResolved() {

        VmRecoveryVerifier verifier = mock(VmRecoveryVerifier.class);
        worker.setVmRecovery(verifier, null);
        when(remediationExecutor.inspectNodeReadiness(NODE))
                .thenReturn(readyObservation(), readyObservation());
        when(verifier.effectiveWorkerObservation(any(), any())).thenReturn(new NodeReadinessObservation(
                true, false, "PROOFS_FAILED",
                "Node k8s-worker1 Ready=True ; k8s-worker1 : critères en échec [cnpg_healthy]"));
        stubEvidencePersistence();

        worker.handleVerifyRemediation(jobClient, job);

        verify(verifier, times(2)).effectiveWorkerObservation(
                argThat(entry -> entry != null && NODE.equals(entry.name())), any(NodeReadinessObservation.class));
        org.mockito.ArgumentCaptor<NodeReadinessObservation> persisted =
                org.mockito.ArgumentCaptor.forClass(NodeReadinessObservation.class);
        verify(incidentCardService, times(2)).recordNodeReadinessEvidence(
                eq(INCIDENT_ID), eq(WORKFLOW_ID), eq(NODE), anyInt(), any(LocalDateTime.class), persisted.capture());
        org.junit.jupiter.api.Assertions.assertFalse(persisted.getAllValues().get(0).ready(),
                "la preuve persistée porte le verdict COMPLET, pas le seul Ready");
        org.junit.jupiter.api.Assertions.assertEquals("PROOFS_FAILED", persisted.getAllValues().get(0).conditionStatus());
        verify(incidentCardService, never()).recordNodeRecoveryVerification(
                anyLong(), anyLong(), anyString(), anyString());
        verify(incidentHistoryService).record(eq(incident), eq(IncidentStatus.RESOLUTION_FAILED), anyString());
        verify(workflowService, never()).completeWorkflow(WORKFLOW_ID);
    }

    @Test
    void readyAndComplementaryProofsPass_recordsPass_throughTheEffectiveObservation() {

        VmRecoveryVerifier verifier = mock(VmRecoveryVerifier.class);
        worker.setVmRecovery(verifier, null);
        when(remediationExecutor.inspectNodeReadiness(NODE))
                .thenReturn(readyObservation(), readyObservation());
        when(verifier.effectiveWorkerObservation(any(), any())).thenAnswer(inv -> inv.getArgument(1));
        stubEvidencePersistence();
        when(incidentCardService.hasTwoConsecutiveNodeReadyEvidence(INCIDENT_ID, WORKFLOW_ID, NODE)).thenReturn(true);
        when(incidentCardService.hasPassVerification(INCIDENT_ID)).thenReturn(true);

        worker.handleVerifyRemediation(jobClient, job);

        verify(verifier, times(2)).effectiveWorkerObservation(any(), any());
        verify(incidentCardService).recordNodeRecoveryVerification(
                eq(INCIDENT_ID), eq(WORKFLOW_ID), eq(NODE), anyString());
        verify(workflowService).completeWorkflow(WORKFLOW_ID);
    }

    @Test
    void evidencePersistenceFailure_failsCamundaJob_withoutResolved() {

        when(remediationExecutor.inspectNodeReadiness(NODE))
                .thenReturn(readyObservation());

        doThrow(new IllegalStateException("Simulated database failure"))
                .when(incidentCardService)
                .recordNodeReadinessEvidence(
                        eq(INCIDENT_ID),
                        eq(WORKFLOW_ID),
                        eq(NODE),
                        eq(1),
                        any(LocalDateTime.class),
                        any(NodeReadinessObservation.class)
                );

        worker.handleVerifyRemediation(jobClient, job);

        verify(incidentCardService, never())
                .recordNodeRecoveryVerification(
                        anyLong(),
                        anyLong(),
                        anyString(),
                        anyString()
                );

        verify(incidentCardService, never())
                .recordExecutionEvent(
                        anyLong(),
                        any(ExecutionEventRequest.class)
                );

        verify(incidentHistoryService, never())
                .record(
                        any(Incident.class),
                        eq(IncidentStatus.RESOLVED),
                        anyString()
                );

        verify(workflowService, never())
                .completeWorkflow(WORKFLOW_ID);

        verify(jobClient).newFailCommand(job);

        verify(jobClient, never())
                .newCompleteCommand(job);
    }
}