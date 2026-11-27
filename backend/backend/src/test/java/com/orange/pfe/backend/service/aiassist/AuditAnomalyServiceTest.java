package com.orange.pfe.backend.service.aiassist;

import com.orange.pfe.backend.dto.aiassist.AiAssistDtos.Anomaly;
import com.orange.pfe.backend.dto.aiassist.AiAssistDtos.AnomalyResponse;
import com.orange.pfe.backend.model.audit.AuditAction;
import com.orange.pfe.backend.model.audit.AuditLogEntry;
import com.orange.pfe.backend.model.audit.AuditModule;
import com.orange.pfe.backend.model.audit.AuditResult;
import com.orange.pfe.backend.model.team.Team;
import com.orange.pfe.backend.model.team.TeamMember;
import com.orange.pfe.backend.model.user.AppUser;
import com.orange.pfe.backend.model.user.UserRole;
import com.orange.pfe.backend.repository.AuditLogSearchRepository;
import com.orange.pfe.backend.repository.TeamMemberRepository;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;
import org.springframework.security.access.AccessDeniedException;

import java.time.LocalDateTime;
import java.util.ArrayList;
import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyInt;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

/** Règles d'anomalie déterministes + périmètre d'accès (admin = tout, chef = son équipe) + phrases IA anonymisées. */
class AuditAnomalyServiceTest {

    private static final LocalDateTime T0 = LocalDateTime.now().minusDays(1).withHour(14).withMinute(0).withSecond(0).withNano(0);

    private AuditLogSearchRepository audit;
    private TeamMemberRepository members;
    private AppUser admin, leadA, leadB, a1, b1, tech;
    private Team teamA, teamB;
    private List<AuditLogEntry> entries;

    @BeforeEach
    void setUp() {
        audit = mock(AuditLogSearchRepository.class);
        members = mock(TeamMemberRepository.class);
        admin = AiTestSupport.user(1, "root", "Ad", "Min", UserRole.ADMIN);
        leadA = AiTestSupport.user(10, "leada", "Lead", "Alpha", UserRole.USER);
        leadB = AiTestSupport.user(20, "leadb", "Lead", "Beta", UserRole.USER);
        a1 = AiTestSupport.user(11, "amine.a1", "Amine", "Alpha", UserRole.USER);
        b1 = AiTestSupport.user(21, "bilel.b1", "Bilel", "Beta", UserRole.USER);
        tech = AiTestSupport.user(30, "simple", "Sim", "Ple", UserRole.USER);
        teamA = AiTestSupport.team(1, "Équipe A", leadA, null);
        teamB = AiTestSupport.team(2, "Équipe B", leadB, null);
        when(members.findByTeamIdWithUser(1L)).thenReturn(List.of(new TeamMember(teamA, a1, "t")));
        when(members.findByTeamIdWithUser(2L)).thenReturn(List.of(new TeamMember(teamB, b1, "t")));
        entries = new ArrayList<>();
        when(audit.searchForExport(any(), anyInt())).thenAnswer(i -> entries);
    }

    private AuditAnomalyService service(AiCompletionClient client) {
        return new AuditAnomalyService(AiTestSupport.guard(client, teamA, teamB), audit, members);
    }

    private AuditAnomalyService service() {
        return service(AiTestSupport.readyClient("1|Un\n2|Deux"));
    }

    private void log(AppUser who, AuditAction action, LocalDateTime at) {
        entries.add(new AuditLogEntry(at, who.getId(), who.getUsername(), "USER", AuditModule.AUTH, action, null, null, null,
                null, null, AuditResult.SUCCESS, null, null));
    }

    private void burst(AppUser who, AuditAction action, int n, LocalDateTime from, int everyMinutes) {
        for (int i = 0; i < n; i++) {
            log(who, action, from.plusMinutes((long) i * everyMinutes));
        }
    }

    // ── règles ────────────────────────────────────────────────────────────────────────────────────────────────────

    @Test
    void repeatedAccessDeniedWithinAnHourIsAnAnomalyAndSeverityGrowsWithCount() {
        burst(a1, AuditAction.ACCESS_DENIED, 5, T0, 5);
        burst(b1, AuditAction.ACCESS_DENIED, 12, T0, 2);

        List<Anomaly> found = service().detectIn(entries);

        assertThat(found).extracting(Anomaly::actor).containsExactly("bilel.b1", "amine.a1");   // gravité haute d'abord
        assertThat(found.get(0).severity()).isEqualTo("HIGH");
        assertThat(found.get(1).severity()).isEqualTo("MEDIUM");
        assertThat(found.get(1).count()).isEqualTo(5);
        assertThat(found.get(1).sentence()).contains("amine.a1");
        assertThat(found.get(1).aiSentence()).isFalse();
    }

    @Test
    void eventsSpreadOverTimeOrBelowThresholdAreNotAnomalies() {
        burst(a1, AuditAction.ACCESS_DENIED, 4, T0, 5);                      // sous le seuil
        burst(b1, AuditAction.ACCESS_DENIED, 6, T0, 40);                     // 6 refus mais étalés sur plus de 3 h : max 2 par heure

        assertThat(service().detectIn(entries)).isEmpty();
    }

    @Test
    void repeatedFailedLoginsWithinThirtyMinutes() {
        burst(a1, AuditAction.LOGIN_FAILED, 6, T0, 3);

        List<Anomaly> found = service().detectIn(entries);

        assertThat(found).hasSize(1);
        assertThat(found.get(0).rule()).isEqualTo("LOGIN_FAILED_BURST");
        assertThat(found.get(0).count()).isEqualTo(6);
    }

