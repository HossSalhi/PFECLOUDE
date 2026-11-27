package com.orange.pfe.backend.dto.routing;

import com.orange.pfe.backend.dto.org.UserRef;
import com.orange.pfe.backend.model.enums.IncidentStatus;
import com.orange.pfe.backend.model.maintenance.MaintenanceStatus;
import com.orange.pfe.backend.model.team.ResourceScope;
import com.orange.pfe.backend.model.team.TaskSourceEvent;
import com.orange.pfe.backend.model.team.TaskStatus;
import jakarta.validation.constraints.NotNull;

import java.util.List;

/** Requêtes et réponses du routage incident → équipe (maintenance en cours). */
public final class RoutingDtos {

    private RoutingDtos() {
    }

    /** Choix du responsable d'équipe face à un incident survenu pendant une maintenance. */
    public enum Decision { CONTINUE, PAUSE }

    /** Où en est le traitement d'un incident par rapport à la maintenance concernée. */
    public enum RoutingState {
        /** Aucun routage : incident hors périmètre, pas de maintenance en cours à ce moment, ou routage non activé. */
        NONE,
        AWAITING_DECISION,
        /** Le responsable a choisi de continuer la maintenance. */
        CONTINUED,
        /** Maintenance suspendue, incident pas encore résolu. */
        PAUSED,
        /** Incident résolu : la reprise de la maintenance est proposée. */
        AWAITING_RESUME,
        /** Maintenance reprise (ou clôturée) : le traitement est terminé. */
        RESUMED
    }

    public record DecisionRequest(@NotNull Long maintenanceId, @NotNull Decision decision) {}

    public record ResumeRequest(@NotNull Long maintenanceId) {}

    public record RoutedTeam(Long id, String name, ResourceScope specialty, UserRef lead) {}

    public record RoutedMaintenance(Long id, String title, String resourceType, String resourceName, MaintenanceStatus status) {}

    public record RoutedTask(Long id, String title, TaskSourceEvent event, TaskStatus status) {}

    public record RoutingView(
            Long incidentId,
            String incidentTitle,
            String incidentType,
            IncidentStatus incidentStatus,
            ResourceScope scope,
            RoutedTeam team,
            RoutedMaintenance maintenance,
            RoutingState state,
            boolean canDecide,
            boolean canResume,
            List<RoutedTask> tasks
    ) {}
}
