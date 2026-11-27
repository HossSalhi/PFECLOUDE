package com.orange.pfe.backend.service.aiassist.telemetry;

import com.fasterxml.jackson.databind.JsonNode;
import com.orange.pfe.backend.model.team.ResourceScope;
import com.orange.pfe.backend.service.aiassist.telemetry.TelemetryModels.Detection;
import com.orange.pfe.backend.service.aiassist.telemetry.TelemetryModels.EntityCoverage;
import com.orange.pfe.backend.service.aiassist.telemetry.TelemetryModels.Finding;
import com.orange.pfe.backend.service.aiassist.telemetry.TelemetryModels.QueryTrace;
import com.orange.pfe.backend.service.aiassist.telemetry.TelemetryModels.SourceQuality;
import com.orange.pfe.backend.service.aiassist.telemetry.TelemetryModels.Trace;
import com.orange.pfe.backend.service.aiassist.telemetry.TelemetryRules.Series;
import com.orange.pfe.backend.service.aiassist.telemetry.TelemetryRules.Thresholds;
import com.orange.pfe.backend.service.supervision.SupervisionClient;
import org.springframework.stereotype.Component;

import java.time.Instant;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.TreeMap;
import java.util.TreeSet;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.CompletionException;
import java.util.concurrent.ExecutionException;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * Détection d'anomalies sur l'historique des métriques (Prometheus) et des logs (Loki), par RÈGLES déterministes. Lecture
 * seule : mêmes requêtes fixes que la page Supervision (aucune requête ne vient du navigateur).
 *
 * <p>Principe central : une absence de MESURE n'est jamais une absence d'ANOMALIE. « Aucune anomalie » n'est rendu que si
 * toutes les sources demandées répondent ET si chaque entité pertinente atteint 80 % de couverture (machines connues sans
 * mesure et sources de logs devenues silencieuses comprises). Chaque requête qui échoue est nommée avec son motif.</p>
 */
@Component
public class TelemetryDetector {

    static final Map<String, String> HOSTS = Map.of(
            "192.168.148.153", "k8s-master", "192.168.148.155", "k8s-worker2", "192.168.148.158", "k8s-worker1",
            "192.168.148.160", "k8s-monitoring", "192.168.148.161", "k8s-aiCamunda", "192.168.148.162", "k8s-lb2");

    static final String CPU_QL = "100-(avg by(instance)(rate(node_cpu_seconds_total{mode=\"idle\"}[5m]))*100)";
    static final String MEM_QL = "(1-node_memory_MemAvailable_bytes/node_memory_MemTotal_bytes)*100";
    static final String DISK_QL = "(1-node_filesystem_avail_bytes{mountpoint=\"/\",fstype!~\"tmpfs|overlay\"}"
            + "/node_filesystem_size_bytes{mountpoint=\"/\",fstype!~\"tmpfs|overlay\"})*100";

    /**
     * Filtre volontairement SIMPLE : mesuré sur la plateforme, la version avec les limites de mot et un filtre de bruit en
     * expression régulière était 11 fois plus lente (6,3 s contre 0,56 s pour 2 h) pour les mêmes séries. Le bruit résiduel
     * (lignes d'information qui citent « error ») est stable : sans effet sur la comparaison à la période de référence.
     */
    private static final String ERR_RE = "(?i)(error|fatal|panic|failed|level=crit)";
    private static final String NOISE = " != \"caller=metrics.go\" != \"level=info\" != \"level=debug\"";
    static final String LOG_HOSTS_QL = "sum by (host) (count_over_time({job=~\"vm-syslog|node-syslog\"} |~ \"" + ERR_RE + "\"" + NOISE + "[1h]))";
    static final String LOG_PODS_QL = "sum by (filename) (count_over_time({job=\"k8s-containers\"} |~ \"" + ERR_RE + "\"" + NOISE + "[1h]))";
    /** Contrôle d'ingestion : TOUTES les lignes par heure (sans filtre, rapide : moins de 3 s pour 24 h). */
    static final String LOG_ALL_HOSTS_QL = "sum by (host) (count_over_time({job=~\"vm-syslog|node-syslog\"}[1h]))";
    static final String LOG_ALL_PODS_QL = "sum by (filename) (count_over_time({job=\"k8s-containers\"}[1h]))";
    private static final Pattern CONTAINER_FILE = Pattern.compile("^.*/containers/([^_/]+)_([^_/]+)_(.+)-[0-9a-f]{64}\\.log$");

