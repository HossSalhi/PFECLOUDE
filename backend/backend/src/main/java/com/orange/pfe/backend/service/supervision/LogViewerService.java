package com.orange.pfe.backend.service.supervision;

import com.fasterxml.jackson.databind.JsonNode;
import com.orange.pfe.backend.dto.supervision.SupervisionDtos.LogLineView;
import com.orange.pfe.backend.dto.supervision.SupervisionDtos.LogLines;
import com.orange.pfe.backend.dto.supervision.SupervisionDtos.LogSource;
import com.orange.pfe.backend.dto.supervision.SupervisionDtos.LogSources;
import com.orange.pfe.backend.service.supervision.SupervisionClient.UnavailableException;
import org.springframework.stereotype.Service;

import java.time.Instant;
import java.time.LocalDateTime;
import java.time.ZoneId;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.List;
import java.util.Set;
import java.util.TreeSet;
import java.util.regex.Pattern;

/**
 * Visualiseur de logs en LECTURE SEULE (réservé aux administrateurs par {@code SecurityConfig}) : les vraies lignes de Loki
 * pour UNE machine ou UNE application, filtrées par niveau et par mot. Les requêtes sont des modèles FIXES : la source est
 * validée, le mot cherché est échappé (jamais interprété comme une expression), la période et le nombre de lignes sont bornés.
 * Les secrets visibles dans une ligne (jetons, mots de passe, clés) sont masqués avant l'envoi. Loki injoignable : réponse
 * « indisponible », jamais d'erreur serveur.
 */
@Service
public class LogViewerService {

    static final int MAX_LINES = 200;
    static final int MIN_MINUTES = 5;
    static final int MAX_MINUTES = 180;
    private static final long CACHE_MS = 60_000L;

    /** Machines collectées par Loki (étiquette {@code host}). */
    private static final Set<String> HOST_NAMES = Set.of("master", "worker1", "worker2", "k8s-monitoring", "k8s-aiCamunda");

    private static final String ERR_RE = "(?i)(level=(error|fatal|crit)|\\\\berror\\\\b|\\\\bfatal\\\\b|\\\\bpanic\\\\b|\\\\bfailed\\\\b)";
    private static final String WARN_RE = "(?i)(level=warn|\\\\bwarn(ing)?\\\\b)";
    private static final String NOISE = " != \"caller=metrics.go\" != \"level=info\" !~ \" I[0-9]{4} \"";
    private static final Pattern HOST_KEY = Pattern.compile("^host:([A-Za-z0-9._-]{1,40})$");
    private static final Pattern APP_KEY = Pattern.compile("^app:([a-z0-9]([-a-z0-9]{0,61}[a-z0-9])?)/([a-z0-9]([-a-z0-9]{0,61}[a-z0-9])?)$");
    private static final Pattern ERR_JAVA = Pattern.compile("(?i)(level=(error|fatal|crit)|\\berror\\b|\\bfatal\\b|\\bpanic\\b|\\bfailed\\b)");
    private static final Pattern WARN_JAVA = Pattern.compile("(?i)(level=warn|\\bwarn(ing)?\\b)");
    private static final Pattern JWT = Pattern.compile("eyJ[A-Za-z0-9_-]{10,}\\.[A-Za-z0-9_-]{5,}\\.[A-Za-z0-9_-]{5,}");
    /** « Bearer xxx » ou « Basic xxx » : le jeton est masqué, le mot de schéma est conservé. */
    private static final Pattern SCHEME = Pattern.compile("(?i)\\b(bearer|basic)\\s+[A-Za-z0-9._~+/=-]{6,}");
    /** « mot-clé = valeur » ou « mot-clé: valeur » : la valeur est masquée (sauf si c'est déjà un schéma masqué). */
    private static final Pattern SECRET = Pattern.compile(
            "(?i)(authorization|token|password|passwd|secret|api[-_]?key|apikey|credential)(\"?\\s*[=:]\\s*\"?)(?!(?:\\*\\*\\*|bearer|basic)\\b)([^\\s\",;&}]{4,})");

    private final SupervisionClient client;
    private volatile long sourcesAt;
    private volatile LogSources cachedSources;

    public LogViewerService(SupervisionClient client) {
        this.client = client;
    }

    // ── Sources ───────────────────────────────────────────────────────────────

    public LogSources sources() {
        long now = System.currentTimeMillis();
        LogSources c = cachedSources;
        if (c != null && c.available() && now - sourcesAt < CACHE_MS) {
            return c;
        }
        try {
            long end = Instant.now().getEpochSecond();
            long start = end - 3600;
            List<LogSource> out = new ArrayList<>();
            Set<String> hosts = new TreeSet<>();
            for (JsonNode h : client.lokiLabelValues("host", start, end)) {
                if (HOST_NAMES.contains(h.asText())) {
                    hosts.add(h.asText());
                }
            }
            hosts.forEach(h -> out.add(new LogSource("host:" + h, "Machine " + h, "HOST")));
            Set<String> apps = new TreeSet<>();
            for (JsonNode f : client.lokiLabelValues("filename", start, end)) {
                String app = SupervisionService.appOf(f.asText());
                if (!app.startsWith("/") && APP_KEY.matcher("app:" + app).matches()) {
                    apps.add(app);
                }
            }
            apps.stream().limit(150).forEach(a -> out.add(new LogSource("app:" + a, "Application " + a, "APP")));
            LogSources result = new LogSources(true, null, out);
            cachedSources = result;
            sourcesAt = now;
            return result;
        } catch (UnavailableException e) {
            return new LogSources(false, e.getMessage(), List.of());
        }
    }

