package com.orange.pfe.backend.service.remediation;

import com.orange.pfe.backend.config.RemediationProperties;
import com.orange.pfe.backend.model.action.ActionType;
import com.orange.pfe.backend.model.action.AutomationMode;
import com.orange.pfe.backend.model.action.RemediationTargetKind;
import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * LOT HELIOS-DETERMINISTIC-VM-RECOVERY-01 — Phase C, mis à jour par VMDOWN-POLICY-20261105.
 *
 * Prouve que le BRAS d'exécution du rallumage (RESTART_VM -> watchdog hôte) :
 *   - refuse toute cible hors de la table fermée de la politique VM_DOWN (jamais de démarrage arbitraire) ;
 *   - n'autorise JAMAIS le control-plane (.153) ni la détection seule (.160) ;
 *   - ne déclare JAMAIS un succès quand le watchdog n'est pas joignable / configuré.
 *
 * Ces chemins n'appellent NI le service IA, NI /predict : la décision de rallumer est déterministe.
 * Aucun appel réel n'atteint le vrai watchdog hôte (192.168.148.1:9099) : les cibles de test sont
 * soit refusées avant tout réseau, soit une adresse morte 127.0.0.1:1 (connexion refusée).
 */
class KubernetesRemediationExecutorRecoveryTest {

    private RemediationPlan restartVmPlan(String targetName) {
        return new RemediationPlan(
                ActionType.RESTART_VM, AutomationMode.AUTO,
                false, true, false, false, false,
                "k8s-nodes", RemediationTargetKind.UNKNOWN, targetName, "test-recovery", "NODE_NOT_READY", 501L);
    }

    private KubernetesRemediationExecutor executor(String watchdogUrl) {
        RemediationProperties props = new RemediationProperties();
        props.setWatchdogBaseUrl(watchdogUrl);          // ancien agent :9999 (n'est plus jamais utilisé)
        props.setMasterWatchdogUrl(watchdogUrl);        // watchdog hôte :9099 (seul transport)
        props.setMasterWatchdogToken(watchdogUrl == null || watchdogUrl.isEmpty() ? "" : "tok");
        return new KubernetesRemediationExecutor(props);
    }

    /** T5 — Cible non reconnue : aucune VM n'est démarrée arbitrairement (refus avant tout appel réseau). */
    @Test
    void restartVm_refuseUneCibleNonNoeud() {
        RemediationExecutionResult r = executor("http://127.0.0.1:1").execute(restartVmPlan("pfe-frontend"));
        assertFalse(r.success(), "une cible non-nœud ne doit JAMAIS démarrer de VM");
        String m = r.message() == null ? "" : r.message().toLowerCase();
        assertTrue(m.contains("noeud") || m.contains("nœud") || m.contains("kubernetes"),
                "le refus doit indiquer que la cible n'est pas un nœud Kubernetes : " + r.message());
    }

    /** T4 — Watchdog non configuré : échec honnête, jamais 'succès'. */
    @Test
    void restartVm_watchdogNonConfigure_nestPasUnSucces() {
        RemediationExecutionResult r = executor("").execute(restartVmPlan("k8s-worker1"));
        assertFalse(r.success(), "watchdog non configuré => jamais 'succès'");
    }

    /** T4bis — Watchdog injoignable (adresse morte) : échec, jamais 'succès'. */
    @Test
    void restartVm_watchdogInjoignable_nestPasUnSucces() {
        RemediationExecutionResult r = executor("http://127.0.0.1:1").execute(restartVmPlan("k8s-worker1"));
        assertFalse(r.success(), "watchdog injoignable => échec, jamais 'succès'");
    }

    /** Déterminisme de la reconnaissance de nœud (aucune IA) : base du refus T5. */
    @Test
    void isKubernetesNode_estDeterministe() {
        assertTrue(RemediationPolicyService.isKubernetesNode("k8s-worker1"));
        assertTrue(RemediationPolicyService.isKubernetesNode("k8s-master"));
        assertFalse(RemediationPolicyService.isKubernetesNode("pfe-frontend"));
        assertFalse(RemediationPolicyService.isKubernetesNode(null));
    }

