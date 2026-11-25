package com.orange.pfe.backend.dto;

/** LOT HELIOS-INCIDENT-CARD-EVENT-INTEGRATION-01 — résultat d'ingestion : incident créé OU corrélé. */
public record HostIncidentIngestionResponse(
        Long incidentId,
        String incidentType,
        String target,
        boolean created,      // true = nouvel incident (nouvel épisode)
        boolean correlated,   // true = corrélé à un incident existant (pas de doublon)
        String status
) {}
