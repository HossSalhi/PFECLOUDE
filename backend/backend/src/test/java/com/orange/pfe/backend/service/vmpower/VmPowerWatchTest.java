package com.orange.pfe.backend.service.vmpower;

import com.orange.pfe.backend.config.VmPowerProperties;
import com.orange.pfe.backend.model.action.ActionType;
import com.orange.pfe.backend.model.action.AutomationMode;
import com.orange.pfe.backend.model.action.IncidentAction;
import com.orange.pfe.backend.model.core.Incident;
import com.orange.pfe.backend.model.enums.IncidentSeverity;
import com.orange.pfe.backend.model.enums.IncidentSource;
import com.orange.pfe.backend.model.enums.IncidentStatus;
import com.orange.pfe.backend.model.workflow.WorkflowInstance;
import com.orange.pfe.backend.repository.IncidentActionRepository;
import com.orange.pfe.backend.repository.IncidentRepository;
import com.orange.pfe.backend.service.ActionService;
import com.orange.pfe.backend.service.CamundaWorkflowService;
import com.orange.pfe.backend.service.IncidentCardService;
import com.orange.pfe.backend.service.IncidentHistoryService;
import com.orange.pfe.backend.service.LeaderElectionService;
import com.orange.pfe.backend.service.NotificationService;
import com.orange.pfe.backend.service.WorkflowService;
import com.orange.pfe.backend.service.remediation.RemediationPlan;
import com.orange.pfe.backend.service.remediation.NodeReadinessObservation;
import com.orange.pfe.backend.service.remediation.RemediationExecutor;
import com.orange.pfe.backend.service.vmpower.VmPowerPolicy.PowerState;
import com.orange.pfe.backend.service.vmpower.VmRecoveryVerifier.PassResult;
import com.orange.pfe.backend.service.vmpower.WatchdogClient.Evidence;
import com.orange.pfe.backend.service.vmpower.WatchdogClient.Guards;
import com.orange.pfe.backend.service.vmpower.WatchdogClient.VmState;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Nested;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;
import org.springframework.dao.DataIntegrityViolationException;
import org.springframework.test.util.ReflectionTestUtils;

import java.time.Instant;
import java.time.LocalDateTime;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.Set;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyCollection;
import static org.mockito.ArgumentMatchers.anyInt;
import static org.mockito.ArgumentMatchers.anyLong;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.contains;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.atLeastOnce;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.times;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

/**
 * VMDOWN-POLICY-20261105 — décision de redémarrage (VmPowerWatch) : détection, confirmation, autorisation,
 * mode ombre, épisode RESOLUTION_FAILED, .160 en DÉTECTION SEULE (consigne finale du 2026-11-06 : jamais rallumée
 * automatiquement), route directe DORMANTE (exercée par la couture de test), .153 ignorée.
 * Tout est simulé (aucun réseau,
 * aucune VM, aucun Camunda) ; le temps est injecté (cycles à T0, T0+15 s, T0+30 s...).
 */
class VmPowerWatchTest {

    static final long T0 = 1_800_000_000_000L;
    static final long CYCLE = 15_000L;

    private VmPowerProperties props;
    private WatchdogClient watchdog;
    private VmProbes probes;
    private IncidentRepository incidents;
    private IncidentActionRepository actions;
    private IncidentHistoryService history;
    private ActionService actionService;
    private WorkflowService workflowService;
    private CamundaWorkflowService camunda;
    private NotificationService notifications;
    private LeaderElectionService leader;
    private IncidentCardService card;
    private VmRecoveryVerifier verifier;
    private RemediationExecutor nodeReadiness;
    private VmPowerWatch watch;

    private final Map<String, VmState> states = new LinkedHashMap<>();
    private final Map<String, VmProbes.Liveness> live = new LinkedHashMap<>();
    private boolean watchdogUp = true;

    @BeforeEach
    void setUp() {
        props = new VmPowerProperties();
        props.setEnabled(true);          // dryRun = true par défaut (MODE OMBRE)
        watchdog = mock(WatchdogClient.class);
        probes = mock(VmProbes.class);
        incidents = mock(IncidentRepository.class);
        actions = mock(IncidentActionRepository.class);
        history = mock(IncidentHistoryService.class);
        actionService = mock(ActionService.class);
        workflowService = mock(WorkflowService.class);
        camunda = mock(CamundaWorkflowService.class);
        notifications = mock(NotificationService.class);
        leader = mock(LeaderElectionService.class);
        card = mock(IncidentCardService.class);
        verifier = mock(VmRecoveryVerifier.class);
        nodeReadiness = mock(RemediationExecutor.class);
        // par défaut : rien n'est « récupéré » (aucune clôture accidentelle dans les tests de décision)
        when(verifier.verifyInfra(any(), any())).thenReturn(passResult(false));
        when(nodeReadiness.inspectNodeReadiness(anyString()))
                .thenReturn(new NodeReadinessObservation(true, false, "False", "NotReady"));
        when(leader.isLeader()).thenReturn(true);
        when(camunda.isReachable()).thenReturn(true);
        for (VmPowerPolicy.Entry e : VmPowerPolicy.entries()) {
            states.put(e.name(), state(e.name(), PowerState.RUNNING));
            live.put(e.name(), up());
        }
        when(watchdog.states()).thenAnswer(inv -> watchdogUp
                ? new WatchdogClient.Result<>(true, 200, new LinkedHashMap<>(states), null)
                : new WatchdogClient.Result<Map<String, VmState>>(false, 0, null, "TIMEOUT"));
        when(probes.livenessAll(any())).thenAnswer(inv -> new LinkedHashMap<>(live));
        when(incidents.save(any(Incident.class))).thenAnswer(inv -> {
            Incident i = inv.getArgument(0);
            if (i.getId() == null) {
                ReflectionTestUtils.setField(i, "id", 900L);
            }
            return i;
        });
        IncidentAction created = new IncidentAction();
        ReflectionTestUtils.setField(created, "id", 500L);
        when(actionService.createAction(any(), any(), anyString(), anyString(), anyString(), any(), anyString(), any()))
                .thenReturn(created);
        when(workflowService.startWorkflow(any(Incident.class), anyString(), anyString(), anyString()))
                .thenReturn(new WorkflowInstance());
        watch = new VmPowerWatch(props, watchdog, probes, incidents, actions, history, actionService,
                workflowService, camunda, notifications, leader, card, verifier, nodeReadiness);
    }

    /** La couture de test (route directe dormante) ne doit jamais fuir d'un test à l'autre. */
    @AfterEach
    void restaurerLaTableDeProduction() {
        VmPowerPolicy.resetForTest();
    }

    // ── Décor ────────────────────────────────────────────────────────────────────────────────────

    private static VmState state(String vm, PowerState ps) {
        return state(vm, ps, 1_000L, false);
    }

    private static VmState state(String vm, PowerState ps, long ageMs, boolean maintenance) {
        Evidence ev = new Evidence("vmrun -T ws list", Instant.now(), ageMs, 120, 5,
                ps == PowerState.RUNNING, "aaaaaaaaaaaa", "bbbbbbbbbbbb");
        Guards g = new Guards(maintenance, false, true, List.of(), 0, 0, null, 8000);
        return new VmState(vm, VmPowerPolicy.modeOf(vm).name(), ps, "OK", ev, g);
    }

    private static PassResult passResult(boolean ok) {
        Map<String, Boolean> c = new LinkedHashMap<>();
        c.put("power_running", ok);
        return new PassResult(ok, c, ok ? "tous les critères OK" : "critères en échec [power_running]");
    }

    private static NodeReadinessObservation ready() {
        return new NodeReadinessObservation(true, true, "True", "Ready");
    }

    private static VmProbes.Liveness up() {
        return new VmProbes.Liveness(3, 3, List.of("tcp/22", "tcp/80", "udp/53"), List.of());
    }

    private static VmProbes.Liveness down() {
        return new VmProbes.Liveness(0, 3, List.of(), List.of("tcp/22", "tcp/80", "udp/53"));
    }

    private void off(String vm) {
        states.put(vm, state(vm, PowerState.POWERED_OFF));
        live.put(vm, down());
    }

    private List<String> cycles(int n) {
        List<String> all = new ArrayList<>();
        for (int i = 0; i < n; i++) {
            all.addAll(watch.runCycle(T0 + i * CYCLE));
        }
        return all;
    }

