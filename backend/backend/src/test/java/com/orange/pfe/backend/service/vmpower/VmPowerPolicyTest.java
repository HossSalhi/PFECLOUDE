package com.orange.pfe.backend.service.vmpower;

import com.orange.pfe.backend.service.vmpower.VmPowerPolicy.Decision;
import com.orange.pfe.backend.service.vmpower.VmPowerPolicy.Mode;
import com.orange.pfe.backend.service.vmpower.VmPowerPolicy.PowerState;
import com.orange.pfe.backend.service.vmpower.VmPowerPolicy.Refusal;
import com.orange.pfe.backend.service.vmpower.VmPowerPolicy.Request;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.Test;

import java.util.Arrays;
import java.util.List;
import java.util.Optional;
import java.util.Set;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * VMDOWN-POLICY-20261105 — table fermée (5 cibles, noms exacts) et autorisation par composition.
 * La classe testée est pure : aucun de ces tests ne touche le réseau, une VM ou une base.
 */
class VmPowerPolicyTest {

    private static final String WORKER1 = "k8s-worker1";
    private static final String WORKER2 = "k8s-worker2";
    private static final String LB2 = "k8s-lb2";
    private static final String MONITORING = "k8s-monitoring";
    private static final String MASTER = "k8s-master";

    /** Requête où TOUS les faits sont favorables à un démarrage ; chaque test en dégrade un seul. */
    private static Request.Builder favorable(String vm) {
        boolean infra = LB2.equals(vm) || MONITORING.equals(vm);
        return Request.builder()
                .vm(vm)
                .incident(infra ? "infra" : "k8s-nodes", vm, infra ? "VM_DOWN" : "NODE_NOT_READY")
                .powerState(PowerState.POWERED_OFF)
                .evidenceAgeMs(1_200L)
                .poweredOff(2, 31_000L)
                .probesFailing(true)
                .maintenance(false)
                .failedEpisodeOpen(false)
                .msSinceLastRestartAction(VmPowerPolicy.NEVER)
                .restartActionsForIncident(0);
    }

    private static Decision decide(Request r) {
        return VmPowerPolicy.authorize(r);
    }

    /** La couture de test ne doit jamais fuir d'un test à l'autre : la table de production est toujours restaurée. */
    @AfterEach
    void restaurerLaTableDeProduction() {
        VmPowerPolicy.resetForTest();
    }

    // ───────────────────────────── Table fermée ─────────────────────────────

    @Test
    void table_exacte_5_cibles_dans_l_ordre_du_yaml() {
        List<VmPowerPolicy.Entry> entries = VmPowerPolicy.entries();
        assertEquals(5, entries.size());
        assertEquals(List.of(WORKER2, WORKER1, LB2, MONITORING, MASTER),
                entries.stream().map(VmPowerPolicy.Entry::name).toList());
        assertEquals(Mode.AUTO, VmPowerPolicy.modeOf(WORKER2));
        assertEquals(Mode.AUTO, VmPowerPolicy.modeOf(WORKER1));
        assertEquals(Mode.AUTO, VmPowerPolicy.modeOf(LB2));
        // consigne finale de l'après-midi du 2026-11-06 : .160 (VM de supervision) est en DÉTECTION SEULE
        assertEquals(Mode.DETECT_ONLY, VmPowerPolicy.modeOf(MONITORING));
        // consigne du 2026-11-17 (inverse « .153 jamais cible ») : le plan de contrôle est AUTO comme les workers
        assertEquals(Mode.AUTO, VmPowerPolicy.modeOf(MASTER));
        assertEquals(VmPowerPolicy.VmClass.K8S_CONTROL_PLANE, VmPowerPolicy.find(MASTER).orElseThrow().vmClass());
        // révision 2026-11-18 : cooldown de redémarrage 30 -> 5 min (la révision du 2026-11-17 « master-auto » n'est plus déployable seule)
        assertEquals("2026-11-18-cooldown-5min", VmPowerPolicy.POLICY_REVISION, "marqueur lu par policy_reconcile.py dans l'image déployée");
    }

    @Test
    void les_cibles_automatiques_sont_exactement_155_158_162_et_153_jamais_160() {
        assertEquals(Set.of(WORKER1, WORKER2, LB2, MASTER), VmPowerPolicy.autoTargets());
        assertTrue(VmPowerPolicy.isAutoRestartable(WORKER1));
        assertTrue(VmPowerPolicy.isAutoRestartable(WORKER2));
        assertTrue(VmPowerPolicy.isAutoRestartable(LB2));
        assertTrue(VmPowerPolicy.isAutoRestartable(MASTER), ".153 est une cible AUTO depuis la consigne du 2026-11-17");
        assertFalse(VmPowerPolicy.isAutoRestartable(MONITORING), ".160 est la VM de supervision : JAMAIS rallumée automatiquement");
        assertTrue(VmPowerPolicy.isDetectionOnly(MONITORING), ".160 : détection seule");
        assertFalse(VmPowerPolicy.isDetectionOnly(LB2));
        assertFalse(VmPowerPolicy.isDetectionOnly(MASTER), ".153 est AUTO, pas DETECT_ONLY");
    }

