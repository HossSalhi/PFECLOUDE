package com.orange.pfe.backend.service.report;

import com.orange.pfe.backend.model.report.Report;
import com.orange.pfe.backend.model.report.ReportType;
import org.junit.jupiter.api.Test;

import java.nio.charset.Charset;
import java.nio.charset.StandardCharsets;
import java.time.LocalDateTime;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

/** Export CSV et PDF d'un rapport relu depuis son JSON, y compris les données hostiles. */
class ReportExporterTest {

    private static final String JSON = "{\"type\":\"TEAMS_TASKS\",\"typeLabel\":\"Équipes et tâches\",\"title\":\"Rapport de test\","
            + "\"periodFrom\":\"2026-11-01T00:00\",\"periodTo\":\"2026-11-02T00:00\","
            + "\"previousFrom\":\"2026-10-31T00:00\",\"previousTo\":\"2026-11-01T00:00\","
            + "\"generatedAt\":\"2026-11-02T01:00\",\"generatedBy\":\"alice\","
            + "\"summary\":[{\"label\":\"Tâches créées\",\"value\":3,\"previous\":2,\"delta\":1.0,\"deltaPercent\":50.0},"
            + "{\"label\":\"Taux\",\"value\":33.3,\"unit\":\"%\"}],"
            + "\"sections\":[{\"title\":\"Par équipe\",\"columns\":[\"Équipe\",\"Créées\"],"
            + "\"rows\":[[\"=1+1\",3],[\"R&D; (labo)\",2],[\"-5 jours\",1]]}],"
            + "\"charts\":[{\"type\":\"bar\",\"title\":\"Tâches par équipe\",\"labels\":[\"a\",\"b\"],"
            + "\"datasets\":[{\"label\":\"Tâches\",\"data\":[3,2]}]}]}";

    private final ReportExporter exporter = new ReportExporter();

    private static Report report(String json) {
        Report r = new Report();
        r.setReportType(ReportType.TEAMS_TASKS);
        r.setTitle("Rapport de test");
        r.setPeriodStart(LocalDateTime.of(2026, 11, 1, 0, 0));
        r.setPeriodEnd(LocalDateTime.of(2026, 11, 2, 0, 0));
        r.setResultJson(json);
        return r;
    }

    @Test
    void csvContainsMetadataIndicatorsAndSectionsWithSemicolonSeparators() {
        ReportExporter.Export export = exporter.toCsv(report(JSON));
        String csv = new String(export.bytes(), StandardCharsets.UTF_8);

        assertThat(export.contentType()).startsWith("text/csv");
        assertThat(export.filename()).isEqualTo("rapport-teams-tasks-20261101-20261102.csv");
        assertThat(csv).startsWith("﻿Titre;Rapport de test\r\n");
        assertThat(csv).contains("Généré par;alice").contains("Période précédente;2026-10-31T00:00 au 2026-11-01T00:00");
        assertThat(csv).contains("Indicateur;Valeur;Unité;Période précédente;Écart;Variation (%)");
        assertThat(csv).contains("Tâches créées;3;;2;1.0;50.0");
        assertThat(csv).contains("Taux;33.3;%;;;");
        assertThat(csv).contains("Par équipe").contains("Équipe;Créées");
    }

    @Test
    void csvNeutralisesFormulasAndQuotesSeparatorsInData() {
        String csv = new String(exporter.toCsv(report(JSON)).bytes(), StandardCharsets.UTF_8);

        assertThat(csv).contains("'=1+1;3");                 // formule neutralisée
        assertThat(csv).contains("\"R&D; (labo)\";2");       // séparateur dans la donnée : guillemets
        assertThat(csv).contains("'-5 jours;1");             // texte commençant par un tiret
        assertThat(csv).doesNotContain("\r\n=1+1");
    }

    @Test
    void pdfIsValidAndShowsTheContent() {
        ReportExporter.Export export = exporter.toPdf(report(JSON));
        String pdf = new String(export.bytes(), Charset.forName("windows-1252"));

        assertThat(export.contentType()).isEqualTo("application/pdf");
        assertThat(export.filename()).endsWith(".pdf");
        assertThat(pdf).startsWith("%PDF-1.4");
        assertThat(pdf.trim()).endsWith("%%EOF");
        assertThat(pdf).contains("Rapport de test").contains("Tâches créées").contains("Par équipe")
                .contains("Tâches par équipe").contains("Comparée à la période");
    }

    @Test
    void corruptedContentIsReportedClearlyInsteadOfCrashing() {
        assertThatThrownBy(() -> exporter.toCsv(report("{pas du json")))
                .isInstanceOf(IllegalStateException.class).hasMessageContaining("Export CSV impossible");
        assertThatThrownBy(() -> exporter.toPdf(report("{pas du json")))
                .isInstanceOf(IllegalStateException.class).hasMessageContaining("Export PDF impossible");
        assertThatThrownBy(() -> exporter.toCsv(report(null))).isInstanceOf(IllegalStateException.class);
    }
}
