package com.orange.pfe.backend.model.team;

import java.util.EnumSet;
import java.util.Set;

/**
 * Statut d'une tâche organisationnelle : À FAIRE, EN COURS, EN ATTENTE, TERMINÉE, ANNULÉE.
 * Sans rapport avec les statuts des tâches d'approbation d'incidents (HumanTask).
 */
public enum TaskStatus {
    TODO,
    IN_PROGRESS,
    ON_HOLD,
    DONE,
    CANCELLED;

    /** Statuts « ouverts » : le travail reste à faire ou en cours. */
    public static final Set<TaskStatus> OPEN = EnumSet.of(TODO, IN_PROGRESS, ON_HOLD);

    public boolean isOpen() {
        return OPEN.contains(this);
    }

    public boolean isClosed() {
        return !isOpen();
    }
}