    /** Prometheus garde 7 jours : au-delà de 6,9 jours en arrière, il n'y a plus de données fiables. */
    static final long METRICS_HISTORY_SECONDS = (long) (6.9 * 86400);
    static final long LOG_WINDOW_MAX_SECONDS = 24 * 3600L;
    static final long LOG_BASELINE_MAX_SECONDS = 12 * 3600L;
    static final int MAX_FINDINGS = 50;

    /** Sous cette couverture une entité est ignorée ; sous 80 % l'analyse est PARTIELLE et le verdict ne peut pas être vert. */
    static final double MIN_COVERAGE = 0.25;
    static final double GOOD_COVERAGE = 0.8;
    /** Une source de conteneurs n'est « pertinente » pour la couverture que si elle écrit au moins ce nombre de lignes par heure. */
    static final double ACTIVE_LINES_PER_HOUR = 20;

    private static final ExecutorService POOL = Executors.newFixedThreadPool(8, r -> {
        Thread t = new Thread(r, "telemetry-detector");
        t.setDaemon(true);
        return t;
    });

    private final SupervisionClient client;

    public TelemetryDetector(SupervisionClient client) {
        this.client = client;
    }

    /** Résultat d'une source : demandée, complète (≥ 80 % partout, aucune requête en échec), exploitable, et sa qualité. */
    private record Outcome(boolean requested, boolean complete, boolean usable, SourceQuality quality) {
        static Outcome notRequested() {
            return new Outcome(false, true, false, null);
        }
    }

    /** Accumulateurs d'une analyse. */
    private static final class Run {
        final List<Finding> findings = new ArrayList<>();
        final List<QueryTrace> queries = new ArrayList<>();
        final List<EntityCoverage> coverage = new ArrayList<>();
        final Set<String> entities = new LinkedHashSet<>();
        Long metricsStep;
        Long logsStep;
        Long baselineStart;
        Long baselineEnd;
        Long logsWindowStart;
        Long logsBaselineStart;
    }

    /**
     * Analyse la fenêtre {@code [now - minutes, now]}. {@code scopes == null} : tous les périmètres ; sinon seulement les
     * entités appartenant à ces périmètres (un chef d'équipe ne reçoit que ceux de sa spécialité).
     */
    public Detection detect(Instant now, int minutes, Set<ResourceScope> scopes) {
        long end = now.getEpochSecond();
        long windowSec = minutes * 60L;
        long windowStart = end - windowSec;
        Run run = new Run();
        List<SourceQuality> quality = new ArrayList<>();

        Outcome metrics = (scopes == null || scopes.contains(ResourceScope.VM))
                ? analyseMetrics(run, end, windowSec, windowStart) : Outcome.notRequested();
        Outcome logs = analyseLogs(run, end, windowSec, scopes);
        for (Outcome o : List.of(metrics, logs)) {
            if (o.requested() && o.quality() != null) {
                quality.add(o.quality());
            }
        }

        run.findings.sort(Comparator.comparingInt((Finding f) -> rank(f.severity())).reversed().thenComparing(Finding::end, Comparator.reverseOrder()));
        List<Finding> kept = run.findings.size() > MAX_FINDINGS ? new ArrayList<>(run.findings.subList(0, MAX_FINDINGS)) : run.findings;

        boolean complete = metrics.complete() && logs.complete();
        boolean usable = metrics.usable() || logs.usable();
        String verdict = !kept.isEmpty() ? TelemetryModels.ANOMALIES
                : complete ? TelemetryModels.NO_ANOMALY
                : usable ? TelemetryModels.PARTIAL_COVERAGE : TelemetryModels.INSUFFICIENT_DATA;
        String dataQuality = complete ? TelemetryModels.DATA_COMPLETE : usable ? TelemetryModels.DATA_PARTIAL : TelemetryModels.DATA_INSUFFICIENT;

        Trace trace = new Trace(TelemetryModels.RULES_VERSION, TelemetryRules.at(windowStart), TelemetryRules.at(end),
                run.baselineStart == null ? null : TelemetryRules.at(run.baselineStart), run.baselineEnd == null ? null : TelemetryRules.at(run.baselineEnd),
                run.metricsStep, run.logsWindowStart == null ? null : TelemetryRules.at(run.logsWindowStart),
                run.logsBaselineStart == null ? null : TelemetryRules.at(run.logsBaselineStart), run.logsStep, thresholds(), run.queries, run.coverage);
        return new Detection(verdict, dataQuality, kept, quality, trace, TelemetryRules.at(windowStart), TelemetryRules.at(end), run.entities.size());
    }

