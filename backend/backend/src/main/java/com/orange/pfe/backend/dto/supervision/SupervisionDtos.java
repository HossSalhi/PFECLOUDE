package com.orange.pfe.backend.dto.supervision;

import java.time.LocalDateTime;
import java.util.List;

/**
 * Réponses de la supervision (lecture seule). Chaque réponse porte {@code available} et {@code error} : si un outil
 * (Prometheus, Loki) ne répond pas, l'interface affiche un message plutôt qu'une erreur serveur.
 */
public final class SupervisionDtos {

    private SupervisionDtos() {
    }

    public record Point(long t, double v) {}

    // ── Machines et cibles (Prometheus) ───────────────────────────────────────
    public record HostStat(String instance, String name, boolean up, Double cpuPercent, Double memoryPercent, Double diskPercent) {}

    public record TargetStat(String job, int up, int total) {}

    public record HostsOverview(boolean available, String error, LocalDateTime generatedAt, List<HostStat> hosts, List<TargetStat> targets) {}

    // ── Séries (Prometheus) ───────────────────────────────────────────────────
    public enum Metric { CPU, MEMORY, DISK }

    public record SeriesLine(String instance, String name, List<Point> points) {}

    public record SeriesResponse(boolean available, String error, Metric metric, String unit, int minutes, int stepSeconds,
                                 List<SeriesLine> lines) {}

    // ── Logs (Loki) ───────────────────────────────────────────────────────────
    public record LogGroup(String key, long total, long errors, long warnings) {}

    public record LogStats(boolean available, String error, int minutes, LocalDateTime generatedAt, long total, long errors,
                           long warnings, List<LogGroup> byHost, List<LogGroup> byApplication) {}

    public record ErrorPattern(String pattern, long count, String host, String category, String label, String meaning,
                               String advice, String example) {}

    public record TopErrors(boolean available, String error, int minutes, List<ErrorPattern> patterns) {}

    public record MeaningEntry(String category, String label, String meaning, String advice) {}

    // ── Lien avec les incidents HELIOS ────────────────────────────────────────
    public record IncidentMark(Long id, String type, String resource, String severity, String status, LocalDateTime createdAt,
                               long errorLogsAround) {}

    public record Correlation(boolean available, String error, int minutes, int stepSeconds, List<Point> errorSeries,
                              List<Point> cpuSeries, List<IncidentMark> incidents) {}

    // ── Camunda (lecture seule) ───────────────────────────────────────────────
    public record DefinitionInfo(String key, String id, String name, int version) {}

    public record CamundaOverview(boolean available, String error, LocalDateTime generatedAt, boolean healthy, String version,
                                  int clusterSize, long active, long completed, long terminated, long incidents,
                                  List<DefinitionInfo> definitions) {}

    public record CamundaInstance(String key, String definitionKey, String definitionId, String definitionName, int version,
                                  String state, boolean hasIncident, LocalDateTime startedAt, LocalDateTime endedAt, Long durationMs) {}

    public record CamundaInstances(boolean available, String error, long total, List<CamundaInstance> items) {}

    public record CamundaIncident(String key, String definitionId, String instanceKey, String errorType, String errorMessage,
                                  String elementId, LocalDateTime createdAt) {}

    public record CamundaIncidents(boolean available, String error, long total, List<CamundaIncident> items) {}

    public record StepInfo(String id, String type, String kind, String name) {}

    public record CamundaSteps(boolean available, String error, String definitionKey, List<StepInfo> steps) {}

    // ── Grafana ───────────────────────────────────────────────────────────────
    public record GrafanaDashboard(String title, String uid, String embedUrl) {}

    /** {@code embedEnabled} : Grafana autorise l'intégration (réglage fait sur la VM) ; sinon l'interface explique la marche à suivre. */
    public record GrafanaConfig(boolean embedEnabled, List<GrafanaDashboard> dashboards) {}
}