    // ── Lignes ────────────────────────────────────────────────────────────────

    /** Lignes d'une source ({@code host:worker1} ou {@code app:pfe/backend}) ; les plus récentes d'abord. */
    public LogLines lines(String source, String level, String q, int minutes, int limit) {
        String selector = selectorOf(source);
        String lvl = level == null ? "ALL" : level.trim().toUpperCase();
        if (!Set.of("ALL", "ERROR", "WARN").contains(lvl)) {
            throw new IllegalArgumentException("Niveau inconnu : " + level);
        }
        String text = q == null ? "" : q.trim();
        if (text.length() > 80 || text.chars().anyMatch(ch -> ch < 32 || ch == 127)) {
            throw new IllegalArgumentException("Texte recherché invalide (80 caractères au plus, sans caractère de contrôle)");
        }
        int m = Math.max(MIN_MINUTES, Math.min(minutes, MAX_MINUTES));
        int n = Math.max(1, Math.min(limit, MAX_LINES));
        StringBuilder logql = new StringBuilder(selector);
        if ("ERROR".equals(lvl)) {
            logql.append(" |~ \"").append(ERR_RE).append('"').append(NOISE);
        } else if ("WARN".equals(lvl)) {
            logql.append(" |~ \"").append(WARN_RE).append('"').append(NOISE);
        }
        if (!text.isEmpty()) {
            logql.append(" |~ \"(?i)").append(logqlString(regexEscape(text))).append('"');
        }
        long end = Instant.now().getEpochSecond();
        LocalDateTime from = LocalDateTime.ofInstant(Instant.ofEpochSecond(end - m * 60L), ZoneId.systemDefault());
        LocalDateTime to = LocalDateTime.ofInstant(Instant.ofEpochSecond(end), ZoneId.systemDefault());
        try {
            JsonNode streams = client.lokiRange(logql.toString(), end - m * 60L, end, 0, n);
            List<LogLineView> lines = new ArrayList<>();
            for (JsonNode s : streams) {
                for (JsonNode v : s.path("values")) {
                    String raw = v.path(1).asText("");
                    LogMeaningCatalog.Meaning known = LogMeaningCatalog.classify(raw);
                    boolean error = ERR_JAVA.matcher(raw).find() || known != LogMeaningCatalog.OTHER;
                    String levelName = error ? "ERROR" : WARN_JAVA.matcher(raw).find() ? "WARN" : "INFO";
                    long seconds = Long.parseLong(v.path(0).asText("0")) / 1_000_000_000L;
                    lines.add(new LogLineView(LocalDateTime.ofInstant(Instant.ofEpochSecond(seconds), ZoneId.systemDefault()),
                            mask(raw.length() > 800 ? raw.substring(0, 799) + "…" : raw), levelName,
                            error ? known.category() : null, error ? known.label() : null));
                }
            }
            lines.sort(Comparator.comparing(LogLineView::at).reversed());
            List<LogLineView> shown = lines.size() > n ? lines.subList(0, n) : lines;
            return new LogLines(true, null, source, lvl, text, m, from, to, lines.size() >= n, shown.size(), shown);
        } catch (UnavailableException e) {
            return new LogLines(false, e.getMessage(), source, lvl, text, m, from, to, false, 0, List.of());
        }
    }

    // ── Règles internes ───────────────────────────────────────────────────────

    /** Sélecteur LogQL d'une source VALIDÉE ; toute autre valeur est refusée. */
    static String selectorOf(String source) {
        if (source != null) {
            var h = HOST_KEY.matcher(source);
            if (h.matches()) {
                if (!HOST_NAMES.contains(h.group(1))) {
                    throw new IllegalArgumentException("Machine inconnue : " + h.group(1));
                }
                return "{job=~\"vm-syslog|node-syslog\", host=\"" + h.group(1) + "\"}";
            }
            var a = APP_KEY.matcher(source);
            if (a.matches()) {
                return "{job=\"k8s-containers\", filename=~\".*_" + a.group(1) + "_" + a.group(3) + "-[0-9a-f]{64}.*\"}";
            }
        }
        throw new IllegalArgumentException("Source de logs invalide");
    }

    /** Échappe les caractères spéciaux d'une expression régulière : le texte cherché est toujours pris littéralement. */
    static String regexEscape(String text) {
        StringBuilder sb = new StringBuilder();
        for (char c : text.toCharArray()) {
            if ("\\.[]{}()*+?^$|".indexOf(c) >= 0) {
                sb.append('\\');
            }
            sb.append(c);
        }
        return sb.toString();
    }

    /** Prépare un texte pour l'intérieur d'une chaîne LogQL entre guillemets (antislash et guillemet doublés). */
    static String logqlString(String text) {
        return text.replace("\\", "\\\\").replace("\"", "\\\"");
    }

    /** Masque les secrets visibles dans une ligne de log. */
    static String mask(String line) {
        String s = JWT.matcher(line).replaceAll("***");
        s = SCHEME.matcher(s).replaceAll("$1 ***");
        return SECRET.matcher(s).replaceAll("$1$2***");
    }
}