    // ── Métriques ────────────────────────────────────────────────────────────────────────────────────────────────

    private Outcome analyseMetrics(Run run, long end, long windowSec, long windowStart) {
        long available = Math.max(0, windowStart - (end - METRICS_HISTORY_SECONDS));
        long baseSec = Math.min(windowSec, available);
        if (baseSec < Math.max(windowSec / 4, 600)) {
            baseSec = 0;                                         // pas assez d'historique pour une référence fiable
        }
        long rangeStart = windowStart - baseSec;
        long step = Math.max(60, Math.min(3600, ((baseSec + windowSec) / 240 / 60) * 60));
        run.metricsStep = step;
        if (baseSec > 0) {
            run.baselineStart = rangeStart;
            run.baselineEnd = windowStart;
        }
        long rs = rangeStart;

        Map<String, String> queries = new LinkedHashMap<>();
        queries.put("CPU", CPU_QL);
        queries.put("MEMORY", MEM_QL);
        queries.put("DISK", DISK_QL);
        Map<String, CompletableFuture<JsonNode>> calls = new LinkedHashMap<>();
        queries.forEach((signal, ql) -> calls.put(signal, CompletableFuture.supplyAsync(() -> client.promRange(ql, rs, end, step), POOL)));

        Set<String> expectedHosts = new TreeSet<>(HOSTS.values());
        double expected = Math.max(1, (double) windowSec / step);
        List<String> failures = new ArrayList<>();
        List<String> belowGood = new ArrayList<>();
        List<String> silent = new ArrayList<>();
        boolean complete = true, usable = false, anyOk = false;
        double covSum = 0;
        int covN = 0;

        for (Map.Entry<String, CompletableFuture<JsonNode>> e : calls.entrySet()) {
            String signal = e.getKey(), ql = queries.get(signal);
            Thresholds th = switch (signal) { case "CPU" -> TelemetryRules.CPU; case "MEMORY" -> TelemetryRules.MEMORY; default -> TelemetryRules.DISK; };
            JsonNode result;
            try {
                result = e.getValue().join();
            } catch (RuntimeException ex) {
                String reason = reasonOf(ex);
                run.queries.add(new QueryTrace("PROM_" + signal, "Prometheus", signal, ql, "FAILED", reason, step,
                        TelemetryRules.at(rangeStart), TelemetryRules.at(end), null, null));
                failures.add(signal + " (" + reason + ")");
                complete = false;
                for (String host : expectedHosts) {                       // aucune mesure : couverture 0, jamais « tout va bien »
                    run.coverage.add(new EntityCoverage("Prometheus", host, signal, 0.0, "requête en échec : " + reason));
                    run.entities.add(host);
                    covN++;
                }
                continue;
            }
            anyOk = true;
            int points = 0;
            Set<String> present = new TreeSet<>();
            for (JsonNode series : result) {
                String entity = hostName(series.path("metric").path("instance").asText(""));
                Series all = parse(series.path("values"));
                int split = firstAtOrAfter(all.t(), windowStart);
                Series win = new Series(copy(all.t(), split, all.size()), copy(all.v(), split, all.size()));
                double[] base = copy(all.v(), 0, split);
                double coverage = Math.min(1.0, win.size() / expected);
                points += all.size();
                present.add(entity);
                run.entities.add(entity);
                run.coverage.add(new EntityCoverage("Prometheus", entity, signal, round2(coverage), win.size() + " mesure(s) sur " + Math.round(expected) + " attendues"));
                covSum += coverage;
                covN++;
                if (coverage < GOOD_COVERAGE) {
                    complete = false;
                    belowGood.add(entity + " (" + signal + " " + Math.round(coverage * 100) + " %)");
                }
                if (coverage < MIN_COVERAGE) {
                    continue;                                            // trop peu de mesures : aucune conclusion
                }
                usable = true;
                for (Finding f : TelemetryRules.metricFindings("VM", entity, signal, th, step, win, base.length >= 12 ? base : null,
                        "PromQL : " + ql, "DISK".equals(signal))) {
                    run.findings.add(f.withQueryRef("PROM_" + signal));
                }
            }
            for (String host : expectedHosts) {                           // machine connue sans aucune mesure
                if (!present.contains(host)) {
                    complete = false;
                    silent.add(host + " (" + signal + ")");
                    run.entities.add(host);
                    run.coverage.add(new EntityCoverage("Prometheus", host, signal, 0.0, "aucune mesure reçue"));
                    covN++;
                }
            }
            run.queries.add(new QueryTrace("PROM_" + signal, "Prometheus", signal, ql, "OK", null, step, TelemetryRules.at(rangeStart),
                    TelemetryRules.at(end), result.size(), points));
        }

        StringBuilder note = new StringBuilder();
        if (!failures.isEmpty()) {
            note.append("Requête(s) en échec : ").append(String.join(" ; ", failures)).append(". ");
        }
        note.append(baseSec > 0 ? "Référence : " + durationLabel(baseSec) + " précédentes." : "Pas de période de référence (rétention Prometheus : 7 jours) : seuls les dépassements absolus sont évalués.");
        if (!silent.isEmpty()) {
            note.append(" Aucune mesure reçue (absence de mesure, pas absence d'anomalie) : ").append(preview(silent)).append('.');
        }
        if (!belowGood.isEmpty()) {
            note.append(" Couverture inférieure à 80 % (mesures manquantes, jamais comptées comme anomalie) : ").append(preview(belowGood)).append('.');
        }
        SourceQuality quality = new SourceQuality("Prometheus (CPU, mémoire, disque)", anyOk, covN == 0 ? 0 : round2(covSum / covN), note.toString().trim());
        return new Outcome(true, complete, usable, quality);
    }

