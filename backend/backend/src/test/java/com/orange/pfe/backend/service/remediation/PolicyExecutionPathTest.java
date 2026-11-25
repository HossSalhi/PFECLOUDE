package com.orange.pfe.backend.service.remediation;

import com.orange.pfe.backend.config.RemediationProperties;
import com.orange.pfe.backend.dto.AIOpsAnalyzeRequest;
import com.orange.pfe.backend.dto.AIPredictResponse;
import com.orange.pfe.backend.model.action.ActionType;
import com.orange.pfe.backend.model.action.AutomationMode;
import com.orange.pfe.backend.model.action.RemediationTargetKind;
import com.orange.pfe.backend.repository.IncidentActionRepository;
import org.junit.jupiter.api.Test;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
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
 * POLICY-EXECUTION-PATH-01 - validation NON DESTRUCTIVE du chemin d execution reel.
 *
 * Aucun de ces tests n appelle le Bridge, le watchdog, l hyperviseur, vmrun ni Kubernetes.
 * L executeur est exerce SANS watchdog configure (ni :9999 ni :9099) : la garde d identite se
 * prononce AVANT toute construction de client HTTP, et une cible Kubernetes s arrete sur
 * "watchdog maitre non configure" (remediation.master-watchdog-url). Les deux branches font
 * donc zero appel reseau, par construction.
 */
class PolicyExecutionPathTest {

    /** Inventaire d autorite (CoreV1Api.listNode()) au 2026-09-08. */
    private static final List<String> NOEUDS = List.of("k8s-master", "k8s-worker1", "k8s-worker2");

    /** VM d infrastructure : hostname reel, puis nom emis par le detecteur (IP2NOM). */
    // VMDOWN-POLICY-20261105 : « k8s-lb2 » (.162) n'est plus ici — c'est une cible AUTOMATIQUE de la politique
    // VM_DOWN (incident VM_DOWN/infra, jamais NODE_NOT_READY). L'alias « lb2 » reste refusé (noms exacts).
    private static final List<String> NON_NOEUDS = List.of(
            "k8s-monitoring", "monitoring",
            "k8s-aiCamunda", "iacamunda",
            "lb2");

    // Sous-chaine volontairement SANS ACCENT : le test reste valide quel que soit
    // l encodage de compilation. Elle est unique au message de refus d identite.
    private static final String MSG_REFUS = "hors cluster";
    // CLOTURE-UNIFY-9099 : marqueur « la cible A FRANCHI la garde d identite+allowlist et atteint le
    // transport unifie :9099 », mais s arrete faute de config (aucun appel reseau). Sous-chaine ASCII
    // unique au message "watchdog maitre non configure (remediation.master-watchdog-url ...)".
    private static final String MSG_GARDE_FRANCHIE = "master-watchdog-url";

    // ------------------------------------------------------------------ outillage

    private static RemediationPolicyService policy() {
        IncidentActionRepository repo = mock(IncidentActionRepository.class);
        when(repo.findRecentActionsForTarget(any(), any(), any(), any())).thenReturn(List.of());
        RemediationProperties p = new RemediationProperties();
        p.setAutomationEnabled(true);
        p.setAllowedActions(Set.of(ActionType.RESTART_VM, ActionType.RESTART_POD));
        p.setAllowlistedApps(Set.of("*"));
        p.setRestartConfidenceThreshold(0.45d);
        return new RemediationPolicyService(p, repo);
    }

    /** Action recommandee NEUTRE : le plan ne peut alors venir que du bloc de forcage. */
    private static final String IA_NEUTRE = "ESCALATE_NODE_OUTAGE_AND_REBALANCE";

    /** Action recommandee qui demande EXPLICITEMENT une action hyperviseur. */
    private static final String IA_VM = "RESTART_NODE_VM";

    private static RemediationPlan decider(String ns, String app, String type, String actionIA) {
        return decider(ns, app, type, actionIA, "CRITICAL");
    }

