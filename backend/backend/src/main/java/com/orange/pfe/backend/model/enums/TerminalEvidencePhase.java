package com.orange.pfe.backend.model.enums;

/**
 * Phase d'un relevé de preuve terminal (commande hôte/VM) associé à un incident.
 * LOT HELIOS-INCIDENT-IDENTITY-CARD-01.
 */
public enum TerminalEvidencePhase {
    /** Relevé AVANT la remédiation (état initial). */
    BEFORE,
    /** Relevé APRÈS la remédiation (état final, pour comparaison). */
    AFTER
}
