package com.orange.pfe.backend.dto;

import com.orange.pfe.backend.model.enums.IncidentSeverity;
import com.orange.pfe.backend.model.enums.IncidentSource;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Size;

public class IncidentRequest {

    @NotBlank(message = "Title obligatoire")
    @Size(max = 255, message = "Title ne peut pas dépasser 255 caractères")
    private String title;

    @NotBlank(message = "Description obligatoire")
    @Size(max = 2000, message = "Description ne peut pas dépasser 2000 caractères")
    private String description;

    @NotNull(message = "Severity obligatoire")
    private IncidentSeverity severity;

    @NotNull(message = "Source obligatoire")
    private IncidentSource source;

    private String namespace;
    private String app;

    public String getTitle() { return title; }
    public void setTitle(String title) { this.title = title; }

    public String getDescription() { return description; }
    public void setDescription(String description) { this.description = description; }

    public IncidentSeverity getSeverity() { return severity; }
    public void setSeverity(IncidentSeverity severity) { this.severity = severity; }

    public IncidentSource getSource() { return source; }
    public void setSource(IncidentSource source) { this.source = source; }

    public String getNamespace() { return namespace; }
    public void setNamespace(String namespace) { this.namespace = namespace; }

    public String getApp() { return app; }
    public void setApp(String app) { this.app = app; }
}