    @Test
    void routes_et_canaux_de_notification_sont_explicites_par_vm() {
        // Chaîne Camunda : workers, .162 et .153 (les quatre cibles automatiques). Aucune VM n'utilise la route directe (dormante).
        for (String vm : List.of(WORKER1, WORKER2, LB2, MASTER)) {
            assertEquals(VmPowerPolicy.Route.CAMUNDA, VmPowerPolicy.routeOf(vm), vm);
            assertTrue(VmPowerPolicy.isCamundaRestartable(vm), vm);
            assertFalse(VmPowerPolicy.isDirectRestartable(vm), vm);
            assertFalse(VmPowerPolicy.isUiOnly(vm), vm);
        }
        // .160 : aucune route de redémarrage, mais ses notifications restent « interface seulement » (aucun e-mail).
        assertEquals(VmPowerPolicy.Route.NONE, VmPowerPolicy.routeOf(MONITORING));
        assertFalse(VmPowerPolicy.isCamundaRestartable(MONITORING), ".160 ne passe jamais par Camunda");
        assertFalse(VmPowerPolicy.isDirectRestartable(MONITORING), "la route directe est dormante");
        assertTrue(VmPowerPolicy.isUiOnly(MONITORING), ".160 : interface seulement, jamais d'e-mail");
        // Toute cible inconnue : aucune route.
        for (String vm : Arrays.asList("lb2", "k8s-aiCamunda", null, "")) {
            assertEquals(VmPowerPolicy.Route.NONE, VmPowerPolicy.routeOf(vm), String.valueOf(vm));
            assertFalse(VmPowerPolicy.isCamundaRestartable(vm), String.valueOf(vm));
            assertFalse(VmPowerPolicy.isDirectRestartable(vm), String.valueOf(vm));
        }
    }

    @Test
    void invariant_de_table_un_mode_auto_a_une_route_et_un_autre_mode_n_en_a_aucune() {
        for (VmPowerPolicy.Entry e : VmPowerPolicy.entries()) {
            assertEquals(e.mode() == Mode.AUTO, e.route() != VmPowerPolicy.Route.NONE, e.name());
        }
        // Le constructeur refuse une table incohérente (AUTO sans route, ou route sans AUTO).
        assertThrows(IllegalArgumentException.class, () -> new VmPowerPolicy.Entry("x", "10.0.0.1", Mode.AUTO,
                VmPowerPolicy.VmClass.INFRA_LB, "infra", "x", "VM_DOWN", VmPowerPolicy.Route.NONE,
                VmPowerPolicy.Notifications.EMAIL_AND_UI));
        assertThrows(IllegalArgumentException.class, () -> new VmPowerPolicy.Entry("x", "10.0.0.1",
                Mode.DENY_RESTART, VmPowerPolicy.VmClass.K8S_CONTROL_PLANE, "k8s-nodes", "x", "NODE_NOT_READY",
                VmPowerPolicy.Route.CAMUNDA, VmPowerPolicy.Notifications.EMAIL_AND_UI));
    }

    @Test
    void chaque_vm_a_son_adresse_et_son_perimetre_d_incident() {
        assertEquals("192.168.148.155", VmPowerPolicy.find(WORKER2).orElseThrow().ip());
        assertEquals("192.168.148.158", VmPowerPolicy.find(WORKER1).orElseThrow().ip());
        assertEquals("192.168.148.162", VmPowerPolicy.find(LB2).orElseThrow().ip());
        assertEquals("192.168.148.160", VmPowerPolicy.find(MONITORING).orElseThrow().ip());
        assertEquals("192.168.148.153", VmPowerPolicy.find(MASTER).orElseThrow().ip());
        // .162 n'est ni un worker ni NODE_NOT_READY : incident VM_DOWN, namespace infra.
        VmPowerPolicy.Entry lb2 = VmPowerPolicy.find(LB2).orElseThrow();
        assertEquals(VmPowerPolicy.VmClass.INFRA_LB, lb2.vmClass());
        assertEquals("VM_DOWN", lb2.incidentType());
        assertEquals("infra", lb2.incidentNamespace());
        assertEquals(LB2, lb2.incidentApp());
    }

