package com.orange.pfe.backend.dto.team;

import com.orange.pfe.backend.model.team.TaskPriority;
import com.orange.pfe.backend.model.team.TaskStatus;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Size;

import java.time.LocalDate;

/** Corps de requêtes du module Équipes / tâches. */
public final class TeamRequests {

    private TeamRequests() {
    }

    public record AddMemberRequest(@NotNull Long userId) {}

    public record ChangeLeadRequest(@NotNull Long userId) {}

    /**
     * Création / modification d'une tâche. {@code teamId} et {@code assigneeId} sont optionnels ;
     * si les deux sont fournis, l'assigné doit être membre de l'équipe.
     */
    public record TaskRequest(
            @NotBlank @Size(max = 200) String title,
            @Size(max = 10000) String description,
            Long teamId,
            Long assigneeId,
            TaskPriority priority,
            LocalDate dueDate
    ) {}

    public record TaskStatusRequest(@NotNull TaskStatus status) {}

    /** {@code assigneeId} nul = retirer l'affectation. */
    public record TaskAssignRequest(Long assigneeId) {}
}
