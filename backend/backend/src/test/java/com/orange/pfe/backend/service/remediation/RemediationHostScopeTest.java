package com.orange.pfe.backend.service.remediation;

import com.orange.pfe.backend.config.RemediationProperties;
import com.orange.pfe.backend.dto.AIOpsAnalyzeRequest;
import com.orange.pfe.backend.dto.AIPredictResponse;
import com.orange.pfe.backend.model.action.ActionType;
import com.orange.pfe.backend.repository.IncidentActionRepository;
import org.junit.jupiter.api.Test;

import java.util.List;
import java.util.Locale;
import java.util.Set;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

/**
 * POLICY-HOST-SCOPE-AUDIT-01 - perimetre hote : noeud Kubernetes vs VM d'infrastructure.
 *
 * Le detecteur pose ns = "k8s-nodes" en CONSTANTE pour ses 16 regles en mode "hote", donc aussi
 * pour monitoring (.160), iacamunda (.161) et lb2 (.162), qui portent toutes un node-exporter et
 * ne sont PAS des noeuds Kubernetes. Ces tests verrouillent la distinction.
 */
class RemediationHostScopeTest {

    /** Inventaire reel du cluster au 2026-09-08, source d'autorite : CoreV1Api.listNode(). */
    private static final List<String> NOEUDS_REELS = List.of("k8s-master", "k8s-worker1", "k8s-worker2");

    /** VM d'infrastructure : hostname reel puis nom emis par le detecteur (IP2NOM). */
    private static final List<String> NON_NOEUDS = List.of(
            "k8s-monitoring", "monitoring",
            "k8s-aiCamunda", "iacamunda",
            "k8s-lb2", "lb2");

    private static RemediationPolicyService service(ActionType... autorisees) {
        IncidentActionRepository repository = mock(IncidentActionRepository.class);
        when(repository.findRecentActionsForTarget(any(), any(), any(), any())).thenReturn(List.of());
        RemediationProperties properties = new RemediationProperties();
        properties.setAutomationEnabled(true);
        properties.setAllowedActions(Set.of(autorisees));
        properties.setAllowlistedApps(Set.of("*"));
        properties.setRestartConfidenceThreshold(0.45d);
        return new RemediationPolicyService(properties, repository);
    }

    private static RemediationPlan plan(RemediationPolicyService s, String ns, String app, String type) {
        AIOpsAnalyzeRequest request = new AIOpsAnalyzeRequest();
        request.setNamespace(ns);
        request.setApp(app);
        AIPredictResponse prediction = new AIPredictResponse();
        prediction.setIncident(true);
        prediction.setIncidentType(type);
        prediction.setSeverity("CRITICAL");
        prediction.setConfidence(0.90d);
        prediction.setRecommendedAction("ESCALATE_NODE_OUTAGE_AND_REBALANCE");
        return s.buildPlan(request, prediction);
    }

    /** Condition telle qu'elle etait AVANT le lot : namespace + type, sans identite de cible. */
    private static boolean conditionAvantCorrection(String ns, String type) {
        return "k8s-nodes".equalsIgnoreCase(ns)
                && type != null && type.toUpperCase(Locale.ROOT).contains("NODE");
    }

    // ---------------------- A / G / H : VM d'infrastructure ----------------------

    @Test
    void testA_iacamunda_nest_pas_un_noeud_kubernetes() {
        assertFalse(RemediationPolicyService.isKubernetesNode("iacamunda"));
        assertFalse(RemediationPolicyService.isKubernetesNode("k8s-aiCamunda"));
    }

    @Test
    void testG_lb2_nest_pas_un_noeud_kubernetes() {
        assertFalse(RemediationPolicyService.isKubernetesNode("lb2"));
        assertFalse(RemediationPolicyService.isKubernetesNode("k8s-lb2"));
    }

    @Test
    void testH_monitoring_nest_pas_un_noeud_kubernetes() {
        assertFalse(RemediationPolicyService.isKubernetesNode("monitoring"));
        assertFalse(RemediationPolicyService.isKubernetesNode("k8s-monitoring"));
    }

    // ---------------------- B / C / D : vrais noeuds ----------------------