    private static RemediationPlan decider(String ns, String app, String type,
                                           String actionIA, String severite) {
        AIOpsAnalyzeRequest req = new AIOpsAnalyzeRequest();
        req.setNamespace(ns);
        req.setApp(app);
        AIPredictResponse pred = new AIPredictResponse();
        pred.setIncident(true);
        pred.setIncidentType(type);
        pred.setSeverity(severite);
        pred.setConfidence(0.90d);
        pred.setRecommendedAction(actionIA);
        return policy().buildPlan(req, pred);
    }

    /** Executeur dont l URL hyperviseur est VIDE : aucun appel sortant n est possible. */
    private static KubernetesRemediationExecutor executeurSansReseau() {
        RemediationProperties p = new RemediationProperties();
        p.setWatchdogBaseUrl("");
        p.setAllowlistedNamespaces(Set.of("*"));
        return new KubernetesRemediationExecutor(p);
    }

    private static RemediationExecutionResult executer(String cible) {
        RemediationPlan plan = new RemediationPlan(
                ActionType.RESTART_VM, AutomationMode.AUTO,
                false, true, false, false, false,
                "k8s-nodes", RemediationTargetKind.UNKNOWN, cible,
                "POLICY-EXECUTION-PATH-01 - plan synthetique, non execute");
        return executeurSansReseau().execute(plan, false);
    }

    private static String source(String chemin) throws IOException {
        return Files.readString(Path.of("src/main/java/com/orange/pfe/backend/" + chemin));
    }

    // ------------------------------------------- ETAPE 4 : le predicat reel

    @Test
    void etape4_predicat_les_huit_cas_exiges() {
        assertFalse(RemediationPolicyService.isKubernetesNode(null));
        assertFalse(RemediationPolicyService.isKubernetesNode(""));
        assertFalse(RemediationPolicyService.isKubernetesNode("iacamunda"));
        assertFalse(RemediationPolicyService.isKubernetesNode("monitoring"));
        assertFalse(RemediationPolicyService.isKubernetesNode("lb2"));
        assertTrue(RemediationPolicyService.isKubernetesNode("k8s-master"));
        assertTrue(RemediationPolicyService.isKubernetesNode("k8s-worker1"));
        assertTrue(RemediationPolicyService.isKubernetesNode("k8s-worker2"));
    }

    @Test
    void etape4_variantes_casse_et_espaces() {
        // La fonction normalise : trim() puis toLowerCase(Locale.ROOT).
        assertTrue(RemediationPolicyService.isKubernetesNode("K8S-MASTER"));
        assertTrue(RemediationPolicyService.isKubernetesNode(" k8s-worker1"));
        assertTrue(RemediationPolicyService.isKubernetesNode("k8s-worker1 "));
        assertFalse(RemediationPolicyService.isKubernetesNode("   "));
        assertFalse(RemediationPolicyService.isKubernetesNode(" IACAMUNDA "));
    }

    @Test
    void etape4_cible_inconnue_refusee() {
        for (String inconnue : List.of("unknown", "inconnue", "192.168.148.161", "k8s-", "worker1",
                                       "backend", "postgres-4", "k8s-lb2")) {
            assertFalse(RemediationPolicyService.isKubernetesNode(inconnue), inconnue);
        }
    }

    // ------------------------------------------- ETAPE 5 : la Policy Java reelle

    @Test
    void etape5_policy_noeud_reel_autorise_RESTART_VM() {
        for (String n : NOEUDS) {
            // Consigne du 2026-11-17 : le control-plane (.153) est une cible AUTO comme les workers -> RESTART_VM.
            assertEquals(ActionType.RESTART_VM, decider("k8s-nodes", n, "NODE_NOT_READY", IA_NEUTRE).actionType(), n);
        }
        assertEquals(decider("k8s-nodes", "k8s-worker1", "NODE_NOT_READY", IA_NEUTRE).autoExecutable(),
                decider("k8s-nodes", "k8s-master", "NODE_NOT_READY", IA_NEUTRE).autoExecutable(), ".153 se comporte comme un worker");
    }