    @Test
    void alias_et_adresses_IP_sont_refuses() {
        for (String cible : List.of("lb2", "monitoring", "iacamunda", "master", "worker1", "worker2",
                "k8s-aiCamunda", "k8s-aicamunda",
                "192.168.148.155", "192.168.148.158", "192.168.148.160", "192.168.148.161", "192.168.148.162",
                "192.168.148.153", "127.0.0.1", "localhost")) {
            assertEquals(Optional.empty(), VmPowerPolicy.find(cible), cible);
            assertEquals(Mode.DENY, VmPowerPolicy.modeOf(cible), cible);
            assertEquals(Optional.of(Refusal.UNKNOWN_VM), VmPowerPolicy.staticRefusal(cible), cible);
            assertEquals(List.of(Refusal.UNKNOWN_VM), decide(favorable(cible).build()).refusals(), cible);
        }
    }

    @Test
    void casse_espaces_et_injection_sont_refuses() {
        for (String cible : List.of("K8S-WORKER1", "K8s-Worker1", " k8s-worker1", "k8s-worker1 ", "\tk8s-worker1",
                "k8s-worker1\n", "k8s-worker1\r\n", "k8s-worker1\u0000",
                "k8s-worker1;reboot", "k8s-worker1 && k8s-master", "k8s-worker1/../k8s-master",
                "k8s-worker1,k8s-worker2", "k8s-worker11", "k8s-worker3", "k8s-worker", "k8s-worker999",
                "k8s-worker1-rogue", "xk8s-worker1", "k8s-lb2 ", "K8S-LB2", "k8s-monitoring\n", "k8s-master ")) {
            assertEquals(Mode.DENY, VmPowerPolicy.modeOf(cible), "cible: [" + cible + "]");
            assertFalse(VmPowerPolicy.isAutoRestartable(cible), "cible: [" + cible + "]");
            assertFalse(decide(favorable(cible).build()).allowed(), "cible: [" + cible + "]");
        }
    }

    @Test
    void null_et_vide_sont_refuses() {
        for (String cible : Arrays.asList(null, "", " ", "   ")) {
            assertEquals(Optional.empty(), VmPowerPolicy.find(cible));
            assertEquals(Mode.DENY, VmPowerPolicy.modeOf(cible));
            assertFalse(VmPowerPolicy.isAutoRestartable(cible));
            assertEquals(Optional.of(Refusal.UNKNOWN_VM), VmPowerPolicy.staticRefusal(cible));
            Decision d = decide(favorable(cible).build());
            assertFalse(d.allowed());
            assertEquals(List.of(Refusal.UNKNOWN_VM), d.refusals());
            assertNull(d.entry());
        }
    }

    @Test
    void garde_statique_par_mode() {
        assertEquals(Optional.empty(), VmPowerPolicy.staticRefusal(WORKER1));
        assertEquals(Optional.empty(), VmPowerPolicy.staticRefusal(WORKER2));
        assertEquals(Optional.empty(), VmPowerPolicy.staticRefusal(LB2));
        assertEquals(Optional.of(Refusal.DETECTION_ONLY), VmPowerPolicy.staticRefusal(MONITORING));
        assertEquals(Optional.empty(), VmPowerPolicy.staticRefusal(MASTER), ".153 est AUTO depuis le 2026-11-17");
    }

    @Test
    void la_table_ne_peut_pas_etre_modifiee_de_l_exterieur() {
        assertThrows(UnsupportedOperationException.class, () -> VmPowerPolicy.entries().clear());
        // autoTargets() est une copie : la modifier ne change pas la politique.
        VmPowerPolicy.autoTargets().add("k8s-evil");
        assertEquals(Set.of(WORKER1, WORKER2, LB2, MASTER), VmPowerPolicy.autoTargets());   // .153 AUTO depuis le 2026-11-17
        assertFalse(VmPowerPolicy.isAutoRestartable("k8s-evil"));
    }

    @Test
    void exactement_cinq_entrees_et_aucune_dupliquee() {
        List<String> noms = VmPowerPolicy.entries().stream().map(VmPowerPolicy.Entry::name).toList();
        List<String> ips = VmPowerPolicy.entries().stream().map(VmPowerPolicy.Entry::ip).toList();
        assertEquals(noms.stream().distinct().count(), noms.size());
        assertEquals(ips.stream().distinct().count(), ips.size());
    }

    // ───────────────────── .160 détection seule, .153 jamais ─────────────────────

