package com.orange.pfe.backend.model.maintenance;

import java.util.EnumSet;
import java.util.Set;

/**
 * Statut d'une opération de maintenance : PLANIFIÉE, EN COURS, SUSPENDUE, TERMINÉE ou ANNULÉE.
 *
 * <p>Transitions de suspension : IN_PROGRESS → SUSPENDED (pause) et SUSPENDED → IN_PROGRESS (reprise), uniquement.
 * Une maintenance suspendue n'est PAS terminée : elle reste active et continue d'occuper sa ressource.
 */
public enum MaintenanceStatus {
    PLANNED,
    IN_PROGRESS,
    SUSPENDED,
    COMPLETED,
    CANCELLED;

    /**
     * Statuts qui occupent la ressource (comptent dans la détection de conflits de planification). SUSPENDED en fait
     * partie : suspendre une maintenance ne libère pas la ressource, sinon une autre pourrait y être planifiée.
     */
    public static final Set<MaintenanceStatus> ACTIVE = EnumSet.of(PLANNED, IN_PROGRESS, SUSPENDED);

    /** Statuts définitifs : aucune modification ni transition n'est plus possible. */
    private static final Set<MaintenanceStatus> FINAL = EnumSet.of(COMPLETED, CANCELLED);

    public boolean isActive() {
        return ACTIVE.contains(this);
    }

    /** Défini explicitement (et non comme « pas actif ») pour qu'un nouveau statut actif ne devienne jamais final par défaut. */
    public boolean isFinal() {
        return FINAL.contains(this);
    }

    /** Maintenance démarrée (en cours ou suspendue) : sa ressource et sa date de début ne sont plus modifiables. */
    public boolean isStarted() {
        return this == IN_PROGRESS || this == SUSPENDED;
    }
}
