package com.orange.pfe.backend.dto.aiassist;

import com.orange.pfe.backend.service.aiassist.AiCompletionResult;

import java.time.Instant;
import java.time.LocalDateTime;
import java.util.List;

/**
 * Réponses de l'assistance IA. Règle commune : les chiffres ({@link Fact}, scores, listes) viennent TOUJOURS du Backend ;
 * seule la {@link Narrative} vient de l'IA, et elle est facultative. Une IA indisponible donne un statut, jamais une erreur.
 */
public final class AiAssistDtos {

    private AiAssistDtos() {
    }

    /** Indicateur calculé par le Backend ; {@code tone} : neutral | good | warn | bad. */
    public record Fact(String label, String value, String tone) {
        public static Fact of(String label, Object value) {
            return new Fact(label, String.valueOf(value), "neutral");
        }

        public static Fact of(String label, Object value, String tone) {
            return new Fact(label, String.valueOf(value), tone);
        }
    }

    /** Texte « Analyse IA » ; {@code status} : OK | DISABLED | NOT_CONFIGURED | UNAVAILABLE | NONE (pas demandé). */
    public record Narrative(String status, String message, String text, String model, Instant generatedAt) {

        public static Narrative from(AiCompletionResult result) {
            if (result instanceof AiCompletionResult.Success ok) {
                return new Narrative("OK", null, ok.text(), ok.model(), ok.generatedAt());
            }
            AiCompletionResult.Unavailable u = (AiCompletionResult.Unavailable) result;
            String status = switch (u.reason()) {
                case DISABLED -> "DISABLED";
                case NOT_CONFIGURED -> "NOT_CONFIGURED";
                default -> "UNAVAILABLE";
            };
            return new Narrative(status, u.message(), null, null, u.at());
        }

        public static Narrative none() {
            return new Narrative("NONE", null, null, null, null);
        }
    }

    /** État d'une fonction pour l'utilisateur : activée, prête (clé configurée), et message éventuel. */
    public record FeatureState(boolean enabled, boolean ready, String message) {
        public static FeatureState off() {
            return new FeatureState(false, false, null);
        }
    }

    /** Ce que l'utilisateur peut faire : l'interface n'affiche une fonction que si elle est activée ET permise. */
    public record Capabilities(boolean admin, List<LedTeam> ledTeams, boolean allowed,
                               FeatureState reportsSummary, FeatureState teamSummary, FeatureState auditAnomalies,
                               FeatureState taskAssignment, FeatureState maintenanceWindow, FeatureState telemetryAnalysis) {
    }

    /** Équipe dont l'utilisateur est le responsable actif. */
    public record LedTeam(Long id, String name) {
    }

    // ── (b) Résumé IA ────────────────────────────────────────────────────────────────────────────────────────────

    public record ReportSummaryResponse(Long reportId, String title, String period, List<Fact> facts, Narrative analysis) {
    }

    public record MemberLoad(Long userId, String displayName, long total, long open, long done, long overdue) {
    }

    public record TeamSummaryAiResponse(Long teamId, String teamName, List<Fact> facts, List<MemberLoad> members,
                                        Narrative analysis) {
    }

    // ── (c) Anomalies d'audit ────────────────────────────────────────────────────────────────────────────────────

    /** {@code severity} : HIGH | MEDIUM | LOW ; {@code sentence} : phrase déterministe, remplacée par celle de l'IA si demandée. */
    public record Anomaly(String key, String rule, String ruleLabel, String severity, String actor, long count,
                          LocalDateTime firstAt, LocalDateTime lastAt, String sentence, boolean aiSentence) {
    }

    public record AnomalyResponse(Long teamId, int days, List<Fact> facts, List<Anomaly> anomalies, Narrative analysis) {
    }

    // ── (a) Recommandation d'assignation ─────────────────────────────────────────────────────────────────────────

    public record TaskRecommendationRequest(Long teamId, String title, String description, String priority) {
    }

    public record Candidate(Long userId, String displayName, int score, long openTasks, long overdueTasks,
                            long similarDone, List<String> reasons) {
    }

    public record AssignmentRecommendation(Long teamId, String teamName, List<Fact> facts, List<Candidate> candidates,
                                           Long recommendedUserId, Narrative analysis) {
    }

    public record AssignmentFeedback(Long teamId, Long userId, boolean accepted) {
    }

    // ── (e) Analyse des logs et métriques ────────────────────────────────────────────────────────────────────────

    /** {@code scope} : ALL | VM | CLUSTER | APPLICATION | SERVICE (ignoré pour un chef d'équipe : sa spécialité s'applique). */
    public record TelemetryAnalysisRequest(Long teamId, String scope, Integer minutes) {
    }

    public record FindingView(String scope, String entity, String signal, String rule, String severity, LocalDateTime start,
                              LocalDateTime end, Double observed, Double baseline, String unit, String detail, String evidence,
                              String queryRef) {
    }

    public record QualityView(String source, boolean available, double coverage, String note) {
    }

    /** Une analyse complète, telle qu'elle est enregistrée dans l'historique (relue à l'identique par l'écran). */
    public record TelemetryAnalysisView(Long id, LocalDateTime requestedAt, String requestedBy, Long teamId, String teamName,
                                        String scope, int minutes, LocalDateTime windowStart, LocalDateTime windowEnd,
                                        String verdict, String dataQuality, int anomalyCount, int highCount, long durationMs,
                                        List<Fact> facts, List<FindingView> findings, List<QualityView> quality,
                                        com.orange.pfe.backend.service.aiassist.telemetry.TelemetryModels.Trace trace,
                                        Narrative analysis) {
    }

    public record TelemetryRunSummary(Long id, LocalDateTime requestedAt, String requestedBy, Long teamId, String teamName,
                                      String scope, int minutes, String verdict, String dataQuality, int anomalyCount,
                                      int highCount, String narrativeStatus) {
    }

    // ── (d) Fenêtre de maintenance ───────────────────────────────────────────────────────────────────────────────

    public record MaintenanceWindowRequest(Long teamId, String resourceType, String resourceName,
                                           Integer durationMinutes, Integer horizonDays) {
    }

    public record WindowCandidate(LocalDateTime start, LocalDateTime end, double averageLoad, double peakLoad, int score,
                                  List<String> notes) {
    }

    public record MaintenanceWindowResponse(Long teamId, boolean metricsAvailable, List<Fact> facts,
                                            List<WindowCandidate> windows, Narrative analysis) {
    }
}