    @Test
    void k8s_monitoring_est_en_detection_seule_refusee_meme_avec_tous_les_faits_favorables() {
        Decision d = decide(favorable(MONITORING).build());
        assertFalse(d.allowed(), d.summary());
        assertEquals(Mode.DETECT_ONLY, d.mode());
        assertEquals(List.of(Refusal.DETECTION_ONLY), d.refusals());
        assertEquals("REFUSE " + MONITORING + " [DETECTION_ONLY]", d.summary());
        // Le motif est TOUJOURS le seul : quel que soit le fait dégradé, la politique tranche avant tout (pas de démarrage).
        for (Request r : List.of(
                favorable(MONITORING).powerState(PowerState.RUNNING).build(),
                favorable(MONITORING).powerState(PowerState.UNKNOWN).build(),
                favorable(MONITORING).maintenance(true).build(),
                favorable(MONITORING).msSinceLastRestartAction(60_000L).build(),
                favorable(MONITORING).restartActionsForIncident(1).build(),
                favorable(MONITORING).incident("k8s-nodes", MONITORING, "NODE_NOT_READY").build(),
                favorable(MONITORING).poweredOff(1, 0L).build(),
                favorable(MONITORING).probesFailing(false).build(),
                Request.builder().vm(MONITORING).build())) {
            assertEquals(List.of(Refusal.DETECTION_ONLY), decide(r).refusals(), r.toString());
        }
    }

    @Test
    void la_route_directe_dormante_reste_exercable_par_la_couture_de_test_sans_toucher_la_production() {
        // La couture remplace la ligne de .160 par l'ancienne variante AUTO/BACKEND_DIRECT (validée le 2026-11-06 13:28).
        VmPowerPolicy.overrideEntryForTest(new VmPowerPolicy.Entry(MONITORING, "192.168.148.160", Mode.AUTO,
                VmPowerPolicy.VmClass.INFRA_MONITORING, "infra", MONITORING, "VM_DOWN",
                VmPowerPolicy.Route.BACKEND_DIRECT, VmPowerPolicy.Notifications.UI_ONLY));
        assertTrue(VmPowerPolicy.isDirectRestartable(MONITORING));
        assertTrue(decide(favorable(MONITORING).build()).allowed());
        // Retour à la table de production : .160 est de nouveau refusée.
        VmPowerPolicy.resetForTest();
        assertEquals(Mode.DETECT_ONLY, VmPowerPolicy.modeOf(MONITORING));
        assertFalse(VmPowerPolicy.isDirectRestartable(MONITORING));
        assertFalse(decide(favorable(MONITORING).build()).allowed());
        // La couture refuse une VM hors table (la table reste fermée).
        assertThrows(IllegalArgumentException.class, () -> VmPowerPolicy.overrideEntryForTest(
                new VmPowerPolicy.Entry("k8s-evil", "10.0.0.9", Mode.AUTO, VmPowerPolicy.VmClass.INFRA_LB, "infra",
                        "k8s-evil", "VM_DOWN", VmPowerPolicy.Route.CAMUNDA, VmPowerPolicy.Notifications.EMAIL_AND_UI)));
        assertEquals(5, VmPowerPolicy.entries().size());
    }

    @Test
    void k8s_master_est_autorise_comme_un_worker_quand_tous_les_faits_sont_reunis() {
        Decision d = decide(favorable(MASTER).build());
        assertTrue(d.allowed(), d.summary());
        assertEquals(Mode.AUTO, d.mode());
        assertEquals("AUTORISE " + MASTER, d.summary());
    }

    @Test
    void k8s_master_garde_tous_les_verrous_des_workers_vm_allumee_non_confirmee_maintenance_cooldown() {
        assertEquals(List.of(Refusal.STATE_RUNNING), decide(favorable(MASTER).powerState(PowerState.RUNNING).build()).refusals());
        assertEquals(List.of(Refusal.STATE_UNKNOWN), decide(favorable(MASTER).powerState(PowerState.UNKNOWN).build()).refusals());
        assertEquals(List.of(Refusal.NOT_CONFIRMED), decide(favorable(MASTER).poweredOff(1, 0L).build()).refusals());
        assertEquals(List.of(Refusal.MAINTENANCE), decide(favorable(MASTER).maintenance(true).build()).refusals());
        assertEquals(List.of(Refusal.COOLDOWN_ACTIVE), decide(favorable(MASTER).msSinceLastRestartAction(60_000L).build()).refusals());
        assertEquals(List.of(Refusal.SCOPE_MISMATCH),
                decide(favorable(MASTER).incident("k8s-nodes", "k8s-worker1", "NODE_NOT_READY").build()).refusals());
    }

