package com.orange.pfe.backend.service.vmpower;

import com.orange.pfe.backend.config.VmPowerProperties;
import com.orange.pfe.backend.model.action.ActionStatus;
import com.orange.pfe.backend.model.action.ActionType;
import com.orange.pfe.backend.model.core.Incident;
import com.orange.pfe.backend.model.core.IncidentTerminalEvidence;
import com.orange.pfe.backend.model.enums.IncidentSeverity;
import com.orange.pfe.backend.model.enums.IncidentSource;
import com.orange.pfe.backend.model.enums.IncidentStatus;
import com.orange.pfe.backend.model.enums.TerminalEvidencePhase;
import com.orange.pfe.backend.repository.IncidentActionRepository;
import com.orange.pfe.backend.repository.IncidentHistoryRepository;
import com.orange.pfe.backend.repository.IncidentRepository;
import com.orange.pfe.backend.repository.IncidentTerminalEvidenceRepository;
import com.orange.pfe.backend.repository.IncidentVerificationRepository;
import com.orange.pfe.backend.repository.WorkflowRepository;
import com.orange.pfe.backend.service.ActionService;
import com.orange.pfe.backend.service.CamundaWorkflowService;
import com.orange.pfe.backend.service.IncidentCardService;
import com.orange.pfe.backend.service.IncidentHistoryService;
import com.orange.pfe.backend.service.LeaderElectionService;
import com.orange.pfe.backend.service.NotificationService;
import com.orange.pfe.backend.service.WorkflowService;
import com.orange.pfe.backend.service.remediation.NodeReadinessObservation;
import com.orange.pfe.backend.service.remediation.RemediationExecutor;
import com.orange.pfe.backend.service.remediation.RemediationPlan;
import com.orange.pfe.backend.service.vmpower.VmPowerPolicy.PowerState;
import com.orange.pfe.backend.service.vmpower.VmRecoveryVerifier.PassResult;
import com.orange.pfe.backend.service.vmpower.WatchdogClient.Evidence;
import com.orange.pfe.backend.service.vmpower.WatchdogClient.Guards;
import com.orange.pfe.backend.service.vmpower.WatchdogClient.VmState;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;

import java.time.Instant;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyLong;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.times;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

/**
 * VMDOWN-POLICY-20261105 — {@link VmPowerWatch} contre la VRAIE couche JPA (H2 en mémoire, contexte Spring réel) :
 * incidents, historique, actions, workflows, preuves et verdicts réellement écrits puis relus. Seuls le réseau
 * (watchdog, sondes), Camunda, les notifications et l'API Kubernetes sont simulés. Les tests unitaires à mocks ne
 * peuvent pas révéler une entité détachée, une contrainte de colonne ou un chargement paresseux : celui-ci le peut.
 */
@SpringBootTest(properties = "jwt.secret=test-only-secret-0123456789-0123456789-0123456789-0123456789")
class VmPowerWatchJpaIntegrationTest {

    static final long T0 = 1_800_000_000_000L;
    static final long CYCLE = 15_000L;

    @Autowired private IncidentRepository incidents;
    @Autowired private IncidentActionRepository actions;
    @Autowired private IncidentHistoryRepository historyRepo;
    @Autowired private IncidentTerminalEvidenceRepository evidenceRepo;
    @Autowired private IncidentVerificationRepository verificationRepo;
    @Autowired private WorkflowRepository workflowRepo;
    @Autowired private IncidentHistoryService history;
    @Autowired private ActionService actionService;
    @Autowired private WorkflowService workflowService;
    @Autowired private IncidentCardService card;

    private WatchdogClient watchdog;
    private VmProbes probes;
    private CamundaWorkflowService camunda;
    private NotificationService notifications;
    private RemediationExecutor nodeReadiness;
    private VmRecoveryVerifier verifier;
    private VmPowerProperties props;
    private VmPowerWatch watch;

    private final Map<String, VmState> states = new LinkedHashMap<>();
    private final Map<String, VmProbes.Liveness> live = new LinkedHashMap<>();
    private final List<Long> created = new ArrayList<>();

