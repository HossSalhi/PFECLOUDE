package com.orange.pfe.backend.service.report;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.orange.pfe.backend.model.report.Report;
import com.orange.pfe.backend.service.org.CsvSupport;
import org.springframework.stereotype.Component;

import java.io.IOException;
import java.io.StringWriter;
import java.nio.charset.StandardCharsets;
import java.time.format.DateTimeFormatter;
import java.util.ArrayList;
import java.util.List;
import java.util.Locale;

/** Export d'un rapport déjà généré (relu depuis son JSON) au format CSV ou PDF. */
@Component
public class ReportExporter {

    private static final ObjectMapper MAPPER = new ObjectMapper();
    private static final DateTimeFormatter DAY = DateTimeFormatter.ofPattern("yyyyMMdd");
    private static final int PDF_MAX_ROWS = 200;
    private static final int PDF_MAX_BARS = 70;

    public record Export(String filename, String contentType, byte[] bytes) {}

    public Export toCsv(Report report) {
        try {
            JsonNode root = MAPPER.readTree(report.getResultJson());
            StringWriter w = new StringWriter();
            w.write(CsvSupport.BOM);
            CsvSupport.writeRow(w, "Titre", text(root, "title"));
            CsvSupport.writeRow(w, "Type", text(root, "typeLabel"));
            CsvSupport.writeRow(w, "Période", text(root, "periodFrom") + " au " + text(root, "periodTo"));
            if (root.has("previousFrom")) {
                CsvSupport.writeRow(w, "Période précédente", text(root, "previousFrom") + " au " + text(root, "previousTo"));
            }
            CsvSupport.writeRow(w, "Généré le", text(root, "generatedAt"));
            CsvSupport.writeRow(w, "Généré par", text(root, "generatedBy"));
            CsvSupport.writeRow(w, "");
            CsvSupport.writeRow(w, "Indicateurs");
            CsvSupport.writeRow(w, "Indicateur", "Valeur", "Unité", "Période précédente", "Écart", "Variation (%)");
            for (JsonNode m : root.path("summary")) {
                CsvSupport.writeRow(w, value(m.path("label")), value(m.path("value")), value(m.path("unit")),
                        value(m.path("previous")), value(m.path("delta")), value(m.path("deltaPercent")));
            }
            for (JsonNode s : root.path("sections")) {
                CsvSupport.writeRow(w, "");
                CsvSupport.writeRow(w, value(s.path("title")));
                List<Object> header = new ArrayList<>();
                s.path("columns").forEach(c -> header.add(value(c)));
                CsvSupport.writeRow(w, header.toArray());
                for (JsonNode row : s.path("rows")) {
                    List<Object> cells = new ArrayList<>();
                    row.forEach(c -> cells.add(value(c)));
                    CsvSupport.writeRow(w, cells.toArray());
                }
            }
            return new Export(filename(report, "csv"), "text/csv; charset=UTF-8",
                    w.toString().getBytes(StandardCharsets.UTF_8));
        } catch (IOException | RuntimeException e) {
            throw new IllegalStateException("Export CSV impossible : " + e.getMessage(), e);
        }
    }

