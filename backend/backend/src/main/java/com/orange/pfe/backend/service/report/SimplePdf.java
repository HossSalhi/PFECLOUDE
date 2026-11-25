package com.orange.pfe.backend.service.report;

import java.io.ByteArrayOutputStream;
import java.nio.charset.Charset;
import java.nio.charset.CharsetEncoder;
import java.text.Normalizer;
import java.util.ArrayList;
import java.util.List;
import java.util.Locale;

/**
 * Générateur PDF minimal (PDF 1.4, polices standard Helvetica / Helvetica-Bold, encodage WinAnsi) sans aucune
 * dépendance externe : titres, textes, tableaux paginés et barres horizontales. Suffisant pour les rapports HELIOS
 * (texte et tableaux) ; il évite d'ajouter une bibliothèque PDF au projet.
 */
public final class SimplePdf {

    private static final float PAGE_W = 595f;
    private static final float PAGE_H = 842f;
    private static final float MARGIN = 40f;
    private static final float BOTTOM = 52f;
    private static final Charset CP1252 = Charset.forName("windows-1252");

    /** Largeurs Helvetica (milli-em) des caractères ASCII 32 à 126. */
    private static final int[] WIDTHS = {
            278, 278, 355, 556, 556, 889, 667, 191, 333, 333, 389, 584, 278, 333, 278, 278,
            556, 556, 556, 556, 556, 556, 556, 556, 556, 556,
            278, 278, 584, 584, 584, 556, 1015,
            667, 667, 722, 722, 667, 611, 778, 722, 278, 500, 667, 556, 833, 722, 778, 667, 778, 722, 667, 611, 722, 667, 944, 667, 667, 611,
            278, 278, 278, 469, 556, 333,
            556, 556, 500, 556, 556, 278, 556, 556, 222, 222, 500, 222, 833, 556, 556, 556, 556, 333, 500, 278, 556, 500, 722, 500, 500, 500,
            334, 260, 334, 584};

    private final List<StringBuilder> pages = new ArrayList<>();
    private StringBuilder page;
    private float y;

    public SimplePdf() {
        newPage();
    }

    // ── Contenu ───────────────────────────────────────────────────────────────

    public SimplePdf title(String text) {
        for (String line : wrap(text, 20, true, PAGE_W - 2 * MARGIN)) {
            ensureSpace(26);
            text(line, MARGIN, y - 20, 20, true, 0.07f, 0.14f, 0.23f);
            y -= 26;
        }
        return this;
    }

    public SimplePdf subtitle(String text) {
        for (String line : wrap(text, 10, false, PAGE_W - 2 * MARGIN)) {
            ensureSpace(14);
            text(line, MARGIN, y - 10, 10, false, 0.35f, 0.40f, 0.46f);
            y -= 14;
        }
        return this;
    }

    public SimplePdf space(float points) {
        y -= points;
        return this;
    }

    public SimplePdf heading(String text) {
        ensureSpace(34);
        y -= 10;
        text(text, MARGIN, y - 13, 13, true, 0.07f, 0.14f, 0.23f);
        y -= 18;
        line(MARGIN, y - 2, PAGE_W - MARGIN, y - 2, 1f, 1f, 0.47f, 0f);
        y -= 8;
        return this;
    }

    public SimplePdf paragraph(String text) {
        for (String line : wrap(text, 10, false, PAGE_W - 2 * MARGIN)) {
            ensureSpace(14);
            text(line, MARGIN, y - 10, 10, false, 0.1f, 0.1f, 0.1f);
            y -= 14;
        }
        return this;
    }

    public SimplePdf table(List<String> columns, List<List<String>> rows) {
        float usable = PAGE_W - 2 * MARGIN;
        float[] widths = columnWidths(columns, rows, usable);
        drawTableHeader(columns, widths);
        boolean zebra = false;
        for (List<String> row : rows) {
            if (y - 15 < BOTTOM) {
                newPage();
                drawTableHeader(columns, widths);
            }
            if (zebra) {
                rect(MARGIN, y - 14, usable, 14, 0.96f, 0.97f, 0.98f);
            }
            float x = MARGIN;
            for (int i = 0; i < columns.size(); i++) {
                String cell = i < row.size() && row.get(i) != null ? row.get(i) : "";
                text(fit(cell, 9, false, widths[i] - 8), x + 4, y - 10, 9, false, 0.1f, 0.1f, 0.1f);
                x += widths[i];
            }
            y -= 14;
            zebra = !zebra;
        }
        y -= 6;
        return this;
    }