    @Test
    void testB_k8s_master_est_un_noeud_kubernetes() {
        assertTrue(RemediationPolicyService.isKubernetesNode("k8s-master"));
    }

    @Test
    void testC_k8s_worker1_est_un_noeud_kubernetes() {
        assertTrue(RemediationPolicyService.isKubernetesNode("k8s-worker1"));
    }

    @Test
    void testD_k8s_worker2_est_un_noeud_kubernetes() {
        assertTrue(RemediationPolicyService.isKubernetesNode("k8s-worker2"));
    }

    @Test
    void inventaire_complet_les_six_vm_sont_classees_correctement() {
        NOEUDS_REELS.forEach(n -> assertTrue(RemediationPolicyService.isKubernetesNode(n), n));
        NON_NOEUDS.forEach(n -> assertFalse(RemediationPolicyService.isKubernetesNode(n), n));
    }

    @Test
    void le_prefixe_k8s_seul_ne_suffit_pas() {
        // k8s-monitoring, k8s-aiCamunda et k8s-lb2 commencent par "k8s-" sans etre des noeuds.
        NON_NOEUDS.stream().filter(n -> n.startsWith("k8s-"))
                .forEach(n -> assertFalse(RemediationPolicyService.isKubernetesNode(n), n));
    }

    @Test
    void entrees_degenerees_refusees() {
        assertFalse(RemediationPolicyService.isKubernetesNode(null));
        assertFalse(RemediationPolicyService.isKubernetesNode(""));
        assertFalse(RemediationPolicyService.isKubernetesNode("   "));
        assertFalse(RemediationPolicyService.isKubernetesNode("backend"));
        assertFalse(RemediationPolicyService.isKubernetesNode("postgres-4"));
    }

    @Test
    void casse_et_espaces_sont_normalises() {
        assertTrue(RemediationPolicyService.isKubernetesNode("K8S-WORKER1"));
        assertTrue(RemediationPolicyService.isKubernetesNode("  k8s-master  "));
    }

    // ---------------------- E / F : effet sur la Policy ----------------------

    @Test
    void testE_iacamunda_avec_NODE_NOT_READY_interdit_RESTART_VM() {
        RemediationPolicyService s = service(ActionType.RESTART_VM, ActionType.RESTART_POD);
        RemediationPlan p = plan(s, "k8s-nodes", "iacamunda", "NODE_NOT_READY");

        // Contre-preuve : la condition d'AVANT le lot etait bel et bien satisfaite.
        assertTrue(conditionAvantCorrection("k8s-nodes", "NODE_NOT_READY"),
                "la condition pre-correction devait matcher - sinon le test ne prouve rien");

        assertNotEquals(ActionType.RESTART_VM, p.actionType());
        assertFalse(p.autoExecutable());
    }

    /**
     * VMDOWN-POLICY-20261105 puis consigne du 2026-11-17 — le control-plane (.153) était DENY_RESTART (escalade) ; il est
     * désormais une cible AUTO comme les workers : NODE_NOT_READY → RESTART_VM (le power-on réel reste soumis à la
     * politique : VM prouvée éteinte, armement, cooldown). Le test de l'ancien refus devient ce test de l'ouverture.
     */
    @Test
    void testF_k8s_master_avec_NODE_NOT_READY_est_une_cible_auto_RESTART_VM() {
        RemediationPolicyService s = service(ActionType.RESTART_VM, ActionType.RESTART_POD);
        RemediationPlan p = plan(s, "k8s-nodes", "k8s-master", "NODE_NOT_READY");
        assertEquals(ActionType.RESTART_VM, p.actionType());
        assertEquals(plan(s, "k8s-nodes", "k8s-worker1", "NODE_NOT_READY").autoExecutable(), p.autoExecutable(),
                ".153 se comporte comme un worker");
    }

    @Test
    void les_trois_noeuds_sont_automatiques_master_compris() {
        RemediationPolicyService s = service(ActionType.RESTART_VM, ActionType.RESTART_POD);
        for (String n : NOEUDS_REELS) {
            assertEquals(ActionType.RESTART_VM, plan(s, "k8s-nodes", n, "NODE_NOT_READY").actionType(), n);
        }
    }

