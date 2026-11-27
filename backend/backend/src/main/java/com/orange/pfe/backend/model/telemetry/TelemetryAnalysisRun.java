package com.orange.pfe.backend.model.telemetry;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.GeneratedValue;
import jakarta.persistence.GenerationType;
import jakarta.persistence.Id;
import jakarta.persistence.Table;
import org.hibernate.annotations.Immutable;

import java.time.LocalDateTime;

/**
 * Une analyse IA des logs et métriques, conservée telle qu'elle a été produite. Entité IMMUABLE (aucun setter), dépôt en
 * ajout seul ET déclencheurs PostgreSQL (UPDATE, DELETE, TRUNCATE refusés). Aucune clé étrangère : la trace survit à la
 * suppression de l'utilisateur ou de l'équipe. Aucune ligne de log brute n'est stockée, seulement des constats chiffrés.
 */
@Entity
@Immutable
@Table(name = "telemetry_analysis_run")
public class TelemetryAnalysisRun {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @Column(name = "requested_at", nullable = false, updatable = false)
    private LocalDateTime requestedAt;

    @Column(name = "requested_by_user_id", updatable = false)
    private Long requestedByUserId;

    @Column(name = "requested_by_username", length = 50, updatable = false)
    private String requestedByUsername;

    @Column(name = "team_id", updatable = false)
    private Long teamId;

    @Column(name = "team_name", length = 100, updatable = false)
    private String teamName;

    @Column(nullable = false, length = 16, updatable = false)
    private String scope;

    @Column(name = "window_minutes", nullable = false, updatable = false)
    private int windowMinutes;

    @Column(name = "window_start", nullable = false, updatable = false)
    private LocalDateTime windowStart;

    @Column(name = "window_end", nullable = false, updatable = false)
    private LocalDateTime windowEnd;

    @Column(nullable = false, length = 20, updatable = false)
    private String verdict;

    @Column(name = "data_quality", nullable = false, length = 12, updatable = false)
    private String dataQuality;

    @Column(name = "anomaly_count", nullable = false, updatable = false)
    private int anomalyCount;

    @Column(name = "high_count", nullable = false, updatable = false)
    private int highCount;

    @Column(name = "findings_json", nullable = false, columnDefinition = "TEXT", updatable = false)
    private String findingsJson;

    @Column(name = "quality_json", nullable = false, columnDefinition = "TEXT", updatable = false)
    private String qualityJson;

    @Column(name = "trace_json", nullable = false, columnDefinition = "TEXT", updatable = false)
    private String traceJson;

    @Column(name = "narrative_status", nullable = false, length = 16, updatable = false)
    private String narrativeStatus;

    @Column(name = "narrative_text", columnDefinition = "TEXT", updatable = false)
    private String narrativeText;

    @Column(name = "narrative_message", length = 300, updatable = false)
    private String narrativeMessage;

    @Column(length = 80, updatable = false)
    private String model;

    @Column(name = "duration_ms", nullable = false, updatable = false)
    private long durationMs;

    protected TelemetryAnalysisRun() {
        // JPA
    }

    public TelemetryAnalysisRun(LocalDateTime requestedAt, Long requestedByUserId, String requestedByUsername, Long teamId,
                                String teamName, String scope, int windowMinutes, LocalDateTime windowStart,
                                LocalDateTime windowEnd, String verdict, String dataQuality, int anomalyCount, int highCount,
                                String findingsJson, String qualityJson, String traceJson, String narrativeStatus,
                                String narrativeText, String narrativeMessage, String model, long durationMs) {
        this.requestedAt = requestedAt;
        this.requestedByUserId = requestedByUserId;
        this.requestedByUsername = requestedByUsername;
        this.teamId = teamId;
        this.teamName = teamName;
        this.scope = scope;
        this.windowMinutes = windowMinutes;
        this.windowStart = windowStart;
        this.windowEnd = windowEnd;
        this.verdict = verdict;
        this.dataQuality = dataQuality;
        this.anomalyCount = anomalyCount;
        this.highCount = highCount;
        this.findingsJson = findingsJson;
        this.qualityJson = qualityJson;
        this.traceJson = traceJson;
        this.narrativeStatus = narrativeStatus;
        this.narrativeText = narrativeText;
        this.narrativeMessage = narrativeMessage;
        this.model = model;
        this.durationMs = durationMs;
    }

    public Long getId() { return id; }
    public LocalDateTime getRequestedAt() { return requestedAt; }
    public Long getRequestedByUserId() { return requestedByUserId; }
    public String getRequestedByUsername() { return requestedByUsername; }
    public Long getTeamId() { return teamId; }
    public String getTeamName() { return teamName; }
    public String getScope() { return scope; }
    public int getWindowMinutes() { return windowMinutes; }
    public LocalDateTime getWindowStart() { return windowStart; }
    public LocalDateTime getWindowEnd() { return windowEnd; }
    public String getVerdict() { return verdict; }
    public String getDataQuality() { return dataQuality; }
    public int getAnomalyCount() { return anomalyCount; }
    public int getHighCount() { return highCount; }
    public String getFindingsJson() { return findingsJson; }
    public String getQualityJson() { return qualityJson; }
    public String getTraceJson() { return traceJson; }
    public String getNarrativeStatus() { return narrativeStatus; }
    public String getNarrativeText() { return narrativeText; }
    public String getNarrativeMessage() { return narrativeMessage; }
    public String getModel() { return model; }
    public long getDurationMs() { return durationMs; }
}