    @BeforeEach
    void setUp() {
        watchdog = mock(WatchdogClient.class);
        probes = mock(VmProbes.class);
        camunda = mock(CamundaWorkflowService.class);
        notifications = mock(NotificationService.class);
        nodeReadiness = mock(RemediationExecutor.class);
        verifier = mock(VmRecoveryVerifier.class);
        LeaderElectionService leader = mock(LeaderElectionService.class);
        when(leader.isLeader()).thenReturn(true);
        when(camunda.isReachable()).thenReturn(true);
        props = new VmPowerProperties();
        props.setEnabled(true);
        for (VmPowerPolicy.Entry e : VmPowerPolicy.entries()) {
            states.put(e.name(), state(e.name(), PowerState.RUNNING));
            live.put(e.name(), new VmProbes.Liveness(3, 3, List.of("tcp/22"), List.of()));
        }
        when(watchdog.states()).thenAnswer(inv -> new WatchdogClient.Result<>(true, 200, new LinkedHashMap<>(states), null));
        when(probes.livenessAll(any())).thenAnswer(inv -> new LinkedHashMap<>(live));
        when(nodeReadiness.inspectNodeReadiness(org.mockito.ArgumentMatchers.anyString()))
                .thenReturn(new NodeReadinessObservation(true, false, "False", "NotReady"));
        when(verifier.verifyInfra(any(), any())).thenReturn(pass(false));
        watch = new VmPowerWatch(props, watchdog, probes, incidents, actions, history, actionService, workflowService,
                camunda, notifications, leader, card, verifier, nodeReadiness);
    }

    @AfterEach
    void cleanup() {
        VmPowerPolicy.resetForTest();   // couture de test (route directe dormante) : jamais d'une fuite d'un test à l'autre
        for (Long id : created) {
            workflowRepo.deleteAll(workflowRepo.findAll().stream()
                    .filter(w -> w.getIncident() != null && id.equals(w.getIncident().getId())).toList());
            actions.deleteAll(actions.findAll().stream()
                    .filter(a -> a.getIncident() != null && id.equals(a.getIncident().getId())).toList());
            evidenceRepo.deleteAll(evidenceRepo.findAll().stream().filter(e -> id.equals(e.getIncidentId())).toList());
            verificationRepo.deleteAll(verificationRepo.findAll().stream().filter(v -> id.equals(v.getIncidentId())).toList());
            historyRepo.deleteAll(historyRepo.findAll().stream()
                    .filter(h -> h.getIncident() != null && id.equals(h.getIncident().getId())).toList());
            incidents.findById(id).ifPresent(incidents::delete);
        }
        created.clear();
        // incidents créés par le watch lui-même (VM_DOWN / VM_UNREACHABLE) : nettoyage par application
        for (String app : List.of("k8s-lb2", "k8s-monitoring", "k8s-worker1", "k8s-master")) {
            incidents.findAll().stream().filter(i -> app.equals(i.getApp())).forEach(i -> {
                workflowRepo.deleteAll(workflowRepo.findAll().stream()
                        .filter(w -> w.getIncident() != null && i.getId().equals(w.getIncident().getId())).toList());
                actions.deleteAll(actions.findAll().stream()
                        .filter(a -> a.getIncident() != null && i.getId().equals(a.getIncident().getId())).toList());
                evidenceRepo.deleteAll(evidenceRepo.findAll().stream().filter(e -> i.getId().equals(e.getIncidentId())).toList());
                verificationRepo.deleteAll(verificationRepo.findAll().stream().filter(v -> i.getId().equals(v.getIncidentId())).toList());
                historyRepo.deleteAll(historyRepo.findAll().stream()
                        .filter(h -> h.getIncident() != null && i.getId().equals(h.getIncident().getId())).toList());
                incidents.delete(i);
            });
        }
    }

    // ── Décor ────────────────────────────────────────────────────────────────────────────────────

    private static VmState state(String vm, PowerState ps) {
        Evidence ev = new Evidence("vmrun -T ws list", Instant.now(), 1000, 100, 5, ps == PowerState.RUNNING, "a", "b");
        return new VmState(vm, VmPowerPolicy.modeOf(vm).name(), ps, "OK", ev,
                new Guards(false, false, true, List.of(), 0, 0, null, 8000));
    }

    private static PassResult pass(boolean ok) {
        Map<String, Boolean> c = new LinkedHashMap<>();
        c.put("power_running", ok);
        return new PassResult(ok, c, ok ? "tous les critères OK" : "critères en échec [power_running]");
    }

    private void off(String vm) {
        states.put(vm, state(vm, PowerState.POWERED_OFF));
        live.put(vm, new VmProbes.Liveness(0, 3, List.of(), List.of("tcp/22", "tcp/80", "udp/53")));
    }

