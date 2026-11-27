package com.orange.pfe.backend.service.supervision;

import com.fasterxml.jackson.databind.JsonNode;
import com.orange.pfe.backend.dto.supervision.SupervisionDtos.Correlation;
import com.orange.pfe.backend.dto.supervision.SupervisionDtos.ErrorPattern;
import com.orange.pfe.backend.dto.supervision.SupervisionDtos.HostStat;
import com.orange.pfe.backend.dto.supervision.SupervisionDtos.HostsOverview;
import com.orange.pfe.backend.dto.supervision.SupervisionDtos.IncidentMark;
import com.orange.pfe.backend.dto.supervision.SupervisionDtos.LogGroup;
import com.orange.pfe.backend.dto.supervision.SupervisionDtos.LogStats;
import com.orange.pfe.backend.dto.supervision.SupervisionDtos.MeaningEntry;
import com.orange.pfe.backend.dto.supervision.SupervisionDtos.Metric;
import com.orange.pfe.backend.dto.supervision.SupervisionDtos.Point;
import com.orange.pfe.backend.dto.supervision.SupervisionDtos.SeriesLine;
import com.orange.pfe.backend.dto.supervision.SupervisionDtos.SeriesResponse;
import com.orange.pfe.backend.dto.supervision.SupervisionDtos.TargetStat;
import com.orange.pfe.backend.dto.supervision.SupervisionDtos.TopErrors;
import com.orange.pfe.backend.model.core.Incident;
import com.orange.pfe.backend.repository.SupervisionIncidentReadRepository;
import com.orange.pfe.backend.service.supervision.SupervisionClient.UnavailableException;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.Instant;
import java.time.LocalDateTime;
import java.time.ZoneId;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.TreeMap;
import java.util.concurrent.ConcurrentHashMap;
import java.util.function.Supplier;
import java.util.regex.Pattern;

/**
 * Supervision en LECTURE SEULE pour tous les utilisateurs connectés : métriques Prometheus, statistiques de logs Loki
 * (avec leur signification), et rapprochement avec les incidents HELIOS. Toutes les requêtes sont des modèles FIXES
 * construits ici ; seules des valeurs bornées (période, nombre de lignes, métrique de l'énumération) viennent de
 * l'appelant. Un outil injoignable donne une réponse « indisponible », jamais une erreur serveur. Les réponses réussies
 * sont gardées 15 s pour ne pas multiplier les requêtes vers Prometheus et Loki quand plusieurs personnes consultent.
 */
@Service
public class SupervisionService {

    static final int MIN_MINUTES = 5;
    static final int MAX_MINUTES = 1440;
    /** Loki lit chaque ligne de la période : au-delà de 3 h la requête devient trop lente pour une consultation interactive. */
    static final int MAX_LOG_MINUTES = 180;
    private static final long CACHE_MS = 15_000L;
    private static final long LOG_CACHE_MS = 60_000L;

    /** Machines connues (adresse → nom) ; une adresse inconnue est affichée telle quelle. */
    private static final Map<String, String> HOST_NAMES = Map.of(
            "192.168.148.153", "k8s-master", "192.168.148.155", "k8s-worker2", "192.168.148.158", "k8s-worker1",
            "192.168.148.160", "k8s-monitoring", "192.168.148.161", "k8s-aiCamunda", "192.168.148.162", "k8s-lb2");

    private static final String CPU_QL = "100-(avg by(instance)(rate(node_cpu_seconds_total{mode=\"idle\"}[5m]))*100)";
    private static final String MEM_QL = "(1-node_memory_MemAvailable_bytes/node_memory_MemTotal_bytes)*100";
    private static final String DISK_QL = "(1-node_filesystem_avail_bytes{mountpoint=\"/\",fstype!~\"tmpfs|overlay\"}"
            + "/node_filesystem_size_bytes{mountpoint=\"/\",fstype!~\"tmpfs|overlay\"})*100";

    /** Lignes d'erreur et d'avertissement ; les lignes de journal des requêtes de Loki lui-même sont exclues (bruit). */
    private static final String ERR_RE = "(?i)(level=(error|fatal|crit)|\\\\berror\\\\b|\\\\bfatal\\\\b|\\\\bpanic\\\\b|\\\\bfailed\\\\b)";
    private static final String WARN_RE = "(?i)(level=warn|\\\\bwarn(ing)?\\\\b)";
    private static final String NOISE = " != \"caller=metrics.go\" != \"level=info\" !~ \" I[0-9]{4} \"";
    private static final String ALL = "{job=~\".+\"}";
    private static final String ERRORS = ALL + " |~ \"" + ERR_RE + "\"" + NOISE;
    private static final String WARNINGS = ALL + " |~ \"" + WARN_RE + "\"" + NOISE;