    @Test
    void le_mode_deny_restart_reste_honore_pour_le_plan_de_controle_par_la_couture_de_test() {
        // Aucune VM n'est DENY_RESTART depuis le 2026-11-17 ; le mode est CONSERVÉ : on le rejoue sur .153 par la couture.
        VmPowerPolicy.overrideEntryForTest(new VmPowerPolicy.Entry(MASTER, "192.168.148.153", Mode.DENY_RESTART,
                VmPowerPolicy.VmClass.K8S_CONTROL_PLANE, "k8s-nodes", MASTER, "NODE_NOT_READY",
                VmPowerPolicy.Route.NONE, VmPowerPolicy.Notifications.EMAIL_AND_UI));
        try {
            Decision d = decide(favorable(MASTER).build());
            assertFalse(d.allowed());
            assertEquals(Mode.DENY_RESTART, d.mode());
            assertEquals(List.of(Refusal.NOT_AUTHORIZED), d.refusals());
            assertEquals(Optional.of(Refusal.NOT_AUTHORIZED), VmPowerPolicy.staticRefusal(MASTER));
        } finally {
            VmPowerPolicy.resetForTest();
        }
        assertEquals(Mode.AUTO, VmPowerPolicy.modeOf(MASTER), "la table de production est restaurée");
    }

    @Test
    void k8s_aiCamunda_hors_table_n_est_pas_autorisee() {
        Decision d = decide(favorable("k8s-aiCamunda").build());
        assertFalse(d.allowed());
        assertEquals(Mode.DENY, d.mode());
        assertEquals(List.of(Refusal.UNKNOWN_VM), d.refusals());
    }

    // ───────────────────── R1 : les 4 cibles automatiques ─────────────────────

    @Test
    void les_quatre_cibles_automatiques_sont_autorisees_quand_tous_les_faits_sont_reunis() {
        for (String vm : List.of(WORKER1, WORKER2, LB2, MASTER)) {
            Decision d = decide(favorable(vm).build());
            assertTrue(d.allowed(), vm + " -> " + d.summary());
            assertEquals(Mode.AUTO, d.mode());
            assertTrue(d.refusals().isEmpty());
            assertEquals("AUTORISE " + vm, d.summary());
        }
        // La supervision (.160) n'est JAMAIS autorisée, même avec tous les faits favorables.
        assertFalse(decide(favorable(MONITORING).build()).allowed(), MONITORING);
    }

    @Test
    void exige_POWERED_OFF_confirme_par_deux_observations_espacees() {
        // une seule observation
        assertEquals(List.of(Refusal.NOT_CONFIRMED),
                decide(favorable(WORKER1).poweredOff(1, 0L).build()).refusals());
        // deux observations trop rapprochées
        assertEquals(List.of(Refusal.NOT_CONFIRMED),
                decide(favorable(WORKER1).poweredOff(2, 29_999L).build()).refusals());
        // bornes exactes autorisées
        assertTrue(decide(favorable(WORKER1).poweredOff(2, 30_000L).build()).allowed());
        assertTrue(decide(favorable(WORKER1).poweredOff(5, 120_000L).build()).allowed());
        // zéro observation
        assertEquals(List.of(Refusal.NOT_CONFIRMED),
                decide(favorable(WORKER1).poweredOff(0, 90_000L).build()).refusals());
    }

    @Test
    void VM_allumee_ne_est_jamais_demarree() {
        Decision d = decide(favorable(WORKER2).powerState(PowerState.RUNNING).build());
        assertFalse(d.allowed());
        // Seul le motif d'état est listé : pas de bruit « non confirmé » pour une VM allumée.
        assertEquals(List.of(Refusal.STATE_RUNNING), d.refusals());
        assertEquals(List.of(Refusal.STATE_RUNNING),
                decide(favorable(LB2).powerState(PowerState.RUNNING).poweredOff(0, 0L).probesFailing(false).build())
                        .refusals());
    }

    @Test
    void etat_UNKNOWN_ou_absent_ne_demarre_jamais() {
        assertEquals(List.of(Refusal.STATE_UNKNOWN),
                decide(favorable(WORKER1).powerState(PowerState.UNKNOWN).build()).refusals());
        assertEquals(List.of(Refusal.STATE_UNKNOWN),
                decide(favorable(WORKER1).powerState(null).build()).refusals());
    }

    @Test
    void preuve_d_etat_perimee_refusee() {
        assertEquals(List.of(Refusal.EVIDENCE_STALE),
                decide(favorable(WORKER1).evidenceAgeMs(15_001L).build()).refusals());
        assertEquals(List.of(Refusal.EVIDENCE_STALE),
                decide(favorable(WORKER1).evidenceAgeMs(-1L).build()).refusals());
        assertTrue(decide(favorable(WORKER1).evidenceAgeMs(15_000L).build()).allowed());
        assertTrue(decide(favorable(WORKER1).evidenceAgeMs(0L).build()).allowed());
    }