    private Incident persisted(String ns, String app, String type, IncidentStatus status) {
        Incident i = new Incident();
        i.setTitle("incident synthétique " + app);
        i.setDescription("test d'intégration VmPowerWatch");
        i.setSeverity(IncidentSeverity.CRITICAL);
        i.setStatus(status);
        i.setSource(IncidentSource.OTHER);
        i.setNamespace(ns);
        i.setApp(app);
        i.setIncidentType(type);
        i = incidents.save(i);
        created.add(i.getId());
        return i;
    }

    private void cycles(int n) {
        for (int k = 0; k < n; k++) {
            watch.runCycle(T0 + k * CYCLE);
        }
    }

    private List<Incident> incidentsOf(String app) {
        return incidents.findAll().stream().filter(i -> app.equals(i.getApp())).toList();
    }

    private long historyRows(Long incidentId) {
        return historyRepo.findAll().stream()
                .filter(h -> h.getIncident() != null && incidentId.equals(h.getIncident().getId())).count();
    }

    // ── Scénarios ────────────────────────────────────────────────────────────────────────────────

    @Test
    void lb2_eteinte_confirmee_cree_VM_DOWN_en_base_puis_lance_action_et_workflow_reels() {
        props.setDryRun(false);
        props.setActiveVms(java.util.Set.of("k8s-lb2"));
        off("k8s-lb2");

        cycles(3);   // 0 s, 15 s, 30 s

        List<Incident> found = incidentsOf("k8s-lb2");
        assertThat(found).hasSize(1);
        Incident incident = incidents.findById(found.get(0).getId()).orElseThrow();
        assertThat(incident.getIncidentType()).isEqualTo("VM_DOWN");
        assertThat(incident.getNamespace()).isEqualTo("infra");
        assertThat(incident.getSource()).isEqualTo(IncidentSource.OTHER);
        assertThat(incident.getStatus()).isEqualTo(IncidentStatus.IN_PROGRESS);

        var recorded = actions.findAll().stream().filter(a -> a.getIncident().getId().equals(incident.getId())).toList();
        assertThat(recorded).hasSize(1);
        assertThat(recorded.get(0).getType()).isEqualTo(ActionType.RESTART_VM);
        assertThat(recorded.get(0).getTargetName()).isEqualTo("k8s-lb2");
        assertThat(recorded.get(0).getTargetNamespace()).isEqualTo("infra");
        assertThat(recorded.get(0).getStatus()).isEqualTo(ActionStatus.PENDING);

        assertThat(workflowRepo.findAll().stream()
                .filter(w -> w.getIncident() != null && w.getIncident().getId().equals(incident.getId()))).hasSize(1);
        assertThat(historyRows(incident.getId())).isGreaterThanOrEqualTo(2);   // OPEN + IN_PROGRESS

        ArgumentCaptor<RemediationPlan> plan = ArgumentCaptor.forClass(RemediationPlan.class);
        verify(camunda).startWorkflow(any(), any(), plan.capture(), any());
        assertThat(plan.getValue().incidentId()).isEqualTo(incident.getId());
        assertThat(plan.getValue().actionType()).isEqualTo(ActionType.RESTART_VM);
        verify(notifications).notifyNewIncident(any(Incident.class));
    }

    @Test
    void cycles_suivants_ne_creent_ni_second_incident_ni_seconde_action() {
        props.setDryRun(false);
        props.setActiveVms(java.util.Set.of("k8s-lb2"));
        off("k8s-lb2");
        cycles(3);
        for (int k = 3; k < 12; k++) {
            watch.runCycle(T0 + k * CYCLE);
        }
        assertThat(incidentsOf("k8s-lb2")).hasSize(1);
        assertThat(actions.findAll().stream().filter(a -> "k8s-lb2".equals(a.getTargetName()))).hasSize(1);
    }

