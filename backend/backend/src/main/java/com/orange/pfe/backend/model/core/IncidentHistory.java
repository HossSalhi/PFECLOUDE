package com.orange.pfe.backend.model.core;

import com.orange.pfe.backend.model.common.BaseEntity;
import com.orange.pfe.backend.model.enums.IncidentStatus;
import jakarta.persistence.*;

@Entity
@Table(name = "incident_history")
public class IncidentHistory extends BaseEntity {

    @ManyToOne
    @JoinColumn(name = "incident_id")
    private Incident incident;

    @Enumerated(EnumType.STRING)
    private IncidentStatus status;

    @Column(length = 1000)
    private String comment;

    public Incident getIncident() { return incident; }
    public void setIncident(Incident incident) { this.incident = incident; }
    public IncidentStatus getStatus() { return status; }
    public void setStatus(IncidentStatus status) { this.status = status; }
    public String getComment() { return comment; }
    public void setComment(String comment) { this.comment = comment; }
}
