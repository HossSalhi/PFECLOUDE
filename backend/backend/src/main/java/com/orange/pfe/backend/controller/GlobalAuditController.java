package com.orange.pfe.backend.controller;

import com.orange.pfe.backend.model.core.IncidentHistory;
import com.orange.pfe.backend.model.enums.IncidentStatus;
import com.orange.pfe.backend.repository.IncidentHistoryRepository;
import io.swagger.v3.oas.annotations.Operation;
import io.swagger.v3.oas.annotations.tags.Tag;
import org.springframework.data.domain.Page;
import org.springframework.data.domain.PageRequest;
import org.springframework.data.domain.Pageable;
import org.springframework.data.domain.Sort;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

import java.time.LocalDateTime;
import java.util.Map;

/**
 * Global audit log: paginated, filterable history of ALL incident status changes.
 * Replaces the per-incident audit view for operators who need cross-incident visibility.
 *
 * GET /api/audit/incidents?status=RESOLVED&page=0&size=20
 * GET /api/audit/incidents?namespace=pfe&page=0&size=20
 * GET /api/audit/stats
 */
@RestController
@RequestMapping("/api/audit")
@Tag(name = "Audit Log", description = "Cross-incident audit trail and statistics")
public class GlobalAuditController {

    private final IncidentHistoryRepository historyRepo;

    public GlobalAuditController(IncidentHistoryRepository historyRepo) {
        this.historyRepo = historyRepo;
    }

    @Operation(summary = "Audit log global — toutes les transitions de status, paginées")
    @GetMapping("/incidents")
    public ResponseEntity<Page<AuditEntryResponse>> listAuditLog(
            @RequestParam(required = false) IncidentStatus status,
            @RequestParam(defaultValue = "0") int page,
            @RequestParam(defaultValue = "20") int size
    ) {
        Pageable pageable = PageRequest.of(page, size, Sort.by(Sort.Direction.DESC, "createdAt"));

        Page<IncidentHistory> entries = (status != null)
                ? historyRepo.findAllByStatus(status, pageable)
                : historyRepo.findAllByOrderByCreatedAtDesc(pageable);

        return ResponseEntity.ok(entries.map(this::toResponse));
    }

    @Operation(summary = "Statistiques d'audit — nombre d'incidents par status")
    @GetMapping("/stats")
    public ResponseEntity<Map<String, Object>> auditStats() {
        long total     = historyRepo.count();
        long resolved  = historyRepo.findAllByStatus(IncidentStatus.RESOLVED,
                PageRequest.of(0, 1)).getTotalElements();
        long waiting   = historyRepo.findAllByStatus(IncidentStatus.WAITING_FOR_APPROVAL,
                PageRequest.of(0, 1)).getTotalElements();
        long inProg    = historyRepo.findAllByStatus(IncidentStatus.IN_PROGRESS,
                PageRequest.of(0, 1)).getTotalElements();

        return ResponseEntity.ok(Map.of(
                "totalTransitions",     total,
                "resolved",             resolved,
                "waitingForApproval",   waiting,
                "inProgress",           inProg,
                "asOf",                 LocalDateTime.now().toString()
        ));
    }

    private AuditEntryResponse toResponse(IncidentHistory h) {
        Long incidentId   = h.getIncident() != null ? h.getIncident().getId()        : null;
        String namespace  = h.getIncident() != null ? h.getIncident().getNamespace() : null;
        String app        = h.getIncident() != null ? h.getIncident().getApp()       : null;
        String severity   = (h.getIncident() != null && h.getIncident().getSeverity() != null)
                ? h.getIncident().getSeverity().name() : null;
        String type       = h.getIncident() != null ? h.getIncident().getIncidentType() : null;

        return new AuditEntryResponse(
                h.getId(),
                incidentId,
                namespace,
                app,
                type,
                severity,
                h.getStatus() != null ? h.getStatus().name() : null,
                h.getComment(),
                h.getCreatedAt()
        );
    }

    public record AuditEntryResponse(
            Long historyId,
            Long incidentId,
            String namespace,
            String app,
            String incidentType,
            String severity,
            String status,
            String comment,
            LocalDateTime timestamp
    ) {}
}