    @Test
    void mode_ombre_ecrit_incident_et_historique_mais_aucune_action_ni_workflow() {
        // dryRun=true par défaut
        off("k8s-lb2");
        cycles(4);
        List<Incident> found = incidentsOf("k8s-lb2");
        assertThat(found).hasSize(1);
        assertThat(found.get(0).getStatus()).isEqualTo(IncidentStatus.OPEN);
        assertThat(actions.findAll().stream().filter(a -> "k8s-lb2".equals(a.getTargetName()))).isEmpty();
        assertThat(workflowRepo.findAll().stream()
                .filter(w -> w.getIncident() != null && w.getIncident().getId().equals(found.get(0).getId()))).isEmpty();
        assertThat(historyRepo.findAll().stream().filter(h -> h.getIncident() != null
                && h.getIncident().getId().equals(found.get(0).getId())
                && h.getComment() != null && h.getComment().contains("MODE OMBRE"))).hasSize(1);
        verify(camunda, never()).startWorkflow(any(), any(), any(), any());
    }

    @Test
    void k8s_monitoring_detection_seule_incident_et_historique_sans_action_ni_post_ni_camunda() {
        // dryRun=true par défaut : l'incident et l'historique sont écrits ; .160 est de toute façon en DÉTECTION SEULE
        off("k8s-monitoring");
        cycles(10);
        List<Incident> found = incidentsOf("k8s-monitoring");
        assertThat(found).hasSize(1);
        assertThat(found.get(0).getStatus()).isEqualTo(IncidentStatus.OPEN);
        assertThat(actions.findAll().stream().filter(a -> "k8s-monitoring".equals(a.getTargetName()))).isEmpty();
        assertThat(historyRepo.findAll().stream().filter(h -> h.getIncident() != null
                && h.getIncident().getId().equals(found.get(0).getId())
                && h.getComment() != null && h.getComment().contains("DÉTECTION SEULE — k8s-monitoring"))).hasSize(1);
        verify(watchdog, never()).restart(anyString(), anyLong(), any());
        verify(notifications).notifyNewIncidentUiOnly(any(Incident.class));
        verify(notifications, never()).notifyNewIncident(any(Incident.class));
        verify(camunda, never()).startWorkflow(any(), any(), any(), any());
    }

    @Test
    void k8s_monitoring_meme_armee_en_mode_reel_aucune_action_aucun_workflow_aucun_post_en_base() {
        // Les trois verrous sont OUVERTS pour .160 (enabled, dry-run=false, nom dans active-vms) : la politique tranche
        // quand même — .160 est la VM de supervision, jamais rallumée automatiquement.
        props.setDryRun(false);
        props.setActiveVms(java.util.Set.of("k8s-monitoring", "k8s-worker1", "k8s-worker2", "k8s-lb2"));
        off("k8s-monitoring");
        cycles(12);
        List<Incident> found = incidentsOf("k8s-monitoring");
        assertThat(found).hasSize(1);
        assertThat(found.get(0).getStatus()).isEqualTo(IncidentStatus.OPEN);
        assertThat(actions.findAll().stream().filter(a -> a.getIncident() != null
                && a.getIncident().getId().equals(found.get(0).getId()))).isEmpty();
        assertThat(workflowRepo.findAll().stream().filter(w -> w.getIncident() != null
                && w.getIncident().getId().equals(found.get(0).getId()))).isEmpty();
        verify(watchdog, never()).restart(anyString(), anyLong(), any());
        verify(camunda, never()).startWorkflow(any(), any(), any(), any());
        verify(notifications, never()).notifyNewIncident(any(Incident.class));
        verify(notifications, never()).notifyEscalation(any());
    }

    // ── Route DIRECTE du backend (DORMANTE : aucune VM de production ne l'utilise ; exercée via la couture de test)
    //    vraie base ; horloge du cycle = horloge réelle ───────────────────────────────────────────

    private List<String> cyclesAt(long base, int from, int to) {
        List<String> all = new ArrayList<>();
        for (int k = from; k < to; k++) {
            all.addAll(watch.runCycle(base + k * CYCLE));
        }
        return all;
    }

    private static WatchdogClient.RestartOutcome started() {
        return new WatchdogClient.RestartOutcome(WatchdogClient.Kind.STARTED, 200, "STARTED", null, null, null,
                "req-1", false, null);
    }

    private void monitoringReal() {
        // Couture de test : .160 reprend l'ancienne variante AUTO / BACKEND_DIRECT / UI_ONLY (validée par l'essai réel du
        // 2026-11-06 13:28) ; restaurée par cleanup().
        VmPowerPolicy.overrideEntryForTest(new VmPowerPolicy.Entry("k8s-monitoring", "192.168.148.160",
                VmPowerPolicy.Mode.AUTO, VmPowerPolicy.VmClass.INFRA_MONITORING, "infra", "k8s-monitoring", "VM_DOWN",
                VmPowerPolicy.Route.BACKEND_DIRECT, VmPowerPolicy.Notifications.UI_ONLY));
        props.setDryRun(false);
        props.setActiveVms(java.util.Set.of("k8s-monitoring"));
        off("k8s-monitoring");
    }

