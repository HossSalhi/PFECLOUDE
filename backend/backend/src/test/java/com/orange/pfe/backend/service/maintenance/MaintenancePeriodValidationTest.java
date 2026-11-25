package com.orange.pfe.backend.service.maintenance;

import org.junit.jupiter.api.Test;

import java.time.LocalDateTime;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatCode;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

/** Cohérence des dates d'une maintenance (fonction pure). */
class MaintenancePeriodValidationTest {

    private static final LocalDateTime START = LocalDateTime.now().plusDays(1).withNano(0);

    @Test
    void acceptsACoherentPeriodAndTheMaximumDuration() {
        assertThatCode(() -> MaintenanceService.validatePeriod(START, START.plusHours(1), true)).doesNotThrowAnyException();
        assertThatCode(() -> MaintenanceService.validatePeriod(START, START.plusDays(31), true)).doesNotThrowAnyException();
    }

    @Test
    void refusesMissingEqualReversedOrTooLongPeriods() {
        assertThatThrownBy(() -> MaintenanceService.validatePeriod(null, START, true)).isInstanceOf(IllegalArgumentException.class);
        assertThatThrownBy(() -> MaintenanceService.validatePeriod(START, null, true)).isInstanceOf(IllegalArgumentException.class);
        assertThatThrownBy(() -> MaintenanceService.validatePeriod(START, START, true)).isInstanceOf(IllegalArgumentException.class);
        assertThatThrownBy(() -> MaintenanceService.validatePeriod(START, START.minusMinutes(1), true))
                .isInstanceOf(IllegalArgumentException.class);
        assertThatThrownBy(() -> MaintenanceService.validatePeriod(START, START.plusDays(31).plusMinutes(1), true))
                .isInstanceOf(IllegalArgumentException.class).hasMessageContaining("31 jours");
    }

    @Test
    void aPeriodEntirelyInThePastIsOnlyRefusedAtCreation() {
        LocalDateTime past = LocalDateTime.now().minusDays(5);
        assertThatThrownBy(() -> MaintenanceService.validatePeriod(past, past.plusHours(2), true))
                .isInstanceOf(IllegalArgumentException.class).hasMessageContaining("passé");
        assertThat(past).isBefore(LocalDateTime.now());
        assertThatCode(() -> MaintenanceService.validatePeriod(past, past.plusHours(2), false)).doesNotThrowAnyException();
        // une maintenance déjà commencée mais pas terminée reste acceptable
        assertThatCode(() -> MaintenanceService.validatePeriod(LocalDateTime.now().minusHours(1),
                LocalDateTime.now().plusHours(1), true)).doesNotThrowAnyException();
    }
}
