package com.orange.pfe.backend.service.aiassist.telemetry;

import com.orange.pfe.backend.service.aiassist.telemetry.TelemetryModels.Finding;
import com.orange.pfe.backend.service.aiassist.telemetry.TelemetryRules.Series;
import org.junit.jupiter.api.Test;

import java.util.Arrays;
import java.util.List;
import java.util.stream.IntStream;

import static org.assertj.core.api.Assertions.assertThat;

/** Règles déterministes : seuils explicites, durées minimales, base de référence facultative, aucun appel réseau. */
class TelemetryRulesTest {

    private static final double STEP = 60;           // une mesure par minute
    private static final long T0 = 1_795_700_000L;

    private static Series series(double step, double... values) {
        double[] t = IntStream.range(0, values.length).mapToDouble(i -> T0 + i * step).toArray();
        return new Series(t, values);
    }

    private static double[] repeat(double value, int n) {
        double[] a = new double[n];
        Arrays.fill(a, value);
        return a;
    }

    private static double[] concat(double[]... parts) {
        return Arrays.stream(parts).flatMapToDouble(Arrays::stream).toArray();
    }

    private static List<Finding> cpu(Series window, double[] baseline) {
        return TelemetryRules.metricFindings("VM", "k8s-worker1", "CPU", TelemetryRules.CPU, STEP, window, baseline, "PromQL : x", false);
    }

    // ── dépassement soutenu ──────────────────────────────────────────────────────────────────────────────────────

    @Test
    void aShortSpikeIsNotAnAnomalyButASustainedOverrunIs() {
        // 10 min à 90 % : moins de 15 min ⇒ rien
        assertThat(cpu(series(STEP, concat(repeat(40, 20), repeat(90, 10))), null)).isEmpty();

        // 20 min à 90 % ⇒ MEDIUM
        List<Finding> found = cpu(series(STEP, concat(repeat(40, 10), repeat(90, 20))), null);
        assertThat(found).hasSize(1);
        assertThat(found.get(0).rule()).isEqualTo("SUSTAINED_HIGH");
        assertThat(found.get(0).severity()).isEqualTo("MEDIUM");
        assertThat(found.get(0).observed()).isEqualTo(90.0);
        assertThat(found.get(0).baseline()).isNull();
        assertThat(found.get(0).detail()).contains("CPU").contains("85").contains("20 min");
    }

    @Test
    void aCriticalPeakInsideASustainedOverrunIsHigh() {
        double[] v = concat(repeat(40, 5), repeat(90, 10), repeat(98, 8), repeat(90, 5));

        List<Finding> found = cpu(series(STEP, v), null);

        assertThat(found).singleElement().satisfies(f -> {
            assertThat(f.severity()).isEqualTo("HIGH");
            assertThat(f.observed()).isEqualTo(98.0);
        });
    }

    @Test
    void thresholdsAreSpecificToEachSignalAndTooFewPointsNeverCount() {
        double[] v = concat(repeat(30, 10), repeat(91, 20));
        // 91 % : élevé pour la mémoire (seuil 90) et pour le disque (85), pas pour le CPU à 85... si, 91 ≥ 85 : les trois
        assertThat(TelemetryRules.metricFindings("VM", "h", "MEMORY", TelemetryRules.MEMORY, STEP, series(STEP, v), null, "q", false)).hasSize(1);
        assertThat(TelemetryRules.metricFindings("VM", "h", "MEMORY", TelemetryRules.MEMORY, STEP, series(STEP, concat(repeat(30, 10), repeat(89, 20))), null, "q", false)).isEmpty();
        // deux mesures espacées d'une heure ne suffisent pas (moins de 3 points)
        assertThat(TelemetryRules.metricFindings("VM", "h", "CPU", TelemetryRules.CPU, 3600, series(3600, 90, 90), null, "q", false)).isEmpty();
    }

    // ── saut par rapport à la référence ──────────────────────────────────────────────────────────────────────────

    @Test
    void aClearJumpAgainstTheBaselineIsFlaggedEvenBelowTheAbsoluteThreshold() {
        double[] baseline = concat(repeat(20, 30), repeat(22, 30));
        Series window = series(STEP, concat(repeat(21, 10), repeat(70, 20)));      // 70 % < 85 % mais 3× l'habituel

        List<Finding> found = cpu(window, baseline);

        assertThat(found).singleElement().satisfies(f -> {
            assertThat(f.rule()).isEqualTo("JUMP_VS_BASELINE");
            assertThat(f.severity()).isEqualTo("MEDIUM");
            assertThat(f.baseline()).isEqualTo(21.0);
            assertThat(f.observed()).isEqualTo(70.0);
        });
    }

    @Test
    void withoutBaselineOrWithANoisyOneThereIsNoJump() {
        Series window = series(STEP, concat(repeat(21, 10), repeat(70, 20)));

        assertThat(cpu(window, null)).isEmpty();                                           // pas de référence
        assertThat(cpu(window, new double[]{20, 21, 22})).isEmpty();                       // référence trop courte (< 12 mesures)
        // référence très variable (10 ↔ 80) : 70 % est habituel, donc pas un saut
        double[] noisy = IntStream.range(0, 60).mapToDouble(i -> i % 2 == 0 ? 10 : 80).toArray();
        assertThat(cpu(window, noisy)).isEmpty();
    }

    @Test
    void aSustainedOverrunIsNotReportedTwice() {
        double[] baseline = repeat(20, 40);
        Series window = series(STEP, concat(repeat(20, 5), repeat(93, 25)));

        assertThat(cpu(window, baseline)).extracting(Finding::rule).containsExactly("SUSTAINED_HIGH");
    }

