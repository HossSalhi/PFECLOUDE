package com.orange.pfe.backend.service.remediation;

import com.orange.pfe.backend.config.RemediationProperties;
import com.orange.pfe.backend.model.action.ActionType;
import com.orange.pfe.backend.model.action.AutomationMode;
import com.orange.pfe.backend.model.action.RemediationTargetKind;
import com.sun.net.httpserver.HttpServer;
import org.junit.jupiter.api.Test;

import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.util.List;
import java.util.concurrent.CopyOnWriteArrayList;
import java.util.concurrent.atomic.AtomicInteger;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * VMDOWN-POLICY-20261105 — RÉÉCRITURE de l'ancien test MASTER-VM-DOWN-20261029 (le nom de la classe est conservé
 * pour la traçabilité de l'historique).
 *
 * <p>CONSIGNE DU 2026-11-17 (inverse l'ancienne « .153 jamais rallumé ») : le control-plane {@code k8s-master} (.153)
 * est une cible AUTO comme les workers. Les cibles automatiques (k8s-worker1, k8s-worker2, k8s-lb2, k8s-master) passent
 * par le MÊME watchdog hôte :9099 (X-Helios-Token) avec l'incident en clé d'idempotence (X-Helios-Incident). Le client
 * ne rejoue jamais un POST /restart. Le mode DENY_RESTART est CONSERVÉ et honoré (refusé AVANT tout transport) : il est
 * rejoué ici sur .153 par la couture de test {@code VmPowerPolicy.overrideEntryForTest}.
 *
 * <p>AUCUN appel à un vrai watchdog / une vraie VM : faux serveurs HTTP locaux (127.0.0.1:0) ou adresse morte.
 */
class KubernetesRemediationExecutorMasterRecoveryTest {

    private static final long INCIDENT = 4242L;

    private RemediationPlan vmPlan(String target) {
        return new RemediationPlan(ActionType.RESTART_VM, AutomationMode.AUTO,
                false, true, false, false, false,
                "k8s-nodes", RemediationTargetKind.UNKNOWN, target, "test-vmdown", "NODE_NOT_READY", INCIDENT);
    }

    private RemediationPlan vmPlanSansIncident(String target) {
        return new RemediationPlan(ActionType.RESTART_VM, AutomationMode.AUTO,
                false, true, false, false, false,
                "k8s-nodes", RemediationTargetKind.UNKNOWN, target, "test-vmdown");
    }

    /** Faux watchdog : répond `code` + `body` et capture chemin, jeton, incident et nombre d'appels. */
    private static final class Fake {
        final HttpServer server;
        final List<String> paths = new CopyOnWriteArrayList<>();
        final List<String> tokens = new CopyOnWriteArrayList<>();
        final List<String> incidents = new CopyOnWriteArrayList<>();
        final AtomicInteger hits = new AtomicInteger();

        Fake(int code, String body) throws Exception {
            server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
            server.createContext("/", ex -> {
                hits.incrementAndGet();
                paths.add(ex.getRequestURI().getPath());
                String t = ex.getRequestHeaders().getFirst("X-Helios-Token");
                tokens.add(t == null ? "" : t);
                String i = ex.getRequestHeaders().getFirst("X-Helios-Incident");
                incidents.add(i == null ? "" : i);
                byte[] b = body.getBytes(StandardCharsets.UTF_8);
                ex.sendResponseHeaders(code, b.length);
                ex.getResponseBody().write(b);
                ex.close();
            });
            server.start();
        }

        String url() {
            return "http://127.0.0.1:" + server.getAddress().getPort();
        }

        void stop() {
            server.stop(0);
        }
    }

    private KubernetesRemediationExecutor exec(String url, String token) {
        RemediationProperties p = new RemediationProperties();
        p.setMasterWatchdogUrl(url);
        p.setMasterWatchdogToken(token);
        return new KubernetesRemediationExecutor(p);
    }

    private static final String STARTED = "{\"vm\":\"x\",\"node\":\"x\",\"started\":true,\"status\":\"STARTED\"}";

    /** Les 4 cibles AUTO (.153 comprise) routent vers le MÊME :9099 avec jeton ET incident ; l'agent :9999 n'est jamais appelé. */
    @Test
    void les_quatre_cibles_auto_passent_par_le_meme_watchdog_avec_jeton_et_incident() throws Exception {
        Fake watchdog = new Fake(200, STARTED);
        Fake legacy9999 = new Fake(202, "accepted\n");
        try {
            RemediationProperties p = new RemediationProperties();
            p.setWatchdogBaseUrl(legacy9999.url());   // ancien agent :9999 VOLONTAIREMENT joignable...
            p.setMasterWatchdogUrl(watchdog.url());
            p.setMasterWatchdogToken("tok");
            KubernetesRemediationExecutor exec = new KubernetesRemediationExecutor(p);
            for (String vm : List.of("k8s-worker1", "k8s-worker2", "k8s-lb2", "k8s-master")) {
                RemediationExecutionResult r = exec.execute(vmPlan(vm));
                assertTrue(r.success(), vm + " -> " + r.message());
                assertTrue(r.message().toLowerCase().contains("vérifier en aval"),
                        "doit dire qu'un 200 n'est pas une preuve de récupération : " + r.message());
            }
            assertEquals(4, watchdog.hits.get());
            assertEquals(List.of("/restart/k8s-worker1", "/restart/k8s-worker2", "/restart/k8s-lb2", "/restart/k8s-master"),
                    watchdog.paths);
            assertTrue(watchdog.tokens.stream().allMatch("tok"::equals), "X-Helios-Token : " + watchdog.tokens);
            assertTrue(watchdog.incidents.stream().allMatch("4242"::equals), "X-Helios-Incident : " + watchdog.incidents);
            assertEquals(0, legacy9999.hits.get(), "...mais l'agent :9999 ne doit PLUS JAMAIS être appelé");
        } finally {
            watchdog.stop();
            legacy9999.stop();
        }
    }

    /** Consigne du 2026-11-17 : le master (.153) est une cible AUTO : UN SEUL POST /restart/k8s-master, avec jeton ET incident. */
    @Test
    void master_est_une_cible_auto_un_seul_post_avec_jeton_et_incident() throws Exception {
        Fake watchdog = new Fake(200, STARTED);
        try {
            RemediationExecutionResult r = exec(watchdog.url(), "tok").execute(vmPlan("k8s-master"));
            assertTrue(r.success(), r.message());
            assertEquals(1, watchdog.hits.get(), "UN SEUL appel (jamais rejoué)");
            assertEquals(List.of("/restart/k8s-master"), watchdog.paths);
            assertEquals(List.of("tok"), watchdog.tokens);
            assertEquals(List.of("4242"), watchdog.incidents);
        } finally {
            watchdog.stop();
        }
    }

    // Le refus du mode DENY_RESTART (conservé, forcé sur .153) est testé dans le paquet de la politique :
    // com.orange.pfe.backend.service.vmpower.MasterDenyRestartExecutorTest (la couture de test est privée au paquet vmpower).

    /**
     * .160 : DÉTECTION SEULE (consigne finale du 2026-11-06) — la politique la refuse AVANT tout réseau : aucun appel
     * au watchdog, donc aucun démarrage, même si le serveur répondrait « STARTED ».
     */
    @Test
    void monitoring_detection_seule_refusee_avant_tout_reseau() throws Exception {
        Fake watchdog = new Fake(200, STARTED);
        try {
            RemediationExecutionResult r = exec(watchdog.url(), "tok").execute(vmPlan("k8s-monitoring"));
            assertFalse(r.success());
            assertEquals(0, watchdog.hits.get());
            assertTrue(r.message().toLowerCase().contains("détection seule"), r.message());
        } finally {
            watchdog.stop();
        }
    }

    /** Cibles inconnues, alias, IP, casse : refus avant tout réseau. */
    @Test
    void cibles_inconnues_alias_et_variantes_refusees_avant_reseau() throws Exception {
        Fake watchdog = new Fake(200, STARTED);
        try {
            KubernetesRemediationExecutor exec = exec(watchdog.url(), "tok");
            for (String cible : List.of("pfe-frontend", "lb2", "monitoring", "k8s-aiCamunda", "K8S-WORKER1",
                    " k8s-worker1", "k8s-worker1 ", "192.168.148.158", "k8s-worker3", "")) {
                assertFalse(exec.execute(vmPlan(cible)).success(), "[" + cible + "]");
            }
            assertEquals(0, watchdog.hits.get(), "aucune de ces cibles ne doit atteindre le watchdog");
        } finally {
            watchdog.stop();
        }
    }

    /** Sans incident, pas de clé d'idempotence : refus avant tout réseau. */
    @Test
    void plan_sans_incident_refuse_avant_reseau() throws Exception {
        Fake watchdog = new Fake(200, STARTED);
        try {
            RemediationExecutionResult r = exec(watchdog.url(), "tok").execute(vmPlanSansIncident("k8s-worker1"));
            assertFalse(r.success());
            assertEquals(0, watchdog.hits.get());
            assertTrue(r.message().contains("X-Helios-Incident"), r.message());
        } finally {
            watchdog.stop();
        }
    }

    /** URL ou jeton absent => refus FERMÉ, aucun appel réseau, aucun repli sur l'agent :9999. */
    @Test
    void sans_configuration_fail_closed_et_jamais_de_repli_9999() throws Exception {
        Fake legacy9999 = new Fake(202, "accepted\n");
        try {
            for (boolean urlSansJeton : new boolean[]{false, true}) {
                RemediationProperties p = new RemediationProperties();
                p.setWatchdogBaseUrl(legacy9999.url());
                if (urlSansJeton) {
                    p.setMasterWatchdogUrl("http://127.0.0.1:1");
                }
                RemediationExecutionResult r = new KubernetesRemediationExecutor(p).execute(vmPlan("k8s-worker1"));
                assertFalse(r.success());
                String m = r.message().toLowerCase();
                assertTrue(m.contains("non configuré") && m.contains("master-watchdog-url"), r.message());
                assertFalse(m.contains("call failed") || m.contains("connection") || m.contains("timed out"), r.message());
            }
            assertEquals(0, legacy9999.hits.get());
        } finally {
            legacy9999.stop();
        }
    }

    /** 200 noop ALREADY_EXECUTED = incident déjà traité : succès, NON relancé. */
    @Test
    void noop_already_executed_est_un_succes_non_rejoue() throws Exception {
        Fake watchdog = new Fake(200, "{\"vm\":\"k8s-lb2\",\"noop\":true,\"status\":\"ALREADY_EXECUTED\","
                + "\"reason\":\"ALREADY_EXECUTED\",\"previous_status\":\"STARTED\",\"replayed\":true}");
        try {
            RemediationExecutionResult r = exec(watchdog.url(), "t").execute(vmPlan("k8s-lb2"));
            assertTrue(r.success());
            assertTrue(r.message().contains("déjà traité") && r.message().contains("non relancé"), r.message());
        } finally {
            watchdog.stop();
        }
    }

    /** 200 noop ALREADY_RUNNING : succès, mais le message DIT qu'AUCUN démarrage n'a été effectué. */
    @Test
    void noop_already_running_n_affirme_aucun_demarrage() throws Exception {
        Fake watchdog = new Fake(200, "{\"vm\":\"k8s-worker1\",\"noop\":true,\"status\":\"ALREADY_RUNNING\","
                + "\"reason\":\"ALREADY_RUNNING\"}");
        try {
            RemediationExecutionResult r = exec(watchdog.url(), "t").execute(vmPlan("k8s-worker1"));
            assertTrue(r.success());
            assertTrue(r.message().contains("AUCUN démarrage effectué"), r.message());
            assertFalse(r.message().contains("démarrée via"), r.message());
        } finally {
            watchdog.stop();
        }
    }

    /** 202 STARTING : démarrage ACCEPTÉ mais non terminé. */
    @Test
    void reponse_202_starting_est_acceptee_non_finale() throws Exception {
        Fake watchdog = new Fake(202, "{\"vm\":\"k8s-worker2\",\"status\":\"STARTING\",\"request_id\":\"r\"}");
        try {
            RemediationExecutionResult r = exec(watchdog.url(), "t").execute(vmPlan("k8s-worker2"));
            assertTrue(r.success());
            assertTrue(r.message().contains("202") && r.message().contains("non terminé"), r.message());
        } finally {
            watchdog.stop();
        }
    }

    /** Refus/échecs HTTP : toujours un ÉCHEC explicite, jamais un faux succès. */
    @Test
    void refus_et_echecs_http_sont_des_echecs_explicites() throws Exception {
        Object[][] cas = {
                {401, "{\"status\":\"REJECTED\",\"error\":\"jeton absent ou invalide\"}", "401"},
                {403, "{\"status\":\"CALLER_NOT_ALLOWED\"}", "CALLER_NOT_ALLOWED"},
                {404, "{\"status\":\"UNKNOWN_VM\"}", "404"},
                {423, "{\"status\":\"MAINTENANCE\"}", "MAINTENANCE"},
                {500, "{\"status\":\"FAILED\",\"detail\":\"VMRUN_RC_1\"}", "FAILED"},
                {503, "{\"status\":\"COOLDOWN_ACTIVE\",\"retry_after_s\":120}", "retry_after_s=120"},
                {503, "{\"status\":\"STATE_UNKNOWN\",\"retry_after_s\":5}", "STATE_UNKNOWN"},
                {200, "{}", "hors contrat"},
        };
        for (Object[] c : cas) {
            Fake watchdog = new Fake((Integer) c[0], (String) c[1]);
            try {
                RemediationExecutionResult r = exec(watchdog.url(), "t").execute(vmPlan("k8s-worker1"));
                assertFalse(r.success(), c[0] + " " + c[1]);
                assertTrue(r.message().contains((String) c[2]), c[0] + " -> " + r.message());
                assertEquals(1, watchdog.hits.get(), "UN SEUL POST, jamais rejoué : " + c[0]);
            } finally {
                watchdog.stop();
            }
        }
    }

    /** Watchdog injoignable : échec, jamais faux succès ; le message dit que le démarrage a pu avoir lieu. */
    @Test
    void watchdog_injoignable_echec_sans_rejeu() {
        RemediationExecutionResult r = exec("http://127.0.0.1:1", "t").execute(vmPlan("k8s-worker1"));
        assertFalse(r.success());
        assertTrue(r.message().contains("a pu avoir lieu") && r.message().contains("AUCUN rejeu"), r.message());
        assertFalse(r.message().contains("\"t\""), "le jeton ne doit jamais apparaître");
    }

    /** Deux exécutions = deux POST : la dé-duplication est portée par le watchdog (clé d'incident), pas ici. */
    @Test
    void deux_executions_font_deux_post_la_deduplication_est_au_watchdog() throws Exception {
        Fake watchdog = new Fake(200, STARTED);
        try {
            KubernetesRemediationExecutor exec = exec(watchdog.url(), "t");
            exec.execute(vmPlan("k8s-worker1"));
            exec.execute(vmPlan("k8s-worker1"));
            assertEquals(2, watchdog.hits.get());
            assertEquals(List.of("4242", "4242"), watchdog.incidents, "même clé d'incident envoyée à chaque appel");
        } finally {
            watchdog.stop();
        }
    }
}
