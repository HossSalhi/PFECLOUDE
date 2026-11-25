package com.orange.pfe.backend.dto;

import com.orange.pfe.backend.model.core.IncidentVerification;

import java.time.LocalDateTime;

/** LOT HELIOS-INCIDENT-IDENTITY-CARD-01 : résultat de vérification finale exposé au frontend. */
public record IncidentVerificationResponse(
        Long id,
        Long incidentId,
        String criteria,
        String result,
        String measuresJson,
        String reason,
        LocalDateTime verifiedAt,
        boolean available
) {
    public static IncidentVerificationResponse from(IncidentVerification v) {
        return new IncidentVerificationResponse(
                v.getId(), v.getIncidentId(), v.getCriteria(), v.getResult(),
                v.getMeasuresJson(), v.getReason(), v.getVerifiedAt(), v.isAvailable());
    }
}
