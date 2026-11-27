package com.orange.pfe.backend.service.aiassist;

import com.orange.pfe.backend.dto.aiassist.AiAssistDtos.AssignmentFeedback;
import com.orange.pfe.backend.dto.aiassist.AiAssistDtos.AssignmentRecommendation;
import com.orange.pfe.backend.dto.aiassist.AiAssistDtos.Candidate;
import com.orange.pfe.backend.dto.aiassist.AiAssistDtos.TaskRecommendationRequest;
import com.orange.pfe.backend.model.audit.AuditAction;
import com.orange.pfe.backend.model.team.TaskStatus;
import com.orange.pfe.backend.model.team.Team;
import com.orange.pfe.backend.model.team.TeamMember;
import com.orange.pfe.backend.model.team.TeamTask;
import com.orange.pfe.backend.model.user.AppUser;
import com.orange.pfe.backend.model.user.UserRole;
import com.orange.pfe.backend.repository.TeamMemberRepository;
import com.orange.pfe.backend.repository.TeamTaskSearchRepository;
import com.orange.pfe.backend.service.audit.AuditEvent;
import com.orange.pfe.backend.service.audit.AuditTrailService;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;
import org.springframework.security.access.AccessDeniedException;

import java.time.LocalDate;
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

/** (a) Score déterministe + justification IA anonymisée + périmètre d'équipe + trace du choix humain. */
class TaskAssignmentAdvisorTest {

    private TeamMemberRepository members;
    private TeamTaskSearchRepository tasks;
    private AuditTrailService audit;
    private AppUser admin, leadA, leadB, sami, karim, tech;
    private Team teamA, teamB;
    private final List<TeamTask> history = new ArrayList<>();

    @BeforeEach
    void setUp() {
        members = mock(TeamMemberRepository.class);
        tasks = mock(TeamTaskSearchRepository.class);
        audit = mock(AuditTrailService.class);
        admin = AiTestSupport.user(1, "root", "Ad", "Min", UserRole.ADMIN);
        leadA = AiTestSupport.user(10, "leada", "Lead", "Alpha", UserRole.USER);
        leadB = AiTestSupport.user(20, "leadb", "Lead", "Beta", UserRole.USER);
        sami = AiTestSupport.user(11, "sami", "Sami", "Trabelsi", UserRole.USER);
        karim = AiTestSupport.user(12, "karim", "Karim", "Ben Salah", UserRole.USER);
        tech = AiTestSupport.user(30, "simple", "Sim", "Ple", UserRole.USER);
        teamA = AiTestSupport.team(1, "Réseau", leadA, null);
        teamB = AiTestSupport.team(2, "Autre", leadB, null);
        when(members.findByTeamIdWithUser(1L)).thenReturn(List.of(new TeamMember(teamA, sami, "t"), new TeamMember(teamA, karim, "t")));
        when(tasks.findAllMatching(any(), anyInt())).thenAnswer(i -> history);
    }

    private TeamTask task(AppUser who, String title, TaskStatus status, LocalDate due) {
        TeamTask t = new TeamTask();
        t.setTitle(title);
        t.setAssignee(who);
        t.setStatus(status);
        t.setDueDate(due);
        if (status == TaskStatus.DONE) {
            t.setCompletedAt(LocalDateTime.now().minusDays(10));
        }
        history.add(t);
        return t;
    }

    private TaskAssignmentAdvisor advisor(AiCompletionClient client) {
        return new TaskAssignmentAdvisor(AiTestSupport.guard(client, teamA, teamB), members, tasks, audit);
    }

    private TaskRecommendationRequest req(long teamId) {
        return new TaskRecommendationRequest(teamId, "Remplacer le switch du rack 4", "câblage réseau", "HIGH");
    }

    // ── scoring ───────────────────────────────────────────────────────────────────────────────────────────────────

    @Test
    void theLeastLoadedMemberWinsAndOverdueTasksWeighMore() {
        task(sami, "Tâche 1", TaskStatus.IN_PROGRESS, null);
        task(sami, "Tâche 2", TaskStatus.TODO, LocalDate.now().minusDays(3));    // en retard
        task(karim, "Tâche 3", TaskStatus.TODO, null);

        List<Candidate> ranked = advisor(AiTestSupport.readyClient("x")).score(List.of(sami, karim), history, "autre sujet",
                LocalDate.now(), LocalDateTime.now());

        assertThat(ranked.get(0).userId()).isEqualTo(karim.getId());
        assertThat(ranked.get(0).score()).isEqualTo(88);                          // 100 - 12 × 1
        assertThat(ranked.get(1).score()).isEqualTo(100 - 24 - 18);               // 2 ouvertes dont 1 en retard
        assertThat(ranked.get(1).overdueTasks()).isEqualTo(1);
        assertThat(ranked.get(1).reasons()).anyMatch(r -> r.contains("en retard"));
    }

    @Test
    void similarCompletedTasksRaiseTheScoreButClosedTasksDoNotCountAsLoad() {
        task(sami, "Câblage réseau salle 2", TaskStatus.DONE, null);
        task(sami, "Câblage réseau salle 3", TaskStatus.DONE, null);
        task(sami, "Annulée", TaskStatus.CANCELLED, null);
        task(karim, "Inventaire papier", TaskStatus.DONE, null);

        List<Candidate> ranked = advisor(AiTestSupport.readyClient("x")).score(List.of(sami, karim), history,
                "Remplacer le switch, câblage réseau", LocalDate.now(), LocalDateTime.now());

        assertThat(ranked.get(0).userId()).isEqualTo(sami.getId());
        assertThat(ranked.get(0).similarDone()).isEqualTo(2);
        assertThat(ranked.get(0).openTasks()).isZero();
        assertThat(ranked.get(0).score()).isEqualTo(100);                          // plafonné à 100
        assertThat(ranked.get(1).score()).isEqualTo(100);
    }

