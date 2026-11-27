package com.orange.pfe.backend.service.aiassist;

import com.orange.pfe.backend.dto.aiassist.AiAssistDtos.ReportSummaryResponse;
import com.orange.pfe.backend.dto.aiassist.AiAssistDtos.TeamSummaryAiResponse;
import com.orange.pfe.backend.dto.report.ReportDtos.ReportDetail;
import com.orange.pfe.backend.dto.report.ReportDtos.ReportItem;
import com.orange.pfe.backend.dto.team.TeamResponses.MemberTaskStats;
import com.orange.pfe.backend.dto.team.TeamResponses.TaskSummaryResponse;
import com.orange.pfe.backend.dto.team.TeamResponses.TeamSummaryResponse;
import com.orange.pfe.backend.model.team.Team;
import com.orange.pfe.backend.model.user.AppUser;
import com.orange.pfe.backend.model.user.UserRole;
import com.orange.pfe.backend.service.report.ReportService;
import com.orange.pfe.backend.service.team.TeamService;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;
import org.springframework.security.access.AccessDeniedException;

import java.util.List;
import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyLong;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

/** (b) Résumé IA : rapport global = administrateurs ; activité d'équipe = administrateurs ou chef actif de CETTE équipe. */
class AiSummaryServiceTest {

    private static final String CONTENT = "{\"title\":\"Rapport hebdomadaire\",\"periodFrom\":\"2026-11-20\",\"periodTo\":\"2026-11-27\","
            + "\"summary\":[{\"label\":\"Incidents\",\"value\":42,\"unit\":null,\"previous\":30,\"delta\":12,\"deltaPercent\":40.0},"
            + "{\"label\":\"MTTR\",\"value\":65.5,\"unit\":\"s\"}],"
            + "\"sections\":[{\"title\":\"Par type\",\"columns\":[\"Type\"],\"rows\":[[\"CPU\"],[\"DISK\"]]}]}";

    private ReportService reports;
    private TeamService teamService;
    private AppUser admin, leadA, leadB, tech;
    private Team teamA, teamB;

    @BeforeEach
    void setUp() {
        reports = mock(ReportService.class);
        teamService = mock(TeamService.class);
        admin = AiTestSupport.user(1, "root", "Ad", "Min", UserRole.SUPER_ADMIN);
        leadA = AiTestSupport.user(10, "leada", "Lead", "Alpha", UserRole.USER);
        leadB = AiTestSupport.user(20, "leadb", "Lead", "Beta", UserRole.USER);
        tech = AiTestSupport.user(30, "simple", "Sim", "Ple", UserRole.USER);
        teamA = AiTestSupport.team(1, "Équipe A", leadA, null);
        teamB = AiTestSupport.team(2, "Équipe B", leadB, null);
        ReportItem item = mock(ReportItem.class);
        when(item.title()).thenReturn("Rapport hebdomadaire");
        when(reports.get(any(), anyLong())).thenReturn(new ReportDetail(item, null, CONTENT));
        when(teamService.summary(any(), anyLong())).thenReturn(new TeamSummaryResponse(1L, "Équipe A",
                new TaskSummaryResponse(10, Map.of("TODO", 3L, "DONE", 6L, "ON_HOLD", 1L), 2, 60.0),
                List.of(new MemberTaskStats(11L, "Sami Trabelsi", 6, 3, 3, 2), new MemberTaskStats(12L, "Karim Ben Salah", 4, 1, 3, 0))));
    }

    private AiSummaryService service(AiCompletionClient client) {
        return new AiSummaryService(AiTestSupport.guard(client, teamA, teamB), reports, teamService);
    }

    @Test
    void adminGetsFactsFromTheStoredReportAndAnAiSummary() {
        AiCompletionClient client = AiTestSupport.readyClient("Situation stable avec 42 incidents.");

        ReportSummaryResponse r = service(client).reportSummary(admin, 5L);

        assertThat(r.title()).isEqualTo("Rapport hebdomadaire");
        assertThat(r.facts()).extracting(f -> f.label() + "=" + f.value()).containsExactly("Incidents=42", "MTTR=65.5 s");
        assertThat(r.analysis().status()).isEqualTo("OK");
        assertThat(r.analysis().text()).contains("42 incidents");
        assertThat(r.analysis().generatedAt()).isNotNull();
        ArgumentCaptor<String> prompt = ArgumentCaptor.forClass(String.class);
        verify(client).complete(any(), any(), prompt.capture());
        assertThat(prompt.getValue()).contains("Incidents : 42").contains("variation : 40 %").contains("Par type").contains("2 ligne(s)");
        assertThat(prompt.getValue()).doesNotContain("CPU").doesNotContain("DISK");   // aucune ligne brute envoyée
    }

