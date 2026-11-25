package com.orange.pfe.backend.service.org;

import com.orange.pfe.backend.model.user.UserRole;
import org.junit.jupiter.api.Test;

import java.util.Arrays;
import java.util.Set;

import static org.assertj.core.api.Assertions.assertThat;

/** Matrice de permissions des quatre modules « organisation » par rôle HELIOS existant. */
class OrgPermissionPolicyTest {

    @Test
    void superAdminHasEveryPermission() {
        assertThat(OrgPermissionPolicy.permissionsOf(UserRole.SUPER_ADMIN))
                .containsExactlyInAnyOrder(OrgPermission.values());
    }

    @Test
    void userCanOnlyReadAndChangeTheStatusOfOwnTasks() {
        Set<OrgPermission> user = OrgPermissionPolicy.permissionsOf(UserRole.USER);
        assertThat(user).containsExactlyInAnyOrder(
                OrgPermission.MAINTENANCE_VIEW, OrgPermission.AUDIT_VIEW_OWN, OrgPermission.TEAMS_VIEW,
                OrgPermission.TASKS_VIEW, OrgPermission.TASKS_UPDATE_OWN_STATUS);
        assertThat(user).doesNotContain(OrgPermission.AUDIT_VIEW_ALL, OrgPermission.AUDIT_EXPORT,
                OrgPermission.REPORTS_VIEW, OrgPermission.TASKS_CREATE, OrgPermission.MAINTENANCE_CREATE);
    }

    @Test
    void adminManagesButCannotDeleteMaintenancesOrTeams() {
        Set<OrgPermission> admin = OrgPermissionPolicy.permissionsOf(UserRole.ADMIN);
        assertThat(admin).contains(
                OrgPermission.MAINTENANCE_CREATE, OrgPermission.MAINTENANCE_UPDATE, OrgPermission.MAINTENANCE_CANCEL,
                OrgPermission.AUDIT_VIEW_ALL, OrgPermission.AUDIT_EXPORT,
                OrgPermission.REPORTS_VIEW, OrgPermission.REPORTS_GENERATE, OrgPermission.REPORTS_EXPORT,
                OrgPermission.TEAMS_CREATE, OrgPermission.TEAMS_MANAGE_MEMBERS,
                OrgPermission.TASKS_CREATE, OrgPermission.TASKS_ASSIGN);
        assertThat(admin).doesNotContain(OrgPermission.MAINTENANCE_DELETE, OrgPermission.TEAMS_DELETE);
    }

    @Test
    void noPermissionExistsToModifyOrDeleteTheAuditLog() {
        // Seules la lecture (la sienne / toute) et l'export existent pour l'audit : pas de modification ni de suppression.
        assertThat(Arrays.stream(OrgPermission.values()).map(Enum::name).filter(n -> n.startsWith("AUDIT_")))
                .containsExactlyInAnyOrder("AUDIT_VIEW_OWN", "AUDIT_VIEW_ALL", "AUDIT_EXPORT");
    }

    @Test
    void unknownRoleHasNoPermission() {
        assertThat(OrgPermissionPolicy.permissionsOf(null)).isEmpty();
        assertThat(OrgPermissionPolicy.allows(null, OrgPermission.TEAMS_VIEW)).isFalse();
        assertThat(OrgPermissionPolicy.allows(UserRole.ADMIN, null)).isFalse();
    }

    @Test
    void everyRoleCanViewItsOwnActivity() {
        for (UserRole role : UserRole.values()) {
            assertThat(OrgPermissionPolicy.allows(role, OrgPermission.AUDIT_VIEW_OWN)).isTrue();
        }
    }
}