    @Test
    void scoreIsClampedAtZeroAndTiesAreBrokenDeterministically() {
        for (int i = 0; i < 12; i++) {
            task(sami, "T" + i, TaskStatus.TODO, LocalDate.now().minusDays(1));
        }

        List<Candidate> ranked = advisor(AiTestSupport.readyClient("x")).score(List.of(sami, karim), history, "x", LocalDate.now(), LocalDateTime.now());

        assertThat(ranked.get(1).score()).isZero();
        assertThat(ranked.get(0).displayName()).isEqualTo("Karim Ben Salah");
    }

    // ── recommandation, IA et périmètre ──────────────────────────────────────────────────────────────────────────

    @Test
    void recommendationReturnsTheRankingAndAnAnonymizedJustification() {
        task(sami, "Tâche 1", TaskStatus.TODO, null);
        AiCompletionClient client = AiTestSupport.readyClient("Technicien A est le moins chargé (aucune tâche ouverte).");

        AssignmentRecommendation r = advisor(client).recommend(admin, req(1));

        assertThat(r.recommendedUserId()).isEqualTo(karim.getId());
        assertThat(r.candidates()).hasSize(2);
        assertThat(r.facts()).isNotEmpty();
        ArgumentCaptor<String> prompt = ArgumentCaptor.forClass(String.class);
        verify(client).complete(any(), any(), prompt.capture());
        assertThat(prompt.getValue()).contains("Technicien A").doesNotContain("Karim").doesNotContain("Sami")
                .doesNotContain("Trabelsi").doesNotContain("karim");
        assertThat(r.analysis().text()).contains("Karim Ben Salah");              // alias remplacé à l'affichage
    }

    @Test
    void aiDownStillReturnsTheDeterministicRanking() {
        AssignmentRecommendation r = advisor(AiTestSupport.clientWith(AiCompletionResult.Reason.TIMEOUT)).recommend(leadA, req(1));

        assertThat(r.candidates()).hasSize(2);
        assertThat(r.analysis().status()).isEqualTo("UNAVAILABLE");
        assertThat(r.recommendedUserId()).isNotNull();
    }

    @Test
    void leaderOfTeamACanRecommendForHisTeamOnly() {
        assertThat(advisor(AiTestSupport.readyClient("x")).recommend(leadA, req(1)).candidates()).hasSize(2);

        assertThatThrownBy(() -> advisor(AiTestSupport.readyClient("x")).recommend(leadA, req(2))).isInstanceOf(AccessDeniedException.class);
        assertThatThrownBy(() -> advisor(AiTestSupport.readyClient("x")).recommend(leadB, req(1))).isInstanceOf(AccessDeniedException.class);
        assertThatThrownBy(() -> advisor(AiTestSupport.readyClient("x")).recommend(tech, req(1))).isInstanceOf(AccessDeniedException.class);
    }

    @Test
    void invalidRequestsAreRejectedAndDisabledMakesNoCall() {
        assertThatThrownBy(() -> advisor(AiTestSupport.readyClient("x")).recommend(admin, new TaskRecommendationRequest(null, "t", null, null)))
                .isInstanceOf(IllegalArgumentException.class);
        assertThatThrownBy(() -> advisor(AiTestSupport.readyClient("x")).recommend(admin, new TaskRecommendationRequest(1L, "  ", null, null)))
                .isInstanceOf(IllegalArgumentException.class);

        AiCompletionClient off = AiTestSupport.clientWith(AiCompletionResult.Reason.DISABLED);
        AssignmentRecommendation r = advisor(off).recommend(admin, req(1));

        assertThat(r.analysis().status()).isEqualTo("DISABLED");
        assertThat(r.candidates()).isEmpty();
        verify(off, never()).complete(any(), any(), any());
    }

    @Test
    void disabledMembersAreNeverSuggestedAndEmptyTeamsAreExplained() {
        karim.setEnabled(false);
        sami.setEnabled(false);

        AssignmentRecommendation r = advisor(AiTestSupport.readyClient("x")).recommend(admin, req(1));

        assertThat(r.candidates()).isEmpty();
        assertThat(r.recommendedUserId()).isNull();
    }

    // ── trace du choix humain ─────────────────────────────────────────────────────────────────────────────────────

    @Test
    void acceptedAndIgnoredSuggestionsAreTracedForTheAcceptanceRate() {
        TaskAssignmentAdvisor a = advisor(AiTestSupport.readyClient("x"));

        a.feedback(leadA, new AssignmentFeedback(1L, 12L, true));
        a.feedback(admin, new AssignmentFeedback(1L, 12L, false));

        ArgumentCaptor<AuditEvent> ev = ArgumentCaptor.forClass(AuditEvent.class);
        verify(audit, org.mockito.Mockito.times(2)).record(ev.capture());
        assertThat(ev.getAllValues()).allMatch(e -> e.action() == AuditAction.ASSIGN);
        assertThat(ev.getAllValues().get(0).summary()).contains("ACCEPTÉE");
        assertThat(ev.getAllValues().get(1).summary()).contains("IGNORÉE");
    }

    @Test
    void aLeaderCannotRecordFeedbackForAnotherTeam() {
        assertThatThrownBy(() -> advisor(AiTestSupport.readyClient("x")).feedback(leadA, new AssignmentFeedback(2L, 12L, true)))
                .isInstanceOf(AccessDeniedException.class);
        verify(audit, never()).record(any());
    }
}
