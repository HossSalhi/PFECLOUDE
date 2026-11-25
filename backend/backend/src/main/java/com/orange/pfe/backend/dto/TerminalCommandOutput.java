package com.orange.pfe.backend.dto;

import java.time.LocalDateTime;

/**
 * LOT HELIOS-INCIDENT-CARD-EVENT-INTEGRATION-01 — sortie d'UNE commande diagnostique, FOURNIE par un
 * mécanisme d'exécution établi (le backend n'exécute rien ici). Sert d'entrée au collecteur qui la
 * transforme en preuve terminal persistée.
 */
public record TerminalCommandOutput(
        String command,
        Integer exitCode,
        String stdout,
        String stderr,
        LocalDateTime capturedAt
) {}
