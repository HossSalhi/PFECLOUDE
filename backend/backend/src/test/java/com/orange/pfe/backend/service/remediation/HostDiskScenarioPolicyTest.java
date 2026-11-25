package com.orange.pfe.backend.service.remediation;

import com.orange.pfe.backend.config.RemediationProperties;
import com.orange.pfe.backend.dto.AIOpsAnalyzeRequest;
import com.orange.pfe.backend.dto.AIPredictResponse;
import com.orange.pfe.backend.model.action.ActionType;
import com.orange.pfe.backend.repository.IncidentActionRepository;
import org.junit.jupiter.api.Test;

import java.util.List;
import java.util.Set;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

/**
 * DISK-E2E-158-02 : eligibilite a l'execution automatique de HOST_RECLAIM_DISK, pour TOUTES les combinaisons des deux drapeaux
 * (ancien drapeau global, drapeau dedie du scenario).
 *
 * <pre>
 *   k8s-worker1 + NODE_DISK_FULL                    : eligible  SSI le drapeau DEDIE est actif (l'ancien n'y change rien)
 *   k8s-worker2 (.155) + NODE_DISK_FULL             : JAMAIS eligible, quels que soient les drapeaux
 *   k8s-worker1/2 + NODE_DISK_PRESSURE / INODES_FULL: eligible  SSI l'ancien drapeau est actif   (chemin d'origine, STRICTEMENT INCHANGE)
 * </pre>
 * Le type d'action du plan (HOST_RECLAIM_DISK) ne depend, lui, d'aucun drapeau. CPU, RAM, VM_DOWN ne dependent d'aucun des deux.
 */
class HostDiskScenarioPolicyTest {

    private static final List<String> TYPES_DISQUE = List.of("NODE_DISK_FULL", "NODE_DISK_PRESSURE", "NODE_INODES_FULL");
    private static final List<String> NOEUDS = List.of("k8s-master", "k8s-worker1", "k8s-worker2");