    /** Barres horizontales : un libellé, une barre proportionnelle à la valeur maximale, puis la valeur. */
    public SimplePdf bars(String title, List<String> labels, List<Double> values) {
        heading(title);
        double max = 0;
        for (Double v : values) {
            max = Math.max(max, v == null ? 0 : v);
        }
        float labelW = 175f;
        float barMax = 240f;
        for (int i = 0; i < labels.size(); i++) {
            ensureSpace(16);
            double v = i < values.size() && values.get(i) != null ? values.get(i) : 0;
            text(fit(labels.get(i), 9, false, labelW - 6), MARGIN, y - 10, 9, false, 0.1f, 0.1f, 0.1f);
            float w = max <= 0 ? 0 : (float) (barMax * v / max);
            if (v > 0 && w < 1.5f) {
                w = 1.5f;
            }
            rect(MARGIN + labelW, y - 11, w, 10, 1f, 0.47f, 0f);
            text(formatNumber(v), MARGIN + labelW + w + 5, y - 10, 9, true, 0.1f, 0.1f, 0.1f);
            y -= 16;
        }
        y -= 6;
        return this;
    }

    // ── Assemblage ────────────────────────────────────────────────────────────

    public byte[] build(String footerLabel) {
        int total = pages.size();
        for (int i = 0; i < total; i++) {
            StringBuilder p = pages.get(i);
            p.append("0.45 0.5 0.55 rg\nBT /F1 8 Tf ").append(f(MARGIN)).append(' ').append(f(26)).append(" Td (")
                    .append(escape(footerLabel + "  —  page " + (i + 1) + "/" + total)).append(") Tj ET\n");
        }
        ByteArrayOutputStream out = new ByteArrayOutputStream();
        List<Integer> offsets = new ArrayList<>();
        write(out, "%PDF-1.4\n");
        out.writeBytes(new byte[]{'%', (byte) 0xE2, (byte) 0xE3, (byte) 0xCF, (byte) 0xD3, '\n'});

        StringBuilder kids = new StringBuilder();
        for (int i = 0; i < total; i++) {
            kids.append(5 + 2 * i).append(" 0 R ");
        }
        object(out, offsets, 1, "<< /Type /Catalog /Pages 2 0 R >>");
        object(out, offsets, 2, "<< /Type /Pages /Kids [" + kids.toString().trim() + "] /Count " + total + " >>");
        object(out, offsets, 3, "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>");
        object(out, offsets, 4, "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>");
        for (int i = 0; i < total; i++) {
            int pageObj = 5 + 2 * i;
            int contentObj = pageObj + 1;
            object(out, offsets, pageObj, "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 " + (int) PAGE_W + " " + (int) PAGE_H
                    + "] /Resources << /Font << /F1 3 0 R /F2 4 0 R >> >> /Contents " + contentObj + " 0 R >>");
            byte[] content = pages.get(i).toString().getBytes(CP1252);
            offsets.add(out.size());
            write(out, contentObj + " 0 obj\n<< /Length " + content.length + " >>\nstream\n");
            out.writeBytes(content);
            write(out, "\nendstream\nendobj\n");
        }
        int xref = out.size();
        int count = offsets.size() + 1;
        write(out, "xref\n0 " + count + "\n0000000000 65535 f \n");
        for (int off : offsets) {
            write(out, String.format(Locale.ROOT, "%010d 00000 n \n", off));
        }
        write(out, "trailer\n<< /Size " + count + " /Root 1 0 R >>\nstartxref\n" + xref + "\n%%EOF\n");
        return out.toByteArray();
    }

    // ── Dessin ────────────────────────────────────────────────────────────────

    private void newPage() {
        page = new StringBuilder();
        pages.add(page);
        y = PAGE_H - MARGIN;
    }

    private void ensureSpace(float needed) {
        if (y - needed < BOTTOM) {
            newPage();
        }
    }

    private void drawTableHeader(List<String> columns, float[] widths) {
        float usable = PAGE_W - 2 * MARGIN;
        ensureSpace(20);
        rect(MARGIN, y - 16, usable, 16, 0.12f, 0.2f, 0.32f);
        float x = MARGIN;
        for (int i = 0; i < columns.size(); i++) {
            text(fit(columns.get(i), 9, true, widths[i] - 8), x + 4, y - 11, 9, true, 1f, 1f, 1f);
            x += widths[i];
        }
        y -= 16;
    }

    private void text(String s, float x, float baseline, float size, boolean bold, float r, float g, float b) {
        page.append(f(r)).append(' ').append(f(g)).append(' ').append(f(b)).append(" rg\nBT /")
                .append(bold ? "F2" : "F1").append(' ').append(f(size)).append(" Tf ")
                .append(f(x)).append(' ').append(f(baseline)).append(" Td (").append(escape(s)).append(") Tj ET\n");
    }

