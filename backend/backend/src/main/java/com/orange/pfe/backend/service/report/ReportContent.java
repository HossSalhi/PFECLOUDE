package com.orange.pfe.backend.service.report;

import java.time.LocalDateTime;
import java.util.ArrayList;
import java.util.Collection;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * Contenu d'un rapport : indicateurs, tableaux et graphiques. Structure simple (maps / listes / nombres / textes)
 * sérialisée en JSON et conservée avec le rapport ; les exports CSV et PDF la relisent.
 */
public final class ReportContent {

    private final List<Map<String, Object>> summary = new ArrayList<>();
    private final List<Map<String, Object>> sections = new ArrayList<>();
    private final List<Map<String, Object>> charts = new ArrayList<>();

    /** Indicateur ; si {@code previous} est fourni (comparaison), l'écart et sa variation en % sont calculés. */
    public ReportContent metric(String label, Number value, Number previous, String unit) {
        Map<String, Object> m = new LinkedHashMap<>();
        m.put("label", label);
        m.put("value", value);
        if (unit != null) {
            m.put("unit", unit);
        }
        if (previous != null) {
            double delta = value.doubleValue() - previous.doubleValue();
            m.put("previous", previous);
            m.put("delta", round1(delta));
            m.put("deltaPercent", previous.doubleValue() == 0 ? null : round1(delta / previous.doubleValue() * 100.0));
        }
        summary.add(m);
        return this;
    }

    public ReportContent section(String title, List<String> columns, List<List<Object>> rows) {
        Map<String, Object> s = new LinkedHashMap<>();
        s.put("title", title);
        s.put("columns", columns);
        s.put("rows", rows);
        sections.add(s);
        return this;
    }

    /** Graphique : type {@code bar}, {@code line} ou {@code doughnut} ; une série par entrée de {@code datasets}. */
    public ReportContent chart(String type, String title, List<String> labels, Map<String, ? extends Collection<? extends Number>> datasets) {
        Map<String, Object> c = new LinkedHashMap<>();
        c.put("type", type);
        c.put("title", title);
        c.put("labels", labels);
        List<Map<String, Object>> sets = new ArrayList<>();
        datasets.forEach((name, values) -> {
            Map<String, Object> d = new LinkedHashMap<>();
            d.put("label", name);
            d.put("data", new ArrayList<>(values));
            sets.add(d);
        });
        c.put("datasets", sets);
        charts.add(c);
        return this;
    }

    public boolean isEmpty() {
        return summary.isEmpty() && sections.isEmpty() && charts.isEmpty();
    }

    /** Résumé court (trois premiers indicateurs) pour la liste des rapports. */
    public String summaryLine() {
        StringBuilder sb = new StringBuilder();
        int n = 0;
        for (Map<String, Object> m : summary) {
            if (n++ >= 3) {
                break;
            }
            if (sb.length() > 0) {
                sb.append(" · ");
            }
            sb.append(m.get("label")).append(" : ").append(format(m.get("value")));
            if (m.get("unit") != null) {
                sb.append(' ').append(m.get("unit"));
            }
        }
        return sb.toString();
    }

    public Map<String, Object> toMap(String type, String typeLabel, String title, ReportPeriod period,
                                     ReportPeriod previous, String generatedBy, LocalDateTime generatedAt) {
        Map<String, Object> root = new LinkedHashMap<>();
        root.put("type", type);
        root.put("typeLabel", typeLabel);
        root.put("title", title);
        root.put("periodFrom", period.from().toString());
        root.put("periodTo", period.to().toString());
        if (previous != null) {
            root.put("previousFrom", previous.from().toString());
            root.put("previousTo", previous.to().toString());
        }
        root.put("generatedAt", generatedAt.toString());
        root.put("generatedBy", generatedBy);
        root.put("summary", summary);
        root.put("sections", sections);
        root.put("charts", charts);
        return root;
    }

    static double round1(double v) {
        return Math.round(v * 10.0) / 10.0;
    }

    private static String format(Object v) {
        if (v instanceof Double d && d == Math.rint(d)) {
            return String.valueOf(d.longValue());
        }
        return String.valueOf(v);
    }
}
