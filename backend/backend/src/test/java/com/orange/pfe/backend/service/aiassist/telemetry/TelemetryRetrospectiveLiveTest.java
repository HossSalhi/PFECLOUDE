package com.orange.pfe.backend.service.aiassist.telemetry;

import com.orange.pfe.backend.config.InfraHealthProperties;
import com.orange.pfe.backend.service.aiassist.telemetry.TelemetryModels.Detection;
import com.orange.pfe.backend.service.aiassist.telemetry.TelemetryModels.Finding;
import com.orange.pfe.backend.service.supervision.SupervisionClient;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.condition.EnabledIfSystemProperty;

import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Instant;
import java.time.temporal.ChronoUnit;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.TreeMap;

/**
 * RÉTROSPECTIVE en LECTURE SEULE : rejoue le détecteur sur les jours disponibles (requêtes GET vers Prometheus et Loki, rien n'est
 * écrit ailleurs que dans le fichier de rapport). Désactivée par défaut ; lancer avec
 * {@code -Dhelios.retro=true [-Dhelios.retro.days=7] [-Dhelios.retro.out=chemin.md]}. Elle ne modifie AUCUN seuil : elle les mesure.
 */
@EnabledIfSystemProperty(named = "helios.retro", matches = "true")
class TelemetryRetrospectiveLiveTest {

    @Test
    void replayTheAvailableDaysAndReport() throws Exception {
        int days = Integer.getInteger("helios.retro.days", 7);
        Path out = Path.of(System.getProperty("helios.retro.out", "target/retrospective.md"));
        TelemetryDetector detector = new TelemetryDetector(new SupervisionClient(new InfraHealthProperties()));
        Instant now = Instant.now().truncatedTo(ChronoUnit.MINUTES);

        StringBuilder md = new StringBuilder("# Rétrospective du détecteur (lecture seule)\n\nExécutée le " + now + " sur " + days + " jour(s).\n\n");
        List<Row> rows = new ArrayList<>();
        // fenêtres de 6 h tous les 6 h (métriques + logs sur les 6 h), puis fenêtres de 24 h par jour
        for (int h = days * 24; h >= 6; h -= 6) {
            rows.add(run(detector, now.minus(h - 6, ChronoUnit.HOURS), 360));
        }
        for (int d = days; d >= 1; d--) {
            rows.add(run(detector, now.minus((d - 1) * 24L, ChronoUnit.HOURS), 1440));
        }

        md.append("| Fenêtre (fin) | Durée | Verdict | Qualité | Constats | Couverture min. | Requêtes en échec |\n|---|---|---|---|---|---|---|\n");
        for (Row r : rows) {
            md.append("| ").append(r.end).append(" | ").append(r.minutes / 60).append(" h | ").append(r.d.verdict()).append(" | ").append(r.d.dataQuality())
                    .append(" | ").append(r.d.findings().size()).append(" | ").append(String.format("%.0f %%", r.minCoverage * 100)).append(" | ")
                    .append(r.failed).append(" |\n");
        }
        Map<String, Integer> verdicts = new TreeMap<>();
        rows.forEach(r -> verdicts.merge(r.d.verdict(), 1, Integer::sum));
        md.append("\n## Répartition des verdicts\n").append(verdicts).append("\n\n## Constats par fenêtre\n");
        for (Row r : rows) {
            if (!r.d.findings().isEmpty()) {
                md.append("\n### ").append(r.end).append(" (").append(r.minutes / 60).append(" h)\n");
                for (Finding f : r.d.findings()) {
                    md.append("- [").append(f.severity()).append("] ").append(f.entity()).append(" — ").append(f.signal()).append(" / ").append(f.rule())
                            .append(" : ").append(f.detail()).append('\n');
                }
                long simultaneous = r.d.findings().stream().filter(f -> "JUMP_VS_BASELINE".equals(f.rule())).map(Finding::entity).distinct().count();
                if (simultaneous >= 3) {
                    md.append("- ⚠ ").append(simultaneous).append(" machines en hausse nette simultanée : faux positif probable (charge commune, gel de l'hôte).\n");
                }
            }
        }
        md.append("\n## Périodes sans données suffisantes\n");
        rows.stream().filter(r -> !"NO_ANOMALY".equals(r.d.verdict()) && !"ANOMALIES".equals(r.d.verdict()) || r.minCoverage < 0.8)
                .forEach(r -> md.append("- ").append(r.end).append(" (").append(r.minutes / 60).append(" h) : ").append(r.d.verdict()).append(", couverture min. ")
                        .append(String.format("%.0f %%", r.minCoverage * 100)).append('\n'));
        md.append("\n## Entités sous 80 % de couverture (nombre de fenêtres concernées)\n");
        Map<String, Integer> low = new TreeMap<>();
        for (Row r : rows) {
            r.d.trace().coverage().stream().filter(c -> c.coverage() < 0.8)
                    .forEach(c -> low.merge(c.source() + " · " + c.entity() + " · " + c.signal() + " (" + c.note() + ")", 1, Integer::sum));
        }
        low.forEach((k, v) -> md.append("- ").append(k).append(" : ").append(v).append(" fenêtre(s)\n"));
        Files.createDirectories(out.toAbsolutePath().getParent());
        Files.writeString(out, md.toString());
        System.out.println("Rétrospective écrite : " + out.toAbsolutePath());
    }

    private record Row(Instant end, int minutes, Detection d, double minCoverage, int failed) {
    }

    private static Row run(TelemetryDetector detector, Instant end, int minutes) {
        Detection d = detector.detect(end, minutes, null);
        double min = d.trace().coverage().stream().mapToDouble(TelemetryModels.EntityCoverage::coverage).min().orElse(0);
        int failed = (int) d.trace().queries().stream().filter(q -> "FAILED".equals(q.status())).count();
        return new Row(end, minutes, d, min, failed);
    }
}
