package com.orange.pfe.backend.service.org;

import org.junit.jupiter.api.Test;

import java.io.StringWriter;
import java.time.LocalDateTime;

import static org.assertj.core.api.Assertions.assertThat;

/** Export CSV : séparateur, guillemets et protection contre l'injection de formules. */
class CsvSupportTest {

    @Test
    void guardsAgainstFormulaInjection() {
        assertThat(CsvSupport.cell("=HYPERLINK(\"http://x\")")).startsWith("\"'=");
        assertThat(CsvSupport.cell("+33 1 23")).startsWith("'+");
        assertThat(CsvSupport.cell("@SUM(A1)")).isEqualTo("'@SUM(A1)");
        assertThat(CsvSupport.cell("-1+2")).isEqualTo("'-1+2");
        assertThat(CsvSupport.cell("\tcmd")).startsWith("'");
    }

    @Test
    void doesNotPrefixNumbersOrOrdinaryText() {
        assertThat(CsvSupport.cell(-5)).isEqualTo("-5");
        assertThat(CsvSupport.cell("-5")).isEqualTo("-5");
        assertThat(CsvSupport.cell("-12,5")).isEqualTo("-12,5");
        assertThat(CsvSupport.cell("équipe réseau")).isEqualTo("équipe réseau");
        assertThat(CsvSupport.cell(null)).isEmpty();
    }

    @Test
    void quotesSeparatorsQuotesAndNewlines() {
        assertThat(CsvSupport.cell("a;b")).isEqualTo("\"a;b\"");
        assertThat(CsvSupport.cell("dit \"oui\"")).isEqualTo("\"dit \"\"oui\"\"\"");
        assertThat(CsvSupport.cell("l1\nl2")).isEqualTo("\"l1\nl2\"");
    }

    @Test
    void formatsDatesAndWritesRowsWithCrlf() throws Exception {
        StringWriter w = new StringWriter();
        CsvSupport.writeRow(w, 1, LocalDateTime.of(2026, 11, 25, 9, 5, 7), "x");
        assertThat(w.toString()).isEqualTo("1;2026-11-25 09:05:07;x\r\n");
    }
}
