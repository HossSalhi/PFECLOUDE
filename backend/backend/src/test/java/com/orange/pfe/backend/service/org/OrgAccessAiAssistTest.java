package com.orange.pfe.backend.service.org;

import com.orange.pfe.backend.model.audit.AuditAction;
import com.orange.pfe.backend.model.audit.AuditResult;
import com.orange.pfe.backend.model.team.Team;
import com.orange.pfe.backend.model.user.AppUser;
import com.orange.pfe.backend.model.user.UserRole;
import com.orange.pfe.backend.service.audit.AuditEvent;
import com.orange.pfe.backend.service.audit.AuditTrailService;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;
import org.springframework.security.access.AccessDeniedException;
import org.springframework.test.util.ReflectionTestUtils;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;

/** Règle d'accès à l'assistance IA : admin = tout ; chef ACTIF de l'équipe précise = son équipe ; sinon rien. */
class OrgAccessAiAssistTest {

    private AuditTrailService audit;
    private OrgAccessService access;

    @BeforeEach
    void setUp() {
        audit = mock(AuditTrailService.class);
        access = new OrgAccessService(audit);
    }

    private AppUser user(long id, UserRole role) {
        AppUser u = new AppUser();
        ReflectionTestUtils.setField(u, "id", id);
        u.setRole(role);
        return u;
    }

    private Team team(long id, AppUser lead, boolean active) {
        Team t = new Team();
        ReflectionTestUtils.setField(t, "id", id);
        t.setLead(lead);
        t.setActive(active);
        return t;
    }

    @Test
    void superAdminAndAdminCanUseEveryTeamAndEvenNoTeam() {
        Team any = team(1, user(50, UserRole.USER), true);
        for (UserRole role : new UserRole[]{UserRole.SUPER_ADMIN, UserRole.ADMIN}) {
            AppUser admin = user(1, role);
            assertThat(access.canUseAiAssistFor(admin, any)).isTrue();
            assertThat(access.canUseAiAssistFor(admin, team(2, null, false))).isTrue();
            assertThat(access.canUseAiAssistFor(admin, null)).isTrue();
        }
    }

    @Test
    void activeLeaderOfThatTeamCanUseIt() {
        AppUser lead = user(10, UserRole.USER);

        assertThat(access.canUseAiAssistFor(lead, team(1, lead, true))).isTrue();
    }

    @Test
    void leaderOfTeamACanNeverUseTeamB() {
        AppUser leadA = user(10, UserRole.USER);
        AppUser leadB = user(11, UserRole.USER);
        Team a = team(1, leadA, true);
        Team b = team(2, leadB, true);

        assertThat(access.canUseAiAssistFor(leadA, a)).isTrue();
        assertThat(access.canUseAiAssistFor(leadA, b)).isFalse();
        assertThat(access.canUseAiAssistFor(leadB, a)).isFalse();
    }

    @Test
    void leaderOfADeactivatedTeamLosesAccess() {
        AppUser lead = user(10, UserRole.USER);

        assertThat(access.canUseAiAssistFor(lead, team(1, lead, false))).isFalse();
    }

    @Test
    void plainTechnicianAndMissingContextHaveNoAccess() {
        AppUser tech = user(20, UserRole.USER);
        Team t = team(1, user(10, UserRole.USER), true);

        assertThat(access.canUseAiAssistFor(tech, t)).isFalse();
        assertThat(access.canUseAiAssistFor(tech, null)).isFalse();
        assertThat(access.canUseAiAssistFor(null, t)).isFalse();
        assertThat(access.canUseAiAssistFor(user(10, UserRole.USER), team(3, null, true))).isFalse();
    }

    @Test
    void requireAllowsAuthorizedWithoutAuditNoise() {
        AppUser lead = user(10, UserRole.USER);

        access.requireAiAssistFor(lead, team(1, lead, true));

        verify(audit, never()).recordIndependent(any());
    }

    @Test
    void requireDeniesAndTracesAccessDenied() {
        AppUser leadA = user(10, UserRole.USER);
        Team b = team(2, user(11, UserRole.USER), true);

        assertThatThrownBy(() -> access.requireAiAssistFor(leadA, b)).isInstanceOf(AccessDeniedException.class);

        ArgumentCaptor<AuditEvent> ev = ArgumentCaptor.forClass(AuditEvent.class);
        verify(audit).recordIndependent(ev.capture());
        assertThat(ev.getValue().action()).isEqualTo(AuditAction.ACCESS_DENIED);
        assertThat(ev.getValue().result()).isEqualTo(AuditResult.DENIED);
        assertThat(ev.getValue().summary()).contains("équipe #2");
    }

    @Test
    void requireRejectsAnonymous() {
        assertThatThrownBy(() -> access.requireAiAssistFor(null, null)).isInstanceOf(AccessDeniedException.class);
    }

    @Test
    void theExistingPermissionMatrixIsUntouchedByTheAiRule() {
        AppUser lead = user(10, UserRole.USER);

        // un chef d'équipe (USER) garde exactement les droits de la matrice : l'IA ne lui ouvre rien d'autre
        assertThat(access.has(lead, OrgPermission.REPORTS_GENERATE)).isFalse();
        assertThat(access.has(lead, OrgPermission.TASKS_ASSIGN)).isFalse();
        assertThat(access.has(lead, OrgPermission.MAINTENANCE_CREATE)).isFalse();
        assertThat(access.has(lead, OrgPermission.AUDIT_VIEW_ALL)).isFalse();
    }
}
