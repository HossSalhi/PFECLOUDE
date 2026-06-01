package com.orange.pfe.backend.dto;

import com.orange.pfe.backend.model.enums.IncidentSeverity;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Size;

public class IncidentPatchRequest {

    @NotBlank(message = "Title obligatoire")
    @Size(max = 255, message = "Title ne peut pas dépasser 255 caractères")
    private String title;

    @NotNull(message = "Severity obligatoire")
    private IncidentSeverity severity;

    public String getTitle() { return title; }
    public void setTitle(String title) { this.title = title; }

    public IncidentSeverity getSeverity() { return severity; }
    public void setSeverity(IncidentSeverity severity) { this.severity = severity; }
}
