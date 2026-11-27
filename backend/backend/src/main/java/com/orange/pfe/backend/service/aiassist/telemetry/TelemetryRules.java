package com.orange.pfe.backend.service.aiassist.telemetry;

import com.orange.pfe.backend.service.aiassist.telemetry.TelemetryModels.Finding;

import java.time.Instant;
import java.time.LocalDateTime;
import java.time.ZoneId;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;
import java.util.Locale;

/**
 * Règles DÉTERMINISTES de détection d'anomalies (aucune IA, aucune donnée inventée) : fonctions pures sur des séries
 * numériques, donc testables sans Prometheus ni Loki. Chaque règle a un seuil explicite ; une base de référence (période
 * précédente) n'est utilisée que si elle existe.
 */
final class TelemetryRules {

    private TelemetryRules() {
    }

    /** Une série de mesures : instants (secondes epoch) et valeurs, de même longueur, triée par instant. */
    record Series(double[] t, double[] v) {
        int size() {
            return v.length;
        }
    }

    // ── Métriques (Prometheus) ───────────────────────────────────────────────────────────────────────────────────

    /** Seuils d'un signal : valeur « élevée » (MEDIUM) et valeur « critique » (HIGH), en pourcentage. */
    record Thresholds(double high, double critical) {
    }

    static final Thresholds CPU = new Thresholds(85, 95);
    static final Thresholds MEMORY = new Thresholds(90, 95);
    static final Thresholds DISK = new Thresholds(85, 92);

    /** Durée minimale d'un dépassement pour compter comme « soutenu » : 15 minutes et au moins 3 mesures consécutives. */
    static final double MIN_RUN_SECONDS = 15 * 60.0;
    static final int MIN_RUN_POINTS = 3;

    /**
     * Règles sur une série de métrique en pourcentage : dépassement SOUTENU d'un seuil, SAUT par rapport à la référence (si
     * elle existe), et, pour le disque, PROJECTION de saturation. {@code baseline} peut être {@code null} ou vide.
     */
    static List<Finding> metricFindings(String scope, String entity, String signal, Thresholds th, double stepSec,
                                        Series window, double[] baseline, String evidence, boolean projectFull) {
        List<Finding> out = new ArrayList<>();
        double base = baseline != null && baseline.length >= 12 ? median(baseline) : Double.NaN;
        Double baseObj = Double.isNaN(base) ? null : round1(base);

        // 1. dépassement soutenu du seuil
        List<int[]> high = runs(window.v(), th.high(), stepSec);
        boolean sustained = false;
        if (!high.isEmpty()) {
            int[] worst = longest(high);
            double peak = max(window.v(), worst[0], worst[1]);
            sustained = true;
            out.add(finding(scope, entity, signal, "SUSTAINED_HIGH", peak >= th.critical() ? "HIGH" : "MEDIUM", window, worst,
                    peak, baseObj, "%",
                    String.format(Locale.ROOT, "%s au-dessus de %.0f %% pendant %s (pic %.1f %%)", label(signal), th.high(),
                            duration(window, worst, stepSec), peak), evidence));
        }

        // 2. saut par rapport à la période de référence (médiane + écart absolu médian, robustes aux valeurs isolées)
        if (!Double.isNaN(base) && !sustained) {
            double mad = median(Arrays.stream(baseline).map(x -> Math.abs(x - base)).toArray());
            double sigma = Math.max(2.0, 1.4826 * mad);
            double threshold = base + Math.max(20.0, 4 * sigma);
            List<int[]> jumps = runs(window.v(), threshold, stepSec);
            if (!jumps.isEmpty()) {
                int[] worst = longest(jumps);
                double peak = max(window.v(), worst[0], worst[1]);
                out.add(finding(scope, entity, signal, "JUMP_VS_BASELINE", peak >= th.critical() ? "HIGH" : "MEDIUM", window,
                        worst, peak, baseObj, "%",
                        String.format(Locale.ROOT, "%s en hausse nette : %.1f %% contre %.1f %% habituellement (pendant %s)",
                                label(signal), peak, base, duration(window, worst, stepSec)), evidence));
            }
        }

        // 3. disque : projection de saturation par régression linéaire (au moins 24 mesures sur 6 h)
        if (projectFull && window.size() >= 24 && span(window) >= 6 * 3600.0) {
            double slopePerHour = slope(window) * 3600.0;
            double last = window.v()[window.size() - 1];
            if (slopePerHour >= 0.05 && last < 100) {
                double hoursLeft = (100 - last) / slopePerHour;
                if (hoursLeft < 72) {
                    int n = window.size();
                    out.add(finding(scope, entity, signal, "FILLING_UP", hoursLeft < 24 ? "HIGH" : "MEDIUM", window,
                            new int[]{0, n - 1}, last, baseObj, "%",
                            String.format(Locale.ROOT, "Disque à %.1f %% et en hausse de %.2f %%/h : plein dans environ %.0f h",
                                    last, slopePerHour, hoursLeft), evidence));
                }
            }
        }
        return out;
    }

