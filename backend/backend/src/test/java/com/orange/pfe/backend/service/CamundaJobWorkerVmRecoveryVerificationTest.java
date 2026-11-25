package com.orange.pfe.backend.service;

import com.orange.pfe.backend.config.RemediationProperties;
import com.orange.pfe.backend.dto.ExecutionEventRequest;
import com.orange.pfe.backend.dto.IncidentVerificationRequest;
import com.orange.pfe.backend.dto.TerminalEvidenceResponse;
import com.orange.pfe.backend.model.core.Incident;
import com.orange.pfe.backend.model.enums.TerminalEvidencePhase;
import com.orange.pfe.backend.model.workflow.WorkflowInstance;
import com.orange.pfe.backend.model.workflow.WorkflowStatus;
import com.orange.pfe.backend.repository.HumanTaskRepository;
import com.orange.pfe.backend.repository.IncidentActionRepository;
import com.orange.pfe.backend.service.remediation.HostCpuVerificationService;
import com.orange.pfe.backend.service.remediation.HostDiskVerificationService;
import com.orange.pfe.backend.service.remediation.HostMemoryVerificationService;
import com.orange.pfe.backend.service.remediation.RecoveryVerificationService;
import com.orange.pfe.backend.service.remediation.RemediationExecutor;
import com.orange.pfe.backend.service.remediation.RemediationMetricsService;
import com.orange.pfe.backend.service.vmpower.VmPowerPolicy;
import com.orange.pfe.backend.service.vmpower.VmRecoveryVerifier;
import com.orange.pfe.backend.service.vmpower.VmRecoveryVerifier.PassResult;
import com.orange.pfe.backend.service.vmpower.WatchdogClient;
import io.camunda.client.CamundaClient;
import io.camunda.client.api.response.ActivatedJob;
import io.camunda.client.api.worker.JobClient;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.mockito.InOrder;
import org.springframework.beans.factory.ObjectProvider;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.LocalDateTime;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyInt;
import static org.mockito.ArgumentMatchers.anyList;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.RETURNS_DEEP_STUBS;
import static org.mockito.Mockito.inOrder;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.times;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

/**
 * VMDOWN-POLICY-20261105 — vérification de récupération de .162 (VM_DOWN / ns infra) : DEUX passes consécutives
 * persistées avant le PASS, le PASS avant le RESOLVED, et jamais de RESOLVED autrement. Tout est simulé.
 */
class CamundaJobWorkerVmRecoveryVerificationTest {

    private static final Long WORKFLOW_ID = 88L;
    private static final Long INCIDENT_ID = 43L;
    private static final String VM = "k8s-lb2";

    private CamundaJobWorkerLifecycle worker;
    private WorkflowService workflowService;
    private IncidentCardService card;
    private NotificationService notifications;
    private VmRecoveryVerifier verifier;
    private WatchdogClient watchdog;
    private JobClient jobClient;
    private ActivatedJob job;
    private int attempts = 4;

    @BeforeEach
    @SuppressWarnings("unchecked")
    void setUp() {
        workflowService = mock(WorkflowService.class);
        card = mock(IncidentCardService.class);
        notifications = mock(NotificationService.class);
        verifier = mock(VmRecoveryVerifier.class);
        watchdog = mock(WatchdogClient.class);
        WorkflowInstance workflow = mock(WorkflowInstance.class);
        Incident incident = mock(Incident.class);
        jobClient = mock(JobClient.class, RETURNS_DEEP_STUBS);
        job = mock(ActivatedJob.class);

        when(job.getVariablesAsMap()).thenReturn(Map.of(
                "workflowId", WORKFLOW_ID, "targetNamespace", "infra", "targetName", VM,
                "actionType", "RESTART_VM", "incidentType", "VM_DOWN"));
        when(job.getKey()).thenReturn(5L);
        when(job.getType()).thenReturn("verify-remediation");
        when(job.getRetries()).thenReturn(3);
        when(workflow.getId()).thenReturn(WORKFLOW_ID);
        when(workflow.getStatus()).thenReturn(WorkflowStatus.RUNNING);
        when(workflow.getIncident()).thenReturn(incident);
        when(incident.getId()).thenReturn(INCIDENT_ID);
        when(workflowService.getWorkflowInstance(WORKFLOW_ID)).thenReturn(workflow);
        when(workflowService.completeWorkflow(WORKFLOW_ID)).thenReturn(workflow);

        when(watchdog.states()).thenReturn(new WatchdogClient.Result<>(true, 200, new LinkedHashMap<>(), null));
        when(card.recordVmRecoveryEvidence(eq(INCIDENT_ID), eq(VM), anyInt(), any(LocalDateTime.class),
                org.mockito.ArgumentMatchers.anyBoolean(), anyString()))
                .thenAnswer(inv -> evidence(inv.getArgument(2)));

        worker = new CamundaJobWorkerLifecycle(
                mock(CamundaClient.class), mock(CamundaWorkflowService.class), workflowService,
                mock(IncidentActionRepository.class), mock(ActionService.class), mock(RemediationExecutor.class),
                mock(RecoveryVerificationService.class), mock(HostCpuVerificationService.class),
                mock(HostDiskVerificationService.class), mock(HostMemoryVerificationService.class),
                mock(RemediationMetricsService.class), mock(HumanTaskRepository.class), mock(HumanTaskService.class),
                mock(RemediationProperties.class), mock(IncidentHistoryService.class),
                notifications, (ObjectProvider<AIOpsOrchestrationService>) mock(ObjectProvider.class),
                (ObjectProvider<InfrastructureHealthScheduler>) mock(ObjectProvider.class), card) {
            @Override
            int nodeReadyVerificationMaxAttempts() {
                return attempts;
            }

            @Override
            void waitBetweenNodeReadyObservations() {
                // pas d'attente réelle dans les tests (15 s en production)
            }
        };
        worker.setVmRecovery(verifier, watchdog);
    }

