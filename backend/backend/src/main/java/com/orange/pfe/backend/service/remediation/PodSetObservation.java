package com.orange.pfe.backend.service.remediation;

import java.util.List;
import java.util.Map;

/**
 * Lecture des pods d'un ensemble étiqueté (ex. les instances d'un cluster CNPG). Sert aux preuves de récupération :
 * « l'API Kubernetes a répondu » est distinct de « l'ensemble est sain » — {@code apiAvailable=false} ne prouve rien.
 */
public record PodSetObservation(boolean apiAvailable, List<PodState> pods) {

    /** Un pod : prêt seulement si sa condition Ready est vraie ET s'il n'est pas en cours de suppression. */
    public record PodState(String name, String phase, boolean ready, Map<String, String> labels) {
        public PodState {
            labels = labels == null ? Map.of() : Map.copyOf(labels);
        }
    }

    public PodSetObservation {
        pods = pods == null ? List.of() : List.copyOf(pods);
    }

    /** API injoignable ou exécuteur sans lecture de pods : aucune preuve. */
    public static PodSetObservation unavailable() {
        return new PodSetObservation(false, List.of());
    }
}
