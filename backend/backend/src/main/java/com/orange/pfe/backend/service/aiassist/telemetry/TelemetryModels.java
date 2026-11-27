package com.orange.pfe.backend.service.aiassist.telemetry;

import java.time.LocalDateTime;
import java.util.List;
import java.util.Map;

/** Résultats de la détection déterministe : aucun de ces chiffres ne vient de l'IA. */
public final class TelemetryModels {

    private TelemetryModels() {
    }

    // ── Verdict : une absence de MESURE n'est jamais présentée comme une absence d'ANOMALIE ──────────────────────

    /** Toutes les sources demandées sont disponibles, chaque entité pertinente atteint 80 % de couverture, aucun constat. */
    public static final String NO_ANOMALY = "NO_ANOMALY";
    /** Au moins un constat (avec avertissement si la qualité des données est partielle). */
    public static final String ANOMALIES = "ANOMALIES";
    /** Aucun constat, mais une source en panne ou une couverture inférieure à 80 % : on ne peut PAS conclure à l'absence d'anomalie. */
    public static final String PARTIAL_COVERAGE = "PARTIAL_COVERAGE";
    /** Aucune donnée exploitable. */
    public static final String INSUFFICIENT_DATA = "INSUFFICIENT_DATA";

    public static final String DATA_COMPLETE = "COMPLETE";
    public static final String DATA_PARTIAL = "PARTIAL";
    public static final String DATA_INSUFFICIENT = "INSUFFICIENT";

    /** Version des règles et seuils : conservée avec chaque analyse pour pouvoir la rejouer. */
    public static final String RULES_VERSION = "telemetry-rules-2";

    /**
     * Un constat chiffré. {@code severity} : HIGH | MEDIUM | LOW ; {@code signal} : CPU | MEMORY | DISK | LOG_ERRORS ;
     * {@code baseline} : valeur de référence (période précédente), {@code null} s'il n'y en avait pas ; {@code evidence} : la
     * requête exacte (PromQL ou LogQL) qui a produit la mesure ; {@code queryRef} : identifiant de la requête dans la trace.
     */
    public record Finding(String scope, String entity, String signal, String rule, String severity, LocalDateTime start,
                          LocalDateTime end, Double observed, Double baseline, String unit, String detail, String evidence,
                          String queryRef) {

        public Finding withQueryRef(String ref) {
            return new Finding(scope, entity, signal, rule, severity, start, end, observed, baseline, unit, detail, evidence, ref);
        }
    }

    /** Qualité d'une source : une mesure manquante n'est PAS une anomalie, elle est signalée à part. */
    public record SourceQuality(String source, boolean available, double coverage, String note) {
    }

    /**
     * Trace d'UNE requête : texte exact, source, plage et pas, état (OK | FAILED) et motif de l'échec. Chaque requête qui a
     * échoué est nommée, jamais masquée.
     */
    public record QueryTrace(String id, String source, String signal, String query, String status, String reason, Long stepSeconds,
                             LocalDateTime start, LocalDateTime end, Integer series, Integer points) {
    }

    /** Couverture d'une entité pour un signal : part des mesures (ou des heures de logs) effectivement reçues. */
    public record EntityCoverage(String source, String entity, String signal, double coverage, String note) {
    }

    /** Tout ce qu'il faut pour rejouer l'analyse : périodes, pas, seuils, requêtes et couverture par entité. */
    public record Trace(String rulesVersion, LocalDateTime windowStart, LocalDateTime windowEnd, LocalDateTime baselineStart,
                        LocalDateTime baselineEnd, Long metricsStepSeconds, LocalDateTime logsWindowStart,
                        LocalDateTime logsBaselineStart, Long logsStepSeconds, Map<String, String> thresholds,
                        List<QueryTrace> queries, List<EntityCoverage> coverage) {
    }

    public record Detection(String verdict, String dataQuality, List<Finding> findings, List<SourceQuality> quality, Trace trace,
                            LocalDateTime windowStart, LocalDateTime windowEnd, int entities) {

        public long count(String severity) {
            return findings.stream().filter(f -> severity.equals(f.severity())).count();
        }
    }
}