    private Incident openIncident(String ns, String app, String type, IncidentStatus status, long id) {
        Incident i = new Incident();
        ReflectionTestUtils.setField(i, "id", id);
        i.setNamespace(ns);
        i.setApp(app);
        i.setIncidentType(type);
        i.setStatus(status);
        i.setSource(IncidentSource.OTHER);
        i.setTitle("t");
        i.setDescription("d");
        when(incidents.findFirstByNamespaceAndAppAndIncidentTypeAndStatusNotInOrderByCreatedAtDesc(
                eq(ns), eq(app), eq(type), anyCollection())).thenReturn(Optional.of(i));
        return i;
    }

    private void real(String... vms) {
        props.setDryRun(false);
        props.setActiveVms(Set.of(vms));
    }

    private void noAction() {
        verify(actionService, never()).createAction(any(), any(), any(), any(), any(), any(), any(), any());
        verify(workflowService, never()).startWorkflow(any(Incident.class), any(), any(), any());
        verify(camunda, never()).startWorkflow(any(), any(), any(), any());
    }

    // ── Détection .162 (indépendante de .160 et de NODE_NOT_READY) ───────────────────────────────

    @Test
    void lb2_POWERED_OFF_confirme_cree_VM_DOWN_infra_et_notifie_complet() {
        off("k8s-lb2");
        List<String> ev = cycles(3);   // 0 s, 15 s, 30 s : 3 observations, écart 30 s

        assertTrue(ev.contains("INCIDENT_CREATED:k8s-lb2"), ev.toString());
        ArgumentCaptor<Incident> c = ArgumentCaptor.forClass(Incident.class);
        verify(incidents, atLeastOnce()).save(c.capture());
        Incident i = c.getAllValues().get(0);
        assertEquals("VM_DOWN", i.getIncidentType());
        assertEquals("infra", i.getNamespace());
        assertEquals("k8s-lb2", i.getApp());
        assertEquals(IncidentSource.OTHER, i.getSource());
        verify(notifications).notifyNewIncident(any(Incident.class));
        verify(notifications, never()).notifyNewIncidentUiOnly(any(Incident.class));
        // mode ombre par défaut : autorisé mais jamais demandé
        assertTrue(ev.contains("SHADOW_WOULD_RESTART:k8s-lb2"), ev.toString());
        noAction();
    }

    @Test
    void un_ou_deux_cycles_ne_creent_rien() {
        off("k8s-lb2");
        assertFalse(cycles(1).stream().anyMatch(e -> e.startsWith("INCIDENT_CREATED")));
        assertFalse(cycles(2).stream().anyMatch(e -> e.startsWith("INCIDENT_CREATED")),
                "2 observations espacées de 15 s ne suffisent pas (30 s requises)");
        verify(incidents, never()).save(any(Incident.class));
        noAction();
    }

    @Test
    void etat_UNKNOWN_ou_preuve_perimee_ne_cree_rien_et_rompt_la_serie() {
        off("k8s-lb2");
        watch.runCycle(T0);
        states.put("k8s-lb2", state("k8s-lb2", PowerState.UNKNOWN));
        watch.runCycle(T0 + CYCLE);          // série rompue
        off("k8s-lb2");
        watch.runCycle(T0 + 2 * CYCLE);
        List<String> ev = watch.runCycle(T0 + 3 * CYCLE);   // seulement 2 obs. consécutives depuis la rupture
        assertFalse(ev.stream().anyMatch(e -> e.startsWith("INCIDENT_CREATED")), ev.toString());

        // preuve périmée (> 15 s) = pas une observation
        states.put("k8s-lb2", state("k8s-lb2", PowerState.POWERED_OFF, 20_000L, false));
        live.put("k8s-lb2", down());
        List<String> stale = new ArrayList<>();
        for (int i = 4; i < 9; i++) {
            stale.addAll(watch.runCycle(T0 + i * CYCLE));
        }
        assertFalse(stale.stream().anyMatch(e -> e.startsWith("INCIDENT_CREATED")), stale.toString());
        verify(incidents, never()).save(any(Incident.class));
    }

    @Test
    void watchdog_injoignable_ne_cree_rien_et_ne_demarre_rien() {
        watchdogUp = false;
        off("k8s-lb2");       // les sondes échouent, mais l'état d'alimentation n'est PAS prouvé
        List<String> ev = cycles(6);
        assertTrue(ev.contains("WATCHDOG_UNREACHABLE"));
        assertFalse(ev.stream().anyMatch(e -> e.startsWith("INCIDENT_CREATED")), ev.toString());
        verify(incidents, never()).save(any(Incident.class));
        noAction();
    }

    @Test
    void lb2_allumee_mais_muette_cree_VM_UNREACHABLE_sans_aucune_demande() {
        states.put("k8s-lb2", state("k8s-lb2", PowerState.RUNNING));
        live.put("k8s-lb2", down());
        List<String> ev = cycles(VmPowerWatch.SILENT_CYCLES_BEFORE_UNREACHABLE + 1);

        assertTrue(ev.contains("UNREACHABLE_INCIDENT:k8s-lb2"), ev.toString());
        ArgumentCaptor<Incident> c = ArgumentCaptor.forClass(Incident.class);
        verify(incidents, atLeastOnce()).save(c.capture());
        assertEquals("VM_UNREACHABLE", c.getAllValues().get(0).getIncidentType());
        assertFalse(ev.stream().anyMatch(e -> e.startsWith("SHADOW") || e.startsWith("LAUNCHED")), ev.toString());
        noAction();
    }

    @Test
    void perte_de_160_ne_bloque_pas_la_detection_de_162() {
        // .160 muette ET d'état inconnu : cela ne doit rien changer à la détection de .162
        states.put("k8s-monitoring", state("k8s-monitoring", PowerState.UNKNOWN));
        live.put("k8s-monitoring", down());
        off("k8s-lb2");
        List<String> ev = cycles(3);
        assertTrue(ev.contains("INCIDENT_CREATED:k8s-lb2"), ev.toString());
        assertFalse(ev.stream().anyMatch(e -> e.contains("k8s-monitoring")), ev.toString());
    }

    // ── .160 : DÉTECTION SEULE (VM de supervision) — et route DIRECTE du backend, dormante ───────

    private static WatchdogClient.RestartOutcome started() {
        return new WatchdogClient.RestartOutcome(WatchdogClient.Kind.STARTED, 200, "STARTED", null, null, null,
                "req-1", false, null);
    }

    private static WatchdogClient.RestartOutcome refusedByWatchdog() {
        return new WatchdogClient.RestartOutcome(WatchdogClient.Kind.REFUSED, 423, "MAINTENANCE", "MAINTENANCE",
                null, null, "req-2", false, null);
    }

    /** Simule la ligne RESTART_VM de .160 créée à {@code createdAtMs} (le repository réel la retrouve par cible). */
    private void monitoringRestartActionAt(long createdAtMs) {
        IncidentAction a = new IncidentAction();
        ReflectionTestUtils.setField(a, "id", 500L);
        ReflectionTestUtils.setField(a, "createdAt", LocalDateTime.ofInstant(
                Instant.ofEpochMilli(createdAtMs), java.time.ZoneId.systemDefault()));
        when(actions.findRecentActionsForTarget(eq("infra"), eq("k8s-monitoring"), eq(ActionType.RESTART_VM), any()))
                .thenReturn(List.of(a));
    }

    /**
     * Comme le repository réel : la ligne RESTART_VM de .160 n'existe QU'APRÈS {@code createAction} (sinon le cooldown
     * de 30 min bloquerait le lancement lui-même). Retourne l'indicateur « action créée ».
     */
    private java.util.concurrent.atomic.AtomicBoolean statefulMonitoringAction(long createdAtMs) {
        java.util.concurrent.atomic.AtomicBoolean created = new java.util.concurrent.atomic.AtomicBoolean(false);
        IncidentAction a = new IncidentAction();
        ReflectionTestUtils.setField(a, "id", 500L);
        ReflectionTestUtils.setField(a, "createdAt", LocalDateTime.ofInstant(
                Instant.ofEpochMilli(createdAtMs), java.time.ZoneId.systemDefault()));
        when(actionService.createAction(any(), any(), anyString(), anyString(), anyString(), any(), anyString(), any()))
                .thenAnswer(inv -> {
                    created.set(true);
                    return a;
                });
        when(actions.findRecentActionsForTarget(eq("infra"), eq("k8s-monitoring"), eq(ActionType.RESTART_VM), any()))
                .thenAnswer(inv -> created.get() ? List.of(a) : List.of());
        return created;
    }