    @Test
    void k8s_monitoring_mode_reel_demande_persistee_avant_l_unique_post_sans_camunda_ni_email() {
        monitoringReal();
        final long[] rowsAtCall = {-1};
        final String[] statusAtCall = {null};
        when(watchdog.restart(eq("k8s-monitoring"), anyLong(), any())).thenAnswer(inv -> {
            rowsAtCall[0] = actions.findAll().stream().filter(a -> "k8s-monitoring".equals(a.getTargetName())).count();
            statusAtCall[0] = incidents.findById(inv.<Long>getArgument(1)).orElseThrow().getStatus().name();
            return started();
        });

        cyclesAt(System.currentTimeMillis(), 0, 12);     // 12 cycles : lancement au 3e, puis attente des preuves

        List<Incident> found = incidentsOf("k8s-monitoring");
        assertThat(found).hasSize(1);
        Incident incident = incidents.findById(found.get(0).getId()).orElseThrow();
        assertThat(incident.getStatus()).isEqualTo(IncidentStatus.IN_PROGRESS);
        assertThat(rowsAtCall[0]).as("la demande (action RESTART_VM) est persistée AVANT l'appel au watchdog").isEqualTo(1);
        assertThat(statusAtCall[0]).as("l'incident est IN_PROGRESS AVANT l'appel").isEqualTo("IN_PROGRESS");
        var recorded = actions.findAll().stream().filter(a -> a.getIncident().getId().equals(incident.getId())).toList();
        assertThat(recorded).hasSize(1);
        assertThat(recorded.get(0).getType()).isEqualTo(ActionType.RESTART_VM);
        assertThat(recorded.get(0).getTargetName()).isEqualTo("k8s-monitoring");
        assertThat(recorded.get(0).getTargetNamespace()).isEqualTo("infra");
        assertThat(recorded.get(0).getStatus()).isEqualTo(ActionStatus.COMPLETED);
        assertThat(workflowRepo.findAll().stream()
                .filter(w -> w.getIncident() != null && w.getIncident().getId().equals(incident.getId())))
                .as("aucun workflow Camunda pour .160").isEmpty();
        verify(camunda, never()).startWorkflow(any(), any(), any(), any());
        verify(watchdog, times(1)).restart(eq("k8s-monitoring"), eq(incident.getId()), any());
        verify(notifications).notifyNewIncidentUiOnly(any(Incident.class));
        verify(notifications, never()).notifyNewIncident(any(Incident.class));
        verify(notifications, never()).notifyEscalation(any());
        verify(notifications, never()).notifyRemediationFailed(any(), any());
    }

    @Test
    void k8s_monitoring_refus_du_watchdog_ecrit_un_etat_explicite_et_ne_boucle_jamais() {
        monitoringReal();
        when(watchdog.restart(anyString(), anyLong(), any())).thenReturn(new WatchdogClient.RestartOutcome(
                WatchdogClient.Kind.DEFERRED, 503, "HOST_RAM_LOW", "HOST_RAM_LOW", 60, null, "req-2", false, null));

        cyclesAt(System.currentTimeMillis(), 0, 14);

        Incident incident = incidents.findById(incidentsOf("k8s-monitoring").get(0).getId()).orElseThrow();
        assertThat(incident.getStatus()).isEqualTo(IncidentStatus.RESOLUTION_FAILED);
        var recorded = actions.findAll().stream().filter(a -> a.getIncident().getId().equals(incident.getId())).toList();
        assertThat(recorded).hasSize(1);
        assertThat(recorded.get(0).getStatus()).isEqualTo(ActionStatus.FAILED);
        verify(watchdog, times(1)).restart(anyString(), anyLong(), any());      // jamais de boucle de démarrage
        verify(notifications).notifyRemediationFailedUiOnly(any(Incident.class), org.mockito.ArgumentMatchers.contains("HOST_RAM_LOW"));
        verify(notifications, never()).notifyRemediationFailed(any(), any());   // aucun e-mail
        assertThat(incidentsOf("k8s-monitoring")).hasSize(1);                   // épisode ouvert : pas de nouvel incident
    }