    // ── Logs ─────────────────────────────────────────────────────────────────────────────────────────────────────

    private Outcome analyseLogs(Run run, long end, long windowSec, Set<ResourceScope> scopes) {
        long logWindow = Math.max(3600, Math.min(windowSec, LOG_WINDOW_MAX_SECONDS));
        long logBase = Math.min(logWindow, LOG_BASELINE_MAX_SECONDS);
        long lStart = end - logWindow;
        long rangeStart = lStart - logBase;
        run.logsStep = 3600L;
        run.logsWindowStart = lStart;
        run.logsBaselineStart = rangeStart;

        Map<String, String> queries = new LinkedHashMap<>();
        queries.put("LOKI_ERRORS_HOSTS", LOG_HOSTS_QL);
        queries.put("LOKI_ERRORS_PODS", LOG_PODS_QL);
        queries.put("LOKI_LINES_HOSTS", LOG_ALL_HOSTS_QL);
        queries.put("LOKI_LINES_PODS", LOG_ALL_PODS_QL);
        Map<String, CompletableFuture<JsonNode>> calls = new LinkedHashMap<>();
        queries.forEach((id, ql) -> calls.put(id, CompletableFuture.supplyAsync(() -> client.lokiRange(ql, rangeStart, end, 3600, 0), POOL)));

        Map<String, JsonNode> results = new LinkedHashMap<>();
        List<String> failures = new ArrayList<>();
        for (Map.Entry<String, CompletableFuture<JsonNode>> e : calls.entrySet()) {
            String id = e.getKey();
            String signal = id.contains("ERRORS") ? "LOG_ERRORS" : "LOG_LINES";
            try {
                JsonNode r = e.getValue().join();
                results.put(id, r);
                int pts = 0;
                for (JsonNode s : r) {
                    pts += s.path("values").size();
                }
                run.queries.add(new QueryTrace(id, "Loki", signal, queries.get(id), "OK", null, 3600L,
                        TelemetryRules.at(rangeStart), TelemetryRules.at(end), r.size(), pts));
            } catch (RuntimeException ex) {
                String reason = reasonOf(ex);
                run.queries.add(new QueryTrace(id, "Loki", signal, queries.get(id), "FAILED", reason, 3600L,
                        TelemetryRules.at(rangeStart), TelemetryRules.at(end), null, null));
                failures.add(id + " (" + reason + ")");
            }
        }
        if (results.isEmpty()) {
            return new Outcome(true, false, false, new SourceQuality("Loki (erreurs des logs)", false, 0,
                    "Requête(s) en échec : " + String.join(" ; ", failures) + ". Logs non analysés."));
        }

        // décompte horaire par entité : erreurs et toutes lignes (absent = 0)
        Map<String, TreeMap<Long, Double>> errors = new LinkedHashMap<>(), lines = new LinkedHashMap<>();
        Map<String, String> scopeOf = new LinkedHashMap<>();
        collect(results.get("LOKI_ERRORS_HOSTS"), "host", true, scopes, errors, scopeOf);
        collect(results.get("LOKI_ERRORS_PODS"), "filename", false, scopes, errors, scopeOf);
        collect(results.get("LOKI_LINES_HOSTS"), "host", true, scopes, lines, scopeOf);
        collect(results.get("LOKI_LINES_PODS"), "filename", false, scopes, lines, scopeOf);

        List<Long> windowBuckets = new ArrayList<>(), baselineBuckets = new ArrayList<>();
        // Loki aligne les pas sur des multiples du pas : la grille d'heures attendues doit l'être aussi, sinon rien ne correspond
        for (long t = ((rangeStart + 3599) / 3600) * 3600; t <= end; t += 3600) {
            (t <= lStart ? baselineBuckets : windowBuckets).add(t);
        }
        double expectedBuckets = Math.max(1, windowBuckets.size());

        boolean complete = failures.isEmpty();
        boolean usable = false;
        boolean linesChecked = results.containsKey("LOKI_LINES_HOSTS") || results.containsKey("LOKI_LINES_PODS");
        List<String> silent = new ArrayList<>(), belowGood = new ArrayList<>();
        double covSum = 0;
        int covN = 0;

        Set<String> universe = new LinkedHashSet<>(lines.keySet());
        universe.addAll(errors.keySet());
        for (String entity : universe) {
            run.entities.add(entity);
            boolean host = entity.startsWith("hôte ");
            TreeMap<Long, Double> l = lines.get(entity);
            if (l != null) {
                double baseMedian = TelemetryRules.median(baselineBuckets.stream().mapToDouble(t -> l.getOrDefault(t, 0.0)).toArray());
                boolean relevant = host || (!Double.isNaN(baseMedian) && baseMedian >= ACTIVE_LINES_PER_HOUR)
                        || windowBuckets.stream().anyMatch(t -> l.getOrDefault(t, 0.0) >= ACTIVE_LINES_PER_HOUR);
                if (relevant) {
                    long withLines = windowBuckets.stream().filter(t -> l.getOrDefault(t, 0.0) > 0).count();
                    double coverage = Math.min(1.0, withLines / expectedBuckets);
                    run.coverage.add(new EntityCoverage("Loki", entity, "LOG_LINES", round2(coverage), withLines + " heure(s) avec des lignes sur " + Math.round(expectedBuckets)));
                    covSum += coverage;
                    covN++;
                    if (coverage < GOOD_COVERAGE) {
                        complete = false;
                        (coverage == 0 ? silent : belowGood).add(entity + (coverage == 0 ? "" : " (" + Math.round(coverage * 100) + " %)"));
                    }
                    if (coverage >= MIN_COVERAGE) {
                        usable = true;
                    }
                }
            }
            TreeMap<Long, Double> err = errors.get(entity);
            String ref = host ? "LOKI_ERRORS_HOSTS" : "LOKI_ERRORS_PODS";
            if (err != null && results.containsKey(ref)) {
                double[] base = baselineBuckets.stream().mapToDouble(t -> err.getOrDefault(t, 0.0)).toArray();
                double[] win = windowBuckets.stream().mapToDouble(t -> err.getOrDefault(t, 0.0)).toArray();
                for (Finding f : TelemetryRules.logFindings(scopeOf.get(entity), entity, base, win, lStart, 3600.0, "LogQL : " + (host ? LOG_HOSTS_QL : LOG_PODS_QL))) {
                    run.findings.add(f.withQueryRef(ref));
                }
                usable = true;
            }
        }
        if (!linesChecked || universe.isEmpty()) {
            complete = false;                                        // ingestion invérifiable, ou aucune ligne reçue : on ne sait rien
        }

        StringBuilder note = new StringBuilder();
        if (!failures.isEmpty()) {
            note.append("Requête(s) en échec : ").append(String.join(" ; ", failures)).append(". ");
        }
        note.append(universe.isEmpty() ? "Aucune ligne de log reçue dans ce périmètre sur la période (absence de mesure, pas absence d'anomalie). "
                : errors.size() + " source(s) avec des erreurs sur " + universe.size() + " observées. ");
        if (!silent.isEmpty()) {
            note.append("Source(s) silencieuse(s) sur la période : ").append(preview(silent)).append(". ");
        }
        if (!belowGood.isEmpty()) {
            note.append("Couverture inférieure à 80 % : ").append(preview(belowGood)).append(". ");
        }
        if (logWindow < windowSec) {
            note.append("Logs analysés sur les dernières ").append(durationLabel(logWindow)).append(" seulement (limite de performance de Loki). ");
        } else if (windowSec < 3600) {
            note.append("Logs analysés sur au moins 1 h (granularité horaire). ");
        }
        note.append("Référence : ").append(durationLabel(logBase)).append(" précédentes.");
        SourceQuality quality = new SourceQuality("Loki (erreurs des logs)", true, covN == 0 ? (universe.isEmpty() ? 0 : 1.0) : round2(covSum / covN), note.toString().trim());
        return new Outcome(true, complete, usable, quality);
    }