    /**
     * VM-RECOVERY-FIX puis consigne du 2026-11-17 — le control-plane (master, .153) était refusé AVANT tout réseau ; il est
     * désormais une cible AUTO : il franchit la garde et TENTE l'appel au watchdog (qui échoue ici seulement sur l'URL morte).
     */
    @Test
    void restartVm_master_est_une_cible_auto_il_tente_l_appel_watchdog() {
        RemediationExecutionResult r = executor("http://127.0.0.1:1").execute(restartVmPlan("k8s-master"));
        assertFalse(r.success(), "URL morte => échec");
        String m = r.message() == null ? "" : r.message().toLowerCase();
        assertTrue(m.contains("watchdog") && m.contains("aucune réponse"),
                "le master (AUTO) doit franchir la garde et tenter l'appel watchdog : " + r.message());
    }

    /**
     * .160 : DÉTECTION SEULE (consigne finale du 2026-11-06 : VM de supervision, jamais rallumée automatiquement). La
     * garde statique de la politique la refuse AVANT tout réseau, quelle que soit la façon dont le plan a été construit.
     */
    @Test
    void restartVm_refuse_160_detection_seule_avant_tout_reseau() {
        RemediationExecutionResult r = executor("http://127.0.0.1:1").execute(restartVmPlan("k8s-monitoring"));
        assertFalse(r.success());
        assertTrue(r.message().toLowerCase().contains("détection seule"), r.message());
        assertTrue(r.message().toLowerCase().contains("administrateur"), r.message());
        assertFalse(r.message().toLowerCase().contains("aucune réponse"), "refus AVANT tout appel : " + r.message());
    }

    /** VMDOWN-POLICY-20261105 + consigne du 2026-11-17 — les 4 cibles AUTOMATIQUES (.153 comprise) routent vers le MÊME
     *  watchdog avec jeton ET incident ; .160 n'atteint JAMAIS le serveur. */
    @Test
    void restartVm_watchdog_http200_quatreVmAuto_avecJetonEtIncident_estUnSucces() throws Exception {
        final java.util.List<String> paths = new java.util.concurrent.CopyOnWriteArrayList<>();
        final java.util.List<String> tokens = new java.util.concurrent.CopyOnWriteArrayList<>();
        final java.util.List<String> incidents = new java.util.concurrent.CopyOnWriteArrayList<>();
        com.sun.net.httpserver.HttpServer srv = com.sun.net.httpserver.HttpServer.create(
                new java.net.InetSocketAddress("127.0.0.1", 0), 0);
        srv.createContext("/", ex -> {
            paths.add(ex.getRequestURI().getPath());
            tokens.add(ex.getRequestHeaders().getFirst("X-Helios-Token"));
            incidents.add(ex.getRequestHeaders().getFirst("X-Helios-Incident"));
            byte[] body = "{\"node\":\"x\",\"started\":true,\"status\":\"STARTED\"}".getBytes(java.nio.charset.StandardCharsets.UTF_8);
            ex.sendResponseHeaders(200, body.length);
            ex.getResponseBody().write(body);
            ex.close();
        });
        srv.start();
        try {
            String url = "http://127.0.0.1:" + srv.getAddress().getPort();
            RemediationProperties props = new RemediationProperties();
            props.setMasterWatchdogUrl(url);
            props.setMasterWatchdogToken("test-token-9099");
            KubernetesRemediationExecutor exec = new KubernetesRemediationExecutor(props);
            for (String node : java.util.List.of("k8s-worker1", "k8s-worker2", "k8s-lb2", "k8s-master")) {
                RemediationExecutionResult r = exec.execute(restartVmPlan(node));
                assertTrue(r.success(), node + " -> HTTP 200 STARTED doit être un succès : " + r.message());
            }
            for (String refuse : java.util.List.of("k8s-monitoring")) {
                assertFalse(exec.execute(restartVmPlan(refuse)).success(), refuse);
            }
            assertTrue(paths.equals(java.util.List.of("/restart/k8s-worker1", "/restart/k8s-worker2", "/restart/k8s-lb2",
                            "/restart/k8s-master")),
                    "seules les 4 cibles AUTO doivent atteindre /restart/<node> : " + paths);
            assertTrue(tokens.stream().allMatch("test-token-9099"::equals), "X-Helios-Token : " + tokens);
            assertTrue(incidents.stream().allMatch("501"::equals), "X-Helios-Incident : " + incidents);
        } finally {
            srv.stop(0);
        }
    }

