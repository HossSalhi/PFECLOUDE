package com.orange.pfe.backend.service;

import com.orange.pfe.backend.config.InfraHealthProperties;
import com.orange.pfe.backend.model.core.Incident;
import com.orange.pfe.backend.model.enums.IncidentSource;
import com.orange.pfe.backend.model.enums.IncidentStatus;
import com.orange.pfe.backend.repository.IncidentRepository;
import com.orange.pfe.backend.model.action.ActionType;
import com.orange.pfe.backend.model.action.AutomationMode;
import com.orange.pfe.backend.model.action.RemediationTargetKind;
import com.orange.pfe.backend.service.remediation.RemediationPlan;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;

import java.time.LocalDateTime;
import java.util.List;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

class InfrastructureHealthSchedulerDeterministicTest {

    private InfraHealthProperties props;
    private InfrastructureHealthService healthService;
    private IncidentRepository incidentRepository;
    private IncidentHistoryService historyService;
    private ActionService actionService;
    private LeaderElectionService leaderElectionService;
    private WorkflowService workflowService;
    private CamundaWorkflowService camundaWorkflowService;
    private NotificationService notificationService;

    private InfrastructureHealthScheduler scheduler;

    @BeforeEach
    void setUp() {
        props = mock(InfraHealthProperties.class);
        healthService = mock(InfrastructureHealthService.class);
        incidentRepository = mock(IncidentRepository.class);
        historyService = mock(IncidentHistoryService.class);
        actionService = mock(ActionService.class);
        leaderElectionService = mock(LeaderElectionService.class);
        workflowService = mock(WorkflowService.class);
        camundaWorkflowService = mock(CamundaWorkflowService.class);
        notificationService = mock(NotificationService.class);

        when(incidentRepository.existsByStatusInAndSourceAndNamespaceAndApp(
                anyList(), any(IncidentSource.class), anyString(), anyString()))
                .thenReturn(false);

        when(incidentRepository.existsBySourceAndNamespaceAndAppAndCreatedAtAfter(
                any(IncidentSource.class), anyString(), anyString(), any(LocalDateTime.class)))
                .thenReturn(false);

        when(incidentRepository.save(any(Incident.class)))
                .thenAnswer(invocation -> invocation.getArgument(0));

        scheduler = new InfrastructureHealthScheduler(
                props,
                healthService,
                incidentRepository,
                historyService,
                actionService,
                leaderElectionService,
                workflowService,
                camundaWorkflowService,
                notificationService
        );
    }

    @Test
    void workerNodeIncidentIsClassifiedDeterministically() {
        boolean created = scheduler.openInfraIncidentFromCamunda(
                "k8s-nodes",
                "k8s-worker1",
                "Node NotReady detected by infrastructure health check"
        );

        assertTrue(created, "Un nouvel incident doit etre cree");

        ArgumentCaptor<Incident> captor = ArgumentCaptor.forClass(Incident.class);
        verify(incidentRepository, atLeastOnce()).save(captor.capture());

        List<Incident> saved = captor.getAllValues();
        Incident incident = saved.get(saved.size() - 1);

        assertEquals(IncidentSource.OTHER, incident.getSource());
        assertEquals("NODE_NOT_READY", incident.getIncidentType());
        assertNotNull(incident.getExplanation());
        assertTrue(
                incident.getExplanation().contains("watchdog"),
                "L'explication doit decrire le chemin de recuperation du noeud"
        );
        assertNotNull(incident.getDescription());
        assertFalse(
                incident.getDescription().contains("confidence"),
                "La description ne doit plus presenter une confiance de prediction"
        );
    }

    @Test
    void activeLegacyAiIncidentPreventsDuplicateCreation() {
        when(incidentRepository.existsByStatusInAndSourceAndNamespaceAndApp(
                anyList(),
                eq(IncidentSource.AI),
                eq("infra"),
                eq("monitoring")
        )).thenReturn(true);

        boolean created = scheduler.openInfraIncidentFromCamunda(
                "infra",
                "monitoring",
                "Monitoring component is unreachable"
        );

        assertFalse(created, "Un incident historique encore actif doit etre correle");
        verify(incidentRepository, never()).save(any(Incident.class));
    }

