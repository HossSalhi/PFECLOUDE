package com.orange.pfe.backend.dto;

import java.time.LocalDateTime;
import java.util.List;

/**
 * LOT HELIOS-INCIDENT-IDENTITY-CARD-01 : carte d'identité d'incident agrégée (identité + chronologie +
 * preuves terminal AVANT/APRÈS + vérification finale + complétude des données). Les données proviennent
 * TOUJOURS du backend ; les champs absents restent null et les listes vides -> l'UI affiche « NON COLLECTÉ ».
 */
public record IncidentCardResponse(
        CardIdentity identity,
        List<CardTimelineEntry> timeline,
        List<TerminalEvidenceResponse> evidenceBefore,
        List<TerminalEvidenceResponse> evidenceAfter,
        IncidentVerificationResponse verification,
        CardCompleteness completeness
) {
    public record CardIdentity(
            Long id,
            String title,
            String incidentType,
            String severity,
            String status,
            String source,
            String namespace,
            String app,
            LocalDateTime detectedAt,
            LocalDateTime updatedAt,
            String description,
            String explanation
    ) {}

    public record CardTimelineEntry(
            Long id,
            String status,
            String comment,
            LocalDateTime at
    ) {}

    public record CardCompleteness(
            boolean hasTimeline,
            boolean hasEvidenceBefore,
            boolean hasEvidenceAfter,
            boolean hasVerification
    ) {}
}
