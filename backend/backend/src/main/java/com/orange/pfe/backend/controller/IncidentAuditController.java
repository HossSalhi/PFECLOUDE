package com.orange.pfe.backend.controller;

import com.orange.pfe.backend.dto.IncidentHistoryResponse;
import com.orange.pfe.backend.exception.IncidentNotFoundException;
import com.orange.pfe.backend.model.core.IncidentHistory;
import com.orange.pfe.backend.model.enums.IncidentStatus;
import com.orange.pfe.backend.repository.IncidentHistoryRepository;
import com.orange.pfe.backend.repository.IncidentRepository;
import io.swagger.v3.oas.annotations.Operation;
import io.swagger.v3.oas.annotations.Parameter;
import org.springframework.data.domain.*;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;
import java.util.List;

@RestController
@RequestMapping("/api/incidents/{id}")
public class IncidentAuditController {

    private final IncidentRepository incidentRepo;
    private final IncidentHistoryRepository historyRepo;

    public IncidentAuditController(IncidentRepository incidentRepo,
                                   IncidentHistoryRepository historyRepo) {
        this.incidentRepo = incidentRepo;
        this.historyRepo = historyRepo;
    }

    @Operation(summary = "Historique complet d'un incident")
    @GetMapping("/history")
    public ResponseEntity<List<IncidentHistoryResponse>> history(@PathVariable Long id) {
        incidentRepo.findById(id)
                .orElseThrow(() -> new IncidentNotFoundException(id));

        List<IncidentHistoryResponse> response = historyRepo.findByIncidentId(id)
                .stream()
                .map(h -> new IncidentHistoryResponse(
                        h.getId(),
                        h.getStatus().name(),
                        h.getComment(),
                        h.getCreatedAt()
                ))
                .toList();

        return ResponseEntity.ok(response);
    }

    @Operation(summary = "Historique paginé avec filtrage")
    @GetMapping("/history/page")
    public ResponseEntity<Page<IncidentHistoryResponse>> historyPage(
            @PathVariable Long id,
            @RequestParam(defaultValue = "0") int page,
            @RequestParam(defaultValue = "10") int size,
            @RequestParam(required = false) IncidentStatus status
    ) {
        incidentRepo.findById(id)
                .orElseThrow(() -> new IncidentNotFoundException(id));

        Pageable pageable = PageRequest.of(page, size, Sort.by(Sort.Direction.DESC, "createdAt"));
        Page<IncidentHistory> histories = (status != null)
                ? historyRepo.findAllByIncidentIdAndStatus(id, status, pageable)
                : historyRepo.findAllByIncidentId(id, pageable);

        return ResponseEntity.ok(histories.map(h -> new IncidentHistoryResponse(
                h.getId(),
                h.getStatus().name(),
                h.getComment(),
                h.getCreatedAt()
        )));
    }
}