    @Test
    void k8s_monitoring_eteinte_detection_seule_incident_interface_seule_et_aucune_demande() {
        off("k8s-monitoring");
        List<String> ev = cycles(3);

        assertTrue(ev.contains("INCIDENT_CREATED:k8s-monitoring"), ev.toString());
        assertTrue(ev.contains("DETECT_ONLY:k8s-monitoring"), ev.toString());
        assertFalse(ev.stream().anyMatch(e -> e.startsWith("SHADOW_WOULD_RESTART") || e.startsWith("LAUNCH")), ev.toString());
        verify(notifications).notifyNewIncidentUiOnly(any(Incident.class));
        verify(notifications, never()).notifyNewIncident(any(Incident.class));
        verify(notifications, never()).notifyEscalation(any());
        verify(notifications, never()).notifyRemediationFailed(any(), any());
        verify(history, atLeastOnce()).record(any(Incident.class), eq(IncidentStatus.OPEN), contains("DÉTECTION SEULE"));
        verify(watchdog, never()).restart(anyString(), anyLong(), any());
        noAction();
    }

    @Test
    void k8s_monitoring_meme_armee_en_mode_reel_ne_redemarre_jamais_et_aucune_autre_machine_non_plus() {
        // Le nom de .160 dans active-vms ne change RIEN : la politique (DETECT_ONLY) tranche avant les trois verrous.
        real("k8s-monitoring", "k8s-worker1", "k8s-worker2", "k8s-lb2");
        off("k8s-monitoring");                                 // .160 éteinte ; toutes les autres tournent

        List<String> ev = cycles(12);

        assertTrue(ev.contains("DETECT_ONLY:k8s-monitoring"), ev.toString());
        verify(watchdog, never()).restart(anyString(), anyLong(), any());   // ni .160, ni .162, ni les workers, ni .153
        noAction();
        verify(notifications, never()).notifyNewIncident(any(Incident.class));
        verify(notifications, never()).notifyEscalation(any());
        verify(notifications, never()).notifyRemediationFailed(any(), any());
    }

    @Test
    void k8s_monitoring_detection_seule_rappel_d_historique_espace_de_15_min_sans_email() {
        off("k8s-monitoring");
        Incident incident = openIncident("infra", "k8s-monitoring", "VM_DOWN", IncidentStatus.OPEN, 77L);
        cycles(3);
        verify(history, times(1)).record(eq(incident), eq(IncidentStatus.OPEN), contains("DÉTECTION SEULE — k8s-monitoring"));
        watch.runCycle(T0 + 10 * 60_000L);   // 10 min plus tard : pas de rappel (15 min)
        verify(history, times(1)).record(eq(incident), eq(IncidentStatus.OPEN), contains("DÉTECTION SEULE — k8s-monitoring"));
        watch.runCycle(T0 + 16 * 60_000L);   // > 15 min : un rappel
        verify(history, times(2)).record(eq(incident), eq(IncidentStatus.OPEN), contains("DÉTECTION SEULE — k8s-monitoring"));
        verify(notifications, never()).notifyNewIncident(any(Incident.class));
        verify(notifications, never()).notifyEscalation(any());
        verify(watchdog, never()).restart(anyString(), anyLong(), any());
        noAction();
    }

    @Test
    void k8s_monitoring_allumee_mais_muette_ouvre_VM_UNREACHABLE_interface_seule_sans_demande() {
        // VM allumée selon vmrun mais toutes les sondes en échec : détection seule, jamais un redémarrage.
        live.put("k8s-monitoring", down());
        List<String> ev = cycles(VmPowerWatch.SILENT_CYCLES_BEFORE_UNREACHABLE + 1);

        assertTrue(ev.contains("UNREACHABLE_INCIDENT:k8s-monitoring"), ev.toString());
        // (le dépôt simulé ne retrouve pas l'incident : le seuil franchi au cycle suivant le crée de nouveau — la base réelle
        //  l'en empêche ; ce qui compte ici : interface seulement, jamais d'e-mail, jamais de demande)
        verify(notifications, atLeastOnce()).notifyNewIncidentUiOnly(any(Incident.class));
        verify(notifications, never()).notifyNewIncident(any(Incident.class));
        verify(watchdog, never()).restart(anyString(), anyLong(), any());
        noAction();
    }

    /**
     * Route DIRECTE du backend (sans Camunda, sans e-mail) : DORMANTE depuis la consigne finale du 2026-11-06 (aucune VM
     * de la table ne l'utilise) mais CONSERVÉE parce qu'elle a été validée par un essai réel sur .160 à 13:28. Ces tests
     * l'exercent en remplaçant, par la couture de test, la ligne de .160 par l'ancienne variante AUTO / BACKEND_DIRECT /
     * UI_ONLY ; la table de production est restaurée après chaque test.
     */
    @Nested
    class RouteDirecteDormante {

        @BeforeEach
        void armerLaRouteDirecteSurK8sMonitoring() {
            VmPowerPolicy.overrideEntryForTest(new VmPowerPolicy.Entry("k8s-monitoring", "192.168.148.160",
                    VmPowerPolicy.Mode.AUTO, VmPowerPolicy.VmClass.INFRA_MONITORING, "infra", "k8s-monitoring",
                    "VM_DOWN", VmPowerPolicy.Route.BACKEND_DIRECT, VmPowerPolicy.Notifications.UI_ONLY));
        }

        @Test
        void k8s_monitoring_mode_reel_redemarre_en_direct_une_seule_fois_sans_camunda_ni_email() {
            real("k8s-monitoring");
            off("k8s-monitoring");
            when(watchdog.restart(anyString(), anyLong(), any())).thenReturn(started());

            List<String> ev = cycles(3);

            assertTrue(ev.contains("LAUNCHED_DIRECT:k8s-monitoring:STARTED"), ev.toString());
            // la DEMANDE est persistée (action RESTART_VM) AVANT l'appel au watchdog, puis UN SEUL POST
            org.mockito.InOrder order = org.mockito.Mockito.inOrder(actionService, incidents, watchdog);
            order.verify(actionService).createAction(any(), eq(ActionType.RESTART_VM), anyString(), anyString(),
                    eq("infra"), any(), eq("k8s-monitoring"), eq(AutomationMode.AUTO));
            order.verify(watchdog, times(1)).restart(eq("k8s-monitoring"), eq(900L), any());
            verify(actionService).markActionCompleted(eq(500L), anyString(), anyString());
            // jamais de Camunda, jamais d'e-mail, jamais d'escalade
            verify(workflowService, never()).startWorkflow(any(Incident.class), any(), any(), any());
            verify(camunda, never()).startWorkflow(any(), any(), any(), any());
            verify(notifications).notifyNewIncidentUiOnly(any(Incident.class));
            verify(notifications, never()).notifyNewIncident(any(Incident.class));
            verify(notifications, never()).notifyEscalation(any());
            verify(notifications, never()).notifyRemediationFailed(any(), any());
            verify(history, atLeastOnce()).record(any(Incident.class), eq(IncidentStatus.IN_PROGRESS), contains("PERSISTÉE"));
        }

        @Test
        void k8s_monitoring_le_post_n_est_jamais_rejoue_aux_cycles_suivants() {
            real("k8s-monitoring");
            off("k8s-monitoring");
            Incident incident = openIncident("infra", "k8s-monitoring", "VM_DOWN", IncidentStatus.OPEN, 77L);
            statefulMonitoringAction(T0 + 30_000L);
            when(watchdog.restart(anyString(), anyLong(), any())).thenReturn(started());

            cycles(3);                                   // lancement au 3e cycle
            assertEquals(IncidentStatus.IN_PROGRESS, incident.getStatus());
            for (int k = 3; k < 12; k++) {               // 9 cycles de plus, VM toujours « éteinte » pour le watch
                watch.runCycle(T0 + k * CYCLE);
            }

            verify(watchdog, times(1)).restart(eq("k8s-monitoring"), eq(77L), any());
            verify(actionService, times(1)).createAction(any(), any(), anyString(), anyString(), anyString(), any(),
                    anyString(), any());
            assertEquals(IncidentStatus.IN_PROGRESS, incident.getStatus(), "toujours dans le budget de preuves");
        }

        @Test
        void k8s_monitoring_refus_du_watchdog_donne_un_etat_explicite_interface_seulement() {
            real("k8s-monitoring");
            off("k8s-monitoring");
            Incident incident = openIncident("infra", "k8s-monitoring", "VM_DOWN", IncidentStatus.OPEN, 77L);
            when(watchdog.restart(anyString(), anyLong(), any())).thenReturn(refusedByWatchdog());

            List<String> ev = cycles(3);

            assertTrue(ev.contains("DIRECT_RESTART_FAILED:k8s-monitoring"), ev.toString());
            assertEquals(IncidentStatus.RESOLUTION_FAILED, incident.getStatus());
            verify(actionService).markActionFailed(eq(500L), contains("MAINTENANCE"), anyString());
            verify(notifications).notifyRemediationFailedUiOnly(eq(incident), contains("MAINTENANCE"));
            verify(notifications, never()).notifyRemediationFailed(any(), any());   // aucun e-mail
            verify(notifications, never()).notifyEscalation(any());
            verify(watchdog, times(1)).restart(anyString(), anyLong(), any());
            // épisode ouvert : plus aucun démarrage aux cycles suivants (pas de boucle)
            for (int k = 3; k < 10; k++) {
                watch.runCycle(T0 + k * CYCLE);
            }
            verify(watchdog, times(1)).restart(anyString(), anyLong(), any());
        }

