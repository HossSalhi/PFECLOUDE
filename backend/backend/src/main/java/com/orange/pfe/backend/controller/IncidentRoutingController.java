package com.orange.pfe.backend.controller;

import com.orange.pfe.backend.dto.routing.RoutingDtos.DecisionRequest;
import com.orange.pfe.backend.dto.routing.RoutingDtos.ResumeRequest;
import com.orange.pfe.backend.dto.routing.RoutingDtos.RoutingView;
import com.orange.pfe.backend.model.user.AppUser;
import com.orange.pfe.backend.service.routing.IncidentTeamRoutingService;
import io.swagger.v3.oas.annotations.Operation;
import io.swagger.v3.oas.annotations.tags.Tag;
import jakarta.validation.Valid;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

/**
 * Décisions du responsable d'équipe face à un incident survenu pendant une maintenance. Les droits sont appliqués par
 * {@link IncidentTeamRoutingService} (responsable de l'équipe concernée, ou {@code MAINTENANCE_UPDATE}) : un appel
 * direct sans ce droit est refusé (403) et journalisé.
 */
@RestController
@RequestMapping("/api/incident-routing")
@Tag(name = "Routage incident → équipe", description = "Maintenance en cours, incident, décision du responsable d'équipe")
public class IncidentRoutingController {

    private final IncidentTeamRoutingService service;

    public IncidentRoutingController(IncidentTeamRoutingService service) {
        this.service = service;
    }

    @Operation(summary = "Situation d'un incident par rapport à la maintenance en cours (équipe, état, décisions possibles)")
    @GetMapping("/incidents/{incidentId}")
    public RoutingView view(@AuthenticationPrincipal AppUser actor, @PathVariable Long incidentId) {
        return service.view(actor, incidentId);
    }

    @Operation(summary = "Continuer ou suspendre la maintenance en cours face à l'incident")
    @PostMapping("/incidents/{incidentId}/decision")
    public RoutingView decide(@AuthenticationPrincipal AppUser actor, @PathVariable Long incidentId,
                              @Valid @RequestBody DecisionRequest request) {
        return service.decide(actor, incidentId, request);
    }

    @Operation(summary = "Reprendre la maintenance suspendue une fois l'incident résolu")
    @PostMapping("/incidents/{incidentId}/resume")
    public RoutingView resume(@AuthenticationPrincipal AppUser actor, @PathVariable Long incidentId,
                              @Valid @RequestBody ResumeRequest request) {
        return service.resume(actor, incidentId, request.maintenanceId());
    }
}
