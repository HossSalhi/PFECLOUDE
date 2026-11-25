package com.orange.pfe.backend.dto;

import java.time.LocalDateTime;

/** LOT HELIOS-INCIDENT-IDENTITY-CARD-01 : écriture interne du résultat de vérification finale. */
public record IncidentVerificationRequest(
        String criteria,
        String result,
        String measuresJson,
        String reason,
        LocalDateTime verifiedAt,
        Boolean available
) {}