    @Test
    void k8s_monitoring_deux_passes_de_preuves_persistees_donnent_RESOLVED_en_interface_seulement() {
        monitoringReal();
        when(watchdog.restart(anyString(), anyLong(), any())).thenReturn(started());
        long base = System.currentTimeMillis();
        cyclesAt(base, 0, 3);                                                   // arrêt confirmé + demande acceptée

        states.put("k8s-monitoring", state("k8s-monitoring", PowerState.RUNNING));   // la VM est revenue
        live.put("k8s-monitoring", new VmProbes.Liveness(3, 3, List.of("tcp/22"), List.of()));
        when(verifier.verifyInfra(any(), any())).thenReturn(pass(true));
        List<String> ev = cyclesAt(base, 3, 6);

        assertThat(ev).contains("RESOLVED_WITH_PROOF:k8s-monitoring:VM_DOWN");
        Incident incident = incidents.findById(incidentsOf("k8s-monitoring").get(0).getId()).orElseThrow();
        assertThat(incident.getStatus()).isEqualTo(IncidentStatus.RESOLVED);
        assertThat(evidenceRepo.findByIncidentIdAndPhaseOrderByCapturedAtAsc(incident.getId(),
                TerminalEvidencePhase.AFTER)).hasSize(2);
        assertThat(card.hasPassVerification(incident.getId())).isTrue();
        verify(watchdog, times(1)).restart(anyString(), anyLong(), any());
        verify(notifications).notifyIncidentResolvedUiOnly(any(Incident.class));
        verify(notifications, never()).notifyIncidentResolved(any(Incident.class));
    }

    @Test
    void k8s_monitoring_budget_depasse_puis_retour_tardif_se_ferme_par_preuves() {
        monitoringReal();
        when(watchdog.restart(anyString(), anyLong(), any())).thenReturn(started());
        long base = System.currentTimeMillis();
        cyclesAt(base, 0, 3);                                                   // demande acceptée

        // 7 min plus tard (budget de 6 min) toujours sans preuve : état explicite, pas de nouveau démarrage
        List<String> late = watch.runCycle(base + 7 * 60_000L);
        assertThat(late).contains("DIRECT_RESTART_FAILED:k8s-monitoring");
        Incident failed = incidents.findById(incidentsOf("k8s-monitoring").get(0).getId()).orElseThrow();
        assertThat(failed.getStatus()).isEqualTo(IncidentStatus.RESOLUTION_FAILED);
        verify(notifications).notifyRemediationFailedUiOnly(any(Incident.class),
                org.mockito.ArgumentMatchers.contains("budget"));
        verify(notifications, never()).notifyRemediationFailed(any(), any());

        // la VM finit par revenir : l'épisode se ferme par deux passes consécutives persistées
        states.put("k8s-monitoring", state("k8s-monitoring", PowerState.RUNNING));
        live.put("k8s-monitoring", new VmProbes.Liveness(3, 3, List.of("tcp/22"), List.of()));
        when(verifier.verifyInfra(any(), any())).thenReturn(pass(true));
        List<String> back = new ArrayList<>();
        back.addAll(watch.runCycle(base + 7 * 60_000L + CYCLE));
        back.addAll(watch.runCycle(base + 7 * 60_000L + 2 * CYCLE));
        assertThat(back).contains("RESOLVED_WITH_PROOF:k8s-monitoring:VM_DOWN");
        assertThat(incidents.findById(failed.getId()).orElseThrow().getStatus()).isEqualTo(IncidentStatus.RESOLVED);
        verify(watchdog, times(1)).restart(anyString(), anyLong(), any());
    }

