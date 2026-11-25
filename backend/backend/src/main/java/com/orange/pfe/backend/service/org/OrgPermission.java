package com.orange.pfe.backend.service.org;

/**
 * Permissions des quatre modules « organisation » (maintenance, audit, rapports, équipes/tâches).
 * Source unique : {@link OrgPermissionPolicy} associe chaque rôle existant ({@code UserRole}) à un ensemble
 * de permissions ; {@link OrgAccessService} les applique côté Backend (jamais seulement côté Angular).
 *
 * <p>Le droit « Approuver » n'existe pas ici : aucun flux d'approbation n'est prévu pour ces modules.
 * Il n'existe volontairement AUCUNE permission de modification ni de suppression du journal d'audit.</p>
 */
public enum OrgPermission {
    MAINTENANCE_VIEW,
    MAINTENANCE_CREATE,
    MAINTENANCE_UPDATE,
    MAINTENANCE_CANCEL,
    MAINTENANCE_DELETE,

    AUDIT_VIEW_OWN,
    AUDIT_VIEW_ALL,
    AUDIT_EXPORT,

    REPORTS_VIEW,
    REPORTS_GENERATE,
    REPORTS_EXPORT,
    REPORTS_SCHEDULE,

    TEAMS_VIEW,
    TEAMS_CREATE,
    TEAMS_UPDATE,
    TEAMS_DEACTIVATE,
    TEAMS_DELETE,
    TEAMS_MANAGE_MEMBERS,

    TASKS_VIEW,
    TASKS_CREATE,
    TASKS_UPDATE,
    TASKS_ASSIGN,
    TASKS_UPDATE_OWN_STATUS
}
