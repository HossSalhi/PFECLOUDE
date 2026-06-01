package com.orange.pfe.backend.service.rules;

import com.orange.pfe.backend.exception.InvalidIncidentStateException;
import com.orange.pfe.backend.model.core.Incident;
import com.orange.pfe.backend.model.enums.IncidentSeverity;
import com.orange.pfe.backend.model.enums.IncidentStatus;

import java.util.EnumMap;
import java.util.EnumSet;
import java.util.Map;
import java.util.Set;

public final class IncidentBusinessRules {

    private IncidentBusinessRules() {}

    // 🔒 Table des transitions autorisées
    private static final Map<IncidentStatus, Set<IncidentStatus>> ALLOWED_TRANSITIONS = new EnumMap<>(IncidentStatus.class);

    static {
        ALLOWED_TRANSITIONS.put(IncidentStatus.NEW, EnumSet.of(IncidentStatus.OPEN, IncidentStatus.IN_PROGRESS));
        ALLOWED_TRANSITIONS.put(IncidentStatus.OPEN, EnumSet.of(IncidentStatus.IN_PROGRESS, IncidentStatus.WAITING_FOR_APPROVAL));
        ALLOWED_TRANSITIONS.put(IncidentStatus.IN_PROGRESS, EnumSet.of(IncidentStatus.RESOLVED, IncidentStatus.WAITING_FOR_APPROVAL));
        ALLOWED_TRANSITIONS.put(IncidentStatus.WAITING_FOR_APPROVAL, EnumSet.of(IncidentStatus.IN_PROGRESS, IncidentStatus.RESOLVED));
        ALLOWED_TRANSITIONS.put(IncidentStatus.RESOLVED, EnumSet.of(IncidentStatus.CLOSED));
        ALLOWED_TRANSITIONS.put(IncidentStatus.CLOSED, EnumSet.of(IncidentStatus.ARCHIVED));
        ALLOWED_TRANSITIONS.put(IncidentStatus.ARCHIVED, EnumSet.noneOf(IncidentStatus.class)); // Aucun
    }

    // 🔹 Vérifie si l'incident peut être modifié
    public static void checkModifiable(Incident i) {
        if (i.getStatus() == IncidentStatus.ARCHIVED)
            throw new InvalidIncidentStateException("Archived incident cannot be modified");
        if (i.getStatus() == IncidentStatus.CLOSED)
            throw new InvalidIncidentStateException("Closed incident cannot be modified");
    }

    // 🔹 Vérifie si la transition de status est autorisée
    public static void checkStatusTransition(IncidentStatus from, IncidentStatus to) {
        if (from == to) return; // Pas de changement

        Set<IncidentStatus> allowed = ALLOWED_TRANSITIONS.get(from);
        if (allowed == null || !allowed.contains(to)) {
            throw new InvalidIncidentStateException(
                    "Transition from " + from + " to " + to + " is not allowed"
            );
        }
    }

    // 🔹 Vérifie si la sévérité peut être modifiée
    public static void checkSeverityChange(Incident i, IncidentSeverity newSeverity) {
        if (newSeverity == null) return;

        if (EnumSet.of(
                IncidentStatus.RESOLVED,
                IncidentStatus.CLOSED,
                IncidentStatus.ARCHIVED
        ).contains(i.getStatus())) {
            throw new InvalidIncidentStateException(
                    "Severity cannot be modified after RESOLVED, CLOSED, or ARCHIVED"
            );
        }
    }
}
