package com.orange.pfe.backend.service.vmpower;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.orange.pfe.backend.config.RemediationProperties;
import com.orange.pfe.backend.config.VmPowerProperties;
import com.orange.pfe.backend.service.vmpower.VmPowerPolicy.PowerState;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;

import java.io.IOException;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.net.http.HttpTimeoutException;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.time.Instant;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;

/**
 * VMDOWN-POLICY-20261105 — client du watchdog hôte (contrat HTTP version 1, voir
 * {@code infra/helios-sot/watchdog/helios-vm-watchdog.py} et les fichiers « golden » de
 * {@code infra/helios-sot/contract/}).
 *
 * <p>Séparation des responsabilités : le BACKEND est le seul appelant autorisé de {@code POST /restart}.
 * Ce client :
 * <ul>
 *   <li>ne REJOUE JAMAIS un {@code POST /restart} (ni retry, ni reconnexion) : l'idempotence est portée par
 *       l'en-tête {@code X-Helios-Incident} côté watchdog, mais l'appelant ne doit pas compter dessus ;</li>
 *   <li>est FAIL-CLOSED : URL ou jeton absents, réponse illisible, statut inattendu ⇒ jamais un succès ;</li>
 *   <li>n'écrit JAMAIS le jeton dans un journal ou un message d'erreur.</li>
 * </ul>
 */
@Component
public class WatchdogClient {

    private static final Logger log = LoggerFactory.getLogger(WatchdogClient.class);
    private static final ObjectMapper MAPPER = new ObjectMapper();

    // ── Modèle ───────────────────────────────────────────────────────────────────────────────────

    public record Evidence(String source, Instant observedAt, long ageMs, long durationMs, Integer runningCount,
                           Boolean vmxMatched, String vmxSha256, String listSha256) {
        /** Preuve exploitable : âge connu et ≤ {@link VmPowerPolicy#EVIDENCE_MAX_AGE_MS}. */
        public boolean isFresh() {
            return ageMs >= 0 && ageMs <= VmPowerPolicy.EVIDENCE_MAX_AGE_MS;
        }
    }

    public record LastStart(String at, Long incident, String result) {
    }

    public record Guards(boolean maintenance, boolean starting, boolean startAllowed, List<String> refusalCodes,
                         int cooldownRemainingS, int backoffRemainingS, LastStart lastStart, Integer hostRamMb) {
    }

    public record VmState(String vm, String mode, PowerState powerState, String stateReason, Evidence evidence,
                          Guards watchdog) {
    }

    /** Résultat d'une lecture : {@code ok} ssi HTTP 200 et corps conforme. */
    public record Result<T>(boolean ok, int http, T value, String error) {
        static <T> Result<T> success(int http, T value) {
            return new Result<>(true, http, value, null);
        }

        static <T> Result<T> failure(int http, String error) {
            return new Result<>(false, http, null, error);
        }
    }

    public record Health(String status, boolean authConfigured, String vmrun, String version, int contract) {
    }

    public enum Kind {
        /** 200 STARTED : `vmrun start` exécuté et VM confirmée dans `vmrun list`. */
        STARTED,
        /** 200 noop ALREADY_RUNNING : la VM était déjà allumée, AUCUN démarrage effectué. */
        ALREADY_RUNNING,
        /** 200 noop ALREADY_STARTING : un démarrage de cette VM est déjà en cours. */
        ALREADY_STARTING,
        /** 200 noop ALREADY_EXECUTED : cet incident a déjà été traité. */
        ALREADY_EXECUTED,
        /** 202 STARTING : démarrage accepté, non terminé (la vérification en aval tranchera). */
        ACCEPTED_STARTING,
        /** 400 / 401 / 403 / 404 / 405 / 409 / 423 : refus définitif de la requête. */
        REFUSED,
        /** 503 : refus temporaire (BACKOFF, COOLDOWN_ACTIVE, STATE_UNKNOWN, HOST_RAM_*, LOCK_BLOCKED...). */
        DEFERRED,
        /** 500 : échec de démarrage (ou rejeu d'un échec). */
        FAILED,
        /** Aucune réponse exploitable (connexion, délai, interruption) : le démarrage a pu avoir lieu. */
        TRANSPORT_ERROR,
        /** Réponse hors contrat : jamais traitée comme un succès. */
        PROTOCOL_ERROR
    }