    private final SupervisionClient client;
    private final SupervisionIncidentReadRepository incidents;
    private final Map<String, Cached> cache = new ConcurrentHashMap<>();

    private record Cached(long at, Object value) {}

    public SupervisionService(SupervisionClient client, SupervisionIncidentReadRepository incidents) {
        this.client = client;
        this.incidents = incidents;
    }

    // ── Machines et cibles ────────────────────────────────────────────────────

    public HostsOverview hosts() {
        return cached("hosts", () -> {
            try {
                Map<String, Double> cpu = byInstance(client.promInstant(CPU_QL));
                Map<String, Double> mem = byInstance(client.promInstant(MEM_QL));
                Map<String, Double> disk = byInstance(client.promInstant(DISK_QL));
                Map<String, Double> up = byInstance(client.promInstant("up{job=\"vm-node-exporters\"}"));
                List<HostStat> hosts = up.keySet().stream().sorted().map(inst -> new HostStat(inst, nameOf(inst),
                        up.get(inst) != null && up.get(inst) >= 1, cpu.get(inst), mem.get(inst), disk.get(inst))).toList();
                Map<String, int[]> perJob = new TreeMap<>();
                for (JsonNode r : client.promInstant("up")) {
                    int[] c = perJob.computeIfAbsent(r.path("metric").path("job").asText("?"), k -> new int[2]);
                    c[1]++;
                    if (value(r) >= 1) {
                        c[0]++;
                    }
                }
                List<TargetStat> targets = perJob.entrySet().stream().map(e -> new TargetStat(e.getKey(), e.getValue()[0], e.getValue()[1])).toList();
                return new HostsOverview(true, null, LocalDateTime.now(), hosts, targets);
            } catch (UnavailableException e) {
                return new HostsOverview(false, e.getMessage(), LocalDateTime.now(), List.of(), List.of());
            }
        }, HostsOverview::available);
    }

    public SeriesResponse series(Metric metric, int minutes) {
        int m = clamp(minutes);
        int step = stepFor(m);
        return cached("series:" + metric + ":" + m, () -> {
            try {
                long end = Instant.now().getEpochSecond();
                String ql = switch (metric) { case CPU -> CPU_QL; case MEMORY -> MEM_QL; case DISK -> DISK_QL; };
                List<SeriesLine> lines = new ArrayList<>();
                for (JsonNode r : client.promRange(ql, end - m * 60L, end, step)) {
                    String inst = stripPort(r.path("metric").path("instance").asText("?"));
                    lines.add(new SeriesLine(inst, nameOf(inst), points(r.path("values"), 1000L)));
                }
                lines.sort(Comparator.comparing(SeriesLine::instance));
                return new SeriesResponse(true, null, metric, "%", m, step, lines);
            } catch (UnavailableException e) {
                return new SeriesResponse(false, e.getMessage(), metric, "%", m, step, List.of());
            }
        }, SeriesResponse::available);
    }

    // ── Logs ──────────────────────────────────────────────────────────────────

