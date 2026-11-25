package com.orange.pfe.backend.model.maintenance;

import java.util.EnumSet;
import java.util.Set;

/** Statut d'une opération de maintenance : PLANIFIÉE, EN COURS, TERMINÉE ou ANNULÉE. */
public enum MaintenanceStatus {
    PLANNED,
    IN_PROGRESS,
    COMPLETED,
    CANCELLED;

    /** Statuts qui occupent la ressource (comptent dans la détection de conflits de planification). */
    public static final Set<MaintenanceStatus> ACTIVE = EnumSet.of(PLANNED, IN_PROGRESS);

    public boolean isActive() {
        return ACTIVE.contains(this);
    }

    public boolean isFinal() {
        return !isActive();
    }
}
