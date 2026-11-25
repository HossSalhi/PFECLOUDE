package com.orange.pfe.backend.service.audit;

import com.fasterxml.jackson.databind.ObjectMapper;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.regex.Pattern;

/**
 * Assainit les valeurs avant écriture dans le journal d'audit : AUCUN mot de passe, token, secret ou clé ne doit
 * jamais y figurer. Le masquage porte sur le nom de la clé (password, token, secret, authorization, api-key, otp,
 * hash…) ET sur la forme de la valeur (jeton de type JWT / « Bearer … »). Les valeurs trop longues sont tronquées
 * et la profondeur est bornée.
 */
public final class AuditSanitizer {

    public static final String MASK = "***";

    private static final Pattern SENSITIVE_KEY = Pattern.compile(
            "(?i).*(password|passwd|pwd|secret|token|authorization|api[-_ ]?key|credential|otp|totp|hash|private[-_ ]?key|bearer|cookie).*");
    private static final Pattern JWT_LIKE = Pattern.compile(
            "^(?i:bearer\\s+)?eyJ[A-Za-z0-9_-]{5,}\\.[A-Za-z0-9_-]{5,}\\.[A-Za-z0-9_-]*$");

    private static final int MAX_STRING = 1000;
    private static final int MAX_DEPTH = 4;
    private static final int MAX_ITEMS = 50;

    private static final ObjectMapper MAPPER = new ObjectMapper();

    private AuditSanitizer() {
    }

    /** Copie assainie (clés ordonnées). Retourne une map vide si l'entrée est nulle. */
    public static Map<String, Object> sanitize(Map<String, ?> values) {
        Map<String, Object> out = new LinkedHashMap<>();
        if (values == null) {
            return out;
        }
        for (Map.Entry<String, ?> e : values.entrySet()) {
            out.put(e.getKey(), sanitizeValue(e.getKey(), e.getValue(), 1));
        }
        return out;
    }

    /** JSON assaini, ou {@code null} si rien à enregistrer. */
    public static String toJson(Map<String, ?> values) {
        if (values == null || values.isEmpty()) {
            return null;
        }
        try {
            return MAPPER.writeValueAsString(sanitize(values));
        } catch (Exception e) {
            return "{\"error\":\"serialisation impossible\"}";
        }
    }

    static Object sanitizeValue(String key, Object value, int depth) {
        if (value == null) {
            return null;
        }
        if (key != null && SENSITIVE_KEY.matcher(key).matches()) {
            return MASK;
        }
        if (value instanceof Boolean || value instanceof Number) {
            return value;
        }
        if (value instanceof Enum<?> en) {
            return en.name();
        }
        if (value instanceof CharSequence cs) {
            String s = cs.toString();
            if (JWT_LIKE.matcher(s).matches()) {
                return MASK;
            }
            return s.length() > MAX_STRING ? s.substring(0, MAX_STRING) + "…" : s;
        }
        if (value instanceof Map<?, ?> m) {
            if (depth >= MAX_DEPTH) {
                return "[…]";
            }
            Map<String, Object> out = new LinkedHashMap<>();
            for (Map.Entry<?, ?> e : m.entrySet()) {
                String k = String.valueOf(e.getKey());
                out.put(k, sanitizeValue(k, e.getValue(), depth + 1));
            }
            return out;
        }
        if (value instanceof Iterable<?> it) {
            if (depth >= MAX_DEPTH) {
                return "[…]";
            }
            List<Object> out = new ArrayList<>();
            int n = 0;
            for (Object o : it) {
                if (n++ >= MAX_ITEMS) {
                    out.add("…");
                    break;
                }
                out.add(sanitizeValue(null, o, depth + 1));
            }
            return out;
        }
        return sanitizeValue(key, String.valueOf(value), depth);
    }
}
