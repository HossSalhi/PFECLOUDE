package com.orange.pfe.backend.service.report;

import com.orange.pfe.backend.model.report.ReportFrequency;
import org.junit.jupiter.api.Test;

import java.time.LocalDateTime;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

/** Périodes d'analyse et dernière période complète d'un planning (mercredi 25 novembre 2026, 10 h 30). */
class ReportPeriodTest {

    private static final LocalDateTime NOW = LocalDateTime.of(2026, 11, 25, 10, 30);

    @Test
    void dailyIsYesterdayWeeklyIsThePreviousMondayToMondayAndMonthlyIsThePreviousMonth() {
        assertThat(ReportPeriod.lastCompleted(ReportFrequency.DAILY, NOW))
                .isEqualTo(new ReportPeriod(LocalDateTime.of(2026, 11, 24, 0, 0), LocalDateTime.of(2026, 11, 25, 0, 0)));
        assertThat(ReportPeriod.lastCompleted(ReportFrequency.WEEKLY, NOW))
                .isEqualTo(new ReportPeriod(LocalDateTime.of(2026, 11, 16, 0, 0), LocalDateTime.of(2026, 11, 23, 0, 0)));
        assertThat(ReportPeriod.lastCompleted(ReportFrequency.MONTHLY, NOW))
                .isEqualTo(new ReportPeriod(LocalDateTime.of(2026, 10, 1, 0, 0), LocalDateTime.of(2026, 11, 1, 0, 0)));
    }

    @Test
    void monthlyCrossesTheYearBoundary() {
        assertThat(ReportPeriod.lastCompleted(ReportFrequency.MONTHLY, LocalDateTime.of(2027, 1, 15, 8, 0)))
                .isEqualTo(new ReportPeriod(LocalDateTime.of(2026, 12, 1, 0, 0), LocalDateTime.of(2027, 1, 1, 0, 0)));
    }

    @Test
    void theSameInstantAlwaysYieldsTheSamePeriodSoTwoRunsCannotProduceDifferentReports() {
        assertThat(ReportPeriod.lastCompleted(ReportFrequency.DAILY, NOW))
                .isEqualTo(ReportPeriod.lastCompleted(ReportFrequency.DAILY, NOW.plusHours(9)));
    }

    @Test
    void thePreviousPeriodHasTheSameLengthAndEndsWhereTheCurrentOneStarts() {
        ReportPeriod p = new ReportPeriod(LocalDateTime.of(2026, 11, 10, 0, 0), LocalDateTime.of(2026, 11, 17, 0, 0));
        ReportPeriod previous = p.previous();
        assertThat(previous.to()).isEqualTo(p.from());
        assertThat(previous.length()).isEqualTo(p.length());
        assertThat(previous.from()).isEqualTo(LocalDateTime.of(2026, 11, 3, 0, 0));
    }

    @Test
    void refusesMissingReversedOrTooLongPeriods() {
        LocalDateTime t = LocalDateTime.of(2026, 11, 1, 0, 0);
        assertThatThrownBy(() -> new ReportPeriod(null, t)).isInstanceOf(IllegalArgumentException.class);
        assertThatThrownBy(() -> new ReportPeriod(t, t)).isInstanceOf(IllegalArgumentException.class);
        assertThatThrownBy(() -> new ReportPeriod(t, t.minusDays(1))).isInstanceOf(IllegalArgumentException.class);
        assertThatThrownBy(() -> new ReportPeriod(t, t.plusDays(400)))
                .isInstanceOf(IllegalArgumentException.class).hasMessageContaining("366");
    }
}