    /**
     * VMDOWN-POLICY-20261105 — INVERSION DÉLIBÉRÉE de l'ancien « workerNodeOutageProducesRestartVmAutoPlan » :
     * le scheduler ne déclenche PLUS aucun redémarrage. Il crée l'incident NODE_NOT_READY et laisse la décision à
     * VmPowerWatch (VM éteinte prouvée, sondes en échec, politique). Aucune action, aucun workflow, aucun appel Camunda.
     */
    @Test
    void workerNodeOutageNeStartsNoRestartDirectlyAnymore() {
        boolean created = scheduler.openInfraIncidentFromCamunda(
                "k8s-nodes", "k8s-worker1", "Node NotReady detected by infrastructure health check");
        assertTrue(created);

        verify(actionService, never()).createAction(any(), any(), any(), any(), any(), any(), any(), any());
        verify(workflowService, never()).startWorkflow(any(Incident.class), any(), any(), any());
        verify(camundaWorkflowService, never()).startWorkflow(any(), any(), any(), any());
        verify(historyService, atLeastOnce()).record(any(Incident.class), any(), contains("Politique VM_DOWN"));
        // la détection existante est conservée : notification complète (SSE + e-mail) pour un worker
        verify(notificationService).notifyNewIncident(any(Incident.class));
    }

    /**
     * .153 — consigne du 2026-11-17 (inverse l'ancien « AUCUNE autorisation de redémarrage ») : cible AUTO comme un worker.
     * Le scheduler détecte et notifie, mais ne crée AUCUNE action ni workflow : la DÉCISION de power-on est laissée à
     * VmPowerWatch (VM prouvée éteinte, armement, cooldown). L'historique le dit.
     */
    @Test
    void masterNodeOutageIsDetectedAndNotifiedLikeAWorkerAndTheRestartDecisionIsLeftToVmPowerWatch() {
        boolean created = scheduler.openInfraIncidentFromCamunda(
                "k8s-nodes", "k8s-master", "Node NotReady detected by infrastructure health check");
        assertTrue(created);

        verify(actionService, never()).createAction(any(), any(), any(), any(), any(), any(), any(), any());
        verify(workflowService, never()).startWorkflow(any(Incident.class), any(), any(), any());
        verify(camundaWorkflowService, never()).startWorkflow(any(), any(), any(), any());
        verify(historyService, atLeastOnce()).record(any(Incident.class), any(), contains("décidé par la surveillance de puissance"));
        verify(notificationService).notifyNewIncident(any(Incident.class));
    }

    /** .160 : ses services (Prometheus/Grafana/Loki) = détection + historique ; ni e-mail ni workflow Camunda. */
    @Test
    void monitoringHostedComponentsAreDetectionOnlyEvenWhenPipelineIsCamunda() {
        org.springframework.test.util.ReflectionTestUtils.setField(scheduler, "detectionPipeline", "camunda");
        when(props.isEnabled()).thenReturn(true);
        when(leaderElectionService.isLeader()).thenReturn(true);
        when(camundaWorkflowService.isReachable()).thenReturn(true);
        when(healthService.checkAll()).thenReturn(List.of(
                new InfrastructureHealthService.ComponentStatus("infra", "prometheus", false, "Injoignable"),
                new InfrastructureHealthService.ComponentStatus("infra", "grafana", false, "Injoignable"),
                new InfrastructureHealthService.ComponentStatus("infra", "loki", false, "Injoignable")));

        scheduler.checkInfrastructure();

        // jamais de workflow de détection Camunda pour les composants de .160
        verify(camundaWorkflowService, never()).startDetectionWorkflow(any(), any(), any(), any());
        verify(camundaWorkflowService, never()).startWorkflow(any(), any(), any(), any());
        verify(actionService, never()).createAction(any(), any(), any(), any(), any(), any(), any(), any());
        // incident + historique + interface SEULEMENT : aucun e-mail/webhook
        ArgumentCaptor<Incident> saved = ArgumentCaptor.forClass(Incident.class);
        verify(incidentRepository, atLeast(3)).save(saved.capture());
        assertEquals(java.util.Set.of("prometheus", "grafana", "loki"),
                saved.getAllValues().stream().map(Incident::getApp).collect(java.util.stream.Collectors.toSet()));
        verify(notificationService, times(3)).notifyNewIncidentUiOnly(any(Incident.class));
        verify(notificationService, never()).notifyNewIncident(any(Incident.class));
    }