    private static TerminalEvidenceResponse evidence(int sample) {
        return new TerminalEvidenceResponse(1000L + sample, INCIDENT_ID, VM, TerminalEvidencePhase.AFTER,
                LocalDateTime.now(), "VM_RECOVERY " + VM + " [sample=" + sample + "]", 0, "PASS", null,
                "{\"sample\":" + sample + ",\"passed\":true}", true, "VM_RECOVERY:" + INCIDENT_ID);
    }

    private static PassResult pass(boolean ok) {
        Map<String, Boolean> c = new LinkedHashMap<>();
        c.put("power_running", ok);
        c.put("ssh", ok);
        return new PassResult(ok, c, ok ? "tous les criteres OK" : "criteres en echec [ssh]");
    }

    private void passes(Boolean... results) {
        PassResult[] rest = new PassResult[results.length - 1];
        for (int i = 1; i < results.length; i++) {
            rest[i - 1] = pass(results[i]);
        }
        when(verifier.verifyInfra(any(), any())).thenReturn(pass(results[0]), rest);
    }

    // ── Succès : 2 passes persistées -> PASS -> RESOLVED, dans cet ordre ───────────────────────────

    @Test
    void deux_passes_reussies_persistees_puis_PASS_puis_RESOLVED() {
        passes(true, true);
        when(card.hasTwoConsecutiveVmRecoveryEvidence(INCIDENT_ID, VM)).thenReturn(true);
        when(card.hasPassVerification(INCIDENT_ID)).thenReturn(true);

        worker.handleVerifyRemediation(jobClient, job);

        verify(card, times(2)).recordVmRecoveryEvidence(eq(INCIDENT_ID), eq(VM), anyInt(), any(LocalDateTime.class),
                eq(true), anyString());
        InOrder order = inOrder(card, workflowService, notifications);
        order.verify(card).recordVmRecoveryVerification(eq(INCIDENT_ID), eq(VM), anyString());
        order.verify(workflowService).completeWorkflow(WORKFLOW_ID);
        order.verify(notifications).notifyIncidentResolvedWithTrace(any(), anyList());
    }

    @Test
    void une_passe_en_echec_casse_la_serie_le_PASS_exige_deux_nouvelles_passes() {
        passes(true, false, true, true);
        when(card.hasTwoConsecutiveVmRecoveryEvidence(INCIDENT_ID, VM)).thenReturn(true);
        when(card.hasPassVerification(INCIDENT_ID)).thenReturn(true);

        worker.handleVerifyRemediation(jobClient, job);

        // les 4 passes sont persistées, dont l'échec (qui casse la série côté base)
        verify(card, times(4)).recordVmRecoveryEvidence(eq(INCIDENT_ID), eq(VM), anyInt(), any(LocalDateTime.class),
                org.mockito.ArgumentMatchers.anyBoolean(), anyString());
        verify(card, times(1)).recordVmRecoveryEvidence(eq(INCIDENT_ID), eq(VM), eq(2), any(LocalDateTime.class),
                eq(false), anyString());
        // la vérification de la paire n'est interrogée qu'après la 3e/4e passe (jamais après 1 seule réussite)
        verify(card, times(1)).hasTwoConsecutiveVmRecoveryEvidence(INCIDENT_ID, VM);
        verify(workflowService).completeWorkflow(WORKFLOW_ID);
    }

