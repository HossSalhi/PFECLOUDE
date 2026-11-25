package com.orange.pfe.backend.service.report;

import com.orange.pfe.backend.model.report.ReportFrequency;

import java.time.DayOfWeek;
import java.time.Duration;
import java.time.LocalDate;
import java.time.LocalDateTime;

/** Période d'analyse [from, to[ d'un rapport. */
public record ReportPeriod(LocalDateTime from, LocalDateTime to) {

    public static final long MAX_DAYS = 366;

    public ReportPeriod {
        if (from == null || to == null) {
            throw new IllegalArgumentException("Les dates de début et de fin de la période sont obligatoires");
        }
        if (!to.isAfter(from)) {
            throw new IllegalArgumentException("La fin de la période doit être postérieure à son début");
        }
        if (Duration.between(from, to).toDays() > MAX_DAYS) {
            throw new IllegalArgumentException("La période d'analyse est limitée à " + MAX_DAYS + " jours");
        }
    }

    public Duration length() {
        return Duration.between(from, to);
    }

    /** Période précédente de même durée, immédiatement avant celle-ci (comparaison entre périodes). */
    public ReportPeriod previous() {
        return new ReportPeriod(from.minus(length()), from);
    }

    /**
     * Dernière période COMPLÈTE d'une fréquence à l'instant {@code now} : hier (quotidien), la semaine précédente
     * du lundi au lundi (hebdomadaire), le mois précédent (mensuel).
     */
    public static ReportPeriod lastCompleted(ReportFrequency frequency, LocalDateTime now) {
        LocalDate today = now.toLocalDate();
        switch (frequency) {
            case DAILY:
                return new ReportPeriod(today.minusDays(1).atStartOfDay(), today.atStartOfDay());
            case WEEKLY:
                LocalDate monday = today.with(DayOfWeek.MONDAY);
                return new ReportPeriod(monday.minusWeeks(1).atStartOfDay(), monday.atStartOfDay());
            case MONTHLY:
            default:
                LocalDate first = today.withDayOfMonth(1);
                return new ReportPeriod(first.minusMonths(1).atStartOfDay(), first.atStartOfDay());
        }
    }
}