    @Test
    void k8s_master_en_deny_restart_force_jamais_d_action_ni_de_nouvel_incident_en_base() {
        // Aucune VM n'est DENY_RESTART depuis le 2026-11-17 ; le mode est CONSERVÉ et honoré : on le rejoue sur .153 par la couture.
        VmPowerPolicy.overrideEntryForTest(new VmPowerPolicy.Entry("k8s-master", "192.168.148.153", VmPowerPolicy.Mode.DENY_RESTART,
                VmPowerPolicy.VmClass.K8S_CONTROL_PLANE, "k8s-nodes", "k8s-master", "NODE_NOT_READY",
                VmPowerPolicy.Route.NONE, VmPowerPolicy.Notifications.EMAIL_AND_UI));
        props.setDryRun(false);
        props.setActiveVms(java.util.Set.of("k8s-master", "k8s-worker1", "k8s-worker2", "k8s-lb2"));
        Incident master = persisted("k8s-nodes", "k8s-master", "NODE_NOT_READY", IncidentStatus.OPEN);
        off("k8s-master");
        cycles(10);
        assertThat(actions.findAll().stream().filter(a -> a.getIncident().getId().equals(master.getId()))).isEmpty();
        assertThat(incidentsOf("k8s-master")).hasSize(1);
        verify(camunda, never()).startWorkflow(any(), any(), any(), any());
    }

    @Test
    void k8s_master_incident_k8s_lance_le_redemarrage_reel_comme_un_worker_quand_il_est_arme() {
        // Consigne du 2026-11-17 : .153 est une cible AUTO (même flux que les workers : incident NODE_NOT_READY, puis power-on décidé ici).
        props.setDryRun(false);
        props.setActiveVms(java.util.Set.of("k8s-master"));
        Incident incident = persisted("k8s-nodes", "k8s-master", "NODE_NOT_READY", IncidentStatus.OPEN);
        off("k8s-master");
        cycles(3);
        Incident reloaded = incidents.findById(incident.getId()).orElseThrow();
        assertThat(reloaded.getStatus()).isEqualTo(IncidentStatus.IN_PROGRESS);
        var recorded = actions.findAll().stream().filter(a -> a.getIncident().getId().equals(incident.getId())).toList();
        assertThat(recorded).hasSize(1);
        assertThat(recorded.get(0).getTargetNamespace()).isEqualTo("k8s-nodes");
        ArgumentCaptor<RemediationPlan> plan = ArgumentCaptor.forClass(RemediationPlan.class);
        verify(camunda).startWorkflow(any(), any(), plan.capture(), any());
        assertThat(plan.getValue().incidentId()).isEqualTo(incident.getId());
    }

    @Test
    void k8s_master_non_armee_reste_en_mode_ombre_aucune_action_meme_eteinte() {
        props.setDryRun(false);
        props.setActiveVms(java.util.Set.of("k8s-worker1", "k8s-worker2", "k8s-lb2"));   // .153 AUTO mais PAS armée (état avant le palier 3)
        Incident master = persisted("k8s-nodes", "k8s-master", "NODE_NOT_READY", IncidentStatus.OPEN);
        off("k8s-master");
        cycles(10);
        assertThat(actions.findAll().stream().filter(a -> a.getIncident().getId().equals(master.getId()))).isEmpty();
        verify(camunda, never()).startWorkflow(any(), any(), any(), any());
    }

    @Test
    void worker_incident_k8s_lance_le_redemarrage_reel_avec_l_incident_comme_cle() {
        props.setDryRun(false);
        props.setActiveVms(java.util.Set.of("k8s-worker1"));
        Incident incident = persisted("k8s-nodes", "k8s-worker1", "NODE_NOT_READY", IncidentStatus.OPEN);
        off("k8s-worker1");
        cycles(3);
        Incident reloaded = incidents.findById(incident.getId()).orElseThrow();
        assertThat(reloaded.getStatus()).isEqualTo(IncidentStatus.IN_PROGRESS);
        var recorded = actions.findAll().stream().filter(a -> a.getIncident().getId().equals(incident.getId())).toList();
        assertThat(recorded).hasSize(1);
        assertThat(recorded.get(0).getTargetNamespace()).isEqualTo("k8s-nodes");
        ArgumentCaptor<RemediationPlan> plan = ArgumentCaptor.forClass(RemediationPlan.class);
        verify(camunda).startWorkflow(any(), any(), plan.capture(), any());
        assertThat(plan.getValue().incidentId()).isEqualTo(incident.getId());
    }

    @Test
    void episode_resolution_failed_reste_ouvert_sans_nouvel_incident_ni_action_avec_un_seul_rappel() {
        props.setDryRun(false);
        props.setActiveVms(java.util.Set.of("k8s-lb2"));
        Incident failed = persisted("infra", "k8s-lb2", "VM_DOWN", IncidentStatus.RESOLUTION_FAILED);
        off("k8s-lb2");
        cycles(6);
        assertThat(incidentsOf("k8s-lb2")).hasSize(1);
        assertThat(actions.findAll().stream().filter(a -> a.getIncident().getId().equals(failed.getId()))).isEmpty();
        assertThat(historyRepo.findAll().stream().filter(h -> h.getIncident() != null
                && h.getIncident().getId().equals(failed.getId())
                && h.getComment() != null && h.getComment().contains("Épisode ouvert"))).hasSize(1);
        verify(notifications, never()).notifyNewIncident(any(Incident.class));
    }

