package com.orange.pfe.backend.dto;

import com.orange.pfe.backend.model.enums.TerminalEvidencePhase;
import jakarta.validation.constraints.NotNull;

import java.util.List;

/**
 * LOT HELIOS-INCIDENT-CARD-EVENT-INTEGRATION-01 — enregistrement d'un lot de preuves terminal à partir de
 * sorties DÉJÀ collectées (BEFORE/AFTER) pour un scénario (CPU/DISK). Le backend ne lance aucune commande ;
 * il persiste ce qui lui est fourni et extrait des mesures au mieux. {@code source} = BRIDGE/WATCHDOG/
 * COLLECTOR/FIXTURE (FIXTURE = donnée de test).
 */
public record TerminalEvidenceCollectRequest(
        String vm,
        @NotNull TerminalEvidencePhase phase,
        String scenario,
        String source,
        List<TerminalCommandOutput> outputs
) {}
