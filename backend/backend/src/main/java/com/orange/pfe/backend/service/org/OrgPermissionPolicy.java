package com.orange.pfe.backend.service.org;

import com.orange.pfe.backend.model.user.UserRole;

import java.util.Collections;
import java.util.EnumMap;
import java.util.EnumSet;
import java.util.Map;
import java.util.Set;

import static com.orange.pfe.backend.service.org.OrgPermission.*;

/**
 * Matrice de permissions des modules « organisation », par rôle HELIOS EXISTANT (SUPER_ADMIN, ADMIN, USER).
 * Les rôles ne sont pas redéfinis : la matrice est un complément, pas un second système d'autorisation.
 *
 * <pre>
 *                                   SUPER_ADMIN  ADMIN  USER
 *  Maintenance : voir                   ✓          ✓      ✓
 *  Maintenance : créer/modifier/annuler ✓          ✓      –
 *  Maintenance : supprimer              ✓          –      –
 *  Audit : sa propre activité           ✓          ✓      ✓
 *  Audit : toute l'activité / export    ✓          ✓      –
 *  Rapports : voir/générer/exporter     ✓          ✓      –
 *  Équipes : voir                       ✓          ✓      ✓ (ses équipes)
 *  Équipes : gérer / membres            ✓          ✓      –
 *  Équipes : supprimer                  ✓          –      –
 *  Tâches : voir                        ✓          ✓      ✓ (les siennes)
 *  Tâches : créer/modifier/affecter     ✓          ✓      –
 *  Tâches : statut de ses tâches        ✓          ✓      ✓
 * </pre>
 */
public final class OrgPermissionPolicy {

    private static final Map<UserRole, Set<OrgPermission>> MATRIX = new EnumMap<>(UserRole.class);

    static {
        Set<OrgPermission> superAdmin = EnumSet.allOf(OrgPermission.class);

        Set<OrgPermission> admin = EnumSet.of(
                MAINTENANCE_VIEW, MAINTENANCE_CREATE, MAINTENANCE_UPDATE, MAINTENANCE_CANCEL,
                AUDIT_VIEW_OWN, AUDIT_VIEW_ALL, AUDIT_EXPORT,
                REPORTS_VIEW, REPORTS_GENERATE, REPORTS_EXPORT, REPORTS_SCHEDULE,
                TEAMS_VIEW, TEAMS_CREATE, TEAMS_UPDATE, TEAMS_DEACTIVATE, TEAMS_MANAGE_MEMBERS,
                TASKS_VIEW, TASKS_CREATE, TASKS_UPDATE, TASKS_ASSIGN, TASKS_UPDATE_OWN_STATUS);

        Set<OrgPermission> user = EnumSet.of(
                MAINTENANCE_VIEW,
                AUDIT_VIEW_OWN,
                TEAMS_VIEW,
                TASKS_VIEW, TASKS_UPDATE_OWN_STATUS);

        MATRIX.put(UserRole.SUPER_ADMIN, Collections.unmodifiableSet(superAdmin));
        MATRIX.put(UserRole.ADMIN, Collections.unmodifiableSet(admin));
        MATRIX.put(UserRole.USER, Collections.unmodifiableSet(user));
    }

    private OrgPermissionPolicy() {
    }

    public static Set<OrgPermission> permissionsOf(UserRole role) {
        if (role == null) {
            return Set.of();
        }
        return MATRIX.getOrDefault(role, Set.of());
    }

    public static boolean allows(UserRole role, OrgPermission permission) {
        return permission != null && permissionsOf(role).contains(permission);
    }
}