        @Test
        void k8s_monitoring_sans_reponse_exploitable_du_watchdog_reste_explicite_et_sans_rejeu() {
            real("k8s-monitoring");
            off("k8s-monitoring");
            Incident incident = openIncident("infra", "k8s-monitoring", "VM_DOWN", IncidentStatus.OPEN, 77L);
            when(watchdog.restart(anyString(), anyLong(), any())).thenReturn(new WatchdogClient.RestartOutcome(
                    WatchdogClient.Kind.TRANSPORT_ERROR, 0, null, null, null, null, "req-3", false, "TIMEOUT"));

            List<String> ev = cycles(3);

            assertTrue(ev.contains("DIRECT_RESTART_FAILED:k8s-monitoring"), ev.toString());
            assertEquals(IncidentStatus.RESOLUTION_FAILED, incident.getStatus());
            verify(watchdog, times(1)).restart(anyString(), anyLong(), any());
            verify(notifications, never()).notifyRemediationFailed(any(), any());
        }

        @Test
        void k8s_monitoring_budget_de_preuves_depasse_passe_en_resolution_failed_sans_email() {
            real("k8s-monitoring");
            Incident incident = openIncident("infra", "k8s-monitoring", "VM_DOWN", IncidentStatus.IN_PROGRESS, 77L);
            monitoringRestartActionAt(T0 - 7 * 60_000L);          // demande acceptée il y a 7 min ; budget = 6 min
            List<String> ev = cycles(1);
            assertTrue(ev.contains("DIRECT_RESTART_FAILED:k8s-monitoring"), ev.toString());
            assertEquals(IncidentStatus.RESOLUTION_FAILED, incident.getStatus());
            verify(notifications).notifyRemediationFailedUiOnly(eq(incident), contains("budget"));
            verify(notifications, never()).notifyRemediationFailed(any(), any());
            verify(watchdog, never()).restart(anyString(), anyLong(), any());
        }

        @Test
        void k8s_monitoring_dans_le_budget_de_preuves_reste_en_cours_sans_nouvelle_action() {
            real("k8s-monitoring");
            Incident incident = openIncident("infra", "k8s-monitoring", "VM_DOWN", IncidentStatus.IN_PROGRESS, 77L);
            monitoringRestartActionAt(T0 - 2 * 60_000L);          // il y a 2 min
            cycles(3);
            assertEquals(IncidentStatus.IN_PROGRESS, incident.getStatus());
            verify(watchdog, never()).restart(anyString(), anyLong(), any());
            noAction();
        }

        /**
         * 2026-11-18 (cooldown 30 -> 5 min) : la fenêtre de recherche de la dernière action ne doit pas être plus COURTE que le budget de
         * preuves du rallumage direct (6 min), sinon une action de 5 min 30 s serait vue comme « aucune action » (NEVER) et ferait échouer
         * le budget trop tôt. Ici le repository est simulé COMME LE VRAI : il ne renvoie que les actions de createdAt >= cutoff.
         */
        @Test
        void k8s_monitoring_action_de_5_min_30_reste_dans_le_budget_de_6_min_malgre_le_cooldown_de_5_min() {
            real("k8s-monitoring");
            Incident incident = openIncident("infra", "k8s-monitoring", "VM_DOWN", IncidentStatus.IN_PROGRESS, 77L);
            IncidentAction a = new IncidentAction();
            ReflectionTestUtils.setField(a, "id", 500L);
            LocalDateTime created = LocalDateTime.ofInstant(
                    Instant.ofEpochMilli(T0 - 5 * 60_000L - 30_000L), java.time.ZoneId.systemDefault());
            ReflectionTestUtils.setField(a, "createdAt", created);
            when(actions.findRecentActionsForTarget(eq("infra"), eq("k8s-monitoring"), eq(ActionType.RESTART_VM), any()))
                    .thenAnswer(inv -> created.isBefore(inv.<LocalDateTime>getArgument(3)) ? List.<IncidentAction>of() : List.of(a));

            List<String> ev = cycles(1);

            assertFalse(ev.contains("DIRECT_RESTART_FAILED:k8s-monitoring"), ev.toString());
            assertEquals(IncidentStatus.IN_PROGRESS, incident.getStatus(), "5 min 30 s < budget de 6 min : pas encore dépassé");
            verify(notifications, never()).notifyRemediationFailedUiOnly(any(), any());
            verify(watchdog, never()).restart(anyString(), anyLong(), any());
        }

        @Test
        void k8s_monitoring_rallumage_direct_cloture_par_deux_passes_de_preuves_en_interface_seulement() {
            Incident incident = openIncident("infra", "k8s-monitoring", "VM_DOWN", IncidentStatus.IN_PROGRESS, 77L);
            monitoringRestartActionAt(T0 - 60_000L);
            when(verifier.verifyInfra(any(), any())).thenReturn(passResult(true));
            when(card.hasTwoConsecutiveVmRecoveryEvidence(77L, "k8s-monitoring")).thenReturn(true);

            List<String> ev = cycles(2);

            assertTrue(ev.contains("RESOLVED_WITH_PROOF:k8s-monitoring:VM_DOWN"), ev.toString());
            assertEquals(IncidentStatus.RESOLVED, incident.getStatus());
            verify(notifications).notifyIncidentResolvedUiOnly(incident);
            verify(notifications, never()).notifyIncidentResolved(any(Incident.class));
        }

        @Test
        void k8s_monitoring_non_activee_explicitement_reste_en_mode_ombre_meme_si_les_autres_le_sont() {
            real("k8s-worker1", "k8s-worker2", "k8s-lb2");        // .160 volontairement absente de la liste
            off("k8s-monitoring");
            List<String> ev = cycles(3);
            assertTrue(ev.contains("SHADOW_WOULD_RESTART:k8s-monitoring"), ev.toString());
            verify(watchdog, never()).restart(anyString(), anyLong(), any());
            noAction();
        }

        @Test
        void la_detection_de_160_ne_declenche_jamais_le_redemarrage_d_une_autre_machine() {
            real("k8s-monitoring", "k8s-worker1", "k8s-worker2", "k8s-lb2");
            off("k8s-monitoring");                                 // .160 éteinte ; toutes les autres tournent
            when(watchdog.restart(anyString(), anyLong(), any())).thenReturn(started());

            cycles(8);

            verify(watchdog, atLeastOnce()).restart(eq("k8s-monitoring"), anyLong(), any());
            verify(watchdog, never()).restart(eq("k8s-lb2"), anyLong(), any());
            verify(watchdog, never()).restart(eq("k8s-worker1"), anyLong(), any());
            verify(watchdog, never()).restart(eq("k8s-worker2"), anyLong(), any());
            verify(watchdog, never()).restart(eq("k8s-master"), anyLong(), any());
        }
    }

    @Test
    void k8s_monitoring_episode_ouvert_rappel_d_historique_espace_sans_email_ni_nouveau_demarrage() {
        real("k8s-monitoring");
        off("k8s-monitoring");
        Incident failed = openIncident("infra", "k8s-monitoring", "VM_DOWN", IncidentStatus.RESOLUTION_FAILED, 77L);
        cycles(4);   // confirmé
        verify(history, times(1)).record(eq(failed), eq(IncidentStatus.RESOLUTION_FAILED), contains("Épisode ouvert"));
        watch.runCycle(T0 + 10 * 60_000L);   // 10 min plus tard : pas de rappel (15 min)
        verify(history, times(1)).record(eq(failed), eq(IncidentStatus.RESOLUTION_FAILED), contains("Épisode ouvert"));
        watch.runCycle(T0 + 16 * 60_000L);   // > 15 min : un rappel
        verify(history, times(2)).record(eq(failed), eq(IncidentStatus.RESOLUTION_FAILED), contains("Épisode ouvert"));
        verify(notifications, never()).notifyNewIncident(any(Incident.class));
        verify(watchdog, never()).restart(anyString(), anyLong(), any());
        noAction();
    }

    // ── .153 : ignorée ───────────────────────────────────────────────────────────────────────────

