package com.orange.pfe.backend.dto.team;

import com.orange.pfe.backend.dto.org.UserRef;
import com.orange.pfe.backend.model.team.ResourceScope;
import com.orange.pfe.backend.model.team.TaskPriority;
import com.orange.pfe.backend.model.team.TaskSourceEvent;
import com.orange.pfe.backend.model.team.TaskStatus;

import java.time.LocalDate;
import java.time.LocalDateTime;
import java.util.List;
import java.util.Map;

/** Réponses du module Équipes / tâches. */
public final class TeamResponses {

    private TeamResponses() {
    }

    public record TeamRef(Long id, String name) {}

    public record TeamResponse(
            Long id,
            String name,
            String description,
            ResourceScope specialty,
            UserRef lead,
            boolean active,
            int memberCount,
            long openTaskCount,
            UserRef createdBy,
            LocalDateTime createdAt,
            LocalDateTime updatedAt
    ) {}

    public record TeamMemberResponse(
            Long userId,
            String username,
            String displayName,
            String role,
            boolean enabled,
            boolean lead,
            LocalDateTime addedAt,
            String addedBy,
            /** Badge calculé : « Chef &lt;spécialité&gt; » ou « Technicien &lt;spécialité&gt; » (jamais stocké). */
            String roleLabel
    ) {}

    public record TaskResponse(
            Long id,
            String title,
            String description,
            TeamRef team,
            UserRef creator,
            UserRef assignee,
            TaskPriority priority,
            TaskStatus status,
            LocalDate dueDate,
            boolean overdue,
            LocalDateTime completedAt,
            LocalDateTime createdAt,
            LocalDateTime updatedAt,
            Long sourceIncidentId,
            TaskSourceEvent sourceEvent
    ) {}

    /** Avancement : compte par statut, retards et taux d'achèvement (% de tâches terminées hors annulées). */
    public record TaskSummaryResponse(
            long total,
            Map<String, Long> byStatus,
            long overdue,
            double completionRate
    ) {}

    public record MemberTaskStats(
            Long userId,
            String displayName,
            long total,
            long open,
            long done,
            long overdue
    ) {}

    public record TeamSummaryResponse(
            Long teamId,
            String teamName,
            TaskSummaryResponse tasks,
            List<MemberTaskStats> members
    ) {}
}