    @Test
    void aucune_vm_dinfrastructure_ne_recoit_RESTART_VM() {
        RemediationPolicyService s = service(ActionType.RESTART_VM, ActionType.RESTART_POD);
        for (String vm : NON_NOEUDS) {
            for (String type : List.of("NODE_NOT_READY", "NODE_HIGH_MEMORY", "NODE_DISK_FULL",
                                       "NODE_OOM_KILL", "NODE_HIGH_CPU")) {
                assertNotEquals(ActionType.RESTART_VM, plan(s, "k8s-nodes", vm, type).actionType(),
                        vm + " / " + type);
            }
        }
    }

    /** Le scenario reellement atteignable : .161 est la VM la plus chargee en memoire (82 %). */
    @Test
    void scenario_reel_NODE_HIGH_MEMORY_sur_iacamunda() {
        RemediationPolicyService s = service(ActionType.RESTART_VM, ActionType.RESTART_POD);
        assertTrue(conditionAvantCorrection("k8s-nodes", "NODE_HIGH_MEMORY"));
        assertNotEquals(ActionType.RESTART_VM,
                plan(s, "k8s-nodes", "iacamunda", "NODE_HIGH_MEMORY").actionType());
    }

    @Test
    void node_high_cpu_selects_quota_but_requires_allowlisting_for_auto_execution() {
        RemediationPolicyService s = service(
                ActionType.RESTART_VM, ActionType.RESTART_POD, ActionType.HOST_RECLAIM_DISK);

        for (String node : NOEUDS_REELS) {
            RemediationPlan p = plan(s, "k8s-nodes", node, "NODE_HIGH_CPU");
            assertEquals(ActionType.HOST_SERVICE_CPU_QUOTA, p.actionType(), node);
            assertNotEquals(ActionType.RESTART_VM, p.actionType(), node);
            assertFalse(p.autoExecutable(), node);
        }
    }

    @Test
    void disk_incidents_use_reclaim_action_without_vm_restart_or_auto_execution() {
        RemediationPolicyService s = service(
                ActionType.RESTART_VM, ActionType.RESTART_POD, ActionType.HOST_RECLAIM_DISK);

        for (String node : NOEUDS_REELS) {
            for (String type : List.of("NODE_DISK_FULL", "NODE_DISK_PRESSURE", "NODE_INODES_FULL")) {
                RemediationPlan p = plan(s, "k8s-nodes", node, type);
                assertEquals(ActionType.HOST_RECLAIM_DISK, p.actionType(), node + " / " + type);
                assertNotEquals(ActionType.RESTART_VM, p.actionType(), node + " / " + type);
                assertFalse(p.autoExecutable(), node + " / " + type);
            }
        }
    }