    // Consigne du 2026-11-17 : .153 n'est plus « ignorée » (DENY_RESTART) ; c'est une cible AUTO au même titre qu'un worker.
    // Le mode DENY_RESTART reste honoré (voir le 3ᵉ test) ; seul l'ARMEMENT (ACTIVE_VMS + DRYRUN=false) décide d'un vrai démarrage.

    @Test
    void k8s_master_arme_et_eteinte_confirmee_lance_le_redemarrage_reel_comme_un_worker() {
        real("k8s-master", "k8s-worker1", "k8s-worker2", "k8s-lb2");
        off("k8s-master");
        Incident incident = openIncident("k8s-nodes", "k8s-master", "NODE_NOT_READY", IncidentStatus.OPEN, 78L);
        List<String> ev = cycles(10);

        assertTrue(ev.contains("LAUNCHED:k8s-master"), ev.toString());
        verify(actionService).createAction(eq(incident), eq(ActionType.RESTART_VM), anyString(), anyString(),
                eq("k8s-nodes"), any(), eq("k8s-master"), eq(AutomationMode.AUTO));
        verify(workflowService).startWorkflow(eq(incident), eq("Node-Remediation-Workflow"), eq("k8s-nodes"),
                eq("k8s-master"));
        ArgumentCaptor<RemediationPlan> plan = ArgumentCaptor.forClass(RemediationPlan.class);
        verify(camunda).startWorkflow(any(), any(), plan.capture(), any());
        assertEquals(ActionType.RESTART_VM, plan.getValue().actionType());
        assertEquals(Long.valueOf(78L), plan.getValue().incidentId(), "l'incident est la clé d'idempotence");
        assertEquals(IncidentStatus.IN_PROGRESS, incident.getStatus());
        verify(watchdog, never()).restart(anyString(), anyLong(), any());   // le POST part du pipeline Camunda, jamais d'ici
    }

    @Test
    void k8s_master_eteinte_mais_non_activee_reste_en_mode_ombre_aucune_action() {
        real("k8s-worker1", "k8s-worker2", "k8s-lb2");                  // armement des trois autres seulement
        off("k8s-master");
        openIncident("k8s-nodes", "k8s-master", "NODE_NOT_READY", IncidentStatus.OPEN, 78L);
        List<String> ev = cycles(6);
        assertTrue(ev.contains("SHADOW_WOULD_RESTART:k8s-master"), ev.toString());
        assertFalse(ev.contains("LAUNCHED:k8s-master"), ev.toString());
        noAction();
    }

    @Test
    void k8s_master_en_deny_restart_force_est_refusee_aucune_action_aucun_incident() {
        VmPowerPolicy.overrideEntryForTest(new VmPowerPolicy.Entry("k8s-master", "192.168.148.153",
                VmPowerPolicy.Mode.DENY_RESTART, VmPowerPolicy.VmClass.K8S_CONTROL_PLANE, "k8s-nodes", "k8s-master",
                "NODE_NOT_READY", VmPowerPolicy.Route.NONE, VmPowerPolicy.Notifications.EMAIL_AND_UI));
        try {
            real("k8s-master", "k8s-worker1", "k8s-worker2", "k8s-lb2");
            off("k8s-master");
            openIncident("k8s-nodes", "k8s-master", "NODE_NOT_READY", IncidentStatus.OPEN, 78L);
            List<String> ev = cycles(10);
            assertFalse(ev.contains("LAUNCHED:k8s-master"), ev.toString());
            assertFalse(ev.contains("SHADOW_WOULD_RESTART:k8s-master"), ev.toString());
            assertTrue(ev.stream().anyMatch(e -> e.startsWith("REFUSED:k8s-master") && e.contains("NOT_AUTHORIZED")),
                    ev.toString());
            verify(incidents, never()).save(any(Incident.class));
            verify(watchdog, never()).restart(eq("k8s-master"), anyLong(), any());
            noAction();
        } finally {
            VmPowerPolicy.resetForTest();
        }
    }

    // ── Workers : décision de redémarrage ────────────────────────────────────────────────────────

    @Test
    void worker_incident_ouvert_et_vm_eteinte_confirmee_mode_ombre_n_agit_pas() {
        off("k8s-worker1");
        openIncident("k8s-nodes", "k8s-worker1", "NODE_NOT_READY", IncidentStatus.OPEN, 101L);
        List<String> ev = cycles(3);
        assertTrue(ev.contains("SHADOW_WOULD_RESTART:k8s-worker1"), ev.toString());
        verify(history, atLeastOnce()).record(any(Incident.class), any(), contains("MODE OMBRE"));
        noAction();
    }

    @Test
    void worker_mode_reel_lance_action_workflow_avec_incident_et_passe_en_cours() {
        real("k8s-worker1");
        off("k8s-worker1");
        Incident incident = openIncident("k8s-nodes", "k8s-worker1", "NODE_NOT_READY", IncidentStatus.OPEN, 101L);
        List<String> ev = cycles(3);

        assertTrue(ev.contains("LAUNCHED:k8s-worker1"), ev.toString());
        verify(actionService).createAction(eq(incident), eq(ActionType.RESTART_VM), anyString(), anyString(),
                eq("k8s-nodes"), any(), eq("k8s-worker1"), eq(AutomationMode.AUTO));
        verify(workflowService).startWorkflow(eq(incident), eq("Node-Remediation-Workflow"), eq("k8s-nodes"),
                eq("k8s-worker1"));
        ArgumentCaptor<RemediationPlan> plan = ArgumentCaptor.forClass(RemediationPlan.class);
        verify(camunda).startWorkflow(any(), any(), plan.capture(), any());
        assertEquals(ActionType.RESTART_VM, plan.getValue().actionType());
        assertEquals(Long.valueOf(101L), plan.getValue().incidentId(), "l'incident est la clé d'idempotence");
        assertTrue(plan.getValue().autoExecutable());
        assertEquals(IncidentStatus.IN_PROGRESS, incident.getStatus());
    }

    @Test
    void worker_mode_reel_mais_vm_non_activee_reste_en_mode_ombre() {
        real("k8s-worker2");                 // activée : seulement worker2
        off("k8s-worker1");
        openIncident("k8s-nodes", "k8s-worker1", "NODE_NOT_READY", IncidentStatus.OPEN, 101L);
        List<String> ev = cycles(3);
        assertTrue(ev.contains("SHADOW_WOULD_RESTART:k8s-worker1"), ev.toString());
        noAction();
    }

    @Test
    void worker_notready_mais_vm_allumee_n_est_jamais_demarree() {
        real("k8s-worker1");
        states.put("k8s-worker1", state("k8s-worker1", PowerState.RUNNING));
        live.put("k8s-worker1", down());      // kubelet/réseau KO mais la VM tourne
        openIncident("k8s-nodes", "k8s-worker1", "NODE_NOT_READY", IncidentStatus.OPEN, 101L);
        List<String> ev = cycles(6);
        assertTrue(ev.stream().anyMatch(e -> e.startsWith("REFUSED:k8s-worker1") && e.contains("STATE_RUNNING")),
                ev.toString());
        noAction();
    }

    @Test
    void maintenance_sur_l_hote_empeche_le_demarrage() {
        real("k8s-worker1");
        off("k8s-worker1");
        states.put("k8s-worker1", state("k8s-worker1", PowerState.POWERED_OFF, 1_000L, true));
        openIncident("k8s-nodes", "k8s-worker1", "NODE_NOT_READY", IncidentStatus.OPEN, 101L);
        List<String> ev = cycles(4);
        assertTrue(ev.stream().anyMatch(e -> e.startsWith("REFUSED:k8s-worker1") && e.contains("MAINTENANCE")),
                ev.toString());
        noAction();
    }

    @Test
    void action_deja_creee_pour_l_incident_empeche_un_second_demarrage() {
        real("k8s-worker1");
        off("k8s-worker1");
        openIncident("k8s-nodes", "k8s-worker1", "NODE_NOT_READY", IncidentStatus.OPEN, 101L);
        when(actions.countByIncidentIdAndType(101L, ActionType.RESTART_VM)).thenReturn(1L);
        List<String> ev = cycles(4);
        assertTrue(ev.stream().anyMatch(e -> e.contains("ACTION_ALREADY_EXISTS")), ev.toString());
        noAction();
    }