    @Test
    void sondes_independantes_non_toutes_en_echec_refusent() {
        assertEquals(List.of(Refusal.PROBES_NOT_FAILING),
                decide(favorable(LB2).probesFailing(false).build()).refusals());
    }

    @Test
    void maintenance_refuse() {
        for (String vm : List.of(WORKER1, WORKER2, LB2)) {
            assertEquals(List.of(Refusal.MAINTENANCE),
                    decide(favorable(vm).maintenance(true).build()).refusals(), vm);
        }
    }

    @Test
    void episode_RESOLUTION_FAILED_ouvert_bloque_un_nouveau_demarrage() {
        assertEquals(List.of(Refusal.EPISODE_OPEN),
                decide(favorable(WORKER2).failedEpisodeOpen(true).build()).refusals());
    }

    @Test
    void cooldown_5_minutes_borne_exacte_et_quel_que_soit_le_statut_de_l_action_precedente() {
        // 2026-11-18 : 30 -> 5 min (consigne de l'utilisateur) ; la valeur est figée ICI pour qu'un changement silencieux échoue.
        assertEquals(java.time.Duration.ofMinutes(5), VmPowerPolicy.RESTART_COOLDOWN);
        assertTrue(VmPowerPolicy.RESTART_COOLDOWN.toMillis() >= 240_000L,
                "le cooldown du backend doit rester >= celui du watchdog (HELIOS_COOLDOWN_S = 240 s)");
        long trenteMin = VmPowerPolicy.RESTART_COOLDOWN.toMillis();     // nom conservé : c'est la borne du cooldown, quelle que soit sa valeur
        assertTrue(decide(favorable(LB2).msSinceLastRestartAction(29L * 60_000L).build()).allowed(),
                "une action vieille de 29 min n'est plus un cooldown (c'était le cas à 30 min)");
        // La politique ne connaît PAS le statut de l'action précédente : une action FAILED compte autant
        // qu'une COMPLETED (c'est l'appelant qui fournit « le temps écoulé depuis la dernière action »).
        assertEquals(List.of(Refusal.COOLDOWN_ACTIVE),
                decide(favorable(LB2).msSinceLastRestartAction(0L).build()).refusals());
        assertEquals(List.of(Refusal.COOLDOWN_ACTIVE),
                decide(favorable(LB2).msSinceLastRestartAction(trenteMin - 1).build()).refusals());
        assertTrue(decide(favorable(LB2).msSinceLastRestartAction(trenteMin).build()).allowed());
        assertTrue(decide(favorable(LB2).msSinceLastRestartAction(VmPowerPolicy.NEVER).build()).allowed());
        // horloge incohérente (durée négative) : par prudence, cooldown actif
        assertEquals(List.of(Refusal.COOLDOWN_ACTIVE),
                decide(favorable(LB2).msSinceLastRestartAction(-5L).build()).refusals());
    }

    @Test
    void une_seule_action_RESTART_VM_par_incident() {
        assertTrue(decide(favorable(WORKER1).restartActionsForIncident(0).build()).allowed());
        assertEquals(List.of(Refusal.ACTION_ALREADY_EXISTS),
                decide(favorable(WORKER1).restartActionsForIncident(1).build()).refusals());
        assertEquals(List.of(Refusal.ACTION_ALREADY_EXISTS),
                decide(favorable(WORKER1).restartActionsForIncident(2).build()).refusals());
        // valeur absurde (négative) : refus fail-closed
        assertEquals(List.of(Refusal.ACTION_ALREADY_EXISTS),
                decide(favorable(WORKER1).restartActionsForIncident(-1).build()).refusals());
    }

    // ───────────────────── Périmètre d'incident (R2 : .162 ≠ NODE_NOT_READY) ─────────────────────

