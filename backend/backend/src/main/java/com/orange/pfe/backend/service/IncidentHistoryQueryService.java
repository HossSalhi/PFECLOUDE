package com.orange.pfe.backend.service;

import com.orange.pfe.backend.dto.IncidentHistoryResponse;
import com.orange.pfe.backend.exception.IncidentNotFoundException;
import com.orange.pfe.backend.repository.IncidentHistoryRepository;
import com.orange.pfe.backend.repository.IncidentRepository;
import org.springframework.stereotype.Service;

import java.util.List;

@Service
public class IncidentHistoryQueryService {

    private final IncidentRepository incidentRepo;
    private final IncidentHistoryRepository historyRepo;

    public IncidentHistoryQueryService(
            IncidentRepository incidentRepo,
            IncidentHistoryRepository historyRepo
    ) {
        this.incidentRepo = incidentRepo;
        this.historyRepo = historyRepo;
    }

    public List<IncidentHistoryResponse> getHistory(Long incidentId) {

        // ✅ Vérification métier
        incidentRepo.findById(incidentId)
                .orElseThrow(() -> new IncidentNotFoundException(incidentId));

        return historyRepo.findByIncidentId(incidentId)
                .stream()
                .map(h -> new IncidentHistoryResponse(
                        h.getId(),
                        h.getStatus().name(),
                        h.getComment(),
                        h.getCreatedAt()
                ))
                .toList();
    }
}