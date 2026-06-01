package com.orange.pfe.backend.service;

import com.orange.pfe.backend.model.core.Incident;
import com.orange.pfe.backend.model.decision.*;
import com.orange.pfe.backend.repository.IncidentDecisionRepository;
import org.springframework.stereotype.Service;

@Service
public class IncidentDecisionService {

    private final IncidentDecisionRepository repo;

    public IncidentDecisionService(IncidentDecisionRepository repo) {
        this.repo = repo;
    }

    public void recordDecision(
            Incident incident,
            DecisionType type,
            DecisionSource source,
            String comment
    ) {
        IncidentDecision d = new IncidentDecision();
        d.setIncident(incident);
        d.setDecisionType(type);
        d.setDecisionSource(source);
        d.setComment(comment);
        repo.save(d);
    }
}
