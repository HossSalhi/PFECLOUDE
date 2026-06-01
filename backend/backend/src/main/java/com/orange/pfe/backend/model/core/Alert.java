package com.orange.pfe.backend.model.core;

import com.orange.pfe.backend.model.common.BaseEntity;
import com.orange.pfe.backend.model.enums.AlertSource;
import jakarta.persistence.*;

@Entity
@Table(name = "alert")
public class Alert extends BaseEntity {

    @Column(nullable = false)
    private String message;

    @Enumerated(EnumType.STRING)
    private AlertSource source;

    @ManyToOne
    @JoinColumn(name = "incident_id")
    private Incident incident;

    public String getMessage() { return message; }
    public void setMessage(String message) { this.message = message; }
    public AlertSource getSource() { return source; }
    public void setSource(AlertSource source) { this.source = source; }
    public Incident getIncident() { return incident; }
    public void setIncident(Incident incident) { this.incident = incident; }
}