    private static void collect(JsonNode result, String label, boolean host, Set<ResourceScope> scopes,
                                Map<String, TreeMap<Long, Double>> into, Map<String, String> scopeOf) {
        if (result == null) {
            return;
        }
        for (JsonNode s : result) {
            String entity;
            ResourceScope scope;
            if (host) {
                entity = "hôte " + s.path("metric").path(label).asText("inconnu");
                scope = ResourceScope.VM;
            } else {
                entity = appOf(s.path("metric").path(label).asText(""));
                scope = scopeOfApp(entity);
            }
            if (scopes != null && !scopes.contains(scope)) {
                continue;
            }
            scopeOf.put(entity, scope.name());
            TreeMap<Long, Double> m = into.computeIfAbsent(entity, k -> new TreeMap<>());
            for (JsonNode p : s.path("values")) {
                m.merge((long) p.path(0).asDouble(), parseDouble(p.path(1).asText("0")), Double::sum);
            }
        }
    }

    // ── Utilitaires ──────────────────────────────────────────────────────────────────────────────────────────────

    static Map<String, String> thresholds() {
        Map<String, String> t = new LinkedHashMap<>();
        t.put("cpu.high/critical", TelemetryRules.CPU.high() + " / " + TelemetryRules.CPU.critical() + " %");
        t.put("memory.high/critical", TelemetryRules.MEMORY.high() + " / " + TelemetryRules.MEMORY.critical() + " %");
        t.put("disk.high/critical", TelemetryRules.DISK.high() + " / " + TelemetryRules.DISK.critical() + " %");
        t.put("sustained.min", "15 min et 3 mesures consécutives");
        t.put("jump", "médiane de référence + max(20 points, 4 σ robustes)");
        t.put("logs.minErrorsPerHour", String.valueOf((int) TelemetryRules.MIN_ERRORS_PER_HOUR));
        t.put("coverage.ignored.below", String.valueOf(MIN_COVERAGE));
        t.put("coverage.complete.from", String.valueOf(GOOD_COVERAGE));
        return t;
    }

