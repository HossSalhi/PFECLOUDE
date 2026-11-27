package com.orange.pfe.backend.model.team;

/**
 * Étape du routage d'un incident qui a produit une tâche d'équipe automatique. Une tâche par (incident, étape) :
 * l'unicité garantit qu'un incident ne produit jamais deux fois la même tâche.
 */
public enum TaskSourceEvent {
    /** Incident survenu pendant une maintenance en cours : décider de continuer ou de suspendre. */
    DECISION,
    /** Maintenance suspendue à cause de l'incident : suivre l'incident jusqu'à sa résolution. */
    FOLLOW_UP,
    /** Incident résolu : reprendre la maintenance suspendue. */
    RESUME
}