    /** Le composant camunda (.161) reste orchestré comme avant : workflow de détection Camunda. */
    @Test
    void camundaComponentOnDotOneSixtyOneKeepsTheCamundaDetectionWorkflow() {
        org.springframework.test.util.ReflectionTestUtils.setField(scheduler, "detectionPipeline", "camunda");
        when(props.isEnabled()).thenReturn(true);
        when(leaderElectionService.isLeader()).thenReturn(true);
        when(camundaWorkflowService.isReachable()).thenReturn(true);
        when(camundaWorkflowService.startDetectionWorkflow(any(), any(), any(), any())).thenReturn(true);
        when(healthService.checkAll()).thenReturn(List.of(
                new InfrastructureHealthService.ComponentStatus("infra", "camunda", false, "Injoignable")));

        scheduler.checkInfrastructure();

        verify(camundaWorkflowService).startDetectionWorkflow(eq("infra"), eq("camunda"), eq("infra"), any());
    }

    /** .160 revenue : clôture « back UP » sans e-mail de résolution. */
    @Test
    void monitoringHostedComponentRecoveryDoesNotSendResolvedEmail() {
        when(props.isEnabled()).thenReturn(true);
        when(leaderElectionService.isLeader()).thenReturn(true);
        when(camundaWorkflowService.isReachable()).thenReturn(true);
        Incident open = new Incident();
        open.setNamespace("infra");
        open.setApp("grafana");
        open.setStatus(IncidentStatus.OPEN);
        open.setTitle("t");
        when(incidentRepository.findBySource(IncidentSource.OTHER)).thenReturn(List.of(open));
        when(incidentRepository.findBySource(IncidentSource.AI)).thenReturn(List.of());
        when(healthService.checkAll()).thenReturn(List.of(
                new InfrastructureHealthService.ComponentStatus("infra", "grafana", true, "HTTP 200")));

        scheduler.checkInfrastructure();

        verify(notificationService).notifyIncidentResolvedUiOnly(open);
        verify(notificationService, never()).notifyIncidentResolved(any(Incident.class));
    }

    @Test
    void recoveredKubernetesNodeIsNotAutoResolvedByScheduler() {

        // Autoriser l'exécution du scheduler
        when(props.isEnabled()).thenReturn(true);
        when(leaderElectionService.isLeader()).thenReturn(true);
        when(camundaWorkflowService.isReachable()).thenReturn(true);

        // Simuler un incident qui a échoué pendant sa résolution
        Incident failedIncident = mock(Incident.class);
        when(failedIncident.getStatus())
                .thenReturn(IncidentStatus.RESOLUTION_FAILED);

        when(incidentRepository.findBySource(IncidentSource.AI))
                .thenReturn(List.of(failedIncident));

        when(incidentRepository.findBySource(IncidentSource.OTHER))
                .thenReturn(List.of(failedIncident));

        // Simuler le retour du worker à l'état Ready
        when(healthService.checkAll()).thenReturn(List.of(
                new InfrastructureHealthService.ComponentStatus(
                        "k8s-nodes",
                        "k8s-worker1",
                        true,
                        "Node Ready"
                )
        ));

        // Exécuter le véritable scheduler
        scheduler.checkInfrastructure();

        // Vérifier que le scheduler n'a pas modifié le statut
        verify(failedIncident, never())
                .setStatus(IncidentStatus.RESOLVED);

        // Vérifier qu'aucune clôture n'a été enregistrée
        verify(incidentRepository, never()).save(any(Incident.class));

        verify(historyService, never()).record(
                any(Incident.class),
                eq(IncidentStatus.RESOLVED),
                anyString()
        );

        verify(notificationService, never())
                .notifyIncidentResolved(any(Incident.class));

        // Vérifier que le scheduler n'a même pas recherché les incidents
        // pour effectuer une résolution automatique
        verify(incidentRepository, never())
                .findBySource(any(IncidentSource.class));
    }
}