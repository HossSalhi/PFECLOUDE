package com.orange.pfe.backend.service.org;

import java.io.IOException;
import java.io.Writer;
import java.time.LocalDate;
import java.time.LocalDateTime;
import java.time.format.DateTimeFormatter;
import java.util.regex.Pattern;

/**
 * Écriture CSV commune aux exports d'audit et de rapports.
 * Séparateur « ; » (Excel en paramètres régionaux français), guillemets RFC 4180, fins de ligne CRLF.
 * Protection contre l'injection de formules (CSV injection) : une cellule texte qui commence par = + - @ (ou
 * tabulation / retour chariot) est préfixée par une apostrophe, sauf s'il s'agit d'un nombre.
 */
public final class CsvSupport {

    public static final char SEPARATOR = ';';
    public static final String BOM = "﻿";

    private static final Pattern NUMBER = Pattern.compile("-?\\d+([.,]\\d+)?");
    private static final DateTimeFormatter DATE_TIME = DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm:ss");

    private CsvSupport() {
    }

    public static String cell(Object value) {
        if (value == null) {
            return "";
        }
        String s;
        if (value instanceof LocalDateTime dt) {
            s = DATE_TIME.format(dt);
        } else if (value instanceof LocalDate d) {
            s = d.toString();
        } else {
            s = String.valueOf(value);
        }
        if (!(value instanceof Number) && needsFormulaGuard(s)) {
            s = "'" + s;
        }
        boolean quote = s.indexOf(SEPARATOR) >= 0 || s.indexOf('"') >= 0 || s.indexOf('\n') >= 0 || s.indexOf('\r') >= 0;
        if (quote) {
            s = "\"" + s.replace("\"", "\"\"") + "\"";
        }
        return s;
    }

    public static void writeRow(Writer out, Object... cells) throws IOException {
        StringBuilder sb = new StringBuilder();
        for (int i = 0; i < cells.length; i++) {
            if (i > 0) {
                sb.append(SEPARATOR);
            }
            sb.append(cell(cells[i]));
        }
        sb.append("\r\n");
        out.write(sb.toString());
    }

    static boolean needsFormulaGuard(String s) {
        if (s.isEmpty()) {
            return false;
        }
        char c = s.charAt(0);
        if (c == '=' || c == '+' || c == '@' || c == '\t' || c == '\r') {
            return true;
        }
        return c == '-' && !NUMBER.matcher(s).matches();
    }
}
