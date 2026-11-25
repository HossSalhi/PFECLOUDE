package com.orange.pfe.backend.dto.team;

import com.orange.pfe.backend.model.team.TaskPriority;
import com.orange.pfe.backend.model.team.TaskStatus;

import java.util.Collection;

/**
 * Filtre de recherche des tâches. {@code visibleToUserId} / {@code visibleTeamIds} restreignent le résultat aux
 * tâches qu'un utilisateur ordinaire a le droit de voir (les siennes, celles qu'il a créées, celles de ses équipes).
 */
public record TaskFilter(
        Long teamId,
        Long assigneeId,
        TaskStatus status,
        TaskPriority priority,
        boolean overdueOnly,
        String text,
        Long visibleToUserId,
        Collection<Long> visibleTeamIds
) {
    public static TaskFilter forTeam(Long teamId) {
        return new TaskFilter(teamId, null, null, null, false, null, null, null);
    }
}
