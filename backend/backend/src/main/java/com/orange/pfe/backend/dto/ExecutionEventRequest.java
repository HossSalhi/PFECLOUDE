package com.orange.pfe.backend.dto;

import com.orange.pfe.backend.model.enums.ExecutionStage;
import jakarta.validation.constraints.NotNull;

/**
 * LOT HELIOS-INCIDENT-CARD-EVENT-INTEGRATION-01 — enregistrement d'une étape d'exécution réelle sur la
 * carte. {@code success} est EXPLICITE (jamais déduit d'un code HTTP) ; une étape COMPLETED avec
 * success=false reste un échec.
 */
public record ExecutionEventRequest(
        @NotNull ExecutionStage stage,
        String action,
        String detail,
        Boolean success
) {}
