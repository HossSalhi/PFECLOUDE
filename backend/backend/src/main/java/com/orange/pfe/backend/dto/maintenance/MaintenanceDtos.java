package com.orange.pfe.backend.dto.maintenance;

import com.orange.pfe.backend.dto.org.UserRef;
import com.orange.pfe.backend.model.maintenance.MaintenanceResourceType;
import com.orange.pfe.backend.model.maintenance.MaintenanceStatus;
import com.orange.pfe.backend.model.team.ResourceScope;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Size;

import java.time.LocalDateTime;

/** Requêtes et réponses du module Maintenance. */
public final class MaintenanceDtos {

    private MaintenanceDtos() {
    }

    public record MaintenanceRequest(
            @NotBlank @Size(max = 200) String title,
            @Size(max = 10000) String description,
            @Size(max = 500) String reason,
            @NotNull MaintenanceResourceType resourceType,
            @NotBlank @Size(max = 200) String resourceName,
            @NotNull LocalDateTime startAt,
            @NotNull LocalDateTime endAt,
            Long responsibleId,
            Long teamId
    ) {
        /** Sans équipe : conserve les appels existants à 8 arguments. */
        public MaintenanceRequest(String title, String description, String reason, MaintenanceResourceType resourceType,
                                  String resourceName, LocalDateTime startAt, LocalDateTime endAt, Long responsibleId) {
            this(title, description, reason, resourceType, resourceName, startAt, endAt, responsibleId, null);
        }
    }

    public record CancelRequest(@Size(max = 500) String reason) {}

    /** Pause d'une maintenance : un motif et/ou l'incident déclencheur (au moins l'un des deux). */
    public record PauseRequest(Long incidentId, @Size(max = 500) String reason) {}

    /** Équipe porteuse d'une maintenance, avec sa spécialité et son responsable. */
    public record TeamInfo(Long id, String name, ResourceScope specialty, UserRef lead) {}

    /**
     * {@code startOverdue} : planifiée dont l'heure de début est passée sans démarrage ;
     * {@code endOverdue} : en cours ou suspendue dont l'heure de fin est dépassée. Simples indicateurs d'affichage.
     * {@code suspendedAt}, {@code suspendReason}, {@code suspendIncidentId} : renseignés seulement si SUSPENDED.
     */
    public record MaintenanceResponse(
            Long id,
            String title,
            String description,
            String reason,
            MaintenanceResourceType resourceType,
            String resourceName,
            LocalDateTime startAt,
            LocalDateTime endAt,
            MaintenanceStatus status,
            UserRef createdBy,
            UserRef responsible,
            TeamInfo team,
            LocalDateTime actualStartAt,
            LocalDateTime actualEndAt,
            String cancelReason,
            LocalDateTime suspendedAt,
            String suspendReason,
            Long suspendIncidentId,
            boolean startOverdue,
            boolean endOverdue,
            LocalDateTime createdAt,
            LocalDateTime updatedAt
    ) {}

    public record HistoryResponse(
            Long id,
            LocalDateTime changedAt,
            String changedBy,
            String changeType,
            String details
    ) {}

    public record ResourceResponse(
            MaintenanceResourceType type,
            String name,
            String label
    ) {}
}