    /** Sur le watchdog, tout code != contrat (ici 500) est un ÉCHEC honnête (jamais de faux succès). */
    @Test
    void restartVm_watchdog_codeInattendu_estUnEchec() throws Exception {
        com.sun.net.httpserver.HttpServer srv = com.sun.net.httpserver.HttpServer.create(
                new java.net.InetSocketAddress("127.0.0.1", 0), 0);
        srv.createContext("/", ex -> {
            byte[] body = "boom\n".getBytes(java.nio.charset.StandardCharsets.UTF_8);
            ex.sendResponseHeaders(500, body.length);
            ex.getResponseBody().write(body);
            ex.close();
        });
        srv.start();
        try {
            String url = "http://127.0.0.1:" + srv.getAddress().getPort();
            RemediationProperties props = new RemediationProperties();
            props.setMasterWatchdogUrl(url);
            props.setMasterWatchdogToken("test-token-9099");
            KubernetesRemediationExecutor exec = new KubernetesRemediationExecutor(props);
            RemediationExecutionResult r = exec.execute(restartVmPlan("k8s-worker1"));
            assertFalse(r.success(), "un code 500 doit être traité comme un échec");
        } finally {
            srv.stop(0);
        }
    }

    /**
     * Table fermée de la politique. Master, k8s-worker999, k8s-worker1-rogue, alias, casse, espaces et toute cible
     * non autorisée sont refusés AVANT tout appel HTTP au watchdog (preuve : le message est un refus
     * d'identité, jamais une erreur réseau). L'URL watchdog pointe une adresse morte : si un appel partait, le
     * message dirait « aucune réponse exploitable ».
     */
    @Test
    void restartVm_tableFermee_refuse_master_worker999_et_cibles_non_autorisees() {
        KubernetesRemediationExecutor exec = executor("http://127.0.0.1:1");
        for (String cible : java.util.List.of("k8s-worker999", "k8s-worker",
                                              "k8s-worker1-rogue", "pfe-frontend", "lb2", "K8S-WORKER1",
                                              " k8s-worker1", "k8s-monitoring")) {
            RemediationExecutionResult r = exec.execute(restartVmPlan(cible));
            assertFalse(r.success(), cible + " doit être refusé (jamais de power-on)");
            String m = r.message() == null ? "" : r.message().toLowerCase();
            assertFalse(m.contains("call failed") || m.contains("connection") || m.contains("timed out")
                            || m.contains("aucune réponse"),
                    cible + " -> refus attendu AVANT tout appel watchdog, obtenu : " + r.message());
            assertTrue(m.contains("worker") || m.contains("noeud") || m.contains("nœud") || m.contains("kubernetes")
                            || m.contains("détection seule"),
                    cible + " -> message de refus d'identité attendu : " + r.message());
        }
        // Contre-épreuve : une cible AUTOMATIQUE (k8s-worker2, k8s-lb2, k8s-master) franchit la garde et TENTE l'appel
        // (elle échoue seulement sur l'URL morte) -> preuve que la table n'est pas sur-restrictive.
        for (String ok : java.util.List.of("k8s-worker2", "k8s-lb2", "k8s-master")) {
            RemediationExecutionResult r = exec.execute(restartVmPlan(ok));
            assertFalse(r.success(), "URL morte => échec");
            String m = r.message() == null ? "" : r.message().toLowerCase();
            assertTrue(m.contains("watchdog") && m.contains("aucune réponse"),
                    ok + " (autorisé) doit franchir la garde et tenter l'appel watchdog : " + r.message());
        }
    }
}