    public record RestartOutcome(Kind kind, int http, String status, String reason, Integer retryAfterS,
                                 String detail, String requestId, boolean replayed, String error) {

        /** Aucune erreur : STARTED, ALREADY_* ou ACCEPTED_STARTING. */
        public boolean noFailure() {
            return kind == Kind.STARTED || kind == Kind.ALREADY_RUNNING || kind == Kind.ALREADY_STARTING
                    || kind == Kind.ALREADY_EXECUTED || kind == Kind.ACCEPTED_STARTING;
        }

        /** Un `vmrun start` a été (ou est en train d'être) émis par CET appel. */
        public boolean startIssued() {
            return kind == Kind.STARTED || kind == Kind.ACCEPTED_STARTING;
        }

        public String describe() {
            return "HTTP " + http + " " + status + (reason != null ? " (" + reason + ")" : "")
                    + (retryAfterS != null ? ", retry_after_s=" + retryAfterS : "")
                    + (error != null ? " — " + error : "");
        }
    }

    // ── Configuration ────────────────────────────────────────────────────────────────────────────

    private final RemediationProperties remediation;
    private final VmPowerProperties vmPower;
    private final HttpClient http;

    public WatchdogClient(RemediationProperties remediation, VmPowerProperties vmPower) {
        this.remediation = remediation;
        this.vmPower = vmPower;
        this.http = HttpClient.newBuilder()
                .version(HttpClient.Version.HTTP_1_1)
                .connectTimeout(Duration.ofMillis(Math.max(100, vmPower.getWatchdogConnectTimeoutMs())))
                .build();
    }

    public boolean isConfigured() {
        return notBlank(remediation.getMasterWatchdogUrl()) && notBlank(remediation.getMasterWatchdogToken());
    }

    private static boolean notBlank(String s) {
        return s != null && !s.isBlank();
    }

    // ── Lectures ─────────────────────────────────────────────────────────────────────────────────

    /** {@code GET /health} — ouvert, sans jeton. */
    public Result<Health> health() {
        if (!notBlank(remediation.getMasterWatchdogUrl())) {
            return Result.failure(0, "CLIENT_NOT_CONFIGURED: URL du watchdog absente");
        }
        Raw raw = send("GET", "/health", Map.of(), vmPower.getWatchdogReadTimeoutMs());
        if (raw.transportError != null) {
            return Result.failure(0, raw.transportError);
        }
        if (raw.status != 200) {
            return Result.failure(raw.status, "HTTP " + raw.status);
        }
        try {
            JsonNode n = MAPPER.readTree(raw.body);
            return Result.success(200, new Health(text(n, "status"), n.path("auth_configured").asBoolean(false),
                    text(n, "vmrun"), text(n, "version"), n.path("contract").asInt(0)));
        } catch (Exception ex) {
            return Result.failure(200, "PROTOCOL_ERROR: corps /health illisible");
        }
    }

    /** {@code GET /vm/<nom>/state}. */
    public Result<VmState> state(String vm) {
        Result<String> guard = guardAuthenticated();
        if (!guard.ok()) {
            return Result.failure(0, guard.error());
        }
        Raw raw = send("GET", "/vm/" + vm + "/state", authHeaders(), vmPower.getWatchdogReadTimeoutMs());
        if (raw.transportError != null) {
            return Result.failure(0, raw.transportError);
        }
        if (raw.status != 200) {
            return Result.failure(raw.status, "HTTP " + raw.status + bodyStatus(raw.body));
        }
        try {
            return Result.success(200, parseState(MAPPER.readTree(raw.body)));
        } catch (Exception ex) {
            return Result.failure(200, "PROTOCOL_ERROR: état illisible (" + ex.getClass().getSimpleName() + ")");
        }
    }

    /** {@code GET /vm/state} — toutes les VM de la table, un seul `vmrun list` côté watchdog. */
    public Result<Map<String, VmState>> states() {
        Result<String> guard = guardAuthenticated();
        if (!guard.ok()) {
            return Result.failure(0, guard.error());
        }
        Raw raw = send("GET", "/vm/state", authHeaders(), vmPower.getWatchdogReadTimeoutMs());
        if (raw.transportError != null) {
            return Result.failure(0, raw.transportError);
        }
        if (raw.status != 200) {
            return Result.failure(raw.status, "HTTP " + raw.status + bodyStatus(raw.body));
        }
        try {
            JsonNode vms = MAPPER.readTree(raw.body).path("vms");
            if (!vms.isObject() || vms.size() == 0) {
                return Result.failure(200, "PROTOCOL_ERROR: section vms absente");
            }
            Map<String, VmState> out = new LinkedHashMap<>();
            vms.fields().forEachRemaining(e -> out.put(e.getKey(), parseState(e.getValue())));
            return Result.success(200, out);
        } catch (Exception ex) {
            return Result.failure(200, "PROTOCOL_ERROR: états illisibles (" + ex.getClass().getSimpleName() + ")");
        }
    }

