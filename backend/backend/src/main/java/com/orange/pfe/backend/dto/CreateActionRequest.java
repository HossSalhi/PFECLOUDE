package com.orange.pfe.backend.dto;

import com.orange.pfe.backend.model.action.ActionType;
import jakarta.validation.constraints.NotNull;

/**
 * DTO pour la création d'une action sur un incident.
 * Le type d'action est un enum pour limiter les choix.
 */
public class CreateActionRequest {

    @NotNull(message = "L'ID de l'incident est obligatoire")
    private Long incidentId;

    @NotNull(message = "Le type d'action est obligatoire")
    private ActionType type; // ✅ Enum: choix limité

    private String description;

    // 🔹 Constructeurs
    public CreateActionRequest() {}

    public CreateActionRequest(Long incidentId, ActionType type, String description) {
        this.incidentId = incidentId;
        this.type = type;
        this.description = description;
    }

    // 🔹 Getters & Setters
    public Long getIncidentId() { return incidentId; }
    public void setIncidentId(Long incidentId) { this.incidentId = incidentId; }

    public ActionType getType() { return type; }
    public void setType(ActionType type) { this.type = type; }

    public String getDescription() { return description; }
    public void setDescription(String description) { this.description = description; }
}