    public LogStats logStats(int minutes) {
        int m = clampLogs(minutes);
        return cached("logstats:" + m, () -> {
            try {
                long now = Instant.now().getEpochSecond();
                String w = "[" + m + "m]";
                String sys = "{job=~\"vm-syslog|node-syslog\"}";
                String pods = "{job=\"k8s-containers\"}";
                // 9 requêtes indépendantes, lancées en parallèle (Loki lit chaque ligne de la période : en série ce serait trop lent)
                List<JsonNode> r = parallel(List.of(
                        "sum(count_over_time(" + ALL + w + "))",
                        "sum(count_over_time(" + ERRORS + w + "))",
                        "sum(count_over_time(" + WARNINGS + w + "))",
                        "sum by (host) (count_over_time(" + sys + w + "))",
                        "sum by (host) (count_over_time(" + sys + " |~ \"" + ERR_RE + "\"" + NOISE + w + "))",
                        "sum by (host) (count_over_time(" + sys + " |~ \"" + WARN_RE + "\"" + NOISE + w + "))",
                        "sum by (filename) (count_over_time(" + pods + w + "))",
                        "sum by (filename) (count_over_time(" + pods + " |~ \"" + ERR_RE + "\"" + NOISE + w + "))",
                        "sum by (filename) (count_over_time(" + pods + " |~ \"" + WARN_RE + "\"" + NOISE + w + "))"), now);
                Map<String, long[]> byHost = new TreeMap<>();
                for (int i = 0; i < 3; i++) {
                    fill(byHost, r.get(3 + i), "host", i);
                }
                Map<String, long[]> byApp = new TreeMap<>();
                for (int i = 0; i < 3; i++) {
                    fillApps(byApp, r.get(6 + i), i);
                }
                return new LogStats(true, null, m, LocalDateTime.now(), sum(r.get(0)), sum(r.get(1)), sum(r.get(2)),
                        groups(byHost, 50), groups(byApp, 50));
            } catch (UnavailableException e) {
                return new LogStats(false, e.getMessage(), m, LocalDateTime.now(), 0, 0, 0, List.of(), List.of());
            }
        }, LogStats::available);
    }

    private static final java.util.concurrent.ExecutorService POOL = java.util.concurrent.Executors.newFixedThreadPool(5, r -> {
        Thread t = new Thread(r, "supervision-query");
        t.setDaemon(true);
        return t;
    });

    /** Exécute plusieurs requêtes Loki instantanées en parallèle, résultats dans l'ordre ; une panne remonte telle quelle. */
    private List<JsonNode> parallel(List<String> queries, long now) {
        List<java.util.concurrent.Future<JsonNode>> futures = queries.stream()
                .map(q -> POOL.submit(() -> client.lokiInstant(q, now))).toList();
        List<JsonNode> out = new ArrayList<>();
        try {
            for (java.util.concurrent.Future<JsonNode> f : futures) {
                out.add(f.get());
            }
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            throw new UnavailableException("Loki : requête interrompue", e);
        } catch (java.util.concurrent.ExecutionException e) {
            if (e.getCause() instanceof UnavailableException u) {
                throw u;
            }
            throw new UnavailableException("Loki : réponse inexploitable", e);
        }
        return out;
    }

    public TopErrors topErrors(int minutes, int limit) {
        int m = clampLogs(minutes);
        int n = Math.max(1, Math.min(limit, 20));
        return cached("toperrors:" + m + ":" + n, () -> {
            try {
                long end = Instant.now().getEpochSecond();
                JsonNode streams = client.lokiRange(ERRORS, end - m * 60L, end, 0, 1000);
                Map<String, ErrorAcc> groups = new LinkedHashMap<>();
                for (JsonNode s : streams) {
                    String host = s.path("stream").path("host").asText(s.path("stream").path("filename").asText(""));
                    for (JsonNode v : s.path("values")) {
                        String line = v.path(1).asText("");
                        String key = normalize(line);
                        groups.computeIfAbsent(key, k -> new ErrorAcc(line)).add(host);
                    }
                }
                List<ErrorPattern> patterns = groups.entrySet().stream()
                        .sorted((a, b) -> Long.compare(b.getValue().count, a.getValue().count)).limit(n)
                        .map(e -> {
                            LogMeaningCatalog.Meaning mean = LogMeaningCatalog.classify(e.getValue().example);
                            return new ErrorPattern(e.getKey(), e.getValue().count, e.getValue().topHost(), mean.category(), mean.label(),
                                    mean.meaning(), mean.advice(), truncate(e.getValue().example, 300));
                        }).toList();
                return new TopErrors(true, null, m, patterns);
            } catch (UnavailableException e) {
                return new TopErrors(false, e.getMessage(), m, List.of());
            }
        }, TopErrors::available);
    }

    public List<MeaningEntry> meanings() {
        return LogMeaningCatalog.all().stream().map(x -> new MeaningEntry(x.category(), x.label(), x.meaning(), x.advice())).toList();
    }

    // ── Rapprochement avec les incidents ──────────────────────────────────────