    @Test
    void cooldown_de_5_minutes_apres_une_action_recente_meme_echouee() {
        real("k8s-worker1");
        off("k8s-worker1");
        openIncident("k8s-nodes", "k8s-worker1", "NODE_NOT_READY", IncidentStatus.OPEN, 101L);
        IncidentAction recent = new IncidentAction();
        // 2 min avant le 3e cycle (horloge injectée du test, pas l'horloge réelle) : DANS le cooldown de 5 min
        ReflectionTestUtils.setField(recent, "createdAt", LocalDateTime.ofInstant(
                Instant.ofEpochMilli(T0 + 2 * CYCLE - 2 * 60_000L), java.time.ZoneId.systemDefault()));
        when(actions.findRecentActionsForTarget(eq("k8s-nodes"), eq("k8s-worker1"), eq(ActionType.RESTART_VM), any()))
                .thenReturn(List.of(recent));
        List<String> ev = cycles(4);
        assertTrue(ev.stream().anyMatch(e -> e.contains("COOLDOWN_ACTIVE")), ev.toString());
        noAction();
    }

    /**
     * 2026-11-18 (cooldown 30 -> 5 min) : une action vieille de 6 min n'est PLUS un cooldown. Avant le changement un arrêt à 6 min du
     * rallumage précédent était différé jusqu'à 30 min (constaté sur .153) ; il est désormais redémarré au cycle suivant.
     */
    @Test
    void une_action_vieille_de_plus_de_5_minutes_n_est_plus_un_cooldown_le_redemarrage_repart() {
        real("k8s-worker1");
        off("k8s-worker1");
        Incident incident = openIncident("k8s-nodes", "k8s-worker1", "NODE_NOT_READY", IncidentStatus.OPEN, 101L);
        IncidentAction ancienne = new IncidentAction();
        ReflectionTestUtils.setField(ancienne, "createdAt", LocalDateTime.ofInstant(
                Instant.ofEpochMilli(T0 + 2 * CYCLE - 6 * 60_000L), java.time.ZoneId.systemDefault()));
        when(actions.findRecentActionsForTarget(eq("k8s-nodes"), eq("k8s-worker1"), eq(ActionType.RESTART_VM), any()))
                .thenReturn(List.of(ancienne));
        List<String> ev = cycles(4);
        assertTrue(ev.contains("LAUNCHED:k8s-worker1"), ev.toString());
        assertFalse(ev.stream().anyMatch(e -> e.contains("COOLDOWN_ACTIVE")), ev.toString());
        verify(actionService).createAction(eq(incident), eq(ActionType.RESTART_VM), anyString(), anyString(),
                eq("k8s-nodes"), any(), eq("k8s-worker1"), eq(AutomationMode.AUTO));
    }

    @Test
    void camunda_injoignable_differe_le_demarrage_sans_creer_d_action() {
        real("k8s-worker1");
        off("k8s-worker1");
        openIncident("k8s-nodes", "k8s-worker1", "NODE_NOT_READY", IncidentStatus.OPEN, 101L);
        when(camunda.isReachable()).thenReturn(false);
        List<String> ev = cycles(3);
        assertTrue(ev.contains("DEFERRED_CAMUNDA_UNREACHABLE:k8s-worker1"), ev.toString());
        noAction();
    }

    @Test
    void echec_du_lancement_du_workflow_marque_l_action_echouee_et_l_incident_resolution_failed() {
        real("k8s-worker1");
        off("k8s-worker1");
        Incident incident = openIncident("k8s-nodes", "k8s-worker1", "NODE_NOT_READY", IncidentStatus.OPEN, 101L);
        when(workflowService.startWorkflow(any(Incident.class), anyString(), anyString(), anyString()))
                .thenThrow(new IllegalStateException("boom"));
        List<String> ev = cycles(3);
        assertTrue(ev.contains("LAUNCH_FAILED:k8s-worker1"), ev.toString());
        verify(actionService).markActionFailed(eq(500L), contains("impossible"), anyString());
        assertEquals(IncidentStatus.RESOLUTION_FAILED, incident.getStatus());
        verify(notifications).notifyRemediationFailed(eq(incident), contains("impossible"));
    }

    @Test
    void incident_deja_en_cours_de_remediation_n_est_pas_relance() {
        real("k8s-worker1");
        off("k8s-worker1");
        openIncident("k8s-nodes", "k8s-worker1", "NODE_NOT_READY", IncidentStatus.IN_PROGRESS, 101L);
        cycles(5);
        noAction();
    }

    // ── Épisode RESOLUTION_FAILED ────────────────────────────────────────────────────────────────

    @Test
    void episode_resolution_failed_ouvert_bloque_incident_action_et_email_avec_rappel_espace() {
        real("k8s-lb2");
        off("k8s-lb2");
        Incident failed = openIncident("infra", "k8s-lb2", "VM_DOWN", IncidentStatus.RESOLUTION_FAILED, 55L);
        cycles(4);
        verify(incidents, never()).save(any(Incident.class));     // aucun nouvel incident
        noAction();                                               // aucune action
        verify(notifications, never()).notifyNewIncident(any(Incident.class));   // aucun e-mail
        verify(history, times(1)).record(eq(failed), eq(IncidentStatus.RESOLUTION_FAILED), contains("Épisode ouvert"));
        watch.runCycle(T0 + 5 * 60_000L);                          // < 15 min : pas de second rappel
        verify(history, times(1)).record(eq(failed), eq(IncidentStatus.RESOLUTION_FAILED), contains("Épisode ouvert"));
        watch.runCycle(T0 + 20 * 60_000L);                         // > 15 min : un rappel
        verify(history, times(2)).record(eq(failed), eq(IncidentStatus.RESOLUTION_FAILED), contains("Épisode ouvert"));
    }

    @Test
    void episode_archive_autorise_un_nouvel_incident() {
        off("k8s-lb2");
        // aucun incident non clos (l'ancien est ARCHIVED : exclu de la recherche) => un nouvel épisode s'ouvre
        List<String> ev = cycles(3);
        assertTrue(ev.contains("INCIDENT_CREATED:k8s-lb2"), ev.toString());
    }

    @Test
    void autre_incident_resolution_failed_du_meme_episode_bloque_l_autorisation() {
        real("k8s-lb2");
        off("k8s-lb2");
        openIncident("infra", "k8s-lb2", "VM_DOWN", IncidentStatus.OPEN, 60L);
        Incident older = new Incident();
        ReflectionTestUtils.setField(older, "id", 59L);
        older.setIncidentType("VM_DOWN");
        older.setStatus(IncidentStatus.RESOLUTION_FAILED);
        when(incidents.findByStatusAndNamespaceAndApp(IncidentStatus.RESOLUTION_FAILED, "infra", "k8s-lb2"))
                .thenReturn(List.of(older));
        List<String> ev = cycles(4);
        assertTrue(ev.stream().anyMatch(e -> e.contains("EPISODE_OPEN")), ev.toString());
        noAction();
    }

    // ── Robustesse ───────────────────────────────────────────────────────────────────────────────

    @Test
    void cycle_desactive_ou_non_leader_ne_contacte_pas_le_watchdog() {
        props.setEnabled(false);
        watch.cycle();
        verify(watchdog, never()).states();
        props.setEnabled(true);
        when(leader.isLeader()).thenReturn(false);
        watch.cycle();
        verify(watchdog, never()).states();
    }

    @Test
    void doublon_d_incident_detecte_par_la_base_ne_leve_aucune_erreur() {
        off("k8s-lb2");
        when(incidents.save(any(Incident.class))).thenThrow(new DataIntegrityViolationException("ux_incident_open_dedup"));
        List<String> ev = cycles(3);
        assertFalse(ev.stream().anyMatch(e -> e.startsWith("ERROR")), ev.toString());
        assertFalse(ev.contains("INCIDENT_CREATED:k8s-lb2"));
        verify(notifications, never()).notifyNewIncident(any(Incident.class));
    }

    // ── 2026-11-21 : nœud PROUVÉ éteint SANS incident Kubernetes ─────────────────────────────────────
    // Cause du « rien ne se passe » lors d'un arrêt de .158 : le nœud mort portait le bail du controller-manager (60 s) puis la
    // grâce des nœuds (40 s) ⇒ Kubernetes ne créait l'incident NODE_NOT_READY qu'après ≈ 2 min. L'ancien test figeait ce défaut
    // (« aucun incident créé par VmPowerWatch pour un worker ») ; il est remplacé par les huit tests ci-dessous.