    @Test
    void globalReportSummaryIsNeverAvailableToATeamLeaderOrATechnician() {
        assertThatThrownBy(() -> service(AiTestSupport.readyClient("x")).reportSummary(leadA, 5L)).isInstanceOf(AccessDeniedException.class);
        assertThatThrownBy(() -> service(AiTestSupport.readyClient("x")).reportSummary(tech, 5L)).isInstanceOf(AccessDeniedException.class);
        verify(reports, never()).get(any(), anyLong());
    }

    @Test
    void reportSummaryDegradesWhenTheAiIsNotConfiguredOrDisabled() {
        ReportSummaryResponse notConfigured = service(AiTestSupport.clientWith(AiCompletionResult.Reason.NOT_CONFIGURED)).reportSummary(admin, 5L);
        assertThat(notConfigured.analysis().status()).isEqualTo("NOT_CONFIGURED");
        assertThat(notConfigured.facts()).hasSize(2);                         // les chiffres restent affichés

        AiCompletionClient off = AiTestSupport.clientWith(AiCompletionResult.Reason.DISABLED);
        ReportSummaryResponse disabled = service(off).reportSummary(admin, 5L);
        assertThat(disabled.analysis().status()).isEqualTo("DISABLED");
        verify(off, never()).complete(any(), any(), any());
        verify(reports, org.mockito.Mockito.times(1)).get(any(), anyLong());   // seulement lors de l'appel « non configuré »
    }

    @Test
    void corruptedReportContentDoesNotBreakTheEndpoint() {
        ReportItem item = mock(ReportItem.class);
        when(item.title()).thenReturn("Titre de secours");
        when(reports.get(any(), anyLong())).thenReturn(new ReportDetail(item, "échec", "pas du json {{"));

        ReportSummaryResponse r = service(AiTestSupport.readyClient("ok")).reportSummary(admin, 5L);

        assertThat(r.title()).isEqualTo("Titre de secours");
        assertThat(r.facts()).isEmpty();
    }

    // ── équipe ────────────────────────────────────────────────────────────────────────────────────────────────────

    @Test
    void leaderGetsHisTeamSummaryWithAnonymizedMembersInThePrompt() {
        AiCompletionClient client = AiTestSupport.readyClient("Technicien A porte la charge la plus élevée.");

        TeamSummaryAiResponse r = service(client).teamSummary(leadA, 1L);

        assertThat(r.teamName()).isEqualTo("Équipe A");
        assertThat(r.facts()).extracting(f -> f.label()).contains("Tâches", "Ouvertes", "En retard", "Avancement");
        assertThat(r.facts().stream().filter(f -> f.label().equals("Ouvertes")).findFirst().orElseThrow().value()).isEqualTo("4");
        assertThat(r.members()).hasSize(2);
        assertThat(r.analysis().text()).contains("Sami Trabelsi");               // alias remplacé à l'affichage
        ArgumentCaptor<String> prompt = ArgumentCaptor.forClass(String.class);
        verify(client).complete(any(), any(), prompt.capture());
        assertThat(prompt.getValue()).contains("Technicien A").contains("Technicien B")
                .doesNotContain("Sami").doesNotContain("Karim").doesNotContain("Trabelsi");
    }

    @Test
    void leaderCannotSummarizeAnotherTeamAndTechnicianCannotAtAll() {
        assertThatThrownBy(() -> service(AiTestSupport.readyClient("x")).teamSummary(leadA, 2L)).isInstanceOf(AccessDeniedException.class);
        assertThatThrownBy(() -> service(AiTestSupport.readyClient("x")).teamSummary(leadB, 1L)).isInstanceOf(AccessDeniedException.class);
        assertThatThrownBy(() -> service(AiTestSupport.readyClient("x")).teamSummary(tech, 1L)).isInstanceOf(AccessDeniedException.class);
        verify(teamService, never()).summary(any(), anyLong());
    }

    @Test
    void adminMaySummarizeAnyTeamButMustNameOne() {
        assertThat(service(AiTestSupport.readyClient("x")).teamSummary(admin, 2L).teamId()).isEqualTo(2L);
        assertThatThrownBy(() -> service(AiTestSupport.readyClient("x")).teamSummary(admin, null)).isInstanceOf(IllegalArgumentException.class);
        assertThatThrownBy(() -> service(AiTestSupport.readyClient("x")).teamSummary(admin, 999L))
                .isInstanceOf(com.orange.pfe.backend.exception.ResourceNotFoundException.class);
    }
}