    @Transactional(readOnly = true)
    public Correlation correlation(int minutes) {
        int m = clampLogs(minutes);
        int step = stepFor(m);
        // les séries (lentes, gardées 60 s) sont séparées des incidents (lus à chaque appel, toujours à jour)
        Correlation base = cached("correlation:" + m, () -> {
            try {
                long end = Instant.now().getEpochSecond();
                long start = end - m * 60L;
                List<Point> errors = new ArrayList<>();
                for (JsonNode r : client.lokiRange("sum(count_over_time(" + ERRORS + "[" + step + "s]))", start, end, step, 0)) {
                    errors.addAll(points(r.path("values"), 1000L));
                }
                List<Point> cpu = new ArrayList<>();
                for (JsonNode r : client.promRange("max(" + CPU_QL + ")", start, end, step)) {
                    cpu.addAll(points(r.path("values"), 1000L));
                }
                return new Correlation(true, null, m, step, errors, cpu, List.of());
            } catch (UnavailableException e) {
                return new Correlation(false, e.getMessage(), m, step, List.of(), List.of(), List.of());
            }
        }, Correlation::available);
        return new Correlation(base.available(), base.error(), m, step, base.errorSeries(), base.cpuSeries(),
                marks(base.errorSeries(), LocalDateTime.now().minusMinutes(m)));
    }

    private List<IncidentMark> marks(List<Point> errorSeries, LocalDateTime since) {
        List<IncidentMark> out = new ArrayList<>();
        for (Incident i : incidents.findByCreatedAtAfterOrderByCreatedAtAsc(since)) {
            if (i.getCreatedAt() == null) {
                continue;
            }
            long at = i.getCreatedAt().atZone(ZoneId.systemDefault()).toInstant().toEpochMilli();
            long around = errorSeries.stream().filter(p -> Math.abs(p.t() - at) <= 5 * 60_000L).mapToLong(p -> (long) p.v()).sum();
            out.add(new IncidentMark(i.getId(), i.getIncidentType(), i.getApp(),
                    i.getSeverity() == null ? null : i.getSeverity().name(), i.getStatus() == null ? null : i.getStatus().name(),
                    i.getCreatedAt(), around));
        }
        return out;
    }

    // ── Aides ─────────────────────────────────────────────────────────────────

    /** Période d'une requête Loki : bornée à 3 h (au-delà, la lecture de toutes les lignes est trop lente). */
    static int clampLogs(int minutes) {
        return Math.min(clamp(minutes), MAX_LOG_MINUTES);
    }

    static int clamp(int minutes) {
        return Math.max(MIN_MINUTES, Math.min(minutes, MAX_MINUTES));
    }

    static int stepFor(int minutes) {
        return (int) Math.max(15, Math.min(600, minutes * 60L / 120));
    }

    static String nameOf(String instance) {
        String ip = stripPort(instance);
        return HOST_NAMES.getOrDefault(ip, ip);
    }

    static String stripPort(String instance) {
        int c = instance.lastIndexOf(':');
        return c > 0 ? instance.substring(0, c) : instance;
    }

    private static double value(JsonNode r) {
        return r.path("value").path(1).asDouble(Double.NaN);
    }

    private static Map<String, Double> byInstance(JsonNode result) {
        Map<String, Double> m = new LinkedHashMap<>();
        for (JsonNode r : result) {
            double v = value(r);
            if (!Double.isNaN(v)) {
                m.put(r.path("metric").path("instance").asText("?"), v);
            }
        }
        return m;
    }

    private static List<Point> points(JsonNode values, long timeFactor) {
        List<Point> out = new ArrayList<>();
        for (JsonNode p : values) {
            double v = p.path(1).asDouble(Double.NaN);
            if (!Double.isNaN(v)) {
                out.add(new Point((long) (p.path(0).asDouble() * timeFactor), v));
            }
        }
        return out;
    }

    private static long sum(JsonNode result) {
        long s = 0;
        for (JsonNode r : result) {
            s += (long) value(r);
        }
        return s;
    }

    private static void fill(Map<String, long[]> into, JsonNode result, String label, int slot) {
        for (JsonNode r : result) {
            String key = r.path("metric").path(label).asText("");
            if (!key.isEmpty()) {
                into.computeIfAbsent(key, k -> new long[3])[slot] = (long) value(r);
            }
        }
    }

