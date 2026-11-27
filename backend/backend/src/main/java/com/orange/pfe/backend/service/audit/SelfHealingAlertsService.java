package com.orange.pfe.backend.service.audit;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.orange.pfe.backend.config.InfraHealthProperties;
import com.orange.pfe.backend.dto.audit.SelfHealingDtos.AlertItem;
import com.orange.pfe.backend.dto.audit.SelfHealingDtos.Alerts;
import com.orange.pfe.backend.dto.audit.SelfHealingDtos.LogSignal;
import com.orange.pfe.backend.dto.audit.SelfHealingDtos.LogSignals;
import com.orange.pfe.backend.dto.audit.SelfHealingDtos.LokiRules;
import com.orange.pfe.backend.dto.audit.SelfHealingDtos.ToolAlerts;
import com.orange.pfe.backend.exception.ResourceNotFoundException;
import com.orange.pfe.backend.model.core.Incident;
import com.orange.pfe.backend.model.enums.IncidentStatus;
import com.orange.pfe.backend.model.user.AppUser;
import com.orange.pfe.backend.repository.IncidentHistoryRepository;
import com.orange.pfe.backend.repository.IncidentRepository;
import com.orange.pfe.backend.service.audit.SelfHealingTraceService.Source;
import com.orange.pfe.backend.service.org.OrgAccessService;
import com.orange.pfe.backend.service.org.OrgPermission;
import com.orange.pfe.backend.service.supervision.LogMeaningCatalog;
import com.orange.pfe.backend.service.supervision.SupervisionClient;
import com.orange.pfe.backend.service.supervision.SupervisionClient.UnavailableException;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.io.IOException;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.time.Duration;
import java.time.Instant;
import java.time.LocalDateTime;
import java.time.ZoneId;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Optional;
import java.util.TreeMap;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * Alertes et signaux liés à un incident, pour l'Audit-trail ({@code AUDIT_VIEW_ALL}) : DÉRIVÉS, en lecture seule.
 * Prometheus (alertes actives et récentes, règles), Alertmanager et Grafana (seulement s'ils sont configurés), règles Loki
 * (s'il existe un ruler), et signaux de logs classés par le catalogue de significations (équivalent d'alertes Loki).
 * Un outil absent, non configuré ou en panne donne un état explicite, jamais une erreur serveur ; aucune écriture.
 */
@Service
public class SelfHealingAlertsService {

    static final int MAX_ALERTS = 50;
    static final int MAX_SERIES = 500;
    static final int SIGNAL_LINES = 200;
    private static final Duration MARGIN = Duration.ofMinutes(15);
    private static final Duration MAX_WINDOW = Duration.ofHours(24);
    private static final Duration SIGNAL_MAX_WINDOW = Duration.ofHours(2);

    /** Nœuds HELIOS → adresse IP (étiquette {@code instance} des métriques de machine). */
    private static final Map<String, String> NODE_IPS = Map.of(
            "k8s-master", "192.168.148.153", "k8s-worker2", "192.168.148.155", "k8s-worker1", "192.168.148.158",
            "k8s-monitoring", "192.168.148.160", "k8s-aicamunda", "192.168.148.161", "k8s-lb2", "192.168.148.162");
    private static final List<String> RESOURCE_KEYS = List.of("deployment", "pod", "container", "app", "service", "pod_name");
    private static final List<String> NODE_KEYS = List.of("node", "nodename", "host", "hostname");
    private static final Pattern LOKI_ALERT_NAME = Pattern.compile("(?m)^\\s*-?\\s*alert:\\s*([A-Za-z0-9_:.-]+)");

    private final ObjectMapper mapper = new ObjectMapper();
    private final HttpClient http = HttpClient.newBuilder().connectTimeout(Duration.ofSeconds(3)).build();
    private final IncidentRepository incidents;
    private final IncidentHistoryRepository history;
    private final SupervisionClient client;
    private final InfraHealthProperties infra;
    private final OrgAccessService access;

    @Value("${helios.supervision.alertmanager-url:}")
    private String alertmanagerUrl;

    /** Jeton Viewer de Grafana (variable d'environnement) : jamais journalisé ni renvoyé. */
    @Value("${helios.supervision.grafana.api-token:}")
    private String grafanaToken;

    public SelfHealingAlertsService(IncidentRepository incidents, IncidentHistoryRepository history, SupervisionClient client,
                                    InfraHealthProperties infra, OrgAccessService access) {
        this.incidents = incidents;
        this.history = history;
        this.client = client;
        this.infra = infra;
        this.access = access;
    }

    // ── Alertes ───────────────────────────────────────────────────────────────

    @Transactional(readOnly = true)
    public Alerts alerts(AppUser actor, Long incidentId) {
        access.require(actor, OrgPermission.AUDIT_VIEW_ALL);
        Incident i = requireIncident(incidentId);
        LocalDateTime now = LocalDateTime.now();
        LocalDateTime from = i.getCreatedAt().minus(MARGIN);
        LocalDateTime end = (resolvedAt(i) == null ? now : resolvedAt(i)).plus(MARGIN);
        LocalDateTime to = end.isAfter(now) ? now : end;
        if (Duration.between(from, to).compareTo(MAX_WINDOW) > 0) {
            to = from.plus(MAX_WINDOW);
        }
        boolean[] truncated = {false};
        ToolAlerts prometheus = prometheus(i, from, to, truncated);
        return new Alerts(from, to, truncated[0], prometheus, alertmanager(i), grafana(i), lokiRules());
    }

    private ToolAlerts prometheus(Incident incident, LocalDateTime from, LocalDateTime to, boolean[] truncated) {
        try {
            Map<String, JsonNode> rules = new LinkedHashMap<>();   // nom de règle → règle
            for (JsonNode g : client.promRules()) {
                for (JsonNode r : g.path("rules")) {
                    rules.putIfAbsent(r.path("name").asText(), r);
                }
            }
            Map<String, Candidate> found = new LinkedHashMap<>();
            ZoneId zone = ZoneId.systemDefault();
            long start = from.atZone(zone).toEpochSecond();
            long stop = to.atZone(zone).toEpochSecond();
            long step = Math.max(60, (stop - start) / 500);
            JsonNode series = client.promRange("ALERTS{alertstate=\"firing\"}", start, stop, step);
            int n = 0;
            for (JsonNode s : series) {
                if (++n > MAX_SERIES) {
                    truncated[0] = true;
                    break;
                }
                Map<String, String> labels = labelsOf(s.path("metric"));
                if (!matches(incident, labels)) {
                    continue;
                }
                Long first = null, last = null;
                for (JsonNode v : s.path("values")) {
                    if ("1".equals(v.path(1).asText())) {
                        long t = (long) v.path(0).asDouble();
                        first = first == null ? t : first;
                        last = t;
                    }
                }
                found.computeIfAbsent(fingerprint(labels), k -> new Candidate(labels)).seen(first, last);
            }
            for (JsonNode a : client.promAlerts()) {
                Map<String, String> labels = labelsOf(a.path("labels"));
                if (!matches(incident, labels)) {
                    continue;
                }
                Candidate c = found.computeIfAbsent(fingerprint(labels), k -> new Candidate(labels));
                c.activeNow = "firing".equals(a.path("state").asText()) || c.activeNow;
                c.summary = a.path("annotations").path("summary").asText(c.summary);
                Instant activeAt = parseInstant(a.path("activeAt").asText(null));
                if (activeAt != null) {
                    c.seen(activeAt.getEpochSecond(), c.activeNow ? Instant.now().getEpochSecond() : null);
                }
            }
            List<AlertItem> items = new ArrayList<>();
            for (Candidate c : found.values()) {
                JsonNode rule = rules.get(c.labels.get("alertname"));
                String ruleIncident = rule == null ? null : rule.path("annotations").path("incident").asText(null);
                items.add(new AlertItem(c.labels.get("alertname"), c.labels.get("severity"),
                        c.summary != null ? c.summary : (rule == null ? null : rule.path("annotations").path("summary").asText(null)),
                        safeLabels(c.labels), time(c.first), time(c.last), c.activeNow, rule == null ? null : rule.path("state").asText(null),
                        ruleIncident, ruleIncident != null && ruleIncident.equalsIgnoreCase(String.valueOf(incident.getIncidentType()))));
            }
            items.sort(Comparator.comparing(AlertItem::lastFiring, Comparator.nullsFirst(Comparator.naturalOrder())).reversed());
            return new ToolAlerts("Prometheus", "OK", items.isEmpty() ? "Aucune alerte liée à cet incident sur la période" : null,
                    items.size() > MAX_ALERTS ? items.subList(0, MAX_ALERTS) : items);
        } catch (UnavailableException e) {
            return new ToolAlerts("Prometheus", "UNAVAILABLE", e.getMessage(), List.of());
        }
    }

    private ToolAlerts alertmanager(Incident incident) {
        if (alertmanagerUrl == null || alertmanagerUrl.isBlank() || !alertmanagerUrl.matches("(?i)^https?://.+")) {
            return new ToolAlerts("Alertmanager", "NOT_CONFIGURED",
                    "Alertmanager n'est pas configuré (propriété helios.supervision.alertmanager-url)", List.of());
        }
        try {
            JsonNode root = fetchJson(alertmanagerUrl.replaceAll("/+$", "") + "/api/v2/alerts?active=true&silenced=false&inhibited=false", null);
            List<AlertItem> items = new ArrayList<>();
            for (JsonNode a : root.isArray() ? root : mapper.createArrayNode()) {
                Map<String, String> labels = labelsOf(a.path("labels"));
                if (matches(incident, labels) && items.size() < MAX_ALERTS) {
                    items.add(new AlertItem(labels.get("alertname"), labels.get("severity"),
                            a.path("annotations").path("summary").asText(null), safeLabels(labels),
                            time(parseInstant(a.path("startsAt").asText(null))), null, "active".equals(a.path("status").path("state").asText("active")),
                            null, null, false));
                }
            }
            return new ToolAlerts("Alertmanager", "OK", items.isEmpty() ? "Aucune alerte active liée à cet incident" : null, items);
        } catch (UnavailableException e) {
            return new ToolAlerts("Alertmanager", "UNAVAILABLE", e.getMessage(), List.of());
        }
    }

    private ToolAlerts grafana(Incident incident) {
        String base = infra.getGrafanaUrl() == null ? "" : infra.getGrafanaUrl().replaceAll("/+$", "");
        if (grafanaToken == null || grafanaToken.isBlank() || !base.matches("(?i)^https?://.+")) {
            return new ToolAlerts("Grafana", "NOT_CONFIGURED",
                    "Aucun jeton de lecture Grafana n'est configuré (variable HELIOS_SUPERVISION_GRAFANA_TOKEN)", List.of());
        }
        try {
            JsonNode root = fetchJson(base + "/api/prometheus/grafana/api/v1/alerts", grafanaToken.trim());
            List<AlertItem> items = new ArrayList<>();
            for (JsonNode a : root.path("data").path("alerts")) {
                Map<String, String> labels = labelsOf(a.path("labels"));
                if (matches(incident, labels) && items.size() < MAX_ALERTS) {
                    items.add(new AlertItem(labels.get("alertname"), labels.get("severity"),
                            a.path("annotations").path("summary").asText(null), safeLabels(labels),
                            time(parseInstant(a.path("activeAt").asText(null))), null, "firing".equals(a.path("state").asText()),
                            a.path("state").asText(null), null, false));
                }
            }
            return new ToolAlerts("Grafana", "OK", items.isEmpty() ? "Aucune alerte Grafana liée à cet incident" : null, items);
        } catch (UnavailableException e) {
            return new ToolAlerts("Grafana", "UNAVAILABLE", e.getMessage(), List.of());
        }
    }

    private LokiRules lokiRules() {
        String base = infra.getLokiUrl() == null ? "" : infra.getLokiUrl().replaceAll("/+$", "");
        if (!base.matches("(?i)^https?://.+")) {
            return new LokiRules("NOT_CONFIGURED", "Loki n'est pas configuré", List.of());
        }
        try {
            HttpResponse<String> r = http.send(HttpRequest.newBuilder().uri(URI.create(base + "/loki/api/v1/rules"))
                    .timeout(Duration.ofSeconds(8)).GET().build(), HttpResponse.BodyHandlers.ofString());
            if (r.statusCode() == 404) {
                return new LokiRules("NOT_CONFIGURED", "Aucune règle d'alerte Loki n'est configurée (pas de ruler) : voir les signaux de logs", List.of());
            }
            if (r.statusCode() != 200) {
                return new LokiRules("UNAVAILABLE", "Loki a répondu HTTP " + r.statusCode(), List.of());
            }
            List<String> names = new ArrayList<>();
            Matcher m = LOKI_ALERT_NAME.matcher(r.body());
            while (m.find() && names.size() < MAX_ALERTS) {
                names.add(m.group(1));
            }
            return new LokiRules("OK", names.isEmpty() ? "Aucune règle d'alerte Loki définie" : null, names);
        } catch (IOException e) {
            return new LokiRules("UNAVAILABLE", "Loki est injoignable", List.of());
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            return new LokiRules("UNAVAILABLE", "Requête interrompue", List.of());
        }
    }

    // ── Signaux de logs ───────────────────────────────────────────────────────

    @Transactional(readOnly = true)
    public LogSignals logSignals(AppUser actor, Long incidentId) {
        access.require(actor, OrgPermission.AUDIT_VIEW_ALL);
        Incident i = requireIncident(incidentId);
        LocalDateTime now = LocalDateTime.now();
        LocalDateTime from = i.getCreatedAt().minusMinutes(5);
        LocalDateTime end = (resolvedAt(i) == null ? now : resolvedAt(i)).plusMinutes(5);
        LocalDateTime to = end.isAfter(now) ? now : end;
        if (Duration.between(from, to).compareTo(SIGNAL_MAX_WINDOW) > 0) {
            to = from.plus(SIGNAL_MAX_WINDOW);
        }
        Optional<Source> source = SelfHealingTraceService.source(i);
        if (source.isEmpty()) {
            return new LogSignals(false, "Les logs de cette ressource ne sont pas collectés par Loki", null, from, to, false, 0, List.of());
        }
        try {
            ZoneId zone = ZoneId.systemDefault();
            JsonNode streams = client.lokiRange(source.get().logql(), from.atZone(zone).toEpochSecond(),
                    to.atZone(zone).toEpochSecond(), 0, SIGNAL_LINES);
            Map<String, Accum> groups = new TreeMap<>();
            int lines = 0;
            for (JsonNode s : streams) {
                for (JsonNode v : s.path("values")) {
                    lines++;
                    String text = v.path(1).asText("");
                    LogMeaningCatalog.Meaning meaning = SelfHealingTraceService.errorMeaning(text);
                    if (meaning == null) {
                        continue;
                    }
                    LocalDateTime at = LocalDateTime.ofInstant(Instant.ofEpochSecond(Long.parseLong(v.path(0).asText("0")) / 1_000_000_000L), zone);
                    groups.computeIfAbsent(meaning.category(), k -> new Accum(meaning)).add(at, text);
                }
            }
            List<LogSignal> signals = new ArrayList<>();
            for (Accum a : groups.values()) {
                signals.add(new LogSignal(a.meaning.category(), a.meaning.label(), a.meaning.meaning(), a.meaning.advice(),
                        a.count, a.first, a.last, a.example.length() > 400 ? a.example.substring(0, 399) + "…" : a.example));
            }
            signals.sort(Comparator.comparingLong(LogSignal::count).reversed());
            return new LogSignals(true, signals.isEmpty() ? "Aucun signal d'erreur dans les logs sur cette fenêtre" : null,
                    source.get().label(), from, to, lines >= SIGNAL_LINES, lines, signals);
        } catch (UnavailableException e) {
            return new LogSignals(false, e.getMessage(), source.get().label(), from, to, false, 0, List.of());
        }
    }

    // ── Rapprochement alerte → incident ───────────────────────────────────────

    /**
     * Une alerte concerne l'incident si ses étiquettes désignent sa ressource : l'adresse ou le nom du nœud pour un
     * incident de machine ; l'espace de noms ET le nom de l'application (déploiement, pod, conteneur…) sinon.
     */
    static boolean matches(Incident i, Map<String, String> labels) {
        String app = i.getApp() == null ? "" : i.getApp().trim().toLowerCase(Locale.ROOT);
        if (app.isEmpty()) {
            return false;
        }
        if ("k8s-nodes".equals(i.getNamespace())) {
            String ip = NODE_IPS.get(app);
            String instance = labels.getOrDefault("instance", "");
            if (ip != null && (instance.equals(ip) || instance.startsWith(ip + ":"))) {
                return true;
            }
            return NODE_KEYS.stream().anyMatch(k -> app.equalsIgnoreCase(labels.get(k)));
        }
        if (i.getNamespace() != null && !i.getNamespace().equals(labels.get("namespace"))) {
            return false;
        }
        return RESOURCE_KEYS.stream().map(labels::get).anyMatch(v -> v != null && v.toLowerCase(Locale.ROOT).contains(app));
    }

    // ── Aides ─────────────────────────────────────────────────────────────────

    private static final class Candidate {
        final Map<String, String> labels;
        Long first;
        Long last;
        boolean activeNow;
        String summary;

        Candidate(Map<String, String> labels) {
            this.labels = labels;
        }

        void seen(Long f, Long l) {
            if (f != null && (first == null || f < first)) {
                first = f;
            }
            if (l != null && (last == null || l > last)) {
                last = l;
            }
        }
    }

    private static final class Accum {
        final LogMeaningCatalog.Meaning meaning;
        long count;
        LocalDateTime first;
        LocalDateTime last;
        String example;

        Accum(LogMeaningCatalog.Meaning meaning) {
            this.meaning = meaning;
        }

        void add(LocalDateTime at, String text) {
            count++;
            if (first == null || at.isBefore(first)) {
                first = at;
                example = text;
            }
            if (last == null || at.isAfter(last)) {
                last = at;
            }
        }
    }

    private JsonNode fetchJson(String url, String bearer) {
        try {
            HttpRequest.Builder b = HttpRequest.newBuilder().uri(URI.create(url)).timeout(Duration.ofSeconds(8)).GET();
            if (bearer != null) {
                b.header("Authorization", "Bearer " + bearer);
            }
            HttpResponse<String> r = http.send(b.build(), HttpResponse.BodyHandlers.ofString());
            if (r.statusCode() == 401 || r.statusCode() == 403) {
                throw new UnavailableException("Accès refusé (HTTP " + r.statusCode() + ") : jeton absent, expiré ou sans droit de lecture", null);
            }
            if (r.statusCode() != 200) {
                throw new UnavailableException("Réponse HTTP " + r.statusCode(), null);
            }
            return mapper.readTree(r.body());
        } catch (IOException e) {
            throw new UnavailableException("Outil injoignable", e);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            throw new UnavailableException("Requête interrompue", e);
        } catch (IllegalArgumentException e) {
            throw new UnavailableException("Adresse invalide", e);
        }
    }

    private static Map<String, String> labelsOf(JsonNode node) {
        Map<String, String> m = new TreeMap<>();
        node.fields().forEachRemaining(e -> {
            if (!"__name__".equals(e.getKey()) && !"alertstate".equals(e.getKey())) {
                m.put(e.getKey(), e.getValue().asText());
            }
        });
        return m;
    }

    /** Étiquettes affichées : au plus 12, valeurs tronquées. */
    private static Map<String, String> safeLabels(Map<String, String> labels) {
        Map<String, String> out = new LinkedHashMap<>();
        labels.forEach((k, v) -> {
            if (out.size() < 12) {
                out.put(k, v.length() > 120 ? v.substring(0, 119) + "…" : v);
            }
        });
        return out;
    }

    private static String fingerprint(Map<String, String> labels) {
        return labels.toString();
    }

    private static Instant parseInstant(String iso) {
        try {
            return iso == null || iso.isBlank() ? null : Instant.parse(iso);
        } catch (RuntimeException e) {
            return null;
        }
    }

    private static LocalDateTime time(Long epochSeconds) {
        return epochSeconds == null ? null : LocalDateTime.ofInstant(Instant.ofEpochSecond(epochSeconds), ZoneId.systemDefault());
    }

    private static LocalDateTime time(Instant i) {
        return i == null ? null : LocalDateTime.ofInstant(i, ZoneId.systemDefault());
    }

    private LocalDateTime resolvedAt(Incident i) {
        return history.findByIncidentId(i.getId()).stream().filter(h -> h.getStatus() == IncidentStatus.RESOLVED)
                .map(h -> h.getCreatedAt()).min(Comparator.naturalOrder()).orElse(null);
    }

    private Incident requireIncident(Long id) {
        return incidents.findById(id).orElseThrow(() -> new ResourceNotFoundException("Incident introuvable : " + id));
    }
}