    /** Motif lisible d'un échec, sans détail interne : les messages du client de supervision sont déjà génériques. */
    static String reasonOf(Throwable t) {
        Throwable c = t;
        while ((c instanceof CompletionException || c instanceof ExecutionException) && c.getCause() != null) {
            c = c.getCause();
        }
        if (c instanceof SupervisionClient.UnavailableException) {
            return c.getMessage();
        }
        return "erreur inattendue (" + c.getClass().getSimpleName() + ")";
    }

    private static String preview(List<String> items) {
        return String.join(", ", items.stream().limit(6).toList()) + (items.size() > 6 ? ", … (+" + (items.size() - 6) + ")" : "");
    }

    static ResourceScope scopeOfApp(String app) {
        int slash = app.indexOf('/');
        String ns = slash > 0 ? app.substring(0, slash) : "";
        String name = slash > 0 ? app.substring(slash + 1) : app;
        if ("kube-system".equals(ns)) {
            return ResourceScope.CLUSTER;
        }
        return ResourceScope.fromIncident("POD_X", ns, name).orElse(ResourceScope.APPLICATION);
    }

    static String appOf(String filename) {
        Matcher m = CONTAINER_FILE.matcher(filename);
        if (m.matches()) {
            return m.group(2) + "/" + m.group(3);
        }
        return filename.isBlank() ? "inconnu" : filename;
    }