    @Test
    void lb2_exige_VM_DOWN_dans_infra_avec_app_k8s_lb2() {
        assertTrue(decide(favorable(LB2).build()).allowed());
        // jamais NODE_NOT_READY / k8s-nodes pour .162
        assertEquals(List.of(Refusal.SCOPE_MISMATCH),
                decide(favorable(LB2).incident("k8s-nodes", LB2, "NODE_NOT_READY").build()).refusals());
        assertEquals(List.of(Refusal.SCOPE_MISMATCH),
                decide(favorable(LB2).incident("k8s-nodes", LB2, "VM_DOWN").build()).refusals());
        assertEquals(List.of(Refusal.SCOPE_MISMATCH),
                decide(favorable(LB2).incident("infra", LB2, "NODE_NOT_READY").build()).refusals());
        // alias dans l'application de l'incident : refusé
        assertEquals(List.of(Refusal.SCOPE_MISMATCH),
                decide(favorable(LB2).incident("infra", "lb2", "VM_DOWN").build()).refusals());
        // namespace en casse différente : refusé (comparaison exacte)
        assertEquals(List.of(Refusal.SCOPE_MISMATCH),
                decide(favorable(LB2).incident("INFRA", LB2, "VM_DOWN").build()).refusals());
    }

    @Test
    void worker_exige_NODE_NOT_READY_dans_k8s_nodes_pour_lui_meme() {
        assertTrue(decide(favorable(WORKER1).build()).allowed());
        assertEquals(List.of(Refusal.SCOPE_MISMATCH),
                decide(favorable(WORKER1).incident("infra", WORKER1, "VM_DOWN").build()).refusals());
        assertEquals(List.of(Refusal.SCOPE_MISMATCH),
                decide(favorable(WORKER1).incident("k8s-nodes", WORKER1, "NODE_HIGH_CPU").build()).refusals());
        // un incident d'un AUTRE nœud ne peut pas justifier le démarrage de celui-ci
        assertEquals(List.of(Refusal.SCOPE_MISMATCH),
                decide(favorable(WORKER1).incident("k8s-nodes", WORKER2, "NODE_NOT_READY").build()).refusals());
        // un incident applicatif (pfe/podinfo) ne démarre pas une VM
        assertEquals(List.of(Refusal.SCOPE_MISMATCH),
                decide(favorable(WORKER2).incident("pfe", "podinfo", "NODE_NOT_READY").build()).refusals());
        assertEquals(List.of(Refusal.SCOPE_MISMATCH),
                decide(favorable(WORKER2).incident(null, null, null).build()).refusals());
    }

    // ───────────────────── Fail-closed et ordre des motifs ─────────────────────

    @Test
    void faits_non_etablis_refuses_fail_closed() {
        Request vide = Request.builder().vm(WORKER1).incident("k8s-nodes", WORKER1, "NODE_NOT_READY").build();
        Decision d = decide(vide);
        assertFalse(d.allowed());
        // état absent => UNKNOWN ; faits d'exécution absents => FACTS_INCOMPLETE
        assertEquals(List.of(Refusal.STATE_UNKNOWN, Refusal.FACTS_INCOMPLETE), d.refusals());

        // POWERED_OFF mais preuves absentes
        Decision d2 = decide(Request.builder().vm(WORKER1).incident("k8s-nodes", WORKER1, "NODE_NOT_READY")
                .powerState(PowerState.POWERED_OFF).maintenance(false).failedEpisodeOpen(false)
                .msSinceLastRestartAction(VmPowerPolicy.NEVER).restartActionsForIncident(0).build());
        assertFalse(d2.allowed());
        assertEquals(List.of(Refusal.FACTS_INCOMPLETE), d2.refusals());
    }

