package com.orange.pfe.backend.model.decision;

import com.orange.pfe.backend.model.common.BaseEntity;
import com.orange.pfe.backend.model.core.Incident;
import jakarta.persistence.*;

@Entity
@Table(name = "incident_decision")
public class IncidentDecision extends BaseEntity {

    @ManyToOne
    @JoinColumn(name = "incident_id", nullable = false)
    private Incident incident;

    @Enumerated(EnumType.STRING)
    private DecisionType decisionType;

    @Enumerated(EnumType.STRING)
    private DecisionSource decisionSource;

    @Column(length = 1000)
    private String comment;

    // getters & setters
    public Incident getIncident() { return incident; }
    public void setIncident(Incident incident) { this.incident = incident; }

    public DecisionType getDecisionType() { return decisionType; }
    public void setDecisionType(DecisionType decisionType) { this.decisionType = decisionType; }

    public DecisionSource getDecisionSource() { return decisionSource; }
    public void setDecisionSource(DecisionSource decisionSource) { this.decisionSource = decisionSource; }

    public String getComment() { return comment; }
    public void setComment(String comment) { this.comment = comment; }
}