    // ── Échecs : jamais de RESOLVED ────────────────────────────────────────────────────────────────

    @Test
    void jamais_deux_passes_consecutives_aucun_PASS_aucun_RESOLVED() {
        passes(true, false, true, false);

        worker.handleVerifyRemediation(jobClient, job);

        verify(card, never()).recordVmRecoveryVerification(any(), any(), any());
        verify(workflowService, never()).completeWorkflow(any());
        verify(notifications, never()).notifyIncidentResolvedWithTrace(any(), anyList());
        verify(card).recordVerification(eq(INCIDENT_ID), any(IncidentVerificationRequest.class));
        verify(card).recordExecutionEvent(eq(INCIDENT_ID), any(ExecutionEventRequest.class));
    }

    @Test
    void verificateur_ou_watchdog_absent_est_fail_closed() {
        worker.setVmRecovery(null, null);
        worker.handleVerifyRemediation(jobClient, job);
        verify(workflowService, never()).completeWorkflow(any());
        verify(card, never()).recordVmRecoveryEvidence(any(), any(), anyInt(), any(), org.mockito.ArgumentMatchers.anyBoolean(), any());
    }

    @Test
    void etat_d_alimentation_indisponible_est_une_passe_en_echec() {
        when(watchdog.states()).thenReturn(new WatchdogClient.Result<>(false, 0, null, "TIMEOUT"));
        worker.handleVerifyRemediation(jobClient, job);
        verify(verifier, never()).verifyInfra(any(), any());
        verify(card, times(attempts)).recordVmRecoveryEvidence(eq(INCIDENT_ID), eq(VM), anyInt(),
                any(LocalDateTime.class), eq(false), anyString());
        verify(workflowService, never()).completeWorkflow(any());
    }

    @Test
    void echec_de_persistance_d_une_passe_fait_echouer_le_job_sans_RESOLVED() {
        passes(true, true);
        when(card.recordVmRecoveryEvidence(eq(INCIDENT_ID), eq(VM), anyInt(), any(LocalDateTime.class),
                org.mockito.ArgumentMatchers.anyBoolean(), anyString()))
                .thenThrow(new IllegalStateException("Simulated database failure"));
        worker.handleVerifyRemediation(jobClient, job);
        verify(card, never()).recordVmRecoveryVerification(any(), any(), any());
        verify(workflowService, never()).completeWorkflow(any());
    }

    @Test
    void garde_de_preuves_RESOLVED_refuse_si_le_PASS_n_est_pas_persiste() {
        passes(true, true);
        when(card.hasTwoConsecutiveVmRecoveryEvidence(INCIDENT_ID, VM)).thenReturn(true);
        when(card.hasPassVerification(INCIDENT_ID)).thenReturn(false);   // le PASS ne se retrouve pas en base

        worker.handleVerifyRemediation(jobClient, job);

        verify(workflowService, never()).completeWorkflow(any());
        verify(notifications, never()).notifyIncidentResolvedWithTrace(any(), anyList());
    }

    // ── Invariants de source (R5) ──────────────────────────────────────────────────────────────────

    @Test
    void invariant_de_source_completeWorkflow_n_est_appele_qu_apres_la_garde_de_preuves() throws IOException {
        String src = Files.readString(Path.of(
                "src/main/java/com/orange/pfe/backend/service/CamundaJobWorkerLifecycle.java"));
        int debut = src.indexOf("void handleVerifyRemediation(");
        int fin = src.indexOf("private void maybeStopCpuLoadAfterRecovery(");
        assertTrue(debut > 0 && fin > debut);
        String corps = src.substring(debut, fin);
        int garde = corps.indexOf("requirePersistedRecoveryPass(");
        int completion = corps.indexOf("workflowService.completeWorkflow(");
        assertTrue(garde > 0, "la garde de preuves est absente de handleVerifyRemediation");
        assertTrue(completion > garde, "completeWorkflow (RESOLVED) doit suivre la garde de preuves");
        assertTrue(src.lines().filter(l -> l.contains("workflowService.completeWorkflow(")
                && !l.trim().startsWith("//") && !l.trim().startsWith("*")).count() == 1,
                "un seul appelant de completeWorkflow dans le worker");
        // et la politique ne connaît .162 que comme cible AUTO
        assertTrue(VmPowerPolicy.isAutoRestartable(VM));
    }
}