    // ── Logs (Loki) : nombre de lignes d'erreur par heure ────────────────────────────────────────────────────────

    /** Nombre minimal d'erreurs par heure avant de parler de pic. */
    static final double MIN_ERRORS_PER_HOUR = 20;

    /**
     * Règles sur les erreurs d'une source de logs : nouvelles erreurs (aucune sur la période de référence) ou PIC par rapport
     * aux heures habituelles. {@code baselineCounts} et {@code windowCounts} sont des comptes HORAIRES (zéros inclus).
     */
    static List<Finding> logFindings(String scope, String entity, double[] baselineCounts, double[] windowCounts,
                                     long windowStartEpoch, double stepSec, String evidence) {
        List<Finding> out = new ArrayList<>();
        if (windowCounts.length == 0) {
            return out;
        }
        double windowMax = Arrays.stream(windowCounts).max().orElse(0);
        double windowTotal = Arrays.stream(windowCounts).sum();
        boolean hasBaseline = baselineCounts.length > 0;
        double baseMedian = hasBaseline ? median(baselineCounts) : 0;
        double baseTotal = hasBaseline ? Arrays.stream(baselineCounts).sum() : 0;
        int peakIndex = indexOfMax(windowCounts);
        LocalDateTime peakAt = at(windowStartEpoch + (long) ((peakIndex + 1) * stepSec));
        LocalDateTime from = at(windowStartEpoch);
        LocalDateTime to = at(windowStartEpoch + (long) (windowCounts.length * stepSec));

        if (hasBaseline && baseTotal == 0 && windowTotal >= MIN_ERRORS_PER_HOUR) {
            out.add(new Finding(scope, entity, "LOG_ERRORS", "NEW_ERRORS", windowMax >= 5 * MIN_ERRORS_PER_HOUR ? "HIGH" : "MEDIUM",
                    from, to, round1(windowTotal), 0.0, "erreurs",
                    String.format(Locale.ROOT, "%.0f lignes d'erreur alors que la période de référence n'en avait aucune (pic %.0f/h à %s)",
                            windowTotal, windowMax, peakAt), evidence, null));
            return out;
        }
        double p90 = hasBaseline ? percentile(baselineCounts, 0.90) : 0;
        double threshold = Math.max(MIN_ERRORS_PER_HOUR, Math.max(3 * baseMedian, 2 * p90));
        if (windowMax >= threshold) {
            out.add(new Finding(scope, entity, "LOG_ERRORS", "ERROR_SPIKE", windowMax >= 5 * threshold ? "HIGH" : "MEDIUM", from, to,
                    round1(windowMax), hasBaseline ? round1(baseMedian) : null, "erreurs/h",
                    String.format(Locale.ROOT, "Pic de %.0f erreurs/h à %s (seuil %.0f, habituel %.0f/h)", windowMax, peakAt,
                            threshold, baseMedian), evidence, null));
        }
        return out;
    }

