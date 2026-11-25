package com.orange.pfe.backend.dto.maintenance;

import com.orange.pfe.backend.dto.org.UserRef;
import com.orange.pfe.backend.model.maintenance.MaintenanceResourceType;
import com.orange.pfe.backend.model.maintenance.MaintenanceStatus;
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
            Long responsibleId
    ) {}

    public record CancelRequest(@Size(max = 500) String reason) {}

    /**
     * {@code startOverdue} : planifiée dont l'heure de début est passée sans démarrage ;
     * {@code endOverdue} : en cours dont l'heure de fin est dépassée. Simples indicateurs d'affichage.
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
            LocalDateTime actualStartAt,
            LocalDateTime actualEndAt,
            String cancelReason,
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