    @Test
    void etape5_k8s_lb2_n_est_jamais_un_noeud_ni_un_NODE_NOT_READY() {
        // .162 : chemin de redemarrage INDEPENDANT de NODE_NOT_READY (VmPowerWatch, incident VM_DOWN/infra).
        for (String type : List.of("NODE_NOT_READY", "VM_DOWN", "VM_POWERED_OFF")) {
            RemediationPlan p = decider("k8s-nodes", "k8s-lb2", type, IA_NEUTRE);
            assertNotEquals(ActionType.RESTART_VM, p.actionType(), type);
            assertFalse(p.autoExecutable(), type);
        }
    }

    @Test
    void etape5_le_forcage_ne_sapplique_plus_aux_vm_infrastructure() {
        // Bloc de forcage seul (l action de l IA est neutre) : la garde isKubernetesNode
        // ajoutee au lot POLICY-HOST-SCOPE-AUDIT-01 fait son office.
        for (String vm : NON_NOEUDS) {
            RemediationPlan p = decider("k8s-nodes", vm, "NODE_NOT_READY", IA_NEUTRE);
            assertNotEquals(ActionType.RESTART_VM, p.actionType(), vm);
            assertFalse(p.autoExecutable(), vm);
        }
    }

    /**
     * ECART MESURE, VOLONTAIREMENT EPINGLE PAR CE TEST.
     *
     * <p>La garde d identite protege le bloc de FORCAGE, pas {@code mapActionType}. Quatre
     * chaines — RESTART_NODE_VM, RESTART_VM, REBOOT_VM, POWER_ON_VM — se traduisent en
     * {@code ActionType.RESTART_VM} sans aucun controle de cible. Si l IA recommande l une
     * d elles sur une VM d infrastructure, la COUCHE DE DECISION produit un plan RESTART_VM,
     * et il peut meme etre marque auto-executable.
     *
     * <p>La securite ne repose donc PAS sur la decision, mais sur la COUCHE D EXECUTION
     * (voir etape6 et l invariant du dernier garde). Ce test echouera le jour ou la
     * decision sera durcie : ce sera alors le signal d actualiser le rapport, pas un bug.
     */
    @Test
    void etape5_ECART_le_mapping_direct_contourne_la_garde_de_forcage() {
        for (String vm : NON_NOEUDS) {
            RemediationPlan p = decider("k8s-nodes", vm, "NODE_NOT_READY", IA_VM);
            assertEquals(ActionType.RESTART_VM, p.actionType(),
                    "ecart attendu et documente pour " + vm);
        }
        // Portee exacte de l ecart, mesuree et non supposee :
        //  - severite CRITICAL : NON auto-executable, mais c est isSeverityEligible qui
        //    l arrete (severity != CRITICAL), PAS l identite de la cible ;
        //  - severite non-CRITICAL : l ecart va jusqu a l AUTO-EXECUTION.
        assertFalse(decider("k8s-nodes", "iacamunda", "NODE_NOT_READY", IA_VM, "CRITICAL").autoExecutable(),
                "CRITICAL est bloque par la severite, pas par l identite");
        assertTrue(decider("k8s-nodes", "iacamunda", "NODE_NOT_READY", IA_VM, "HIGH").autoExecutable(),
                "ecart maximal : plan RESTART_VM AUTO-EXECUTABLE sur une VM hors cluster");
    }

