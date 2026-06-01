package com.orange.pfe.backend.dto;

import com.orange.pfe.backend.model.enums.IncidentSeverity;
import com.orange.pfe.backend.model.enums.IncidentStatus;
import com.orange.pfe.backend.model.enums.IncidentSource;

import java.time.LocalDateTime;

public class IncidentResponse {

    private Long id;
    private String title;
    private String description;
    private String explanation;
    private IncidentSeverity severity;
    private IncidentStatus status;
    private IncidentSource source;
    private String incidentType;
    private Double confidence;
    private String namespace;
    private String app;
    private LocalDateTime createdAt;
    private LocalDateTime updatedAt;

    public IncidentResponse(Long id, String title, String description, String explanation,
                            IncidentSeverity severity, IncidentStatus status,
                            IncidentSource source, String incidentType, Double confidence,
                            String namespace, String app,
                            LocalDateTime createdAt, LocalDateTime updatedAt) {
        this.id = id;
        this.title = title;
        this.description = description;
        this.explanation = explanation;
        this.severity = severity;
        this.status = status;
        this.source = source;
        this.incidentType = incidentType;
        this.confidence = confidence;
        this.namespace = namespace;
        this.app = app;
        this.createdAt = createdAt;
        this.updatedAt = updatedAt;
    }

    public Long getId() { return id; }
    public String getTitle() { return title; }
    public String getDescription() { return description; }
    public String getExplanation() { return explanation; }
    public IncidentSeverity getSeverity() { return severity; }
    public IncidentStatus getStatus() { return status; }
    public IncidentSource getSource() { return source; }
    public String getIncidentType() { return incidentType; }
    public Double getConfidence() { return confidence; }
    public String getNamespace() { return namespace; }
    public String getApp() { return app; }
    public LocalDateTime getCreatedAt() { return createdAt; }
    public LocalDateTime getUpdatedAt() { return updatedAt; }
}