    /** Nom d'application d'un fichier de conteneur : {@code /var/log/pods/<namespace>_<pod>_<uid>/<conteneur>/0.log}. */
    static String appOf(String filename) {
        // format des liens de conteneurs : /var/log/containers/<pod>_<namespace>_<conteneur>-<id 64 hex>.log
        java.util.regex.Matcher c = CONTAINER_FILE.matcher(filename);
        if (c.matches()) {
            return c.group(2) + "/" + c.group(3);
        }
        String[] parts = filename.split("/");
        for (int i = 0; i < parts.length; i++) {
            if ("pods".equals(parts[i]) && i + 1 < parts.length) {
                String[] seg = parts[i + 1].split("_");
                if (seg.length >= 2) {
                    String container = i + 2 < parts.length ? parts[i + 2] : seg[1];
                    return seg[0] + "/" + container;
                }
            }
        }
        return filename;
    }

    private static void fillApps(Map<String, long[]> into, JsonNode result, int slot) {
        for (JsonNode r : result) {
            String app = appOf(r.path("metric").path("filename").asText(""));
            if (!app.isEmpty()) {
                into.computeIfAbsent(app, k -> new long[3])[slot] += (long) value(r);
            }
        }
    }

    private static List<LogGroup> groups(Map<String, long[]> m, int limit) {
        return m.entrySet().stream().map(e -> new LogGroup(e.getKey(), e.getValue()[0], e.getValue()[1], e.getValue()[2]))
                .sorted((a, b) -> a.errors() != b.errors() ? Long.compare(b.errors(), a.errors()) : Long.compare(b.total(), a.total()))
                .limit(limit).toList();
    }

    private static final Pattern TS = Pattern.compile("^\\S*\\d{4}-\\d{2}-\\d{2}[T ]\\d{2}:\\d{2}:\\d{2}\\S*\\s*");
    private static final Pattern KV_TS = Pattern.compile("\\bts=\\S+");
    private static final Pattern UUID = Pattern.compile("\\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\\b");
    private static final Pattern HEX = Pattern.compile("\\b[0-9a-f]{12,}\\b");
    private static final Pattern IP = Pattern.compile("\\b\\d{1,3}(\\.\\d{1,3}){3}(:\\d+)?\\b");
    private static final Pattern NUM = Pattern.compile("\\b\\d+(\\.\\d+)?(ms|s|m|h|µs|ns)?\\b");
    private static final Pattern PID = Pattern.compile("\\[\\d+]");
    private static final Pattern KLOG = Pattern.compile("\\b[EWIF]\\d{4}\\b");
    private static final Pattern CONTAINER_FILE = Pattern.compile("^.*/containers/([^_/]+)_([^_/]+)_(.+)-[0-9a-f]{64}\\.log$");

    /** Retire horodatage, identifiants et nombres pour regrouper des lignes qui ne diffèrent que par ces détails. */
    static String normalize(String line) {
        String s = TS.matcher(line == null ? "" : line).replaceFirst("");
        s = KV_TS.matcher(s).replaceAll("ts=<t>");
        s = UUID.matcher(s).replaceAll("<id>");
        s = HEX.matcher(s).replaceAll("<id>");
        s = IP.matcher(s).replaceAll("<ip>");
        s = PID.matcher(s).replaceAll("[<pid>]");
        s = KLOG.matcher(s).replaceAll("<klog>");
        s = NUM.matcher(s).replaceAll("<n>");
        s = s.replaceAll("\\s+", " ").trim();
        return truncate(s, 200);
    }

    private static String truncate(String s, int max) {
        return s.length() <= max ? s : s.substring(0, max - 1) + "…";
    }

    private static final class ErrorAcc {
        final String example;
        long count;
        final Map<String, Long> hosts = new LinkedHashMap<>();

        ErrorAcc(String example) {
            this.example = example;
        }

        void add(String host) {
            count++;
            hosts.merge(host == null ? "" : host, 1L, Long::sum);
        }

        String topHost() {
            return hosts.entrySet().stream().max(Map.Entry.comparingByValue()).map(Map.Entry::getKey).orElse("");
        }
    }

    @SuppressWarnings("unchecked")
    private <T> T cached(String key, Supplier<T> loader, java.util.function.Predicate<T> cacheable) {
        long now = System.currentTimeMillis();
        Cached c = cache.get(key);
        long ttl = key.startsWith("logstats") || key.startsWith("toperrors") || key.startsWith("correlation") ? LOG_CACHE_MS : CACHE_MS;
        if (c != null && now - c.at < ttl) {
            return (T) c.value;
        }
        T value = loader.get();
        if (cacheable.test(value)) {
            cache.put(key, new Cached(now, value));
        }
        return value;
    }
}
