package com.orange.pfe.backend.dto;

import com.orange.pfe.backend.model.enums.TerminalEvidencePhase;
import jakarta.validation.constraints.NotNull;

import java.time.LocalDateTime;

/**
 * LOT HELIOS-INCIDENT-IDENTITY-CARD-01 : écriture interne d'une preuve terminal (par un mécanisme
 * d'exécution établi : Bridge / watchdog, ou une fixture de test). Ce lot n'exécute AUCUNE commande ;
 * il enregistre ce qui lui est fourni.
 */
public record TerminalEvidenceRequest(
        String vm,
        @NotNull TerminalEvidencePhase phase,
        LocalDateTime capturedAt,
        String command,
        Integer exitCode,
        String stdout,
        String stderr,
        String measuresJson,
        Boolean available,
        String source
) {}
