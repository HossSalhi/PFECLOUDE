package com.orange.pfe.backend.service;

import com.orange.pfe.backend.model.core.Incident;
import com.orange.pfe.backend.model.core.IncidentHistory;
import com.orange.pfe.backend.model.enums.IncidentStatus;
import com.orange.pfe.backend.repository.IncidentHistoryRepository;
import com.orange.pfe.backend.repository.IncidentRepository;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

@Service
@Transactional
public class IncidentHistoryService {

    private final IncidentRepository incidentRepo;
    private final IncidentHistoryRepository historyRepo;

    public IncidentHistoryService(IncidentRepository incidentRepo, IncidentHistoryRepository historyRepo) {
        this.incidentRepo = incidentRepo;
        this.historyRepo = historyRepo;
    }

    public void record(Incident incident, IncidentStatus status, String comment) {
        IncidentHistory h = new IncidentHistory();
        // getReferenceById creates a proxy without loading the entity or its lazy collections.
        // Writing the FK directly avoids LazyInitializationException when the caller holds
        // a detached Incident (e.g. Camunda job workers running outside a JPA session).
        h.setIncident(incidentRepo.getReferenceById(incident.getId()));
        h.setStatus(status);
        h.setComment(comment);
        historyRepo.save(h);
    }
}