    // ── Outils numériques ────────────────────────────────────────────────────────────────────────────────────────

    /** Plages d'indices consécutifs {@code v >= threshold}, assez longues (≥ 3 mesures et ≥ 15 min) pour compter. */
    static List<int[]> runs(double[] v, double threshold, double stepSec) {
        List<int[]> out = new ArrayList<>();
        int i = 0;
        while (i < v.length) {
            if (v[i] >= threshold) {
                int j = i;
                while (j + 1 < v.length && v[j + 1] >= threshold) {
                    j++;
                }
                int points = j - i + 1;
                if (points >= MIN_RUN_POINTS && points * stepSec >= MIN_RUN_SECONDS) {
                    out.add(new int[]{i, j});
                }
                i = j + 1;
            } else {
                i++;
            }
        }
        return out;
    }

    private static int[] longest(List<int[]> runs) {
        int[] best = runs.get(0);
        for (int[] r : runs) {
            if (r[1] - r[0] > best[1] - best[0]) {
                best = r;
            }
        }
        return best;
    }

    static double median(double[] values) {
        if (values.length == 0) {
            return Double.NaN;
        }
        double[] s = values.clone();
        Arrays.sort(s);
        int n = s.length;
        return n % 2 == 1 ? s[n / 2] : (s[n / 2 - 1] + s[n / 2]) / 2.0;
    }

    static double percentile(double[] values, double p) {
        if (values.length == 0) {
            return 0;
        }
        double[] s = values.clone();
        Arrays.sort(s);
        return s[(int) Math.min(s.length - 1, Math.floor(p * (s.length - 1) + 0.5))];
    }

    /** Pente (unité par seconde) de la régression linéaire de la série. */
    static double slope(Series s) {
        int n = s.size();
        double mt = Arrays.stream(s.t()).average().orElse(0);
        double mv = Arrays.stream(s.v()).average().orElse(0);
        double num = 0, den = 0;
        for (int i = 0; i < n; i++) {
            num += (s.t()[i] - mt) * (s.v()[i] - mv);
            den += (s.t()[i] - mt) * (s.t()[i] - mt);
        }
        return den == 0 ? 0 : num / den;
    }

    private static double span(Series s) {
        return s.size() < 2 ? 0 : s.t()[s.size() - 1] - s.t()[0];
    }

    private static double max(double[] v, int from, int to) {
        double m = Double.NEGATIVE_INFINITY;
        for (int i = from; i <= to; i++) {
            m = Math.max(m, v[i]);
        }
        return m;
    }

    private static int indexOfMax(double[] v) {
        int best = 0;
        for (int i = 1; i < v.length; i++) {
            if (v[i] > v[best]) {
                best = i;
            }
        }
        return best;
    }

    private static Finding finding(String scope, String entity, String signal, String rule, String severity, Series s, int[] run,
                                   double observed, Double baseline, String unit, String detail, String evidence) {
        return new Finding(scope, entity, signal, rule, severity, at((long) s.t()[run[0]]), at((long) s.t()[run[1]]),
                round1(observed), baseline, unit, detail, evidence, null);
    }

    private static String duration(Series s, int[] run, double stepSec) {
        double seconds = (run[1] - run[0] + 1) * stepSec;
        return seconds >= 3600 ? String.format(Locale.ROOT, "%.1f h", seconds / 3600) : String.format(Locale.ROOT, "%.0f min", seconds / 60);
    }

    private static String label(String signal) {
        return switch (signal) {
            case "CPU" -> "CPU";
            case "MEMORY" -> "Mémoire";
            case "DISK" -> "Disque";
            default -> signal;
        };
    }

    static LocalDateTime at(long epochSeconds) {
        return LocalDateTime.ofInstant(Instant.ofEpochSecond(epochSeconds), ZoneId.systemDefault());
    }

    static double round1(double v) {
        return Math.round(v * 10.0) / 10.0;
    }
}