    static Series parse(JsonNode values) {
        List<double[]> pts = new ArrayList<>();
        for (JsonNode p : values) {
            double v = parseDouble(p.path(1).asText("NaN"));
            if (!Double.isNaN(v) && !Double.isInfinite(v)) {
                pts.add(new double[]{p.path(0).asDouble(), v});
            }
        }
        double[] t = new double[pts.size()], v = new double[pts.size()];
        for (int i = 0; i < pts.size(); i++) {
            t[i] = pts.get(i)[0];
            v[i] = pts.get(i)[1];
        }
        return new Series(t, v);
    }

    private static double parseDouble(String s) {
        try {
            return Double.parseDouble(s);
        } catch (NumberFormatException e) {
            return Double.NaN;
        }
    }

    private static int firstAtOrAfter(double[] t, long epoch) {
        for (int i = 0; i < t.length; i++) {
            if (t[i] >= epoch) {
                return i;
            }
        }
        return t.length;
    }

    private static double[] copy(double[] a, int from, int to) {
        return java.util.Arrays.copyOfRange(a, from, to);
    }

    static String hostName(String instance) {
        String ip = instance.contains(":") ? instance.substring(0, instance.indexOf(':')) : instance;
        return HOSTS.getOrDefault(ip, ip.isBlank() ? "inconnu" : ip);
    }

    private static int rank(String severity) {
        return "HIGH".equals(severity) ? 3 : "MEDIUM".equals(severity) ? 2 : 1;
    }

    private static double round2(double v) {
        return Math.round(v * 100.0) / 100.0;
    }

    static String durationLabel(long seconds) {
        if (seconds >= 2 * 86400 && seconds % 86400 == 0) {
            return (seconds / 86400) + " j";
        }
        if (seconds >= 3600) {
            return String.format(Locale.ROOT, "%.0f h", seconds / 3600.0);
        }
        return (seconds / 60) + " min";
    }
}
