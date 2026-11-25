package com.orange.pfe.backend.service.vmpower;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ArrayNode;
import com.fasterxml.jackson.databind.node.IntNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import com.fasterxml.jackson.databind.node.TextNode;
import com.orange.pfe.backend.config.RemediationProperties;
import com.orange.pfe.backend.config.VmPowerProperties;
import com.orange.pfe.backend.service.vmpower.VmPowerPolicy.PowerState;
import com.orange.pfe.backend.service.vmpower.WatchdogClient.Kind;
import com.orange.pfe.backend.service.vmpower.WatchdogClient.RestartOutcome;
import com.sun.net.httpserver.HttpServer;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

import java.io.IOException;
import java.net.InetAddress;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.time.Instant;
import java.util.ArrayList;
import java.util.Iterator;
import java.util.List;
import java.util.Map;
import java.util.concurrent.CopyOnWriteArrayList;
import java.util.concurrent.atomic.AtomicReference;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * VMDOWN-POLICY-20261105 — {@link WatchdogClient} contre un faux serveur qui rejoue le CONTRAT « golden »
 * ({@code infra/helios-sot/contract/*.json}, produit par le vrai watchdog et vérifié par
 * {@code test_watchdog_contract.py}). Aucune VM, aucun réseau externe : serveur JDK sur 127.0.0.1.
 */
class WatchdogClientTest {

    private static final ObjectMapper MAPPER = new ObjectMapper();
    private static final String TOKEN = "jeton-de-test-java-0123456789abcdef";

    /** Une requête reçue par le faux watchdog. */
    record Seen(String method, String path, Map<String, String> headers) {
    }

    private HttpServer server;
    private final List<Seen> seen = new CopyOnWriteArrayList<>();
    private final AtomicReference<Reply> reply = new AtomicReference<>(new Reply(200, "{}", 0));
    private RemediationProperties remediation;
    private VmPowerProperties vmPower;
    private WatchdogClient client;

    record Reply(int status, String body, long delayMs) {
    }

    @BeforeEach
    void setUp() throws IOException {
        server = HttpServer.create(new InetSocketAddress(InetAddress.getLoopbackAddress(), 0), 0);
        server.createContext("/", exchange -> {
            Map<String, String> headers = new java.util.TreeMap<>(String.CASE_INSENSITIVE_ORDER);
            exchange.getRequestHeaders().forEach((k, v) -> headers.put(k, v.get(0)));
            seen.add(new Seen(exchange.getRequestMethod(), exchange.getRequestURI().getRawPath(), headers));
            Reply r = reply.get();
            try {
                if (r.delayMs() > 0) {
                    Thread.sleep(r.delayMs());
                }
            } catch (InterruptedException ie) {
                Thread.currentThread().interrupt();
            }
            byte[] out = r.body().getBytes(StandardCharsets.UTF_8);
            exchange.getResponseHeaders().add("Content-Type", "application/json");
            try {
                exchange.sendResponseHeaders(r.status(), out.length == 0 ? -1 : out.length);
                if (out.length > 0) {
                    exchange.getResponseBody().write(out);
                }
            } catch (IOException ignored) {
                // le client a abandonné (délai) : normal dans le test de timeout
            } finally {
                exchange.close();
            }
        });
        server.start();
        remediation = new RemediationProperties();
        remediation.setMasterWatchdogUrl("http://127.0.0.1:" + server.getAddress().getPort());
        remediation.setMasterWatchdogToken(TOKEN);
        vmPower = new VmPowerProperties();
        vmPower.setWatchdogReadTimeoutMs(1500);
        vmPower.setWatchdogRestartTimeoutMs(1500);
        client = new WatchdogClient(remediation, vmPower);
    }

    @AfterEach
    void tearDown() {
        server.stop(0);
    }

    // ───────────────────────────── Golden ─────────────────────────────

    static Path goldenDir() {
        Path dir = Paths.get("").toAbsolutePath();
        for (int i = 0; i < 8 && dir != null; i++, dir = dir.getParent()) {
            Path c = dir.resolve("infra").resolve("helios-sot").resolve("contract");
            if (Files.isDirectory(c)) {
                return c;
            }
        }
        throw new AssertionError("infra/helios-sot/contract introuvable depuis " + Paths.get("").toAbsolutePath());
    }

    static JsonNode golden(String name) throws IOException {
        return MAPPER.readTree(Files.readString(goldenDir().resolve(name + ".json"), StandardCharsets.UTF_8));
    }

    /** Remplace les marqueurs de normalisation du golden par des valeurs plausibles. */
    static JsonNode concrete(JsonNode node, String key) {
        if (node.isObject()) {
            ObjectNode out = MAPPER.createObjectNode();
            Iterator<Map.Entry<String, JsonNode>> it = node.fields();
            while (it.hasNext()) {
                Map.Entry<String, JsonNode> e = it.next();
                out.set(e.getKey(), concrete(e.getValue(), e.getKey()));
            }
            return out;
        }
        if (node.isArray()) {
            ArrayNode out = MAPPER.createArrayNode();
            node.forEach(n -> out.add(concrete(n, key)));
            return out;
        }
        if (node.isTextual()) {
            switch (node.asText()) {
                case "<iso>": return TextNode.valueOf(Instant.now().toString());
                case "<hex12>": return TextNode.valueOf("0123456789ab");
                case "<id>": return TextNode.valueOf("req-golden");
                case "<int>": return IntNode.valueOf("retry_after_s".equals(key) ? 30 : 100);
                default: return node;
            }
        }
        return node;
    }

    private void serveGolden(String name) throws IOException {
        JsonNode g = golden(name);
        JsonNode response = g.get("response");
        reply.set(new Reply(response.get("status").asInt(),
                MAPPER.writeValueAsString(concrete(response.get("body"), null)), 0));
    }

    private RestartOutcome restartFromGolden(String name) throws IOException {
        serveGolden(name);
        JsonNode req = golden(name).get("request");
        String path = req.get("path").asText();
        String vm = path.substring("/restart/".length());
        long incident = req.get("headers").has("X-Helios-Incident")
                ? Long.parseLong(req.get("headers").get("X-Helios-Incident").asText()) : 1L;
        return client.restart(vm, incident, "req-test");
    }

    @Test
    void chaque_reponse_restart_du_contrat_est_classee_comme_prevu() throws IOException {
        Map<String, Kind> attendu = new java.util.LinkedHashMap<>();
        attendu.put("restart-started", Kind.STARTED);
        attendu.put("restart-started-lb2", Kind.STARTED);
        attendu.put("restart-already-running", Kind.ALREADY_RUNNING);
        attendu.put("restart-already-executed", Kind.ALREADY_EXECUTED);
        attendu.put("restart-already-starting", Kind.ALREADY_STARTING);
        attendu.put("restart-starting", Kind.ACCEPTED_STARTING);
        for (String refused : List.of("restart-detection-only", "restart-not-authorized", "restart-unknown-vm",
                "restart-incident-required", "restart-incident-conflict", "restart-bad-token",
                "restart-missing-token", "restart-caller-not-allowed", "restart-maintenance")) {
            attendu.put(refused, Kind.REFUSED);
        }
        for (String deferred : List.of("restart-auth-not-configured", "restart-state-unknown", "restart-ram-low",
                "restart-cooldown", "restart-backoff", "restart-lock-blocked")) {
            attendu.put(deferred, Kind.DEFERRED);
        }
        attendu.put("restart-failed", Kind.FAILED);
        attendu.put("restart-failed-replayed", Kind.FAILED);

        for (Map.Entry<String, Kind> e : attendu.entrySet()) {
            RestartOutcome o = restartFromGolden(e.getKey());
            assertEquals(e.getValue(), o.kind(), e.getKey() + " -> " + o.describe());
            int httpAttendu = golden(e.getKey()).get("response").get("status").asInt();
            assertEquals(httpAttendu, o.http(), e.getKey());
            assertEquals(golden(e.getKey()).get("response").get("body").get("status").asText(), o.status(), e.getKey());
        }
    }

    @Test
    void les_noms_invalides_et_incidents_invalides_sont_des_refus() throws IOException {
        for (String name : List.of("restart-invalid-name", "restart-incident-invalid")) {
            serveGolden(name);
            RestartOutcome o = client.restart("k8s-worker1", 1, "r");
            assertEquals(Kind.REFUSED, o.kind(), name);
            assertEquals(400, o.http());
        }
    }

    @Test
    void seuls_STARTED_ALREADY_et_ACCEPTED_sont_sans_echec_et_seuls_STARTED_et_ACCEPTED_ont_emis_un_start()
            throws IOException {
        for (Kind k : Kind.values()) {
            boolean sansEchec = List.of(Kind.STARTED, Kind.ALREADY_RUNNING, Kind.ALREADY_STARTING,
                    Kind.ALREADY_EXECUTED, Kind.ACCEPTED_STARTING).contains(k);
            RestartOutcome o = new RestartOutcome(k, 0, "x", null, null, null, "r", false, null);
            assertEquals(sansEchec, o.noFailure(), k.name());
            assertEquals(k == Kind.STARTED || k == Kind.ACCEPTED_STARTING, o.startIssued(), k.name());
        }
        assertFalse(restartFromGolden("restart-already-running").startIssued(),
                "ALREADY_RUNNING : aucun démarrage effectué par HELIOS");
        assertFalse(restartFromGolden("restart-already-executed").startIssued());
    }

    @Test
    void le_rejeu_d_un_echec_est_signale() throws IOException {
        RestartOutcome o = restartFromGolden("restart-failed-replayed");
        assertTrue(o.replayed());
        assertEquals(Kind.FAILED, o.kind());
        assertNotNull(o.detail());
        assertFalse(restartFromGolden("restart-failed").replayed());
    }

    @Test
    void les_503_portent_retry_after_s() throws IOException {
        for (String name : List.of("restart-cooldown", "restart-backoff", "restart-state-unknown",
                "restart-ram-low", "restart-lock-blocked")) {
            RestartOutcome o = restartFromGolden(name);
            assertEquals(Integer.valueOf(30), o.retryAfterS(), name);
        }
    }

    // ───────────────────────────── États (GET) ─────────────────────────────

    @Test
    void etat_d_une_vm_allumee_est_lu_avec_ses_preuves() throws IOException {
        serveGolden("state-running");
        WatchdogClient.Result<WatchdogClient.VmState> r = client.state("k8s-worker1");
        assertTrue(r.ok(), r.error());
        WatchdogClient.VmState s = r.value();
        assertEquals("k8s-worker1", s.vm());
        assertEquals("AUTO", s.mode());
        assertEquals(PowerState.RUNNING, s.powerState());
        assertEquals("OK", s.stateReason());
        assertTrue(s.evidence().isFresh());
        assertEquals(Boolean.TRUE, s.evidence().vmxMatched());
        assertEquals("vmrun -T ws list", s.evidence().source());
        assertNotNull(s.evidence().observedAt());
        assertFalse(s.watchdog().startAllowed());
        assertTrue(s.watchdog().refusalCodes().contains("STATE_RUNNING"));
        assertNull(s.watchdog().lastStart());
    }

    @Test
    void etat_d_une_vm_eteinte_autorise_le_demarrage() throws IOException {
        serveGolden("state-powered-off");
        WatchdogClient.VmState s = client.state("k8s-worker1").value();
        assertEquals(PowerState.POWERED_OFF, s.powerState());
        assertEquals(Boolean.FALSE, s.evidence().vmxMatched());
        assertTrue(s.watchdog().startAllowed());
        assertEquals(List.of(), s.watchdog().refusalCodes());
    }

    @Test
    void timeout_de_vmrun_est_UNKNOWN_et_jamais_une_erreur() throws IOException {
        serveGolden("state-unknown-timeout");
        WatchdogClient.Result<WatchdogClient.VmState> r = client.state("k8s-worker1");
        assertTrue(r.ok());
        assertEquals(PowerState.UNKNOWN, r.value().powerState());
        assertEquals("VMRUN_TIMEOUT", r.value().stateReason());
        assertNull(r.value().evidence().runningCount());
        assertNull(r.value().evidence().vmxMatched());
        assertFalse(r.value().watchdog().startAllowed());
    }

    @Test
    void modes_detection_seule_et_non_autorise_sont_lus() throws IOException {
        serveGolden("state-detect-only");
        WatchdogClient.VmState s = client.state("k8s-monitoring").value();
        assertEquals("DETECT_ONLY", s.mode());
        assertEquals(List.of("DETECTION_ONLY", "STATE_RUNNING").stream().filter(s.watchdog().refusalCodes()::contains)
                .findFirst().orElse(null), "DETECTION_ONLY");
        assertFalse(s.watchdog().startAllowed());
        serveGolden("state-deny-restart");
        WatchdogClient.VmState m = client.state("k8s-master").value();
        assertEquals("DENY_RESTART", m.mode());
        assertTrue(m.watchdog().refusalCodes().contains("NOT_AUTHORIZED"));
    }

    @Test
    void toutes_les_vm_en_un_appel() throws IOException {
        serveGolden("states-all");
        WatchdogClient.Result<Map<String, WatchdogClient.VmState>> r = client.states();
        assertTrue(r.ok(), r.error());
        assertEquals(List.of("k8s-worker2", "k8s-worker1", "k8s-lb2", "k8s-monitoring", "k8s-master").size(),
                r.value().size());
        assertEquals(PowerState.RUNNING, r.value().get("k8s-worker1").powerState());
        assertEquals(PowerState.RUNNING, r.value().get("k8s-master").powerState());
        assertEquals(PowerState.POWERED_OFF, r.value().get("k8s-lb2").powerState());
        assertEquals("DETECT_ONLY", r.value().get("k8s-monitoring").mode());   // .160 : détection seule (consigne finale)
        assertEquals("AUTO", r.value().get("k8s-master").mode());              // .153 : cible AUTO depuis la consigne du 2026-11-17
    }

    @Test
    void monitoring_detection_seule_etat_lu_et_demande_de_demarrage_refusee_sans_demarrage() throws IOException {
        serveGolden("state-detect-only");
        WatchdogClient.VmState s = client.state("k8s-monitoring").value();
        assertEquals("DETECT_ONLY", s.mode());
        assertFalse(s.watchdog().startAllowed(), "le watchdog n'autorise JAMAIS le démarrage de .160");
        assertTrue(s.watchdog().refusalCodes().contains("DETECTION_ONLY"), s.watchdog().refusalCodes().toString());
        RestartOutcome o = restartFromGolden("restart-detection-only");
        assertEquals(WatchdogClient.Kind.REFUSED, o.kind());
        assertEquals(403, o.http());
        assertFalse(o.startIssued(), "aucun vmrun start pour .160");
    }

    @Test
    void etat_en_erreur_http_est_un_echec_de_lecture() throws IOException {
        serveGolden("state-bad-token");
        WatchdogClient.Result<WatchdogClient.VmState> r = client.state("k8s-worker1");
        assertFalse(r.ok());
        assertEquals(401, r.http());
        serveGolden("state-unknown-vm");
        assertEquals(404, client.state("lb2").http());
    }

    @Test
    void sante_ouverte_sans_jeton() throws IOException {
        serveGolden("health");
        WatchdogClient.Result<WatchdogClient.Health> r = client.health();
        assertTrue(r.ok(), r.error());
        assertEquals("ok", r.value().status());
        assertTrue(r.value().authConfigured());
        assertEquals("ok", r.value().vmrun());
        assertEquals(1, r.value().contract());
        assertFalse(seen.get(0).headers().containsKey("X-Helios-Token"), "/health n'envoie pas le jeton");
    }

    @Test
    void preuve_d_etat_trop_vieille_ou_negative_n_est_pas_fraiche() {
        WatchdogClient.Evidence e = new WatchdogClient.Evidence("s", Instant.now(), 15_000, 10, 6, true, "a", "b");
        assertTrue(e.isFresh());
        assertFalse(new WatchdogClient.Evidence("s", Instant.now(), 15_001, 10, 6, true, "a", "b").isFresh());
        assertFalse(new WatchdogClient.Evidence("s", Instant.now(), -1, 10, 6, true, "a", "b").isFresh());
        assertTrue(new WatchdogClient.Evidence("s", Instant.now(), 0, 10, 6, true, "a", "b").isFresh());
    }

    // ───────────────────────────── Requêtes émises ─────────────────────────────

    @Test
    void le_post_porte_jeton_incident_et_identifiant_de_requete_et_n_est_envoye_qu_une_fois() throws IOException {
        serveGolden("restart-started");
        client.restart("k8s-worker1", 4242, "req-abc");
        assertEquals(1, seen.size());
        Seen s = seen.get(0);
        assertEquals("POST", s.method());
        assertEquals("/restart/k8s-worker1", s.path());
        assertEquals(TOKEN, s.headers().get("X-Helios-Token"));
        assertEquals("4242", s.headers().get("X-Helios-Incident"));
        assertEquals("req-abc", s.headers().get("X-Helios-Request-Id"));
    }

    @Test
    void un_identifiant_de_requete_est_genere_si_absent() throws IOException {
        serveGolden("restart-started");
        client.restart("k8s-worker1", 1, null);
        assertTrue(seen.get(0).headers().get("X-Helios-Request-Id").matches("[0-9a-f]{16}"));
    }

    @Test
    void les_lectures_portent_le_jeton() throws IOException {
        serveGolden("state-running");
        client.state("k8s-worker1");
        assertEquals(TOKEN, seen.get(0).headers().get("X-Helios-Token"));
        assertEquals("GET", seen.get(0).method());
        assertEquals("/vm/k8s-worker1/state", seen.get(0).path());
    }

    // ───────────────────────────── Jamais de rejeu, fail-closed ─────────────────────────────

    @Test
    void aucun_rejeu_du_post_apres_une_reponse_d_echec() throws IOException {
        for (String name : List.of("restart-failed", "restart-state-unknown", "restart-cooldown",
                "restart-detection-only")) {
            seen.clear();
            restartFromGolden(name);
            assertEquals(1, seen.size(), name + " : le client ne doit jamais rejouer un POST /restart");
        }
    }

    @Test
    void delai_depasse_donne_TRANSPORT_ERROR_sans_rejeu() {
        vmPower.setWatchdogRestartTimeoutMs(400);
        client = new WatchdogClient(remediation, vmPower);
        reply.set(new Reply(200, "{\"status\":\"STARTED\",\"started\":true}", 2500));
        RestartOutcome o = client.restart("k8s-worker1", 9, "r");
        assertEquals(Kind.TRANSPORT_ERROR, o.kind());
        assertFalse(o.noFailure(), "un délai n'est JAMAIS un succès : le démarrage reste à vérifier en aval");
        assertTrue(o.error().startsWith("TIMEOUT"));
        assertFalse(o.error().contains(TOKEN));
        assertEquals(1, seen.size());
    }

    @Test
    void serveur_arrete_donne_TRANSPORT_ERROR() {
        server.stop(0);
        RestartOutcome o = client.restart("k8s-worker1", 9, "r");
        assertEquals(Kind.TRANSPORT_ERROR, o.kind());
        assertFalse(o.error().contains(TOKEN));
    }

    @Test
    void client_non_configure_refuse_sans_aucun_appel_reseau() {
        remediation.setMasterWatchdogToken("");
        RestartOutcome o = client.restart("k8s-worker1", 9, "r");
        assertEquals(Kind.REFUSED, o.kind());
        assertEquals("CLIENT_NOT_CONFIGURED", o.status());
        assertFalse(client.isConfigured());
        assertFalse(client.state("k8s-worker1").ok());
        assertFalse(client.states().ok());
        remediation.setMasterWatchdogToken(TOKEN);
        remediation.setMasterWatchdogUrl("  ");
        assertEquals(Kind.REFUSED, client.restart("k8s-worker1", 9, "r").kind());
        assertEquals(0, seen.size());
    }

    @Test
    void reponses_hors_contrat_ne_sont_jamais_un_succes() {
        List<Reply> horsContrat = List.of(
                new Reply(200, "pas du json", 0),
                new Reply(200, "{}", 0),
                new Reply(200, "{\"status\":\"STARTED\"}", 0),                       // started absent
                new Reply(200, "{\"status\":\"STARTED\",\"started\":false}", 0),
                new Reply(200, "{\"status\":\"AUTRE\",\"noop\":true}", 0),
                new Reply(200, "{\"status\":\"ALREADY_RUNNING\"}", 0),               // noop absent
                new Reply(202, "{\"status\":\"STARTED\"}", 0),
                new Reply(302, "{}", 0),
                new Reply(418, "{}", 0));
        for (Reply r : horsContrat) {
            reply.set(r);
            RestartOutcome o = client.restart("k8s-worker1", 9, "r");
            assertEquals(Kind.PROTOCOL_ERROR, o.kind(), r.status() + " " + r.body());
            assertFalse(o.noFailure(), r.body());
        }
    }

    @Test
    void etat_hors_contrat_est_un_echec_de_lecture() {
        List<Reply> horsContrat = List.of(new Reply(200, "pas du json", 0), new Reply(200, "{\"vms\":{}}", 0));
        reply.set(horsContrat.get(0));
        assertFalse(client.state("k8s-worker1").ok());
        assertFalse(client.states().ok());
        reply.set(horsContrat.get(1));
        assertFalse(client.states().ok());
        // un état sans power_state valide est UNKNOWN : jamais un démarrage
        reply.set(new Reply(200, "{\"vm\":\"k8s-worker1\",\"power_state\":\"SUSPENDED\"}", 0));
        assertEquals(PowerState.UNKNOWN, client.state("k8s-worker1").value().powerState());
        List<WatchdogClient.Result<?>> ignore = new ArrayList<>();
        assertTrue(ignore.isEmpty());
    }
}
