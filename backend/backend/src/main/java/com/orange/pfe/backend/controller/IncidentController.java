package com.orange.pfe.backend.controller;

import com.orange.pfe.backend.dto.*;
import com.orange.pfe.backend.model.enums.IncidentStatus;
import com.orange.pfe.backend.service.AIOpsOrchestrationService;
import com.orange.pfe.backend.service.IncidentService;
import com.orange.pfe.backend.service.WorkflowService;
import jakarta.validation.Valid;
import jakarta.validation.constraints.Max;
import jakarta.validation.constraints.Min;
import org.springframework.data.domain.Page;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.validation.annotation.Validated;
import org.springframework.web.bind.annotation.*;

import java.util.List;

@RestController
@RequestMapping("/api/incidents")
@Validated
public class IncidentController {

    private final IncidentService service;
    private final AIOpsOrchestrationService orchestrationService;
    private final WorkflowService workflowService;

    public IncidentController(IncidentService service,
                              AIOpsOrchestrationService orchestrationService,
                              WorkflowService workflowService) {
        this.service = service;
        this.orchestrationService = orchestrationService;
        this.workflowService = workflowService;
    }

    @PostMapping
    public ResponseEntity<IncidentResponse> create(@Valid @RequestBody IncidentRequest r) {
        return ResponseEntity.status(HttpStatus.CREATED)
                .body(service.createIncident(r));
    }

    @GetMapping("/{id}")
    public ResponseEntity<IncidentResponse> getById(@PathVariable Long id) {
        return ResponseEntity.ok(service.getById(id));
    }

    @GetMapping
    public ResponseEntity<Page<IncidentResponse>> getAll(
            @RequestParam(defaultValue = "0") @Min(0) int page,
            @RequestParam(defaultValue = "10") @Min(1) @Max(100) int size,
            @RequestParam(required = false) IncidentStatus status) {
        return ResponseEntity.ok(service.getAll(page, size, status));
    }

    @PutMapping("/{id}/status/{status}")
    public ResponseEntity<IncidentResponse> updateStatus(@PathVariable Long id,
                                                         @PathVariable IncidentStatus status) {
        // "Demarrer" depuis la LISTE = passage IN_PROGRESS : declenche directement la
        // remediation Camunda (le clic operateur vaut approbation) -> pas besoin de
        // passer par la page Taches Humaines.
        if (status == IncidentStatus.IN_PROGRESS) {
            orchestrationService.executeRecommendation(id);
            return ResponseEntity.ok(service.getById(id));
        }
        return ResponseEntity.ok(service.updateStatus(id, status));
    }

    @PatchMapping("/{id}")
    public ResponseEntity<IncidentResponse> patch(@PathVariable Long id,
                                                  @Valid @RequestBody IncidentPatchRequest r) {
        return ResponseEntity.ok(service.patchIncident(id, r));
    }

    @PostMapping("/{id}/execute")
    public ResponseEntity<Void> execute(@PathVariable Long id) {
        orchestrationService.executeRecommendation(id);
        return ResponseEntity.ok().build();
    }

    @GetMapping("/{id}/workflow-logs")
    public ResponseEntity<List<WorkflowLogDTO>> getWorkflowLogs(@PathVariable Long id) {
        return ResponseEntity.ok(workflowService.getWorkflowLogsForIncident(id));
    }

    @DeleteMapping("/{id}")
    public ResponseEntity<Void> archive(@PathVariable Long id) {
        service.archiveIncident(id);
        return ResponseEntity.noContent().build();
    }
}