    @Test
    void worker_eteint_confirme_sans_incident_kubernetes_le_backend_cree_NODE_NOT_READY_et_lance_le_rallumage() {
        real("k8s-worker1", "k8s-worker2");
        off("k8s-worker1");     // éteinte, AUCUN incident NODE_NOT_READY (la détection K8s n'est pas encore passée)
        List<String> ev = cycles(3);   // 0 s, 15 s, 30 s : 3 observations, écart 30 s

        assertTrue(ev.contains("INCIDENT_CREATED:k8s-worker1"), ev.toString());
        assertTrue(ev.contains("LAUNCHED:k8s-worker1"), ev.toString());
        ArgumentCaptor<Incident> c = ArgumentCaptor.forClass(Incident.class);
        verify(incidents, atLeastOnce()).save(c.capture());
        Incident created = c.getAllValues().get(0);
        assertEquals("NODE_NOT_READY", created.getIncidentType());
        assertEquals("k8s-nodes", created.getNamespace());
        assertEquals("k8s-worker1", created.getApp());
        assertEquals(IncidentSource.OTHER, created.getSource());
        assertEquals(IncidentSeverity.CRITICAL, created.getSeverity());
        verify(notifications).notifyNewIncident(any(Incident.class));
        // une seule demande, pour le nœud éteint ; l'autre worker n'est pas touché
        verify(actionService, times(1)).createAction(any(), eq(ActionType.RESTART_VM), anyString(), anyString(),
                eq("k8s-nodes"), any(), eq("k8s-worker1"), eq(AutomationMode.AUTO));
        verify(workflowService, times(1)).startWorkflow(any(Incident.class), eq("Node-Remediation-Workflow"),
                eq("k8s-nodes"), eq("k8s-worker1"));
        verify(actionService, never()).createAction(any(), any(), any(), any(), any(), any(), eq("k8s-worker2"), any());
    }

    @Test
    void worker_eteint_confirme_sans_incident_kubernetes_en_mode_ombre_cree_l_incident_mais_ne_demande_rien() {
        off("k8s-worker1");     // dry-run par défaut : MODE OMBRE
        List<String> ev = cycles(3);
        assertTrue(ev.contains("INCIDENT_CREATED:k8s-worker1"), ev.toString());
        assertTrue(ev.contains("SHADOW_WOULD_RESTART:k8s-worker1"), ev.toString());
        assertFalse(ev.contains("LAUNCHED:k8s-worker1"), ev.toString());
        noAction();
    }

    @Test
    void worker_eteint_depuis_moins_de_30_s_ne_cree_aucun_incident() {
        real("k8s-worker1");
        off("k8s-worker1");
        List<String> ev = cycles(2);   // 0 s et 15 s : deux observations espacées de 15 s seulement
        assertFalse(ev.stream().anyMatch(e -> e.startsWith("INCIDENT_CREATED")), ev.toString());
        verify(incidents, never()).save(any(Incident.class));
        noAction();
    }

    @Test
    void worker_allume_mais_muet_sans_incident_kubernetes_ne_cree_rien_et_ne_demarre_rien() {
        real("k8s-worker1");
        states.put("k8s-worker1", state("k8s-worker1", PowerState.RUNNING));
        live.put("k8s-worker1", down());      // kubelet/réseau KO mais la VM tourne : jamais « éteinte confirmée »
        List<String> ev = cycles(8);
        assertFalse(ev.stream().anyMatch(e -> e.startsWith("INCIDENT_CREATED")), ev.toString());
        verify(incidents, never()).save(any(Incident.class));
        noAction();
    }

    @Test
    void worker_doublon_detecte_par_la_base_ne_cree_rien_ne_leve_aucune_erreur_et_ne_demarre_rien() {
        real("k8s-worker1");
        off("k8s-worker1");
        when(incidents.save(any(Incident.class))).thenThrow(new DataIntegrityViolationException("ux_incident_open_dedup"));
        List<String> ev = cycles(3);
        assertFalse(ev.stream().anyMatch(e -> e.startsWith("ERROR")), ev.toString());
        assertFalse(ev.contains("INCIDENT_CREATED:k8s-worker1"), ev.toString());
        verify(notifications, never()).notifyNewIncident(any(Incident.class));
        noAction();
    }

    @Test
    void k8s_master_eteinte_confirmee_sans_incident_kubernetes_le_backend_le_cree_et_lance_comme_un_worker() {
        real("k8s-master", "k8s-worker1", "k8s-worker2", "k8s-lb2");
        off("k8s-master");
        List<String> ev = cycles(3);
        assertTrue(ev.contains("INCIDENT_CREATED:k8s-master"), ev.toString());
        assertTrue(ev.contains("LAUNCHED:k8s-master"), ev.toString());
        verify(actionService, times(1)).createAction(any(), eq(ActionType.RESTART_VM), anyString(), anyString(),
                eq("k8s-nodes"), any(), eq("k8s-master"), eq(AutomationMode.AUTO));
        verify(actionService, never()).createAction(any(), any(), any(), any(), any(), any(), eq("k8s-worker1"), any());
    }

    @Test
    void mode_non_auto_force_ne_cree_jamais_d_incident_de_noeud_sans_detection_kubernetes() {
        VmPowerPolicy.overrideEntryForTest(new VmPowerPolicy.Entry("k8s-master", "192.168.148.153",
                VmPowerPolicy.Mode.DENY_RESTART, VmPowerPolicy.VmClass.K8S_CONTROL_PLANE, "k8s-nodes", "k8s-master",
                "NODE_NOT_READY", VmPowerPolicy.Route.NONE, VmPowerPolicy.Notifications.EMAIL_AND_UI));
        try {
            real("k8s-master", "k8s-worker1", "k8s-worker2", "k8s-lb2");
            off("k8s-master");
            List<String> ev = cycles(10);
            assertFalse(ev.stream().anyMatch(e -> e.startsWith("INCIDENT_CREATED:k8s-master")), ev.toString());
            verify(incidents, never()).save(any(Incident.class));
            noAction();
        } finally {
            VmPowerPolicy.resetForTest();
        }
    }

    @Test
    void incident_cree_par_le_backend_respecte_le_cooldown_de_5_minutes() {
        real("k8s-worker1");
        off("k8s-worker1");
        IncidentAction recent = new IncidentAction();
        // 2 min avant le 3e cycle (horloge injectée du test) : DANS le cooldown de 5 min
        ReflectionTestUtils.setField(recent, "createdAt", LocalDateTime.ofInstant(
                Instant.ofEpochMilli(T0 + 2 * CYCLE - 2 * 60_000L), java.time.ZoneId.systemDefault()));
        when(actions.findRecentActionsForTarget(eq("k8s-nodes"), eq("k8s-worker1"), eq(ActionType.RESTART_VM), any()))
                .thenReturn(List.of(recent));
        List<String> ev = cycles(4);
        assertTrue(ev.contains("INCIDENT_CREATED:k8s-worker1"), ev.toString());
        assertTrue(ev.stream().anyMatch(e -> e.contains("COOLDOWN_ACTIVE")), ev.toString());
        noAction();
    }
    // ── Clôture par preuves (R5) ─────────────────────────────────────────────────────────────────

    @Test
    void cloture_exige_deux_passes_persistees_puis_PASS_puis_RESOLVED_sans_email_pour_160() {
        Incident incident = openIncident("infra", "k8s-monitoring", "VM_DOWN", IncidentStatus.OPEN, 300L);
        when(verifier.verifyInfra(any(), any())).thenReturn(passResult(true));
        when(card.hasTwoConsecutiveVmRecoveryEvidence(300L, "k8s-monitoring")).thenReturn(true);

        List<String> first = watch.runCycle(T0);
        verify(card).recordVmRecoveryEvidence(eq(300L), eq("k8s-monitoring"), eq(1), any(LocalDateTime.class),
                eq(true), anyString());
        assertFalse(first.stream().anyMatch(e -> e.startsWith("RESOLVED_WITH_PROOF")), "une seule passe ne suffit pas");
        assertEquals(IncidentStatus.OPEN, incident.getStatus());
        verify(card, never()).recordVmRecoveryVerification(any(), any(), any());

        List<String> second = watch.runCycle(T0 + CYCLE);
        assertTrue(second.contains("RESOLVED_WITH_PROOF:k8s-monitoring:VM_DOWN"), second.toString());
        org.mockito.InOrder order = org.mockito.Mockito.inOrder(card, incidents, notifications);
        order.verify(card).recordVmRecoveryVerification(eq(300L), eq("k8s-monitoring"), anyString());
        order.verify(incidents).save(incident);
        order.verify(notifications).notifyIncidentResolvedUiOnly(incident);
        assertEquals(IncidentStatus.RESOLVED, incident.getStatus());
        verify(notifications, never()).notifyIncidentResolved(any(Incident.class));
    }