    // ── Démarrage ────────────────────────────────────────────────────────────────────────────────

    /**
     * {@code POST /restart/<nom>} — UNE SEULE tentative, jamais rejouée par ce client.
     *
     * @param incidentId incident justifiant le démarrage (clé d'idempotence côté watchdog)
     * @param requestId  identifiant de corrélation, ou {@code null} pour en générer un
     */
    public RestartOutcome restart(String vm, long incidentId, String requestId) {
        String rid = (requestId == null || requestId.isBlank())
                ? UUID.randomUUID().toString().replace("-", "").substring(0, 16) : requestId;
        Result<String> guard = guardAuthenticated();
        if (!guard.ok()) {
            return new RestartOutcome(Kind.REFUSED, 0, "CLIENT_NOT_CONFIGURED", null, null, null, rid, false,
                    guard.error());
        }
        Map<String, String> headers = new LinkedHashMap<>(authHeaders());
        headers.put("X-Helios-Incident", Long.toString(incidentId));
        headers.put("X-Helios-Request-Id", rid);
        Raw raw = send("POST", "/restart/" + vm, headers, vmPower.getWatchdogRestartTimeoutMs());
        if (raw.transportError != null) {
            // Le démarrage a PU avoir lieu : l'état sera tranché par la vérification, jamais par un rejeu.
            return new RestartOutcome(Kind.TRANSPORT_ERROR, 0, "TRANSPORT_ERROR", null, null, null, rid, false,
                    raw.transportError);
        }
        return classify(raw, rid);
    }

    static RestartOutcome classify(Raw raw, String fallbackRequestId) {
        JsonNode n;
        try {
            n = MAPPER.readTree(raw.body == null || raw.body.isBlank() ? "{}" : raw.body);
        } catch (Exception ex) {
            return new RestartOutcome(Kind.PROTOCOL_ERROR, raw.status, "UNREADABLE", null, null, null,
                    fallbackRequestId, false, "corps illisible");
        }
        String status = text(n, "status");
        String reason = text(n, "reason");
        String rid = text(n, "request_id") != null ? text(n, "request_id") : fallbackRequestId;
        Integer retry = n.hasNonNull("retry_after_s") ? n.get("retry_after_s").asInt() : null;
        String detail = text(n, "detail");
        boolean replayed = n.path("replayed").asBoolean(false);
        String error = text(n, "error");
        int code = raw.status;

        Kind kind;
        if (code == 200) {
            boolean noop = n.path("noop").asBoolean(false);
            if (!noop && n.path("started").asBoolean(false) && "STARTED".equals(status)) {
                kind = Kind.STARTED;
            } else if (noop && "ALREADY_RUNNING".equals(status)) {
                kind = Kind.ALREADY_RUNNING;
            } else if (noop && "ALREADY_STARTING".equals(status)) {
                kind = Kind.ALREADY_STARTING;
            } else if (noop && "ALREADY_EXECUTED".equals(status)) {
                kind = Kind.ALREADY_EXECUTED;
            } else {
                kind = Kind.PROTOCOL_ERROR;
                error = "réponse 200 hors contrat (status=" + status + ")";
            }
        } else if (code == 202) {
            kind = "STARTING".equals(status) ? Kind.ACCEPTED_STARTING : Kind.PROTOCOL_ERROR;
            if (kind == Kind.PROTOCOL_ERROR) {
                error = "réponse 202 hors contrat (status=" + status + ")";
            }
        } else if (code == 400 || code == 401 || code == 403 || code == 404 || code == 405 || code == 409
                || code == 423) {
            kind = Kind.REFUSED;
        } else if (code == 503) {
            kind = Kind.DEFERRED;
        } else if (code == 500) {
            kind = Kind.FAILED;
        } else {
            kind = Kind.PROTOCOL_ERROR;
            error = "statut HTTP inattendu " + code;
        }
        return new RestartOutcome(kind, code, status == null ? "HTTP_" + code : status, reason, retry, detail,
                rid, replayed, error);
    }

    // ── Détails ──────────────────────────────────────────────────────────────────────────────────