    /**
     * Contre-preuve du test precedent : ce n est pas la severite qui protege les vrais
     * noeuds non plus. A severite egale, noeud et non-noeud sont traites identiquement par
     * la couche de decision — seule l EXECUTION les separe.
     */
    @Test
    void etape5_ECART_a_severite_egale_noeud_et_non_noeud_sont_indistinguables() {
        RemediationPlan noeud = decider("k8s-nodes", "k8s-worker1", "NODE_NOT_READY", IA_VM, "HIGH");
        RemediationPlan vm = decider("k8s-nodes", "iacamunda", "NODE_NOT_READY", IA_VM, "HIGH");
        assertEquals(noeud.actionType(), vm.actionType());
        assertEquals(noeud.autoExecutable(), vm.autoExecutable());
        // ... et pourtant l executeur les separe sans ambiguite :
        assertTrue(executer("k8s-worker1").message().contains(MSG_GARDE_FRANCHIE));
        assertTrue(executer("iacamunda").message().contains(MSG_REFUS));
    }

    @Test
    void etape5_les_quatre_chaines_hyperviseur_sont_toutes_concernees() {
        for (String a : List.of("RESTART_NODE_VM", "RESTART_VM", "REBOOT_VM", "POWER_ON_VM")) {
            assertEquals(ActionType.RESTART_VM,
                    decider("k8s-nodes", "iacamunda", "NODE_NOT_READY", a).actionType(), a);
            // ... mais l executeur les refuse toutes.
            assertTrue(executer("iacamunda").message().contains(MSG_REFUS), a);
        }
    }

    // ------------------------------------------- ETAPE 6 : l executeur (dernier garde)

    @Test
    void etape6_executeur_refuse_toute_vm_infrastructure_sans_appel_sortant() {
        for (String vm : NON_NOEUDS) {
            RemediationExecutionResult r = executer(vm);
            assertFalse(r.success(), vm);
            // k8s-monitoring (.160) est en DÉTECTION SEULE (consigne finale du 2026-11-06) : refusée par la politique,
            // avec un message qui le dit ; les autres (alias, hors table) sont refusées comme cibles inconnues.
            assertTrue(r.message().contains(MSG_REFUS), vm + " -> " + r.message());
            if ("k8s-monitoring".equals(vm)) {
                assertTrue(r.message().contains("DÉTECTION SEULE"), vm + " -> " + r.message());
                assertTrue(r.message().contains("administrateur"), vm + " -> " + r.message());
            }
            // Contre-preuve : le refus vient de l identite, PAS de l URL absente.
            assertFalse(r.message().contains(MSG_GARDE_FRANCHIE), vm);
        }
    }

    @Test
    void etape6_executeur_refuse_cible_inconnue_nulle_ou_vide() {
        for (String c : List.of("unknown", "inconnue", "192.168.148.161", "")) {
            RemediationExecutionResult r = executer(c);
            assertFalse(r.success(), c);
            assertTrue(r.message().contains(MSG_REFUS), c + " -> " + r.message());
        }
    }

    @Test
    void etape6_executeur_laisse_passer_un_vrai_worker_sans_joindre_l_hyperviseur() {
        // Les noeuds AUTO (workers et, depuis la consigne du 2026-11-17, le control-plane .153) franchissent la garde de
        // l executeur ; l arret suivant est l URL vide => AUCUN appel HTTP. C est la preuve demandee sans toucher l hyperviseur.
        // (Le refus par mode DENY_RESTART force est couvert par KubernetesRemediationExecutorMasterRecoveryTest.)
        for (String n : List.of("k8s-worker1", "k8s-worker2", "k8s-master")) {
            RemediationExecutionResult r = executer(n);
            assertFalse(r.message().contains(MSG_REFUS), n + " -> " + r.message());
            assertTrue(r.message().contains(MSG_GARDE_FRANCHIE), n + " -> " + r.message());
        }
        // .162 (k8s-lb2) : cible AUTOMATIQUE -> franchit la garde statique et s arrete faute de configuration.
        RemediationExecutionResult lb2 = executer("k8s-lb2");
        assertFalse(lb2.success());
        assertTrue(lb2.message().contains(MSG_GARDE_FRANCHIE), lb2.message());
    }

    // ------------------------------------------- ETAPE 7 : AIOpsOrchestrationService