    private static RemediationPolicyService service(boolean ancienDrapeau, boolean drapeauScenario) {
        IncidentActionRepository repository = mock(IncidentActionRepository.class);
        when(repository.findRecentActionsForTarget(any(), any(), any(), any())).thenReturn(List.of());
        RemediationProperties properties = new RemediationProperties();
        properties.setAutomationEnabled(true);
        properties.setHostDiskRemediationEnabled(ancienDrapeau);
        properties.setHostDiskE2e158Enabled(drapeauScenario);
        // HOST_RECLAIM_DISK est volontairement absent de la liste generale : seule l'exception DISK peut le rendre automatique.
        properties.setAllowedActions(Set.of(ActionType.RESTART_VM, ActionType.RESTART_POD));
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

    private static boolean attendu(boolean ancienDrapeau, boolean drapeauScenario, String noeud, String type) {
        if ("NODE_DISK_FULL".equals(type)) {
            return "k8s-worker1".equals(noeud) && drapeauScenario;  // scenario : drapeau dedie seul ; .155 : JAMAIS
        }
        // NODE_DISK_PRESSURE / NODE_INODES_FULL : comme avant (ancien drapeau, les deux workers)
        return ("k8s-worker1".equals(noeud) || "k8s-worker2".equals(noeud)) && ancienDrapeau;
    }

    @Test
    void matriceCompleteDesDrapeauxCiblesEtTypes() {
        boolean[] valeurs = {false, true};
        for (boolean ancien : valeurs) {
            for (boolean scenario : valeurs) {
                RemediationPolicyService s = service(ancien, scenario);
                for (String noeud : NOEUDS) {
                    for (String type : TYPES_DISQUE) {
                        String cas = "ancien=" + ancien + " scenario=" + scenario + " " + noeud + " / " + type;
                        RemediationPlan p = plan(s, "k8s-nodes", noeud, type);
                        boolean auto = attendu(ancien, scenario, noeud, type);

                        assertEquals(auto, p.autoExecutable(), cas);
                        assertEquals(!auto, p.manualFollowUp(), cas);
                        assertEquals(!auto, p.approvalRequired(), cas);
                        if (!"k8s-master".equals(noeud)) {
                            assertEquals(ActionType.HOST_RECLAIM_DISK, p.actionType(), "le type d'action ne depend d'aucun drapeau : " + cas);
                        }
                    }
                }
            }
        }
    }

    @Test
    void worker2NEstJamaisEligiblePourNodeDiskFull() {
        boolean[] valeurs = {false, true};
        for (boolean ancien : valeurs) {
            for (boolean scenario : valeurs) {
                RemediationPolicyService s = service(ancien, scenario);
                RemediationPlan p = plan(s, "k8s-nodes", "k8s-worker2", "NODE_DISK_FULL");
                assertFalse(p.autoExecutable(), "ancien=" + ancien + " scenario=" + scenario);
                assertTrue(p.manualFollowUp());
                assertTrue(p.approvalRequired());
            }
        }
    }

    @Test
    void leScenarioNExigeQueSonDrapeauDedieEtPasLAncien() {
        assertTrue(plan(service(false, true), "k8s-nodes", "k8s-worker1", "NODE_DISK_FULL").autoExecutable(),
                "drapeau dedie seul : eligible");
        assertFalse(plan(service(true, false), "k8s-nodes", "k8s-worker1", "NODE_DISK_FULL").autoExecutable(),
                "ancien drapeau seul : NON eligible");
        assertFalse(plan(service(false, false), "k8s-nodes", "k8s-worker1", "NODE_DISK_FULL").autoExecutable());
        assertTrue(plan(service(true, true), "k8s-nodes", "k8s-worker1", "NODE_DISK_FULL").autoExecutable());
    }

    @Test
    void pressureEtInodesRestentStrictementInchanges() {
        // Consigne : NODE_DISK_PRESSURE et NODE_INODES_FULL ne sont PAS modifies (ancien drapeau, memes cibles qu'avant).
        for (String noeud : List.of("k8s-worker1", "k8s-worker2")) {
            for (String type : List.of("NODE_DISK_PRESSURE", "NODE_INODES_FULL")) {
                String cas = noeud + " / " + type;
                assertTrue(plan(service(true, false), "k8s-nodes", noeud, type).autoExecutable(), "ancien seul : comme avant : " + cas);
                assertTrue(plan(service(true, true), "k8s-nodes", noeud, type).autoExecutable(), "les deux : comme avant : " + cas);
                assertFalse(plan(service(false, true), "k8s-nodes", noeud, type).autoExecutable(),
                        "le drapeau du scenario n'ouvre PAS " + cas);
                assertFalse(plan(service(false, false), "k8s-nodes", noeud, type).autoExecutable(), cas);
            }
        }
    }

    @Test
    void uneCibleHorsPerimetreOuUnAutreNamespaceNEstJamaisEligible() {
        for (boolean ancien : new boolean[] {false, true}) {
            for (boolean scenario : new boolean[] {false, true}) {
                RemediationPolicyService s = service(ancien, scenario);
                assertFalse(plan(s, "k8s-nodes", "monitoring", "NODE_DISK_FULL").autoExecutable());
                assertFalse(plan(s, "k8s-nodes", "iacamunda", "NODE_DISK_FULL").autoExecutable());
                assertFalse(plan(s, "pfe", "k8s-worker1", "NODE_DISK_FULL").autoExecutable(), "autre namespace");
            }
        }
    }

    @Test
    void cpuRamEtVmDownNeDependentDAucunDesDeuxDrapeaux() {
        List<String> autresTypes = List.of("NODE_HIGH_CPU", "NODE_CPU_SATURATION", "NODE_HIGH_MEMORY", "NODE_MEMORY_PRESSURE",
                "NODE_NOT_READY", "VM_DOWN", "VM_POWERED_OFF", "NODE_TIME_DRIFT");
        RemediationPolicyService reference = service(false, false);
        for (boolean ancien : new boolean[] {false, true}) {
            for (boolean scenario : new boolean[] {false, true}) {
                RemediationPolicyService s = service(ancien, scenario);
                for (String noeud : NOEUDS) {
                    for (String type : autresTypes) {
                        assertEquals(plan(reference, "k8s-nodes", noeud, type), plan(s, "k8s-nodes", noeud, type),
                                "plan modifie par un drapeau DISK : ancien=" + ancien + " scenario=" + scenario + " " + noeud + " / " + type);
                    }
                }
            }
        }
    }

    @Test
    void lesPlansCpuEtVmRestentCeuxAttendus() {
        RemediationPolicyService s = service(true, true);
        for (String noeud : NOEUDS) {
            assertEquals(ActionType.HOST_SERVICE_CPU_QUOTA, plan(s, "k8s-nodes", noeud, "NODE_HIGH_CPU").actionType(), noeud);
        }
        assertEquals(ActionType.RESTART_VM, plan(s, "k8s-nodes", "k8s-worker1", "VM_DOWN").actionType());
        assertEquals(ActionType.RESTART_VM, plan(s, "k8s-nodes", "k8s-worker2", "VM_DOWN").actionType());
        // consigne du 2026-11-17 : .153 (plan de contrôle) est une cible AUTO, routée vers RESTART_VM comme un worker ; seul
        // l'armement (ACTIVE_VMS + DRYRUN=false) décide d'un vrai démarrage, et VmPowerWatch exige la preuve « VM éteinte ».
        assertEquals(ActionType.RESTART_VM, plan(s, "k8s-nodes", "k8s-master", "VM_DOWN").actionType(),
                "le control-plane .153 est routé vers RESTART_VM comme un worker (consigne du 2026-11-17)");
    }
}
