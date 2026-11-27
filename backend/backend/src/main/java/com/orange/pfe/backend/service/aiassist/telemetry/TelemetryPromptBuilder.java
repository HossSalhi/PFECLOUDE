package com.orange.pfe.backend.service.aiassist.telemetry;

import com.orange.pfe.backend.service.aiassist.AiNames;
import com.orange.pfe.backend.service.aiassist.telemetry.TelemetryModels.Detection;
import com.orange.pfe.backend.service.aiassist.telemetry.TelemetryModels.EntityCoverage;
import com.orange.pfe.backend.service.aiassist.telemetry.TelemetryModels.Finding;

import java.util.Comparator;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Set;
import java.util.regex.Pattern;

/**
 * Construit le prompt envoyé au fournisseur IA et garantit ce qui n'en sort jamais : aucune ligne de log brute, aucun secret,
 * aucun nom d'utilisateur ou d'équipe, aucune adresse IP, aucun identifiant interne de ressource. Les machines, applications
 * et services sont remplacés par des alias (« Machine A », « Application B »…) rétablis seulement à l'affichage du texte reçu.
 * Seuls des constats chiffrés, des libellés de règles et des notes de qualité des données sont transmis.
 */
final class TelemetryPromptBuilder {

    private static final Pattern IPV4 = Pattern.compile("\\b\\d{1,3}(?:\\.\\d{1,3}){3}(?::\\d{1,5})?\\b");
    private static final Pattern EMAIL = Pattern.compile("[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\\.[A-Za-z]{2,}");
    private static final Pattern SECRET = Pattern.compile("(?i)\\b(bearer|basic)\\s+[A-Za-z0-9._~+/=-]{6,}|eyJ[A-Za-z0-9_-]{8,}\\.[A-Za-z0-9_-]{8,}\\.[A-Za-z0-9_-]{4,}|gsk_[A-Za-z0-9]{10,}");

    private TelemetryPromptBuilder() {
    }

    static String build(Detection d, String scope, int minutes, AiNames names) {
        Set<String> entities = entityNames(d);
        StringBuilder p = new StringBuilder("Analyse automatique des métriques (Prometheus) et des logs (Loki) de HELIOS.\n")
                .append("Périmètre : ").append(scope).append(" ; période : ").append(minutes).append(" min, du ").append(d.windowStart())
                .append(" au ").append(d.windowEnd()).append(".\nVerdict des règles : ").append(TelemetryAnalysisService.verdictLabel(d.verdict()))
                .append(" ; qualité des données : ").append(qualityLabel(d.dataQuality())).append(" ; ")
                .append(d.findings().size()).append(" constat(s) dont ").append(d.count("HIGH")).append(" de gravité haute ; ")
                .append(d.entities()).append(" entité(s) analysée(s).\n");
        int i = 0;
        for (Finding f : d.findings()) {
            if (++i > 12) {
                p.append("… et ").append(d.findings().size() - 12).append(" autre(s) constat(s).\n");
                break;
            }
            p.append(i).append(". [").append(f.severity()).append("] ").append(names.alias(f.entity(), prefixOf(f.entity())))
                    .append(" — ").append(f.detail()).append('\n');
        }
        p.append("Qualité des sources :\n");
        d.quality().forEach(q -> p.append("- ").append(q.source()).append(q.available() ? "" : " (INDISPONIBLE)").append(" : ").append(q.note()).append('\n'));
        String text = sanitize(p.toString(), entities, names);
        return text + "\nRédige une synthèse de 3 à 5 phrases : dis clairement s'il y a des anomalies ou non — et dis que l'absence d'anomalie n'est "
                + "établie que si la qualité des données est complète —, résume les constats les plus importants avec leurs chiffres, et "
                + "signale les données manquantes, les couvertures partielles et les sources indisponibles. Ne déduis aucune cause, ne conclus "
                + "à aucune amélioration ni stabilité que les chiffres ne montrent pas, et ne propose aucune action.";
    }

    /** Remplace les ressources connues par des alias, puis masque adresses IP, e-mails et jetons éventuels. */
    static String sanitize(String text, Set<String> entities, AiNames names) {
        String out = text;
        List<String> sorted = entities.stream().sorted(Comparator.comparingInt(String::length).reversed()).toList();
        for (String e : sorted) {
            if (!e.isBlank()) {
                out = out.replace(e, names.alias(e, prefixOf(e)));
            }
        }
        out = SECRET.matcher(out).replaceAll("[secret masqué]");
        out = EMAIL.matcher(out).replaceAll("[e-mail masqué]");
        return IPV4.matcher(out).replaceAll("[adresse masquée]");
    }

    static Set<String> entityNames(Detection d) {
        Set<String> names = new LinkedHashSet<>();
        d.findings().forEach(f -> names.add(f.entity()));
        if (d.trace() != null) {
            for (EntityCoverage c : d.trace().coverage()) {
                names.add(c.entity());
            }
        }
        return names;
    }

    /** Catégorie neutre d'une ressource : on ne transmet jamais son nom. */
    static String prefixOf(String entity) {
        if (entity.startsWith("hôte ") || !entity.contains("/")) {
            return "Machine";
        }
        return switch (TelemetryDetector.scopeOfApp(entity)) {
            case CLUSTER -> "Composant du cluster";
            case SERVICE -> "Service";
            case APPLICATION, VM -> "Application";
        };
    }

    private static String qualityLabel(String q) {
        return TelemetryModels.DATA_COMPLETE.equals(q) ? "complète" : TelemetryModels.DATA_PARTIAL.equals(q) ? "PARTIELLE" : "INSUFFISANTE";
    }
}
