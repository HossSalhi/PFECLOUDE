package com.orange.pfe.backend.dto.report;

import com.orange.pfe.backend.model.report.ReportFrequency;
import com.orange.pfe.backend.model.report.ReportStatus;
import com.orange.pfe.backend.model.report.ReportTrigger;
import com.orange.pfe.backend.model.report.ReportType;
import jakarta.validation.constraints.NotNull;

import java.time.LocalDateTime;

/** Requêtes et réponses du module Rapports. */
public final class ReportDtos {

    private ReportDtos() {
    }

    public record GenerateRequest(
            @NotNull ReportType type,
            @NotNull LocalDateTime from,
            @NotNull LocalDateTime to,
            boolean compare
    ) {}

    public record ScheduleRequest(
            @NotNull ReportType type,
            @NotNull ReportFrequency frequency,
            Boolean enabled
    ) {}

    public record ScheduleToggleRequest(@NotNull Boolean enabled) {}

    public record ReportItem(
            Long id,
            ReportType type,
            String typeLabel,
            String title,
            LocalDateTime periodStart,
            LocalDateTime periodEnd,
            boolean comparePrevious,
            ReportTrigger trigger,
            ReportStatus status,
            String summary,
            String generatedBy,
            LocalDateTime generatedAt
    ) {}

    /** {@code content} : JSON du rapport (indicateurs, tableaux, graphiques), à analyser côté client. */
    public record ReportDetail(ReportItem report, String errorMessage, String content) {}

    public record ScheduleResponse(
            Long id,
            ReportType type,
            String typeLabel,
            ReportFrequency frequency,
            boolean enabled,
            String createdBy,
            LocalDateTime createdAt,
            LocalDateTime lastGeneratedAt
    ) {}

    public record ReportTypeInfo(ReportType type, String label, String description) {}
}
