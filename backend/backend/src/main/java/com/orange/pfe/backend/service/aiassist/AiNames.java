package com.orange.pfe.backend.service.aiassist;

import java.util.ArrayList;
import java.util.Comparator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * Anonymisation avant envoi au fournisseur IA : chaque personne devient « Technicien A », « Technicien B »… dans le prompt,
 * puis les alias sont remplacés par les vrais noms dans le texte reçu, pour l'affichage seulement. Aucun nom, e-mail ni
 * identifiant ne quitte le système.
 */
public final class AiNames {

    private final Map<String, String> aliasByReal = new LinkedHashMap<>();

    private final Map<String, Integer> counters = new LinkedHashMap<>();

    /** Alias stable d'une personne (créé à la première demande). */
    public String alias(String realName) {
        return alias(realName, "Technicien");
    }

    /** Alias stable d'un nom quelconque, numéroté par catégorie : « Machine A », « Application B »… */
    public String alias(String realName, String prefix) {
        String key = realName == null || realName.isBlank() ? "inconnu" : realName.trim();
        return aliasByReal.computeIfAbsent(key, k -> prefix + " " + letters(counters.merge(prefix, 1, Integer::sum) - 1));
    }

    /** Remplace les alias du texte généré par les vrais noms (les plus longs d'abord : « Technicien AA » avant « A »). */
    public String restore(String text) {
        if (text == null) {
            return null;
        }
        List<Map.Entry<String, String>> entries = new ArrayList<>(aliasByReal.entrySet());
        entries.sort(Comparator.comparingInt((Map.Entry<String, String> e) -> e.getValue().length()).reversed());
        String out = text;
        for (Map.Entry<String, String> e : entries) {
            out = out.replace(e.getValue(), e.getKey());
        }
        return out;
    }

    private static String letters(int index) {
        StringBuilder sb = new StringBuilder();
        int n = index;
        do {
            sb.insert(0, (char) ('A' + n % 26));
            n = n / 26 - 1;
        } while (n >= 0);
        return sb.toString();
    }
}
