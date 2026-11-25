package com.orange.pfe.backend.model.enums;

/**
 * LOT HELIOS-TERMINAL-EVIDENCE-REAL-COLLECTION-01 — provenance d'une preuve, pour ne JAMAIS présenter une
 * donnée non vérifiée comme une mesure réelle issue d'une VM.
 */
public enum EvidenceProvenance {
    /** Sortie réelle d'une commande exécutée dans la VM (terminal). */
    REAL_EXECUTION,
    /** État réel lu via l'API Kubernetes en lecture seule (ex. nœud Ready) — pas un terminal de VM. */
    KUBERNETES_API,
    /** Mesure Prometheus — NUMÉRIQUE, pas une preuve terminal. */
    PROMETHEUS,
    /** Donnée de TEST (fixture) — jamais une preuve réelle. */
    FIXTURE,
    /** Non collecté / non disponible. */
    UNAVAILABLE;

    /** Seule une exécution réelle dans la VM constitue une PREUVE TERMINAL réelle. */
    public boolean isRealTerminal() {
        return this == REAL_EXECUTION;
    }

    /** Provenances dignes de confiance comme observation réelle (hors fixture/indisponible). */
    public boolean isTrustedObservation() {
        return this == REAL_EXECUTION || this == KUBERNETES_API || this == PROMETHEUS;
    }

    /** Mappe une étiquette de source libre vers une provenance (défaut UNAVAILABLE si inconnue/absente). */
    public static EvidenceProvenance fromSource(String source) {
        if (source == null) return UNAVAILABLE;
        switch (source.trim().toUpperCase()) {
            case "WATCHDOG":
            case "SSH":
            case "REAL_EXECUTION":
            case "BRIDGE":
                return REAL_EXECUTION;
            case "KUBERNETES_API":
            case "K8S_API":
            case "K8S":
                return KUBERNETES_API;
            case "PROMETHEUS":
                return PROMETHEUS;
            case "FIXTURE":
                return FIXTURE;
            default:
                return UNAVAILABLE;
        }
    }
}