    static VmState parseState(JsonNode n) {
        JsonNode ev = n.path("evidence");
        Evidence evidence = new Evidence(
                text(ev, "source"),
                parseInstant(text(ev, "observed_at")),
                ev.path("age_ms").asLong(-1),
                ev.path("duration_ms").asLong(-1),
                ev.hasNonNull("running_count") ? ev.get("running_count").asInt() : null,
                ev.hasNonNull("vmx_matched") ? ev.get("vmx_matched").asBoolean() : null,
                text(ev, "vmx_sha256_12"),
                text(ev, "list_sha256_12"));
        JsonNode w = n.path("watchdog");
        List<String> codes = new java.util.ArrayList<>();
        w.path("refusal_codes").forEach(c -> codes.add(c.asText()));
        JsonNode ls = w.path("last_start");
        LastStart last = ls.isObject()
                ? new LastStart(text(ls, "at"), ls.hasNonNull("incident") ? ls.get("incident").asLong() : null,
                text(ls, "result"))
                : null;
        Guards guards = new Guards(w.path("maintenance").asBoolean(false), w.path("starting").asBoolean(false),
                w.path("start_allowed").asBoolean(false), List.copyOf(codes), w.path("cooldown_remaining_s").asInt(0),
                w.path("backoff_remaining_s").asInt(0), last,
                w.hasNonNull("host_ram_mb") ? w.get("host_ram_mb").asInt() : null);
        return new VmState(text(n, "vm"), text(n, "mode"), PowerState.fromWire(text(n, "power_state")),
                text(n, "state_reason"), evidence, guards);
    }

    private Result<String> guardAuthenticated() {
        if (!notBlank(remediation.getMasterWatchdogUrl()) || !notBlank(remediation.getMasterWatchdogToken())) {
            return Result.failure(0, "CLIENT_NOT_CONFIGURED: watchdog (URL ou jeton) non configuré — "
                    + "remediation.master-watchdog-url / remediation.master-watchdog-token");
        }
        return Result.success(0, "ok");
    }

    private Map<String, String> authHeaders() {
        return Map.of("X-Helios-Token", remediation.getMasterWatchdogToken());
    }

    record Raw(int status, String body, String transportError) {
    }

    private Raw send(String method, String path, Map<String, String> headers, int readTimeoutMs) {
        String base = remediation.getMasterWatchdogUrl().strip();
        while (base.endsWith("/")) {
            base = base.substring(0, base.length() - 1);
        }
        try {
            HttpRequest.Builder b = HttpRequest.newBuilder(URI.create(base + path))
                    .timeout(Duration.ofMillis(Math.max(200, readTimeoutMs)));
            headers.forEach(b::header);
            if ("POST".equals(method)) {
                b.POST(HttpRequest.BodyPublishers.noBody());
            } else {
                b.GET();
            }
            HttpResponse<String> r = http.send(b.build(), HttpResponse.BodyHandlers.ofString(StandardCharsets.UTF_8));
            log.info("watchdog {} {} -> HTTP {}", method, path, r.statusCode());
            return new Raw(r.statusCode(), r.body(), null);
        } catch (HttpTimeoutException ex) {
            log.warn("watchdog {} {} -> délai dépassé ({} ms)", method, path, readTimeoutMs);
            return new Raw(0, null, "TIMEOUT: aucune réponse du watchdog en " + readTimeoutMs + " ms");
        } catch (InterruptedException ex) {
            Thread.currentThread().interrupt();
            return new Raw(0, null, "INTERRUPTED: appel watchdog interrompu");
        } catch (IOException ex) {
            log.warn("watchdog {} {} -> erreur réseau: {}", method, path, ex.getClass().getSimpleName());
            return new Raw(0, null, "IO_ERROR: " + ex.getClass().getSimpleName());
        } catch (IllegalArgumentException ex) {
            return new Raw(0, null, "CLIENT_CONFIG_ERROR: URL watchdog invalide");
        }
    }

    private static String bodyStatus(String body) {
        try {
            String s = text(MAPPER.readTree(body == null ? "{}" : body), "status");
            return s == null ? "" : " " + s;
        } catch (Exception ex) {
            return "";
        }
    }

    private static String text(JsonNode n, String field) {
        JsonNode v = n == null ? null : n.get(field);
        return v == null || v.isNull() ? null : v.asText();
    }

    private static Instant parseInstant(String s) {
        try {
            return s == null ? null : Instant.parse(s);
        } catch (Exception ex) {
            return null;
        }
    }
}
