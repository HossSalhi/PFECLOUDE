package com.orange.pfe.backend.service.team;

import com.orange.pfe.backend.model.team.ResourceScope;

/**
 * Libellé de rôle CALCULÉ, jamais stocké : « Chef &lt;spécialité&gt; » pour le responsable d'une équipe, « Technicien
 * &lt;spécialité&gt; » pour un simple membre. Il ne modifie ni {@code AppUser} ni {@code UserRole} : les droits restent
 * ceux du rôle HELIOS existant, ce libellé n'est qu'un badge d'affichage.
 */
public final class RoleLabels {

    private RoleLabels() {
    }

    /** Équipe sans spécialité : « Chef d'équipe » / « Technicien ». */
    public static String of(ResourceScope specialty, boolean lead) {
        if (specialty == null) {
            return lead ? "Chef d'équipe" : "Technicien";
        }
        return (lead ? "Chef " : "Technicien ") + name(specialty);
    }

    public static String name(ResourceScope scope) {
        return switch (scope) {
            case VM -> "VM";
            case CLUSTER -> "Cluster";
            case APPLICATION -> "Application";
            case SERVICE -> "Service";
        };
    }
}
