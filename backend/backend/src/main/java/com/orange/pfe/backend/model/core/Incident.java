package com.orange.pfe.backend.model.core;

import com.orange.pfe.backend.model.common.BaseEntity;
import com.orange.pfe.backend.model.enums.*;
import jakarta.persistence.*;
import java.util.ArrayList;
import java.util.List;

@Entity
@Table(name = "incident")
public class Incident extends BaseEntity {

    @Column(nullable = false)
    private String title;

    @Column(nullable = false, length = 1000)
    private String description;

    @Column(columnDefinition = "TEXT")
    private String explanation;

    @Enumerated(EnumType.STRING)
    private IncidentSeverity severity;

    @Enumerated(EnumType.STRING)
    private IncidentStatus status;

    @Enumerated(EnumType.STRING)
    private IncidentSource source;

    @Column(name = "incident_type")
    private String incidentType;

    @Column(name = "namespace")
    private String namespace;

    @Column(name = "app")
    private String app;

    @OneToMany(mappedBy = "incident", cascade = CascadeType.ALL, orphanRemoval = true)
    private List<Alert> alerts = new ArrayList<>();

    @OneToMany(mappedBy = "incident", cascade = CascadeType.ALL, orphanRemoval = true)
    private List<IncidentHistory> history = new ArrayList<>();

    // getters & setters
    public String getTitle() { return title; }
    public void setTitle(String title) { this.title = title; }

    public String getDescription() { return description; }
    public void setDescription(String description) { this.description = description; }

    public String getExplanation() { return explanation; }
    public void setExplanation(String explanation) { this.explanation = explanation; }

    public IncidentSeverity getSeverity() { return severity; }
    public void setSeverity(IncidentSeverity severity) { this.severity = severity; }

    public IncidentStatus getStatus() { return status; }
    public void setStatus(IncidentStatus status) { this.status = status; }

    public IncidentSource getSource() { return source; }
    public void setSource(IncidentSource source) { this.source = source; }

    public String getIncidentType() { return incidentType; }
    public void setIncidentType(String incidentType) { this.incidentType = incidentType; }

    public String getNamespace() { return namespace; }
    public void setNamespace(String namespace) { this.namespace = namespace; }

    public String getApp() { return app; }
    public void setApp(String app) { this.app = app; }

    public List<Alert> getAlerts() { return alerts; }
    public List<IncidentHistory> getHistory() { return history; }
}