    public Export toPdf(Report report) {
        try {
            JsonNode root = MAPPER.readTree(report.getResultJson());
            SimplePdf pdf = new SimplePdf();
            pdf.title(text(root, "title"));
            pdf.subtitle(text(root, "typeLabel") + " — période du " + text(root, "periodFrom") + " au " + text(root, "periodTo"));
            if (root.has("previousFrom")) {
                pdf.subtitle("Comparée à la période du " + text(root, "previousFrom") + " au " + text(root, "previousTo"));
            }
            pdf.subtitle("Généré le " + text(root, "generatedAt") + " par " + text(root, "generatedBy"));

            JsonNode summary = root.path("summary");
            if (summary.size() > 0) {
                boolean compared = root.has("previousFrom");
                pdf.heading("Résumé");
                List<List<String>> rows = new ArrayList<>();
                for (JsonNode m : summary) {
                    List<String> row = new ArrayList<>();
                    row.add(text(m, "label"));
                    row.add(withUnit(m));
                    if (compared) {
                        row.add(m.has("previous") ? format(m.get("previous")) : "");
                        row.add(m.has("delta") ? signed(m.get("delta")) + percent(m.path("deltaPercent")) : "");
                    }
                    rows.add(row);
                }
                pdf.table(compared ? List.of("Indicateur", "Valeur", "Précédente", "Écart") : List.of("Indicateur", "Valeur"), rows);
            }

            for (JsonNode chart : root.path("charts")) {
                List<String> labels = new ArrayList<>();
                chart.path("labels").forEach(l -> labels.add(l.asText()));
                int limit = Math.min(labels.size(), PDF_MAX_BARS);
                for (JsonNode ds : chart.path("datasets")) {
                    List<Double> values = new ArrayList<>();
                    ds.path("data").forEach(v -> values.add(v.asDouble(0)));
                    String title = text(chart, "title") + (chart.path("datasets").size() > 1 ? " — " + text(ds, "label") : "");
                    pdf.bars(title, labels.subList(0, limit), values.subList(0, Math.min(values.size(), limit)));
                }
            }

            for (JsonNode s : root.path("sections")) {
                pdf.heading(text(s, "title"));
                List<String> columns = new ArrayList<>();
                s.path("columns").forEach(c -> columns.add(c.asText()));
                List<List<String>> rows = new ArrayList<>();
                int n = 0;
                for (JsonNode row : s.path("rows")) {
                    if (n++ >= PDF_MAX_ROWS) {
                        break;
                    }
                    List<String> cells = new ArrayList<>();
                    row.forEach(c -> cells.add(format(c)));
                    rows.add(cells);
                }
                if (rows.isEmpty()) {
                    pdf.paragraph("Aucune donnée sur la période.");
                } else {
                    pdf.table(columns, rows);
                    if (s.path("rows").size() > PDF_MAX_ROWS) {
                        pdf.paragraph("… " + (s.path("rows").size() - PDF_MAX_ROWS) + " ligne(s) supplémentaire(s) : voir l'export CSV.");
                    }
                }
            }
            return new Export(filename(report, "pdf"), "application/pdf", pdf.build("HELIOS — Rapport"));
        } catch (IOException | RuntimeException e) {
            throw new IllegalStateException("Export PDF impossible : " + e.getMessage(), e);
        }
    }

    // ── Outils ────────────────────────────────────────────────────────────────

    private static String filename(Report r, String ext) {
        return "rapport-" + r.getReportType().name().toLowerCase(Locale.ROOT).replace('_', '-') + "-"
                + DAY.format(r.getPeriodStart()) + "-" + DAY.format(r.getPeriodEnd()) + "." + ext;
    }

    private static String text(JsonNode n, String field) {
        JsonNode v = n.path(field);
        return v.isMissingNode() || v.isNull() ? "" : v.asText();
    }

    private static Object value(JsonNode n) {
        if (n == null || n.isMissingNode() || n.isNull()) {
            return "";
        }
        return n.isNumber() ? n.numberValue() : n.asText();
    }

    private static String format(JsonNode n) {
        if (n == null || n.isMissingNode() || n.isNull()) {
            return "";
        }
        if (n.isNumber()) {
            double d = n.asDouble();
            return d == Math.rint(d) ? String.valueOf((long) d) : String.format(Locale.ROOT, "%.1f", d);
        }
        return n.asText();
    }

    private static String withUnit(JsonNode m) {
        String unit = text(m, "unit");
        return format(m.get("value")) + (unit.isEmpty() ? "" : " " + unit);
    }

    private static String signed(JsonNode delta) {
        double d = delta.asDouble();
        String s = format(delta);
        return d > 0 ? "+" + s : s;
    }

    private static String percent(JsonNode p) {
        if (p == null || p.isNull() || p.isMissingNode()) {
            return "";
        }
        double d = p.asDouble();
        return " (" + (d > 0 ? "+" : "") + String.format(Locale.ROOT, "%.1f", d) + " %)";
    }
}
