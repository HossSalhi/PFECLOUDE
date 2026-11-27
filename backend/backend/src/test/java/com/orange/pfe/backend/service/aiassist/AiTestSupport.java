package com.orange.pfe.backend.service.aiassist;

import com.orange.pfe.backend.model.team.ResourceScope;
import com.orange.pfe.backend.model.team.Team;
import com.orange.pfe.backend.model.user.AppUser;
import com.orange.pfe.backend.model.user.UserRole;
import com.orange.pfe.backend.repository.TeamRepository;
import com.orange.pfe.backend.service.audit.AuditTrailService;
import com.orange.pfe.backend.service.org.OrgAccessService;
import org.springframework.test.util.ReflectionTestUtils;

import java.time.Instant;
import java.util.Optional;

import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

/** Outils de test communs : utilisateurs, équipes et garde d'accès RÉELLE (règle admin / chef actif) autour de dépôts simulés. */
final class AiTestSupport {

    private AiTestSupport() {
    }

    static AppUser user(long id, String username, String first, String last, UserRole role) {
        AppUser u = new AppUser();
        ReflectionTestUtils.setField(u, "id", id);
        u.setUsername(username);
        u.setFirstName(first);
        u.setLastName(last);
        u.setRole(role);
        u.setEnabled(true);
        return u;
    }

    static Team team(long id, String name, AppUser lead, ResourceScope specialty) {
        Team t = new Team();
        ReflectionTestUtils.setField(t, "id", id);
        ReflectionTestUtils.setField(t, "name", name);
        t.setLead(lead);
        t.setActive(true);
        t.setSpecialty(specialty);
        return t;
    }

    /** Garde réelle (vraie règle d'accès) ; seuls les dépôts et le client IA sont simulés. */
    static AiAssistGuard guard(AiCompletionClient client, Team... teams) {
        TeamRepository repo = mock(TeamRepository.class);
        for (Team t : teams) {
            when(repo.findById(t.getId())).thenReturn(Optional.of(t));
        }
        when(repo.findById(eq(999L))).thenReturn(Optional.empty());
        OrgAccessService access = new OrgAccessService(mock(AuditTrailService.class));
        return new AiAssistGuard(access, repo, client);
    }

    /** Client IA simulé : activé et configuré, il répond {@code text}. */
    static AiCompletionClient readyClient(String text) {
        AiCompletionClient c = mock(AiCompletionClient.class);
        when(c.notReadyReason(any())).thenReturn(null);
        when(c.complete(any(), any(), any())).thenReturn(new AiCompletionResult.Success(text, "test-model", Instant.now()));
        return c;
    }

    static AiCompletionClient clientWith(AiCompletionResult.Reason reason) {
        AiCompletionClient c = mock(AiCompletionClient.class);
        when(c.notReadyReason(any())).thenReturn(reason);
        when(c.complete(any(), any(), any())).thenReturn(new AiCompletionResult.Unavailable(
                reason == null ? AiCompletionResult.Reason.UNREACHABLE : reason, Instant.now()));
        return c;
    }
}