    @Test
    void etape7_aiops_invariant_de_source_la_garde_est_presente_et_conjonctive() throws IOException {
        String src = source("service/AIOpsOrchestrationService.java");
        int i = src.indexOf("boolean isNode =");
        assertTrue(i > 0, "bloc isNode introuvable");
        String bloc = src.substring(i, src.indexOf(';', i) + 1);
        assertTrue(bloc.contains("RemediationPolicyService.isKubernetesNode(app)"),
                "la garde d identite a disparu du bloc isNode : " + bloc);
        assertTrue(bloc.contains("&& RemediationPolicyService.isKubernetesNode(app)"),
                "la garde doit etre CONJONCTIVE (&&), pas alternative : " + bloc);
    }

    @Test
    void etape7_aiops_predicat_compose_refuse_les_vm_infrastructure() {
        // Reproduction exacte de l expression d AIOpsOrchestrationService (l.1325-1329).
        for (String vm : NON_NOEUDS) {
            assertFalse(isNodeAIOps("NODE_NOT_READY", vm), vm);
            assertFalse(isNodeAIOps("NODE_HIGH_MEMORY", vm), vm);
        }
        for (String n : NOEUDS) {
            assertTrue(isNodeAIOps("NODE_NOT_READY", n), n);
        }
        // Une app applicative avec un type NODE_* ne doit plus donner RESTART_VM.
        assertFalse(isNodeAIOps("NODE_NOT_READY", "backend"));
    }

    private static boolean isNodeAIOps(String incidentType, String app) {
        return ((incidentType != null && incidentType.toUpperCase(Locale.ROOT).contains("NODE"))
                || (app != null && app.toLowerCase(Locale.ROOT).startsWith("k8s-worker")))
                && RemediationPolicyService.isKubernetesNode(app);
    }

    // ------------------------------------------- ETAPE 8 : le cas le plus dangereux

    @Test
    void etape8_NODE_HIGH_MEMORY_sur_iacamunda_ne_peut_produire_aucune_action_hyperviseur() {
        // Scenario que l audit initial avait montre comme satisfaisant les conditions de
        // forcage : ns sentinelle + type NODE_* + VM hors cluster.
        // (1) forcage : neutralise par la garde d identite
        RemediationPlan p = decider("k8s-nodes", "iacamunda", "NODE_HIGH_MEMORY", IA_NEUTRE);
        assertNotEquals(ActionType.RESTART_VM, p.actionType(), "forcage");
        assertFalse(p.autoExecutable(), "auto-execution");

        // (2) orchestration : refusee
        assertFalse(isNodeAIOps("NODE_HIGH_MEMORY", "iacamunda"), "orchestration");

        // (3) meme si l IA reclamait explicitement une action hyperviseur, l EXECUTION est
        //     refusee — c est la couche qui porte reellement la garantie.
        assertEquals(ActionType.RESTART_VM,
                decider("k8s-nodes", "iacamunda", "NODE_HIGH_MEMORY", IA_VM).actionType(),
                "ecart de decision, documente");
        RemediationExecutionResult r = executer("iacamunda");
        assertFalse(r.success(), "executeur");
        assertTrue(r.message().contains(MSG_REFUS), r.message());
    }

    // ------------------------------------------- ETAPE 9 : le cas valide

    @Test
    void etape9_cas_valide_est_techniquement_autorisable_mais_non_execute() {
        RemediationPlan p = decider("k8s-nodes", "k8s-worker1", "NODE_NOT_READY", IA_NEUTRE);
        assertEquals(ActionType.RESTART_VM, p.actionType());
        assertEquals("k8s-worker1", p.targetName());
        assertEquals("k8s-nodes", p.targetNamespace());

        RemediationExecutionResult r = executer("k8s-worker1");
        assertTrue(r.message().contains(MSG_GARDE_FRANCHIE),
                "la garde doit etre franchie sans execution : " + r.message());
        assertFalse(r.success(), "aucune execution reelle ne doit aboutir dans ce test");
    }

    // ------------------------------------------- ETAPE 14 : chaine synthetique