    @Test
    void chaque_fait_manquant_pris_isole_refuse() {
        // On retire UN fait à la fois d'une requête par ailleurs favorable.
        Request base = favorable(LB2).build();
        List<Request> variantes = List.of(
                new Request(base.vm(), base.incidentNamespace(), base.incidentApp(), base.incidentType(),
                        base.powerState(), null, base.poweredOffObservations(), base.poweredOffSpanMs(),
                        base.probesFailing(), base.maintenance(), base.failedEpisodeOpen(),
                        base.msSinceLastRestartAction(), base.restartActionsForIncident()),
                new Request(base.vm(), base.incidentNamespace(), base.incidentApp(), base.incidentType(),
                        base.powerState(), base.evidenceAgeMs(), null, base.poweredOffSpanMs(),
                        base.probesFailing(), base.maintenance(), base.failedEpisodeOpen(),
                        base.msSinceLastRestartAction(), base.restartActionsForIncident()),
                new Request(base.vm(), base.incidentNamespace(), base.incidentApp(), base.incidentType(),
                        base.powerState(), base.evidenceAgeMs(), base.poweredOffObservations(), null,
                        base.probesFailing(), base.maintenance(), base.failedEpisodeOpen(),
                        base.msSinceLastRestartAction(), base.restartActionsForIncident()),
                new Request(base.vm(), base.incidentNamespace(), base.incidentApp(), base.incidentType(),
                        base.powerState(), base.evidenceAgeMs(), base.poweredOffObservations(),
                        base.poweredOffSpanMs(), null, base.maintenance(), base.failedEpisodeOpen(),
                        base.msSinceLastRestartAction(), base.restartActionsForIncident()),
                new Request(base.vm(), base.incidentNamespace(), base.incidentApp(), base.incidentType(),
                        base.powerState(), base.evidenceAgeMs(), base.poweredOffObservations(),
                        base.poweredOffSpanMs(), base.probesFailing(), null, base.failedEpisodeOpen(),
                        base.msSinceLastRestartAction(), base.restartActionsForIncident()),
                new Request(base.vm(), base.incidentNamespace(), base.incidentApp(), base.incidentType(),
                        base.powerState(), base.evidenceAgeMs(), base.poweredOffObservations(),
                        base.poweredOffSpanMs(), base.probesFailing(), base.maintenance(), null,
                        base.msSinceLastRestartAction(), base.restartActionsForIncident()),
                new Request(base.vm(), base.incidentNamespace(), base.incidentApp(), base.incidentType(),
                        base.powerState(), base.evidenceAgeMs(), base.poweredOffObservations(),
                        base.poweredOffSpanMs(), base.probesFailing(), base.maintenance(),
                        base.failedEpisodeOpen(), null, base.restartActionsForIncident()),
                new Request(base.vm(), base.incidentNamespace(), base.incidentApp(), base.incidentType(),
                        base.powerState(), base.evidenceAgeMs(), base.poweredOffObservations(),
                        base.poweredOffSpanMs(), base.probesFailing(), base.maintenance(),
                        base.failedEpisodeOpen(), base.msSinceLastRestartAction(), null));
        assertTrue(decide(base).allowed());
        for (int i = 0; i < variantes.size(); i++) {
            Decision d = decide(variantes.get(i));
            assertFalse(d.allowed(), "variante " + i);
            assertEquals(List.of(Refusal.FACTS_INCOMPLETE), d.refusals(), "variante " + i);
        }
    }

    @Test
    void tous_les_motifs_sont_listes_sans_doublon_dans_l_ordre_de_declaration() {
        Decision d = decide(favorable(WORKER1)
                .incident("infra", WORKER1, "VM_DOWN")
                .evidenceAgeMs(60_000L)
                .poweredOff(1, 0L)
                .probesFailing(false)
                .maintenance(true)
                .failedEpisodeOpen(true)
                .msSinceLastRestartAction(1_000L)
                .restartActionsForIncident(1)
                .build());
        assertFalse(d.allowed());
        assertEquals(List.of(
                Refusal.SCOPE_MISMATCH, Refusal.EVIDENCE_STALE, Refusal.NOT_CONFIRMED, Refusal.PROBES_NOT_FAILING,
                Refusal.MAINTENANCE, Refusal.EPISODE_OPEN, Refusal.COOLDOWN_ACTIVE, Refusal.ACTION_ALREADY_EXISTS),
                d.refusals());
        assertEquals(d.refusals().stream().distinct().count(), d.refusals().size());
        assertEquals("REFUSE k8s-worker1 [SCOPE_MISMATCH,EVIDENCE_STALE,NOT_CONFIRMED,PROBES_NOT_FAILING,"
                + "MAINTENANCE,EPISODE_OPEN,COOLDOWN_ACTIVE,ACTION_ALREADY_EXISTS]", d.summary());
    }

    @Test
    void requete_nulle_est_une_erreur_de_programmation() {
        assertThrows(NullPointerException.class, () -> VmPowerPolicy.authorize(null));
    }

    @Test
    void la_decision_est_immuable_et_expose_les_codes() {
        Decision d = decide(favorable(WORKER1).maintenance(true).build());
        assertThrows(UnsupportedOperationException.class, () -> d.refusals().add(Refusal.UNKNOWN_VM));
        assertEquals(List.of("MAINTENANCE"), d.refusalCodes());
        assertNotNull(d.entry());
    }

    // ───────────────────── Lecture du fil (watchdog) ─────────────────────

    @Test
    void lecture_du_fil_toute_valeur_inconnue_devient_UNKNOWN() {
        assertEquals(PowerState.RUNNING, PowerState.fromWire("RUNNING"));
        assertEquals(PowerState.RUNNING, PowerState.fromWire(" running "));
        assertEquals(PowerState.POWERED_OFF, PowerState.fromWire("POWERED_OFF"));
        for (String bruit : Arrays.asList(null, "", " ", "SUSPENDED", "off", "powered off", "STARTING", "ok", "1")) {
            assertEquals(PowerState.UNKNOWN, PowerState.fromWire(bruit), String.valueOf(bruit));
        }
    }
}
