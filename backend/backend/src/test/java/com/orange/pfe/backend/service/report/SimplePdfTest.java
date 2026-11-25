package com.orange.pfe.backend.service.report;

import org.junit.jupiter.api.Test;

import java.nio.charset.Charset;
import java.util.ArrayList;
import java.util.List;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

import static org.assertj.core.api.Assertions.assertThat;

/** Le générateur PDF interne produit un document structurellement valide (en-tête, pages, table xref, EOF). */
class SimplePdfTest {

    private static final Charset CP1252 = Charset.forName("windows-1252");

    private static String text(byte[] pdf) {
        return new String(pdf, CP1252);
    }

    private static byte[] sample(int rows) {
        SimplePdf pdf = new SimplePdf();
        pdf.title("Rapport d'activité — équipe (réseau)").subtitle("Période du 2026-11-01 au 2026-11-30");
        pdf.heading("Résumé");
        List<List<String>> table = new ArrayList<>();
        for (int i = 0; i < rows; i++) {
            table.add(List.of("Ligne " + i, String.valueOf(i * 3), "Évolution = +" + i));
        }
        pdf.table(List.of("Indicateur", "Valeur", "Commentaire"), table);
        pdf.bars("Répartition", List.of("alpha", "bêta", "gamma"), List.of(10.0, 4.5, 0.0));
        pdf.paragraph("Aucune donnée supplémentaire.");
        return pdf.build("HELIOS — Rapport");
    }

    @Test
    void producesAWellFormedPdfDocument() {
        byte[] bytes = sample(5);
        String s = text(bytes);

        assertThat(s).startsWith("%PDF-1.4");
        assertThat(s.trim()).endsWith("%%EOF");
        assertThat(s).contains("/Type /Catalog").contains("/BaseFont /Helvetica").contains("/BaseFont /Helvetica-Bold");
        assertThat(s).contains("/Count 1");
    }

    @Test
    void theXrefTableAndStartxrefPointToTheRightOffsets() {
        byte[] bytes = sample(5);
        String s = text(bytes);

        Matcher start = Pattern.compile("startxref\\n(\\d+)\\n%%EOF").matcher(s);
        assertThat(start.find()).isTrue();
        int xrefOffset = Integer.parseInt(start.group(1));
        assertThat(s.substring(xrefOffset)).startsWith("xref\n0 ");

        Matcher entries = Pattern.compile("(\\d{10}) 00000 n ").matcher(s.substring(xrefOffset));
        int number = 0;
        while (entries.find()) {
            number++;
            int offset = Integer.parseInt(entries.group(1));
            assertThat(s.substring(offset)).as("objet " + number).startsWith(number + " 0 obj");
        }
        assertThat(number).isGreaterThanOrEqualTo(6);   // catalogue, pages, 2 polices, 1 page + son contenu
    }

    @Test
    void paginatesLongTablesAcrossSeveralPagesAndKeepsAccents() {
        byte[] bytes = sample(300);
        String s = text(bytes);

        Matcher count = Pattern.compile("/Count (\\d+)").matcher(s);
        assertThat(count.find()).isTrue();
        assertThat(Integer.parseInt(count.group(1))).isGreaterThan(2);
        assertThat(s).contains("page 1/").contains("Évolution").contains("bêta").contains("d'activité");
        // chaque page porte sa pagination « n/N »
        Matcher pages = Pattern.compile("page (\\d+)/(\\d+)").matcher(s);
        int seen = 0;
        while (pages.find()) {
            seen++;
        }
        assertThat(seen).isEqualTo(Integer.parseInt(count.group(1)));
    }

    @Test
    void escapesPdfSpecialCharactersAndReplacesWhatWinAnsiCannotRepresent() {
        assertThat(SimplePdf.escape("a (b) \\ c")).isEqualTo("a \\(b\\) \\\\ c");
        assertThat(SimplePdf.escape("flux → sortie ≥ 3")).isEqualTo("flux -> sortie >= 3");
        assertThat(SimplePdf.escape("日本語")).isEqualTo("???");
        assertThat(SimplePdf.escape(null)).isEmpty();
    }

    @Test
    void truncatesTextThatDoesNotFitAndKeepsShortTextUntouched() {
        assertThat(SimplePdf.fit("court", 9, false, 200)).isEqualTo("court");
        String fitted = SimplePdf.fit("un texte vraiment beaucoup trop long pour la colonne", 9, false, 60);
        assertThat(fitted).endsWith("…");
        assertThat(SimplePdf.width(fitted, 9, false)).isLessThanOrEqualTo(60f);
        assertThat(SimplePdf.wrap("un deux trois quatre cinq six sept huit neuf dix", 10, false, 80).size()).isGreaterThan(1);
    }

    @Test
    void anEmptyDocumentIsStillValid() {
        String s = text(new SimplePdf().build("HELIOS"));
        assertThat(s).startsWith("%PDF-1.4").contains("/Count 1");
        assertThat(s.trim()).endsWith("%%EOF");
    }
}