    @Test
    void offHoursLoginsAreFlaggedWithGrowingSeverity() {
        log(a1, AuditAction.LOGIN, T0.withHour(23));
        log(b1, AuditAction.LOGIN, T0.withHour(2));
        log(b1, AuditAction.LOGIN, T0.minusDays(1).withHour(3));
        log(b1, AuditAction.LOGIN_2FA, T0.minusDays(2).withHour(4));
        log(tech, AuditAction.LOGIN, T0.withHour(10));                         // en journée : rien

        List<Anomaly> found = service().detectIn(entries);

        assertThat(found).extracting(Anomaly::actor).containsExactly("bilel.b1", "amine.a1");
        assertThat(found.get(0).severity()).isEqualTo("MEDIUM");
        assertThat(found.get(1).severity()).isEqualTo("LOW");
    }

    @Test
    void deleteBurstsAndPrivilegeChangesHaveTheirOwnRules() {
        burst(a1, AuditAction.DELETE, 5, T0, 4);
        burst(b1, AuditAction.ROLE_CHANGE, 3, T0, 60);

        List<Anomaly> found = service().detectIn(entries);

        assertThat(found).extracting(Anomaly::rule).containsExactlyInAnyOrder("DELETE_BURST", "PRIVILEGE_CHANGES");
    }

    // ── périmètre ─────────────────────────────────────────────────────────────────────────────────────────────────

    private void seedTwoTeams() {
        burst(a1, AuditAction.ACCESS_DENIED, 6, T0, 3);
        burst(b1, AuditAction.ACCESS_DENIED, 6, T0, 3);
    }

    @Test
    void adminSeesEveryTeamOrFiltersOnOne() {
        seedTwoTeams();

        assertThat(service().detect(admin, null, 7).anomalies()).extracting(Anomaly::actor).containsExactlyInAnyOrder("amine.a1", "bilel.b1");
        assertThat(service().detect(admin, 1L, 7).anomalies()).extracting(Anomaly::actor).containsExactly("amine.a1");
    }

    @Test
    void teamLeaderSeesOnlyHisOwnTeamMembers() {
        seedTwoTeams();

        AnomalyResponse r = service().detect(leadA, 1L, 7);

        assertThat(r.anomalies()).extracting(Anomaly::actor).containsExactly("amine.a1");
        assertThat(r.facts()).isNotEmpty();
    }

    @Test
    void teamLeaderNeverSeesAnotherTeamNorTheGlobalView() {
        seedTwoTeams();

        assertThatThrownBy(() -> service().detect(leadA, 2L, 7)).isInstanceOf(AccessDeniedException.class);
        assertThatThrownBy(() -> service().detect(leadA, null, 7)).isInstanceOf(AccessDeniedException.class);
        assertThatThrownBy(() -> service().explain(leadA, 2L, 7)).isInstanceOf(AccessDeniedException.class);
        verify(audit, never()).searchForExport(any(), anyInt());
    }

    @Test
    void plainTechnicianHasNoAccess() {
        assertThatThrownBy(() -> service().detect(tech, 1L, 7)).isInstanceOf(AccessDeniedException.class);
        assertThatThrownBy(() -> service().detect(tech, null, 7)).isInstanceOf(AccessDeniedException.class);
    }

    @Test
    void disabledFeatureComputesNothing() {
        seedTwoTeams();

        AnomalyResponse r = service(AiTestSupport.clientWith(AiCompletionResult.Reason.DISABLED)).detect(admin, null, 7);

        assertThat(r.anomalies()).isEmpty();
        assertThat(r.analysis().status()).isEqualTo("DISABLED");
        verify(audit, never()).searchForExport(any(), anyInt());
    }

    // ── phrases IA ────────────────────────────────────────────────────────────────────────────────────────────────

    @Test
    void aiSentencesReplaceTheDeterministicOnesAndRealNamesNeverLeave() {
        seedTwoTeams();
        AiCompletionClient client = AiTestSupport.readyClient("1|Technicien A a été refusé 6 fois en quelques minutes.\n2|Technicien B aussi.");

        AnomalyResponse r = service(client).explain(admin, null, 7);

        ArgumentCaptor<String> prompt = ArgumentCaptor.forClass(String.class);
        verify(client).complete(any(), any(), prompt.capture());
        assertThat(prompt.getValue()).contains("Technicien A").contains("Technicien B")
                .doesNotContain("amine.a1").doesNotContain("bilel.b1");
        assertThat(r.anomalies()).allMatch(Anomaly::aiSentence);
        assertThat(r.anomalies().get(0).sentence()).containsAnyOf("amine.a1", "bilel.b1").doesNotContain("Technicien");
        assertThat(r.analysis().status()).isEqualTo("OK");
    }

    @Test
    void whenTheAiIsDownTheDeterministicSentencesRemain() {
        seedTwoTeams();

        AnomalyResponse r = service(AiTestSupport.clientWith(AiCompletionResult.Reason.QUOTA)).explain(admin, null, 7);

        // notReadyReason = QUOTA n'est pas « désactivé » : la détection reste fournie, seule la phrase IA manque
        assertThat(r.anomalies()).hasSize(2).noneMatch(Anomaly::aiSentence);
        assertThat(r.analysis().status()).isEqualTo("UNAVAILABLE");
        assertThat(r.anomalies().get(0).sentence()).isNotBlank();
    }

    @Test
    void noAnomalyMeansNoAiCall() {
        AiCompletionClient client = AiTestSupport.readyClient("x");

        AnomalyResponse r = service(client).explain(admin, null, 7);

        assertThat(r.anomalies()).isEmpty();
        verify(client, never()).complete(any(), any(), any());
    }
}
