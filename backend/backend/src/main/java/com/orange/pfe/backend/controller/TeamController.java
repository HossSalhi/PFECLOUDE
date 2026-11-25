package com.orange.pfe.backend.controller;

import com.orange.pfe.backend.dto.team.TeamRequest;
import com.orange.pfe.backend.dto.team.TeamRequests.AddMemberRequest;
import com.orange.pfe.backend.dto.team.TeamRequests.ChangeLeadRequest;
import com.orange.pfe.backend.dto.team.TeamResponses.TeamMemberResponse;
import com.orange.pfe.backend.dto.team.TeamResponses.TeamResponse;
import com.orange.pfe.backend.dto.team.TeamResponses.TeamSummaryResponse;
import com.orange.pfe.backend.model.user.AppUser;
import com.orange.pfe.backend.service.team.TeamService;
import io.swagger.v3.oas.annotations.Operation;
import io.swagger.v3.oas.annotations.tags.Tag;
import jakarta.validation.Valid;
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

import java.util.List;

/**
 * Gestion des équipes et de leurs membres. Les droits (matrice {@code OrgPermissionPolicy}) sont appliqués par
 * {@link TeamService} : un appel direct à l'API sans le droit requis est refusé (403), quel que soit l'écran.
 */
@RestController
@RequestMapping("/api/teams")
@Tag(name = "Équipes", description = "Équipes, responsables et membres")
public class TeamController {

    private final TeamService teamService;

    public TeamController(TeamService teamService) {
        this.teamService = teamService;
    }

    @Operation(summary = "Lister les équipes (toutes pour un gestionnaire, les siennes sinon)")
    @GetMapping
    public List<TeamResponse> list(@AuthenticationPrincipal AppUser actor,
                                   @RequestParam(defaultValue = "false") boolean includeInactive,
                                   @RequestParam(required = false) String q) {
        return teamService.list(actor, includeInactive, q);
    }

    @Operation(summary = "Détail d'une équipe")
    @GetMapping("/{id}")
    public TeamResponse get(@AuthenticationPrincipal AppUser actor, @PathVariable Long id) {
        return teamService.get(actor, id);
    }

    @Operation(summary = "Créer une équipe")
    @PostMapping
    @ResponseStatus(HttpStatus.CREATED)
    public TeamResponse create(@AuthenticationPrincipal AppUser actor, @Valid @RequestBody TeamRequest request) {
        return teamService.create(actor, request);
    }

    @Operation(summary = "Modifier une équipe (nom, description, responsable)")
    @PutMapping("/{id}")
    public TeamResponse update(@AuthenticationPrincipal AppUser actor, @PathVariable Long id,
                               @Valid @RequestBody TeamRequest request) {
        return teamService.update(actor, id, request);
    }

    @Operation(summary = "Désigner le responsable de l'équipe")
    @PutMapping("/{id}/lead")
    public TeamResponse changeLead(@AuthenticationPrincipal AppUser actor, @PathVariable Long id,
                                   @Valid @RequestBody ChangeLeadRequest request) {
        return teamService.changeLead(actor, id, request.userId());
    }

    @Operation(summary = "Désactiver une équipe")
    @PostMapping("/{id}/deactivate")
    public TeamResponse deactivate(@AuthenticationPrincipal AppUser actor, @PathVariable Long id) {
        return teamService.deactivate(actor, id);
    }

    @Operation(summary = "Réactiver une équipe")
    @PostMapping("/{id}/activate")
    public TeamResponse activate(@AuthenticationPrincipal AppUser actor, @PathVariable Long id) {
        return teamService.activate(actor, id);
    }

    @Operation(summary = "Supprimer une équipe (réservé : aucune tâche rattachée)")
    @DeleteMapping("/{id}")
    @ResponseStatus(HttpStatus.NO_CONTENT)
    public void delete(@AuthenticationPrincipal AppUser actor, @PathVariable Long id) {
        teamService.delete(actor, id);
    }

    @Operation(summary = "Membres de l'équipe")
    @GetMapping("/{id}/members")
    public List<TeamMemberResponse> members(@AuthenticationPrincipal AppUser actor, @PathVariable Long id) {
        return teamService.members(actor, id);
    }

    @Operation(summary = "Ajouter un membre")
    @PostMapping("/{id}/members")
    @ResponseStatus(HttpStatus.CREATED)
    public TeamMemberResponse addMember(@AuthenticationPrincipal AppUser actor, @PathVariable Long id,
                                        @Valid @RequestBody AddMemberRequest request) {
        return teamService.addMember(actor, id, request.userId());
    }

    @Operation(summary = "Retirer un membre")
    @DeleteMapping("/{id}/members/{userId}")
    @ResponseStatus(HttpStatus.NO_CONTENT)
    public void removeMember(@AuthenticationPrincipal AppUser actor, @PathVariable Long id,
                             @PathVariable Long userId) {
        teamService.removeMember(actor, id, userId);
    }

    @Operation(summary = "Avancement des tâches de l'équipe, par statut et par membre")
    @GetMapping("/{id}/summary")
    public TeamSummaryResponse summary(@AuthenticationPrincipal AppUser actor, @PathVariable Long id) {
        return teamService.summary(actor, id);
    }
}
