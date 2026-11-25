package com.orange.pfe.backend.dto.maintenance;

import com.orange.pfe.backend.model.maintenance.MaintenanceResourceType;
import com.orange.pfe.backend.model.maintenance.MaintenanceStatus;

import java.time.LocalDateTime;

/**
 * Filtre de recherche des maintenances. Période [from, to[ : sont retenues les maintenances dont la période
 * chevauche cet intervalle.
 */
public record MaintenanceFilter(
        MaintenanceStatus status,
        MaintenanceResourceType resourceType,
        String resourceName,
        Long responsibleId,
        LocalDateTime from,
        LocalDateTime to,
        String text
) {
    public static MaintenanceFilter range(LocalDateTime from, LocalDateTime to) {
        return new MaintenanceFilter(null, null, null, null, from, to, null);
    }
}
