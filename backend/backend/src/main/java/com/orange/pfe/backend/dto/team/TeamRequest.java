package com.orange.pfe.backend.dto.team;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Size;

/** Création / modification d'une équipe. {@code leadId} : responsable (utilisateur existant et actif), optionnel. */
public record TeamRequest(
        @NotBlank @Size(max = 100) String name,
        @Size(max = 1000) String description,
        Long leadId
) {}
