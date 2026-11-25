package com.orange.pfe.backend.dto;

import jakarta.validation.constraints.NotBlank;

import java.time.LocalDateTime;

/**
 * LOT HELIOS-INCIDENT-CARD-EVENT-INTEGRATION-01 — ingestion d'un incident host-mesuré (ex. NODE_HIGH_CPU)
 * qui, aujourd'hui, transite par Camunda-direct → Bridge SANS être persisté. Permet de créer/corréler une
 * carte à partir des données RÉELLEMENT disponibles. Aucun champ n'est inventé ; {@code detectedAt} (heure
 * de détection) est distingué de {@code observedAt} (heure d'observation) et de l'heure de persistance.
 */
public record HostIncidentIngestionRequest(
        @NotBlank String incidentType,
        String namespace,
        @NotBlank String target,
        String severity,
        LocalDateTime detectedAt,
        LocalDateTime observedAt,
        String detail,
        /** Clé d'épisode explicite si la source en fournit une (sinon corrélation par fenêtre active). */
        String episodeKey
) {}