    @Test
    void cloture_par_preuves_ecrit_deux_preuves_un_PASS_puis_RESOLVED_pour_160() {
        Incident incident = persisted("infra", "k8s-monitoring", "VM_DOWN", IncidentStatus.OPEN);
        when(verifier.verifyInfra(any(), any())).thenReturn(pass(true));

        List<String> first = watch.runCycle(T0);
        assertThat(first).noneMatch(e -> e.startsWith("RESOLVED_WITH_PROOF"));
        assertThat(incidents.findById(incident.getId()).orElseThrow().getStatus()).isEqualTo(IncidentStatus.OPEN);

        List<String> second = watch.runCycle(T0 + CYCLE);
        assertThat(second).contains("RESOLVED_WITH_PROOF:k8s-monitoring:VM_DOWN");

        Incident resolved = incidents.findById(incident.getId()).orElseThrow();
        assertThat(resolved.getStatus()).isEqualTo(IncidentStatus.RESOLVED);
        List<IncidentTerminalEvidence> evidence = evidenceRepo.findByIncidentIdAndPhaseOrderByCapturedAtAsc(
                incident.getId(), TerminalEvidencePhase.AFTER);
        assertThat(evidence).hasSize(2);
        assertThat(evidence).allSatisfy(e -> {
            assertThat(e.getSource()).isEqualTo("VM_RECOVERY:" + incident.getId());
            assertThat(e.getStdout()).isEqualTo("PASS");
            assertThat(e.getVm()).isEqualTo("k8s-monitoring");
        });
        assertThat(verificationRepo.findAll().stream()
                .filter(v -> incident.getId().equals(v.getIncidentId()) && "PASS".equals(v.getResult()))).hasSize(1);
        assertThat(card.hasPassVerification(incident.getId())).isTrue();
        verify(notifications).notifyIncidentResolvedUiOnly(any(Incident.class));
        verify(notifications, never()).notifyIncidentResolved(any(Incident.class));
    }

    @Test
    void jamais_de_RESOLVED_si_les_deux_passes_ne_sont_pas_consecutives_en_base() {
        Incident incident = persisted("infra", "k8s-monitoring", "VM_DOWN", IncidentStatus.OPEN);
        when(verifier.verifyInfra(any(), any())).thenReturn(pass(true), pass(false), pass(true), pass(false));
        cycles(4);   // vrai, FAUX (écrit pour casser la série), vrai, faux
        assertThat(incidents.findById(incident.getId()).orElseThrow().getStatus()).isEqualTo(IncidentStatus.OPEN);
        assertThat(verificationRepo.findAll().stream().filter(v -> incident.getId().equals(v.getIncidentId()))).isEmpty();
        assertThat(card.hasPassVerification(incident.getId())).isFalse();
    }

    @Test
    void noeud_worker_revenu_Ready_sans_redemarrage_est_cloture_par_deux_lectures_persistees() {
        Incident incident = persisted("k8s-nodes", "k8s-worker1", "NODE_NOT_READY", IncidentStatus.OPEN);
        when(nodeReadiness.inspectNodeReadiness("k8s-worker1"))
                .thenReturn(new NodeReadinessObservation(true, true, "True", "Ready", "KubeletReady", 5L));
        // Un worker n'est « revenu » que si TOUTES les preuves (kubelet, SSH, etcd, CNPG, backend) passent.
        when(verifier.verifyWorker(any(), any())).thenReturn(pass(true));
        cycles(2);
        assertThat(incidents.findById(incident.getId()).orElseThrow().getStatus()).isEqualTo(IncidentStatus.RESOLVED);
        assertThat(evidenceRepo.findByIncidentIdAndPhaseOrderByCapturedAtAsc(incident.getId(),
                TerminalEvidencePhase.AFTER)).hasSize(2);
        assertThat(card.hasPassVerification(incident.getId())).isTrue();
        verify(notifications).notifyIncidentResolved(any(Incident.class));
    }
}
