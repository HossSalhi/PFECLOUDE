package com.orange.pfe.backend.controller;

import com.orange.pfe.backend.dto.team.TeamRequests.TaskAssignRequest;
import com.orange.pfe.backend.dto.team.TeamRequests.TaskRequest;
import com.orange.pfe.backend.dto.team.TeamRequests.TaskStatusRequest;
import com.orange.pfe.backend.dto.team.TeamResponses.TaskResponse;
import com.orange.pfe.backend.dto.team.TeamResponses.TaskSummaryResponse;
import com.orange.pfe.backend.model.team.TaskPriority;
import com.orange.pfe.backend.model.team.TaskStatus;
import com.orange.pfe.backend.model.user.AppUser;
import com.orange.pfe.backend.service.team.TeamTaskService;
import io.swagger.v3.oas.annotations.Operation;
import io.swagger.v3.oas.annotations.tags.Tag;
import jakarta.validation.Valid;
import org.springframework.data.domain.Page;
import org.springframework.http.HttpStatus;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PatchMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.PutMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.ResponseStatus;
import org.springframework.web.bind.annotation.RestController;

/**
 * Tâches organisationnelles affectées aux membres des équipes. Chemin distinct de {@code /api/tasks}, qui reste le
 * système de tâches d'approbation d'incidents. Les droits sont appliqués par {@link TeamTaskService}.
 */
@RestController
@RequestMapping("/api/team-tasks")
@Tag(name = "Tâches d'équipe", description = "Tâches organisationnelles ou techniques affectées aux membres")
public class TeamTaskController {

    private final TeamTaskService taskService;

    public TeamTaskController(TeamTaskService taskService) {
        this.taskService = taskService;
    }

    @Operation(summary = "Lister / filtrer les tâches (équipe, responsable, statut, priorité, retard, recherche)")
    @GetMapping
    public Page<TaskResponse> list(@AuthenticationPrincipal AppUser actor,
                                   @RequestParam(required = false) Long teamId,
                                   @RequestParam(required = false) Long assigneeId,
                                   @RequestParam(required = false) TaskStatus status,
                                   @RequestParam(required = false) TaskPriority priority,
                                   @RequestParam(defaultValue = "false") boolean overdue,
                                   @RequestParam(defaultValue = "false") boolean mine,
                                   @RequestParam(required = false) String q,
                                   @RequestParam(defaultValue = "0") int page,
                                   @RequestParam(defaultValue = "25") int size) {
        return taskService.search(actor,
                new TeamTaskService.Query(teamId, assigneeId, status, priority, overdue, mine, q), page, size);
    }

    @Operation(summary = "Avancement global des tâches visibles (par statut, retards, taux d'achèvement)")
    @GetMapping("/summary")
    public TaskSummaryResponse summary(@AuthenticationPrincipal AppUser actor,
                                       @RequestParam(required = false) Long teamId,
                                       @RequestParam(defaultValue = "false") boolean mine) {
        return taskService.summary(actor, teamId, mine);
    }

    @Operation(summary = "Détail d'une tâche")
    @GetMapping("/{id}")
    public TaskResponse get(@AuthenticationPrincipal AppUser actor, @PathVariable Long id) {
        return taskService.get(actor, id);
    }

    @Operation(summary = "Créer une tâche")
    @PostMapping
    @ResponseStatus(HttpStatus.CREATED)
    public TaskResponse create(@AuthenticationPrincipal AppUser actor, @Valid @RequestBody TaskRequest request) {
        return taskService.create(actor, request);
    }

    @Operation(summary = "Modifier une tâche ouverte")
    @PutMapping("/{id}")
    public TaskResponse update(@AuthenticationPrincipal AppUser actor, @PathVariable Long id,
                               @Valid @RequestBody TaskRequest request) {
        return taskService.update(actor, id, request);
    }

    @Operation(summary = "Affecter / réaffecter une tâche à un membre")
    @PatchMapping("/{id}/assignee")
    public TaskResponse assign(@AuthenticationPrincipal AppUser actor, @PathVariable Long id,
                               @RequestBody TaskAssignRequest request) {
        return taskService.assign(actor, id, request.assigneeId());
    }

    @Operation(summary = "Modifier le statut d'une tâche (son responsable peut le faire pour ses propres tâches)")
    @PatchMapping("/{id}/status")
    public TaskResponse changeStatus(@AuthenticationPrincipal AppUser actor, @PathVariable Long id,
                                     @Valid @RequestBody TaskStatusRequest request) {
        return taskService.changeStatus(actor, id, request.status());
    }
}