    @Test
    void critical_disk_auto_execution_requires_enabled_flag_and_approved_scope() {
        IncidentActionRepository repository = mock(IncidentActionRepository.class);
        when(repository.findRecentActionsForTarget(any(), any(), any(), any()))
                .thenReturn(List.of());

        RemediationProperties properties = new RemediationProperties();
        properties.setAutomationEnabled(true);
        properties.setHostDiskRemediationEnabled(true);
        // HOST_RECLAIM_DISK est volontairement absent de la liste generale.
        properties.setAllowedActions(Set.of(ActionType.RESTART_VM, ActionType.RESTART_POD));
        properties.setAllowlistedApps(Set.of("*"));
        properties.setRestartConfidenceThreshold(0.45d);

        RemediationPolicyService s = new RemediationPolicyService(properties, repository);

        // NODE_DISK_PRESSURE / NODE_INODES_FULL : chemin d'origine STRICTEMENT INCHANGE (ancien drapeau global, memes
        // cibles qu'avant : k8s-worker1 ET k8s-worker2) -- DISK-E2E-158-02 n'y touche pas.
        for (String node : List.of("k8s-worker1", "k8s-worker2")) {
            for (String type : List.of("NODE_DISK_PRESSURE", "NODE_INODES_FULL")) {
                RemediationPlan p = plan(s, "k8s-nodes", node, type);

                assertEquals(ActionType.HOST_RECLAIM_DISK, p.actionType(), node + " / " + type);
                assertTrue(p.autoExecutable(),
                        "Le DISK CRITICAL autorise doit etre executable lorsque le flag est actif : "
                                + node + " / " + type + " : " + p.rationale());
                assertFalse(p.approvalRequired(), node + " / " + type);
                assertFalse(p.manualFollowUp(), node + " / " + type);
            }
        }

        // NODE_DISK_FULL n'a QU'UNE voie : le scenario (k8s-worker1), gouverne par son drapeau DEDIE ; l'ancien ne l'active pas.
        RemediationPlan scenario = plan(s, "k8s-nodes", "k8s-worker1", "NODE_DISK_FULL");
        assertEquals(ActionType.HOST_RECLAIM_DISK, scenario.actionType());
        assertFalse(scenario.autoExecutable(), "l'ancien drapeau global ne doit pas activer DISK_E2E_158");

        // k8s-worker2 (.155, primaire CNPG) + NODE_DISK_FULL : JAMAIS eligible, quel que soit le drapeau.
        RemediationPlan p155 = plan(s, "k8s-nodes", "k8s-worker2", "NODE_DISK_FULL");
        assertEquals(ActionType.HOST_RECLAIM_DISK, p155.actionType(), "k8s-worker2 / NODE_DISK_FULL");
        assertFalse(p155.autoExecutable(), "k8s-worker2 ne doit jamais etre auto-executable pour NODE_DISK_FULL");
        assertTrue(p155.manualFollowUp(), "k8s-worker2 / NODE_DISK_FULL");

        // Une cible hors du perimetre DISK ne beneficie pas de l'exception CRITICAL.
        RemediationPlan badTarget = plan(s, "k8s-nodes", "monitoring", "NODE_DISK_FULL");
        assertEquals(ActionType.ESCALATION, badTarget.actionType(), "Une cible hors allowlist doit etre refusee par la politique");
        assertFalse(badTarget.autoExecutable(),
                "L'exception DISK ne doit pas autoriser une cible hors allowlist");

        // Un incident CRITICAL non-DISK reste bloque par la regle generale.
        RemediationPlan criticalNode = plan(s, "k8s-nodes", "k8s-worker1", "NODE_NOT_READY");
        assertEquals(ActionType.RESTART_VM, criticalNode.actionType());
        assertFalse(criticalNode.autoExecutable(),
                "L'exception DISK ne doit pas autoriser les autres incidents CRITICAL");
    }
    @Test
    void declared_vm_recovery_incidents_keep_restart_vm_route_for_real_nodes() {
        RemediationPolicyService s = service(
                ActionType.RESTART_VM, ActionType.RESTART_POD, ActionType.HOST_RECLAIM_DISK);

        for (String node : NOEUDS_REELS) {
            for (String type : List.of("NODE_NOT_READY", "VM_DOWN", "VM_POWERED_OFF")) {
                RemediationPlan p = plan(s, "k8s-nodes", node, type);
                // Consigne du 2026-11-17 : les trois nœuds (control-plane .153 compris) sont routés vers RESTART_VM.
                assertEquals(ActionType.RESTART_VM, p.actionType(), node + " / " + type);
            }
        }
    }

    // ---------------------- table de verite du lot ----------------------

    @Test
    void table_de_verite() {
        RemediationPolicyService s = service(ActionType.RESTART_VM, ActionType.RESTART_POD);
        assertNotEquals(ActionType.RESTART_VM, plan(s, "k8s-nodes", "iacamunda",   "TARGET_DOWN").actionType());
        assertEquals   (ActionType.RESTART_VM, plan(s, "k8s-nodes", "k8s-master",  "NODE_NOT_READY").actionType());   // .153 AUTO (2026-11-17)
        assertEquals   (ActionType.RESTART_VM, plan(s, "k8s-nodes", "k8s-worker2", "NODE_NOT_READY").actionType());
        assertNotEquals(ActionType.RESTART_VM, plan(s, "pfe",       "k8s-master",  "NODE_NOT_READY").actionType());
        assertNotEquals(ActionType.RESTART_VM, plan(s, "k8s-nodes", "k8s-worker1", "TARGET_DOWN").actionType());
    }
}