    private void rect(float x, float yTop, float w, float h, float r, float g, float b) {
        page.append(f(r)).append(' ').append(f(g)).append(' ').append(f(b)).append(" rg\n")
                .append(f(x)).append(' ').append(f(yTop)).append(' ').append(f(w)).append(' ').append(f(h)).append(" re f\n");
    }

    private void line(float x1, float y1, float x2, float y2, float width, float r, float g, float b) {
        page.append(f(r)).append(' ').append(f(g)).append(' ').append(f(b)).append(" RG ").append(f(width)).append(" w ")
                .append(f(x1)).append(' ').append(f(y1)).append(" m ").append(f(x2)).append(' ').append(f(y2)).append(" l S\n");
    }

    // ── Mesure et texte ───────────────────────────────────────────────────────

    static float width(String s, float size, boolean bold) {
        float total = 0;
        for (int i = 0; i < s.length(); i++) {
            total += glyphWidth(s.charAt(i));
        }
        return total * size / 1000f * (bold ? 1.08f : 1f);
    }

    private static int glyphWidth(char c) {
        if (c >= 32 && c <= 126) {
            return WIDTHS[c - 32];
        }
        String base = Normalizer.normalize(String.valueOf(c), Normalizer.Form.NFD).replaceAll("\\p{M}", "");
        if (!base.isEmpty() && base.charAt(0) >= 32 && base.charAt(0) <= 126) {
            return WIDTHS[base.charAt(0) - 32];
        }
        return 556;
    }

    static List<String> wrap(String text, float size, boolean bold, float maxWidth) {
        List<String> lines = new ArrayList<>();
        String clean = text == null ? "" : text.replace("\r", "");
        for (String paragraph : clean.split("\n", -1)) {
            StringBuilder current = new StringBuilder();
            for (String word : paragraph.split(" ")) {
                String candidate = current.length() == 0 ? word : current + " " + word;
                if (width(candidate, size, bold) <= maxWidth || current.length() == 0) {
                    current = new StringBuilder(candidate);
                } else {
                    lines.add(current.toString());
                    current = new StringBuilder(word);
                }
            }
            lines.add(current.toString());
        }
        return lines;
    }

    /** Tronque avec « … » pour tenir dans la largeur donnée. */
    static String fit(String s, float size, boolean bold, float maxWidth) {
        String v = s == null ? "" : s.replace('\n', ' ');
        if (width(v, size, bold) <= maxWidth) {
            return v;
        }
        String ellipsis = "…";
        int end = v.length();
        while (end > 0 && width(v.substring(0, end) + ellipsis, size, bold) > maxWidth) {
            end--;
        }
        return v.substring(0, end) + ellipsis;
    }

    private static float[] columnWidths(List<String> columns, List<List<String>> rows, float usable) {
        int n = columns.size();
        float[] w = new float[n];
        float total = 0;
        for (int i = 0; i < n; i++) {
            float max = width(columns.get(i), 9, true);
            for (List<String> row : rows) {
                if (i < row.size() && row.get(i) != null) {
                    max = Math.max(max, width(row.get(i), 9, false));
                }
            }
            w[i] = Math.min(Math.max(max + 12, 38f), usable);
            total += w[i];
        }
        float scale = usable / total;
        for (int i = 0; i < n; i++) {
            w[i] *= scale;
        }
        return w;
    }

    private static String formatNumber(double v) {
        return v == Math.rint(v) ? String.valueOf((long) v) : String.format(Locale.ROOT, "%.1f", v);
    }

    // ── Encodage PDF ──────────────────────────────────────────────────────────

    private static void object(ByteArrayOutputStream out, List<Integer> offsets, int number, String body) {
        offsets.add(out.size());
        write(out, number + " 0 obj\n" + body + "\nendobj\n");
    }

    private static void write(ByteArrayOutputStream out, String s) {
        out.writeBytes(s.getBytes(CP1252));
    }

    private static String f(float v) {
        return String.format(Locale.ROOT, "%.2f", v);
    }

    /** Remplace ce que WinAnsi ne sait pas représenter, puis échappe les caractères spéciaux du format PDF. */
    static String escape(String s) {
        String t = (s == null ? "" : s).replace("→", "->").replace("≥", ">=").replace("≤", "<=").replace("·", "-");
        CharsetEncoder enc = CP1252.newEncoder();
        StringBuilder sb = new StringBuilder();
        for (int i = 0; i < t.length(); i++) {
            char c = t.charAt(i);
            if (c < 32 || !enc.canEncode(c)) {
                c = '?';
            }
            if (c == '\\' || c == '(' || c == ')') {
                sb.append('\\');
            }
            sb.append(c);
        }
        return sb.toString();
    }
}