    // ── disque : projection de saturation ────────────────────────────────────────────────────────────────────────

    @Test
    void aFillingDiskIsProjectedAndRanked() {
        double[] fast = IntStream.range(0, 40).mapToDouble(i -> 70 + 0.5 * i).toArray();       // +2 %/h : plein dans ~5 h
        List<Finding> found = TelemetryRules.metricFindings("VM", "k8s-master", "DISK", TelemetryRules.DISK, 900, series(900, fast), null, "q", true);

        assertThat(found).extracting(Finding::rule).contains("FILLING_UP");
        Finding filling = found.stream().filter(f -> f.rule().equals("FILLING_UP")).findFirst().orElseThrow();
        assertThat(filling.severity()).isEqualTo("HIGH");
        assertThat(filling.detail()).contains("plein dans environ");
    }

    @Test
    void aSlowOrDecreasingDiskIsLeftAloneAndProjectionCanBeDisabled() {
        double[] slow = IntStream.range(0, 40).mapToDouble(i -> 50 + 0.005 * i).toArray();
        double[] falling = IntStream.range(0, 40).mapToDouble(i -> 80 - 0.5 * i).toArray();
        double[] fast = IntStream.range(0, 40).mapToDouble(i -> 70 + 0.5 * i).toArray();

        assertThat(TelemetryRules.metricFindings("VM", "h", "DISK", TelemetryRules.DISK, 900, series(900, slow), null, "q", true)).isEmpty();
        assertThat(TelemetryRules.metricFindings("VM", "h", "DISK", TelemetryRules.DISK, 900, series(900, falling), null, "q", true)).isEmpty();
        assertThat(TelemetryRules.metricFindings("VM", "h", "CPU", TelemetryRules.CPU, 900, series(900, fast), null, "q", false)
                .stream().map(Finding::rule)).doesNotContain("FILLING_UP");
    }

    // ── logs ─────────────────────────────────────────────────────────────────────────────────────────────────────

    @Test
    void newErrorsAreFlaggedWhenTheBaselineHadNone() {
        List<Finding> found = TelemetryRules.logFindings("APPLICATION", "pfe/backend", new double[]{0, 0, 0, 0}, new double[]{0, 30, 4}, T0, 3600, "LogQL : q");

        assertThat(found).singleElement().satisfies(f -> {
            assertThat(f.rule()).isEqualTo("NEW_ERRORS");
            assertThat(f.severity()).isEqualTo("MEDIUM");
            assertThat(f.observed()).isEqualTo(34.0);
            assertThat(f.baseline()).isEqualTo(0.0);
        });
    }

    @Test
    void anErrorSpikeNeedsBothAnAbsoluteMinimumAndAMultipleOfTheUsualRate() {
        double[] base = {5, 6, 5, 7, 5, 6, 10, 5};

        assertThat(TelemetryRules.logFindings("VM", "hôte master", base, new double[]{6, 19, 7}, T0, 3600, "q")).isEmpty();       // 19 < 20
        List<Finding> spike = TelemetryRules.logFindings("VM", "hôte master", base, new double[]{6, 40, 7}, T0, 3600, "q");
        assertThat(spike).singleElement().satisfies(f -> {
            assertThat(f.rule()).isEqualTo("ERROR_SPIKE");
            assertThat(f.severity()).isEqualTo("MEDIUM");
            assertThat(f.observed()).isEqualTo(40.0);
        });
        assertThat(TelemetryRules.logFindings("VM", "hôte master", base, new double[]{6, 500, 7}, T0, 3600, "q").get(0).severity()).isEqualTo("HIGH");
    }

    @Test
    void aNoisyButSteadySourceIsNotFlaggedAndEmptyWindowsAreIgnored() {
        double[] noisy = {120, 130, 110, 140, 125, 135};
        assertThat(TelemetryRules.logFindings("APPLICATION", "pfe/x", noisy, new double[]{130, 140}, T0, 3600, "q")).isEmpty();
        assertThat(TelemetryRules.logFindings("APPLICATION", "pfe/x", noisy, new double[]{}, T0, 3600, "q")).isEmpty();
    }

    @Test
    void withoutABaselineOnlyTheAbsoluteMinimumApplies() {
        List<Finding> found = TelemetryRules.logFindings("SERVICE", "infra/loki", new double[]{}, new double[]{3, 60, 2}, T0, 3600, "q");

        assertThat(found).singleElement().satisfies(f -> {
            assertThat(f.rule()).isEqualTo("ERROR_SPIKE");
            assertThat(f.baseline()).isNull();
        });
    }

    // ── outils ───────────────────────────────────────────────────────────────────────────────────────────────────

    @Test
    void medianPercentileAndRunsBehave() {
        assertThat(TelemetryRules.median(new double[]{3, 1, 2})).isEqualTo(2.0);
        assertThat(TelemetryRules.median(new double[]{4, 1, 3, 2})).isEqualTo(2.5);
        assertThat(TelemetryRules.median(new double[]{})).isNaN();
        assertThat(TelemetryRules.percentile(new double[]{1, 2, 3, 4, 5, 6, 7, 8, 9, 10}, 0.9)).isBetween(9.0, 10.0);
        assertThat(TelemetryRules.runs(new double[]{1, 9, 9, 9, 1, 9, 9, 1}, 5, 600)).hasSize(1);       // 2 mesures consécutives : trop court
        assertThat(TelemetryRules.runs(new double[]{9, 9, 9}, 5, 60)).isEmpty();                        // 3 mesures mais 3 min < 15 min
    }
}
