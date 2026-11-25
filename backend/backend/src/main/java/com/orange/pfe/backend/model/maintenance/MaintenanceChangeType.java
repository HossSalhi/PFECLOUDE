package com.orange.pfe.backend.model.maintenance;

/** Nature d'un changement conservé dans l'historique d'une maintenance. */
public enum MaintenanceChangeType {
    CREATED,
    UPDATED,
    STARTED,
    COMPLETED,
    CANCELLED
}
