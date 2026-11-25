package com.orange.pfe.backend.controller;

import com.orange.pfe.backend.dto.maintenance.MaintenanceDtos.CancelRequest;
import com.orange.pfe.backend.dto.maintenance.MaintenanceDtos.HistoryResponse;
import com.orange.pfe.backend.dto.maintenance.MaintenanceDtos.MaintenanceRequest;
import com.orange.pfe.backend.dto.maintenance.MaintenanceDtos.MaintenanceResponse;
import com.orange.pfe.backend.dto.maintenance.MaintenanceDtos.ResourceResponse;
import com.orange.pfe.backend.model.maintenance.MaintenanceResourceType;
import com.orange.pfe.backend.model.maintenance.MaintenanceStatus;
import com.orange.pfe.backend.model.user.AppUser;
import com.orange.pfe.backend.service.maintenance.MaintenanceService;
import io.swagger.v3.oas.annotations.Operation;
import io.swagger.v3.oas.annotations.tags.Tag;
import jakarta.validation.Valid;
import org.springframework.data.domain.Page;
import org.springframework.format.annotation.DateTimeFormat;
import org.springframework.http.HttpStatus;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.PutMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.ResponseStatus;
import org.springframework.web.bind.annotation.RestController;

import java.time.LocalDateTime;
import java.util.List;

/**
 * Gestion de la maintenance (planification, suivi, calendrier). Module DÉCLARATIF : aucune de ces opérations
 * n'agit sur la supervision, la remédiation ni le drapeau de maintenance de l'hôte. Droits appliqués par
 * {@link MaintenanceService}.
 */
@RestController
@RequestMapping("/api/maintenance")
@Tag(name = "Maintenance", description = "Planification et suivi des opérations de maintenance")
public class MaintenanceController {

    private final MaintenanceService service;

    public MaintenanceController(MaintenanceService service) {
        this.service = service;
    }

    @Operation(summary = "Lister / filtrer les maintenances (statut, ressource, responsable, période, recherche)")
    @GetMapping
    public Page<MaintenanceResponse> list(
            @AuthenticationPrincipal AppUser actor,
            @RequestParam(required = false) MaintenanceStatus status,
            @RequestParam(required = false) MaintenanceResourceType resourceType,
            @RequestParam(required = false) String resource,
            @RequestParam(required = false) Long responsibleId,
            @RequestParam(required = false) @DateTimeFormat(iso = DateTimeFormat.ISO.DATE_TIME) LocalDateTime from,
            @RequestParam(required = false) @DateTimeFormat(iso = DateTimeFormat.ISO.DATE_TIME) LocalDateTime to,
            @RequestParam(required = false) String q,
            @RequestParam(defaultValue = "0") int page,
            @RequestParam(defaultValue = "25") int size) {
        return service.search(actor,
                new MaintenanceService.Query(status, resourceType, resource, responsibleId, from, to, q), page, size);
    }

    @Operation(summary = "Calendrier : maintenances dont la période chevauche l'intervalle (93 jours max)")
    @GetMapping("/calendar")
    public List<MaintenanceResponse> calendar(
            @AuthenticationPrincipal AppUser actor,
            @RequestParam @DateTimeFormat(iso = DateTimeFormat.ISO.DATE_TIME) LocalDateTime from,
            @RequestParam @DateTimeFormat(iso = DateTimeFormat.ISO.DATE_TIME) LocalDateTime to) {
        return service.calendar(actor, from, to);
    }

    @Operation(summary = "Maintenances actuellement en cours (information de gestion)")
    @GetMapping("/active")
    public List<MaintenanceResponse> inProgress(@AuthenticationPrincipal AppUser actor) {
        return service.inProgress(actor);
    }

    @Operation(summary = "Ressources auxquelles une maintenance peut être associée")
    @GetMapping("/resources")
    public List<ResourceResponse> resources(@AuthenticationPrincipal AppUser actor) {
        return service.resources(actor);
    }

    @Operation(summary = "Détail d'une maintenance")
    @GetMapping("/{id}")
    public MaintenanceResponse get(@AuthenticationPrincipal AppUser actor, @PathVariable Long id) {
        return service.get(actor, id);
    }

    @Operation(summary = "Historique des changements d'une maintenance")
    @GetMapping("/{id}/history")
    public List<HistoryResponse> history(@AuthenticationPrincipal AppUser actor, @PathVariable Long id) {
        return service.history(actor, id);
    }

    @Operation(summary = "Planifier une maintenance")
    @PostMapping
    @ResponseStatus(HttpStatus.CREATED)
    public MaintenanceResponse create(@AuthenticationPrincipal AppUser actor,
                                      @Valid @RequestBody MaintenanceRequest request) {
        return service.create(actor, request);
    }

    @Operation(summary = "Modifier une maintenance planifiée (ou, en cours : description, motif, responsable, fin)")
    @PutMapping("/{id}")
    public MaintenanceResponse update(@AuthenticationPrincipal AppUser actor, @PathVariable Long id,
                                      @Valid @RequestBody MaintenanceRequest request) {
        return service.update(actor, id, request);
    }

    @Operation(summary = "Déclarer la ressource en maintenance (démarrer)")
    @PostMapping("/{id}/start")
    public MaintenanceResponse start(@AuthenticationPrincipal AppUser actor, @PathVariable Long id) {
        return service.start(actor, id);
    }

    @Operation(summary = "Terminer une maintenance en cours")
    @PostMapping("/{id}/complete")
    public MaintenanceResponse complete(@AuthenticationPrincipal AppUser actor, @PathVariable Long id) {
        return service.complete(actor, id);
    }

    @Operation(summary = "Annuler une maintenance planifiée ou en cours")
    @PostMapping("/{id}/cancel")
    public MaintenanceResponse cancel(@AuthenticationPrincipal AppUser actor, @PathVariable Long id,
                                      @Valid @RequestBody(required = false) CancelRequest request) {
        return service.cancel(actor, id, request == null ? null : request.reason());
    }

    @Operation(summary = "Supprimer une maintenance planifiée ou annulée (réservé ; la trace d'audit est conservée)")
    @DeleteMapping("/{id}")
    @ResponseStatus(HttpStatus.NO_CONTENT)
    public void delete(@AuthenticationPrincipal AppUser actor, @PathVariable Long id) {
        service.delete(actor, id);
    }
}