    @Test
    void echec_apres_une_reussite_est_ecrit_pour_casser_la_serie() {
        openIncident("infra", "k8s-monitoring", "VM_DOWN", IncidentStatus.OPEN, 300L);
        when(verifier.verifyInfra(any(), any())).thenReturn(passResult(true), passResult(false), passResult(true),
                passResult(true));
        when(card.hasTwoConsecutiveVmRecoveryEvidence(300L, "k8s-monitoring")).thenReturn(true);

        cycles(4);

        verify(card).recordVmRecoveryEvidence(eq(300L), eq("k8s-monitoring"), eq(1), any(), eq(true), anyString());
        verify(card).recordVmRecoveryEvidence(eq(300L), eq("k8s-monitoring"), eq(2), any(), eq(false), anyString());
        verify(card).recordVmRecoveryEvidence(eq(300L), eq("k8s-monitoring"), eq(3), any(), eq(true), anyString());
        verify(card).recordVmRecoveryEvidence(eq(300L), eq("k8s-monitoring"), eq(4), any(), eq(true), anyString());
        verify(card, times(1)).hasTwoConsecutiveVmRecoveryEvidence(300L, "k8s-monitoring");
        verify(card).recordVmRecoveryVerification(eq(300L), eq("k8s-monitoring"), anyString());
    }

    @Test
    void pendant_la_panne_aucune_ecriture_de_preuve_en_base() {
        openIncident("infra", "k8s-monitoring", "VM_DOWN", IncidentStatus.OPEN, 300L);
        cycles(12);   // verifier => échec à chaque passe, jamais de série en cours
        verify(card, never()).recordVmRecoveryEvidence(any(), any(), anyInt(), any(), org.mockito.ArgumentMatchers.anyBoolean(), any());
    }

    @Test
    void paire_non_retrouvee_en_base_empeche_le_PASS_et_le_RESOLVED() {
        Incident incident = openIncident("infra", "k8s-lb2", "VM_DOWN", IncidentStatus.OPEN, 301L);
        when(verifier.verifyInfra(any(), any())).thenReturn(passResult(true));
        when(card.hasTwoConsecutiveVmRecoveryEvidence(301L, "k8s-lb2")).thenReturn(false);
        cycles(4);
        verify(card, never()).recordVmRecoveryVerification(any(), any(), any());
        assertEquals(IncidentStatus.OPEN, incident.getStatus());
    }

    @Test
    void incident_dont_le_workflow_est_en_cours_n_est_jamais_cloture_par_le_watch() {
        openIncident("infra", "k8s-lb2", "VM_DOWN", IncidentStatus.IN_PROGRESS, 302L);
        when(verifier.verifyInfra(any(), any())).thenReturn(passResult(true));
        cycles(4);
        verify(card, never()).recordVmRecoveryEvidence(any(), any(), anyInt(), any(), org.mockito.ArgumentMatchers.anyBoolean(), any());
        verify(incidents, never()).save(any(Incident.class));
    }

    @Test
    void noeud_master_ferme_par_deux_passes_de_preuves_completes_avec_notification_complete() {
        // 2026-11-17 : .153 est fermée comme un worker — par les critères COMPLETS (verifyWorker : Ready + kubelet + SSH + etcd +
        // CNPG + backend + apiserver 6443), plus par la seule lecture « Ready » de l'ancien chemin d'infrastructure.
        Incident incident = openIncident("k8s-nodes", "k8s-master", "NODE_NOT_READY", IncidentStatus.OPEN, 303L);
        when(nodeReadiness.inspectNodeReadiness("k8s-master")).thenReturn(ready());
        when(card.hasTwoConsecutiveVmRecoveryEvidence(303L, "k8s-master")).thenReturn(true);
        Map<String, Boolean> ok = new LinkedHashMap<>();
        ok.put("node_ready", true);
        ok.put("apiserver_6443", true);
        ok.put("etcd_healthy", true);
        when(verifier.verifyWorker(any(), any())).thenReturn(new PassResult(true, ok, "k8s-master : tout est OK"));

        List<String> ev = cycles(2);

        assertTrue(ev.contains("RESOLVED_WITH_PROOF:k8s-master:NODE_NOT_READY"), ev.toString());
        verify(card, times(2)).recordVmRecoveryEvidence(eq(303L), eq("k8s-master"), anyInt(), any(),
                eq(true), contains("apiserver_6443"));
        assertEquals(IncidentStatus.RESOLVED, incident.getStatus());
        verify(notifications).notifyIncidentResolved(incident);
        verify(verifier, never()).verifyInfra(any(), any());
        noAction();   // .153 : fermé par preuves, aucun démarrage
    }

    @Test
    void noeud_master_Ready_mais_apiserver_6443_injoignable_n_est_jamais_resolu() {
        Incident incident = openIncident("k8s-nodes", "k8s-master", "NODE_NOT_READY", IncidentStatus.OPEN, 307L);
        when(nodeReadiness.inspectNodeReadiness("k8s-master")).thenReturn(ready());
        when(card.hasTwoConsecutiveVmRecoveryEvidence(307L, "k8s-master")).thenReturn(true);
        Map<String, Boolean> failing = new LinkedHashMap<>();
        failing.put("node_ready", true);
        failing.put("apiserver_6443", false);
        when(verifier.verifyWorker(any(), any()))
                .thenReturn(new PassResult(false, failing, "k8s-master : critères en échec [apiserver_6443]"));

        List<String> ev = cycles(6);

        assertFalse(ev.stream().anyMatch(e -> e.startsWith("RESOLVED_WITH_PROOF")), ev.toString());
        assertEquals(IncidentStatus.OPEN, incident.getStatus());
        verify(card, never()).recordVmRecoveryVerification(any(), any(), any());
        noAction();
    }

    @Test
    void worker_ferme_uniquement_si_toutes_les_preuves_passent_pas_sur_le_seul_Ready() {
        Incident incident = openIncident("k8s-nodes", "k8s-worker1", "NODE_NOT_READY", IncidentStatus.OPEN, 306L);
        when(nodeReadiness.inspectNodeReadiness("k8s-worker1")).thenReturn(ready());
        when(card.hasTwoConsecutiveVmRecoveryEvidence(306L, "k8s-worker1")).thenReturn(true);
        // nœud Ready mais une preuve complémentaire (CNPG par exemple) échoue : jamais RESOLVED
        Map<String, Boolean> failing = new LinkedHashMap<>();
        failing.put("node_ready", true);
        failing.put("cnpg_healthy", false);
        when(verifier.verifyWorker(any(), any())).thenReturn(new PassResult(false, failing, "k8s-worker1 : critères en échec [cnpg_healthy]"));
        List<String> ev = cycles(4);
        assertFalse(ev.stream().anyMatch(e -> e.startsWith("RESOLVED_WITH_PROOF")), ev.toString());
        assertEquals(IncidentStatus.OPEN, incident.getStatus());
        verify(card, never()).recordVmRecoveryVerification(any(), any(), any());

        // toutes les preuves passent deux fois de suite : fermeture avec notification complète (workers = e-mail)
        Map<String, Boolean> ok = new LinkedHashMap<>();
        ok.put("node_ready", true);
        ok.put("cnpg_healthy", true);
        when(verifier.verifyWorker(any(), any())).thenReturn(new PassResult(true, ok, "k8s-worker1 : tout est OK"));
        List<String> later = new ArrayList<>();
        later.addAll(watch.runCycle(T0 + 10 * CYCLE));
        later.addAll(watch.runCycle(T0 + 11 * CYCLE));
        assertTrue(later.contains("RESOLVED_WITH_PROOF:k8s-worker1:NODE_NOT_READY"), later.toString());
        assertEquals(IncidentStatus.RESOLVED, incident.getStatus());
        verify(notifications).notifyIncidentResolved(incident);
        noAction();   // fermé par preuves : aucun démarrage
    }

    @Test
    void episode_resolution_failed_se_ferme_par_preuves_reelles() {
        Incident failed = openIncident("infra", "k8s-lb2", "VM_DOWN", IncidentStatus.RESOLUTION_FAILED, 304L);
        when(verifier.verifyInfra(any(), any())).thenReturn(passResult(true));
        when(card.hasTwoConsecutiveVmRecoveryEvidence(304L, "k8s-lb2")).thenReturn(true);

        List<String> ev = cycles(2);

        assertTrue(ev.contains("RESOLVED_WITH_PROOF:k8s-lb2:VM_DOWN"), ev.toString());
        assertEquals(IncidentStatus.RESOLVED, failed.getStatus());
        verify(notifications).notifyIncidentResolved(failed);
    }

    @Test
    void sans_etat_watchdog_les_vm_d_infrastructure_ne_sont_pas_evaluees() {
        watchdogUp = false;
        openIncident("infra", "k8s-lb2", "VM_DOWN", IncidentStatus.OPEN, 305L);
        when(verifier.verifyInfra(any(), any())).thenReturn(passResult(true));
        cycles(4);
        verify(verifier, never()).verifyInfra(any(), any());
        verify(card, never()).recordVmRecoveryEvidence(any(), any(), anyInt(), any(), org.mockito.ArgumentMatchers.anyBoolean(), any());
    }
}
