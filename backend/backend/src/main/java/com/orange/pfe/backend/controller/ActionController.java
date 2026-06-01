package com.orange.pfe.backend.controller;

import com.orange.pfe.backend.dto.CreateActionRequest;
import com.orange.pfe.backend.dto.IncidentActionResponse;
import com.orange.pfe.backend.exception.ActionException;
import com.orange.pfe.backend.exception.ResourceNotFoundException;
import com.orange.pfe.backend.model.action.ActionStatus;
import com.orange.pfe.backend.model.action.ActionType;
import com.orange.pfe.backend.model.action.IncidentAction;
import com.orange.pfe.backend.model.core.Incident;
import com.orange.pfe.backend.service.ActionService;
import com.orange.pfe.backend.repository.IncidentActionRepository;
import com.orange.pfe.backend.repository.IncidentRepository;

import io.swagger.v3.oas.annotations.Operation;
import io.swagger.v3.oas.annotations.tags.Tag;

import jakarta.validation.Valid;
import jakarta.validation.constraints.Min;

import org.springframework.data.domain.*;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.security.core.Authentication;
import org.springframework.security.core.context.SecurityContextHolder;
import org.springframework.validation.annotation.Validated;
import org.springframework.web.bind.annotation.*;

import java.util.Arrays;
import java.util.List;
import java.util.stream.Collectors;

@RestController
@RequestMapping("/api/actions")
@Tag(name = "Actions", description = "API pour la gestion des actions sur incidents")
@Validated
public class ActionController {

    private final ActionService actionService;
    private final IncidentActionRepository actionRepo;
    private final IncidentRepository incidentRepo;

    public ActionController(ActionService actionService,
                            IncidentActionRepository actionRepo,
                            IncidentRepository incidentRepo) {
        this.actionService = actionService;
        this.actionRepo = actionRepo;
        this.incidentRepo = incidentRepo;
    }

    // -------------------- GET Toutes les actions (paginé) --------------------
    @Operation(summary = "Récupérer toutes les actions (paginé)")
    @GetMapping
    public Page<IncidentActionResponse> getAllActions(
            @RequestParam(defaultValue = "0") @Min(0) int page,
            @RequestParam(defaultValue = "20") @Min(1) int size
    ) {
        Pageable pageable = PageRequest.of(page, Math.min(size, 100),
                Sort.by(Sort.Direction.DESC, "executedAt"));
        return actionRepo.findAll(pageable).map(IncidentActionResponse::fromEntity);
    }

    // -------------------- GET Actions par incident --------------------
    @Operation(summary = "Récupérer les actions d'un incident")
    @GetMapping("/incident/{incidentId}")
    public List<IncidentActionResponse> getActionsByIncident(@PathVariable Long incidentId) {
        return actionRepo.findByIncidentId(incidentId, Pageable.unpaged()).stream()
                .map(IncidentActionResponse::fromEntity)
                .collect(Collectors.toList());
    }

    // -------------------- POST Créer une action --------------------
    @Operation(summary = "Créer une action")
    @PostMapping
    public ResponseEntity<IncidentActionResponse> createAction(
            @Valid @RequestBody CreateActionRequest request
    ) {
        Incident incident = incidentRepo.findById(request.getIncidentId())
                .orElseThrow(() -> new ResourceNotFoundException(
                        "Incident non trouvé: " + request.getIncidentId()
                ));

        // Récupérer utilisateur connecté
        Authentication auth = SecurityContextHolder.getContext().getAuthentication();
        String username = auth != null ? auth.getName() : "SYSTEM";

        IncidentAction action = actionService.createAction(
                incident,
                request.getType(),
                request.getDescription(),
                username
        );

        return ResponseEntity.status(HttpStatus.CREATED)
                .body(IncidentActionResponse.fromEntity(action));
    }

    // -------------------- POST Compléter une action --------------------
    @Operation(summary = "Compléter une action")
    @PostMapping("/{id}/complete")
    public ResponseEntity<IncidentActionResponse> completeAction(@PathVariable Long id) {
        Authentication auth = SecurityContextHolder.getContext().getAuthentication();
        String username = auth != null ? auth.getName() : "SYSTEM";

        actionService.markActionCompleted(id, username);

        IncidentAction updated = actionRepo.findById(id)
                .orElseThrow(() -> new ResourceNotFoundException("Action non trouvée: " + id));

        return ResponseEntity.ok(IncidentActionResponse.fromEntity(updated));
    }

    // -------------------- POST Échouer une action --------------------
    @Operation(summary = "Échouer une action")
    @PostMapping("/{id}/fail")
    public ResponseEntity<IncidentActionResponse> failAction(@PathVariable Long id,
                                                             @RequestParam(required = false) String reason) {
        Authentication auth = SecurityContextHolder.getContext().getAuthentication();
        String username = auth != null ? auth.getName() : "SYSTEM";

        actionService.markActionFailed(id, reason, username);

        IncidentAction updated = actionRepo.findById(id)
                .orElseThrow(() -> new ResourceNotFoundException("Action non trouvée: " + id));

        return ResponseEntity.ok(IncidentActionResponse.fromEntity(updated));
    }

    // -------------------- GET Historique avec pagination --------------------
    @Operation(summary = "Historique des actions")
    @GetMapping("/history")
    public Page<IncidentActionResponse> getActionHistory(
            @RequestParam(defaultValue = "0") @Min(0) int page,
            @RequestParam(defaultValue = "20") @Min(1) int size,
            @RequestParam(required = false) Long incidentId,
            @RequestParam(required = false) String status
    ) {
        Pageable pageable = PageRequest.of(page, size, Sort.by(Sort.Direction.DESC, "executedAt"));
        Page<IncidentAction> actionsPage;

        if (incidentId != null && status != null) {
            ActionStatus actionStatus = parseStatus(status);
            actionsPage = actionRepo.findByIncidentIdAndStatus(incidentId, actionStatus, pageable);
        } else if (incidentId != null) {
            actionsPage = actionRepo.findByIncidentId(incidentId, pageable);
        } else if (status != null) {
            ActionStatus actionStatus = parseStatus(status);
            actionsPage = actionRepo.findByStatus(actionStatus, pageable);
        } else {
            actionsPage = actionRepo.findAll(pageable);
        }

        return actionsPage.map(IncidentActionResponse::fromEntity);
    }

    // -------------------- GET Types d'action disponibles --------------------
    @Operation(summary = "Récupérer les types d'action disponibles")
    @GetMapping("/types")
    public List<String> getActionTypes() {
        return Arrays.stream(ActionType.values())
                .map(Enum::name)
                .collect(Collectors.toList());
    }

    // -------------------- Méthode utilitaire --------------------
    private ActionStatus parseStatus(String status) {
        try {
            return ActionStatus.valueOf(status.toUpperCase());
        } catch (IllegalArgumentException e) {
            throw new ActionException("Statut invalide: " + status);
        }
    }
}