    @Test
    void etape14_chaine_synthetique_refus_puis_autorisation() {
        // (a) NODE_NOT_READY / iacamunda -> REFUSED, quelle que soit l action recommandee
        for (String actionIA : List.of(IA_NEUTRE, IA_VM)) {
            RemediationPlan refuse = decider("k8s-nodes", "iacamunda", "NODE_NOT_READY", actionIA);
            RemediationExecutionResult r = executer(refuse.targetName());
            assertFalse(r.success(), actionIA);
            assertTrue(r.message().contains(MSG_REFUS), actionIA + " -> " + r.message());
        }
        // Avec une action IA neutre, le refus intervient deja a la decision.
        assertFalse(decider("k8s-nodes", "iacamunda", "NODE_NOT_READY", IA_NEUTRE).autoExecutable());

        // (b) NODE_NOT_READY / k8s-worker1 -> AUTHORIZED, sans execution reelle
        RemediationPlan ok = decider("k8s-nodes", "k8s-worker1", "NODE_NOT_READY", IA_NEUTRE);
        assertEquals(ActionType.RESTART_VM, ok.actionType());
        RemediationExecutionResult r = executer(ok.targetName());
        assertFalse(r.message().contains(MSG_REFUS));
        assertTrue(r.message().contains(MSG_GARDE_FRANCHIE));
    }

    // ------------------------------------------- invariants du perimetre

    @Test
    void invariant_le_dernier_garde_avant_hyperviseur_existe_et_precede_l_appel() throws IOException {
        String src = source("service/remediation/KubernetesRemediationExecutor.java");
        int debut = src.indexOf("private RemediationExecutionResult restartVm(");
        assertTrue(debut > 0, "restartVm introuvable");
        int fin = src.indexOf("private RemediationExecutionResult restartPod(", debut);
        assertTrue(fin > debut, "fin de restartVm introuvable");
        String corps = src.substring(debut, fin);
        // VMDOWN-POLICY-20261105 : la garde d identite est la politique UNIQUE (table fermee, noms exacts).
        int garde = corps.indexOf("VmPowerPolicy.staticRefusal(nodeName)");
        int config = corps.indexOf("watchdogClient.isConfigured()");
        int appelTransport = corps.indexOf("watchdogClient.restart(");
        assertTrue(garde > 0, "la garde de politique est ABSENTE de restartVm");
        assertTrue(config > garde, "la garde de politique doit preceder la verification de configuration");
        assertTrue(appelTransport > config, "la configuration doit etre verifiee avant tout appel au watchdog");
        // Le transport HTTP n est plus dans l executeur : il vit dans WatchdogClient (un seul POST, jamais rejoue).
        assertFalse(corps.contains("HttpClient"), "restartVm ne doit plus construire de client HTTP");
        assertFalse(corps.contains("getMasterWatchdogUrl()"), "l URL du watchdog est lue par le client, pas ici");
        assertEquals(1, corps.split("watchdogClient\\.restart\\(", -1).length - 1,
                "UN SEUL appel POST /restart dans restartVm (jamais de boucle de rejeu)");
        assertFalse(src.contains("restartVmViaWatchdog("), "l ancien transport ne doit plus exister");
        assertFalse(src.contains("RESTARTABLE_WORKER_NODES"), "plus d allowlist worker en dur : la table est VmPowerPolicy");
    }

    @Test
    void invariant_triggerHostWatchdogRestart_reste_du_code_mort() throws IOException {
        String src = source("service/CamundaJobWorkerLifecycle.java");
        long appels = src.lines()
                .filter(l -> l.contains("triggerHostWatchdogRestart"))
                .filter(l -> !l.contains("private String triggerHostWatchdogRestart"))
                .filter(l -> !l.trim().startsWith("//") && !l.trim().startsWith("*"))
                .count();
        assertEquals(0L, appels, "triggerHostWatchdogRestart a retrouve un appelant");
    }
}
