package com.orange.pfe.backend.model.report;

import com.orange.pfe.backend.model.user.AppUser;
import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.EnumType;
import jakarta.persistence.Enumerated;
import jakarta.persistence.FetchType;
import jakarta.persistence.GeneratedValue;
import jakarta.persistence.GenerationType;
import jakarta.persistence.Id;
import jakarta.persistence.JoinColumn;
import jakarta.persistence.ManyToOne;
import jakarta.persistence.PrePersist;
import jakarta.persistence.Table;
import jakarta.persistence.UniqueConstraint;

import java.time.LocalDateTime;

/**
 * Rapport généré. Le contenu (statistiques, tableaux, graphiques) est calculé à la génération à partir des
 * données réelles et conservé en JSON dans {@code resultJson} pour les consultations ultérieures.
 * Un planning ne peut produire qu'un rapport par période (contrainte d'unicité) : pas de doublon, même avec
 * plusieurs réplicas du Backend.
 */
@Entity
@Table(name = "report",
        uniqueConstraints = @UniqueConstraint(name = "uq_report_scheduled_period",
                columnNames = {"schedule_id", "period_start", "period_end"}))
public class Report {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @Enumerated(EnumType.STRING)
    @Column(name = "report_type", nullable = false, length = 30)
    private ReportType reportType;

    @Column(nullable = false, length = 200)
    private String title;

    @Column(name = "period_start", nullable = false)
    private LocalDateTime periodStart;

    @Column(name = "period_end", nullable = false)
    private LocalDateTime periodEnd;

    @Column(name = "compare_previous", nullable = false)
    private boolean comparePrevious;

    @Enumerated(EnumType.STRING)
    @Column(name = "trigger_type", nullable = false, length = 10)
    private ReportTrigger trigger = ReportTrigger.MANUAL;

    @ManyToOne(fetch = FetchType.LAZY)
    @JoinColumn(name = "schedule_id")
    private ReportSchedule schedule;

    @Enumerated(EnumType.STRING)
    @Column(nullable = false, length = 12)
    private ReportStatus status = ReportStatus.COMPLETED;

    @Column(name = "error_message", length = 500)
    private String errorMessage;

    @Column(length = 1000)
    private String summary;

    @Column(name = "result_json", columnDefinition = "TEXT")
    private String resultJson;

    @ManyToOne(fetch = FetchType.LAZY)
    @JoinColumn(name = "generated_by_id")
    private AppUser generatedBy;

    @Column(name = "generated_by_username", length = 50)
    private String generatedByUsername;

    @Column(name = "generated_at", nullable = false, updatable = false)
    private LocalDateTime generatedAt;

    @PrePersist
    protected void onCreate() {
        if (this.generatedAt == null) {
            this.generatedAt = LocalDateTime.now();
        }
    }

    public Long getId() { return id; }

    public ReportType getReportType() { return reportType; }
    public void setReportType(ReportType reportType) { this.reportType = reportType; }

    public String getTitle() { return title; }
    public void setTitle(String title) { this.title = title; }

    public LocalDateTime getPeriodStart() { return periodStart; }
    public void setPeriodStart(LocalDateTime periodStart) { this.periodStart = periodStart; }

    public LocalDateTime getPeriodEnd() { return periodEnd; }
    public void setPeriodEnd(LocalDateTime periodEnd) { this.periodEnd = periodEnd; }

    public boolean isComparePrevious() { return comparePrevious; }
    public void setComparePrevious(boolean comparePrevious) { this.comparePrevious = comparePrevious; }

    public ReportTrigger getTrigger() { return trigger; }
    public void setTrigger(ReportTrigger trigger) { this.trigger = trigger; }

    public ReportSchedule getSchedule() { return schedule; }
    public void setSchedule(ReportSchedule schedule) { this.schedule = schedule; }

    public ReportStatus getStatus() { return status; }
    public void setStatus(ReportStatus status) { this.status = status; }

    public String getErrorMessage() { return errorMessage; }
    public void setErrorMessage(String errorMessage) { this.errorMessage = errorMessage; }

    public String getSummary() { return summary; }
    public void setSummary(String summary) { this.summary = summary; }

    public String getResultJson() { return resultJson; }
    public void setResultJson(String resultJson) { this.resultJson = resultJson; }

    public AppUser getGeneratedBy() { return generatedBy; }
    public void setGeneratedBy(AppUser generatedBy) { this.generatedBy = generatedBy; }

    public String getGeneratedByUsername() { return generatedByUsername; }
    public void setGeneratedByUsername(String generatedByUsername) { this.generatedByUsername = generatedByUsername; }

    public LocalDateTime getGeneratedAt() { return generatedAt; }
    public void setGeneratedAt(LocalDateTime generatedAt) { this.generatedAt = generatedAt; }
}
