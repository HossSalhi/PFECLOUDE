package com.orange.pfe.backend.service.aiassist;

import com.orange.pfe.backend.dto.aiassist.AiAssistDtos.AssignmentFeedback;
import com.orange.pfe.backend.dto.aiassist.AiAssistDtos.MaintenanceWindowRequest;
import com.orange.pfe.backend.dto.aiassist.AiAssistDtos.TaskRecommendationRequest;
import com.orange.pfe.backend.model.team.Team;
import com.orange.pfe.backend.model.team.TeamMember;
import com.orange.pfe.backend.model.user.AppUser;
import com.orange.pfe.backend.model.user.UserRole;
import com.orange.pfe.backend.repository.AuditLogSearchRepository;
import com.orange.pfe.backend.repository.MaintenanceRepository;
import com.orange.pfe.backend.repository.TeamMemberRepository;
import com.orange.pfe.backend.repository.TeamTaskSearchRepository;
import com.orange.pfe.backend.service.audit.AuditTrailService;
import com.orange.pfe.backend.service.report.ReportService;
import com.orange.pfe.backend.service.supervision.SupervisionClient;
import com.orange.pfe.backend.service.team.TeamService;
import org.junit.jupiter.api.Test;
import org.springframework.security.access.AccessDeniedException;

import java.util.List;
import java.util.function.Consumer;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyInt;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

/**
 * Étape 6 — tests d'accès CROISÉS : pour chacune des 4 fonctions, un chef de l'équipe A ne peut ni déclencher ni voir quoi
 * que ce soit pour l'équipe B (et inversement) ; un technicien simple n'a aucun accès ; un administrateur a tout. Aucun appel
 * IA ni aucune lecture de données n'a lieu pour une demande refusée.
 */
class AiCrossTeamAccessTest {

    private final AppUser admin = AiTestSupport.user(1, "root", "Ad", "Min", UserRole.SUPER_ADMIN);
    private final AppUser leadA = AiTestSupport.user(10, "leada", "Lead", "Alpha", UserRole.USER);
    private final AppUser leadB = AiTestSupport.user(20, "leadb", "Lead", "Beta", UserRole.USER);
    private final AppUser tech = AiTestSupport.user(30, "simple", "Sim", "Ple", UserRole.USER);
    private final AppUser memberA = AiTestSupport.user(11, "membera", "Mem", "A", UserRole.USER);
    private final AppUser memberB = AiTestSupport.user(21, "memberb", "Mem", "B", UserRole.USER);
    private final Team teamA = AiTestSupport.team(1, "Équipe A", leadA, null);
    private final Team teamB = AiTestSupport.team(2, "Équipe B", leadB, null);

    private final AiCompletionClient client = AiTestSupport.readyClient("texte");
    private final AiAssistGuard guard = AiTestSupport.guard(client, teamA, teamB);
    private final AuditLogSearchRepository auditRepo = mock(AuditLogSearchRepository.class);
    private final TeamMemberRepository members = mock(TeamMemberRepository.class);
    private final TeamTaskSearchRepository tasks = mock(TeamTaskSearchRepository.class);
    private final MaintenanceRepository maintenances = mock(MaintenanceRepository.class);
    private final SupervisionClient prom = mock(SupervisionClient.class);
    private final ReportService reports = mock(ReportService.class);
    private final TeamService teamService = mock(TeamService.class);
    private final AuditTrailService auditTrail = mock(AuditTrailService.class);

    /** Les 4 fonctions pour une équipe donnée (le résumé d'équipe compte avec la fonction « résumé »). */
    private List<Consumer<AppUser>> featuresFor(long teamId) {
        AiSummaryService summaries = new AiSummaryService(guard, reports, teamService);
        AuditAnomalyService anomalies = new AuditAnomalyService(guard, auditRepo, members);
        TaskAssignmentAdvisor advisor = new TaskAssignmentAdvisor(guard, members, tasks, auditTrail);
        MaintenanceWindowAdvisor windows = new MaintenanceWindowAdvisor(guard, prom, maintenances);
        return List.of(
                u -> summaries.teamSummary(u, teamId),
                u -> anomalies.detect(u, teamId, 7),
                u -> anomalies.explain(u, teamId, 7),
                u -> advisor.recommend(u, new TaskRecommendationRequest(teamId, "Tâche", null, null)),
                u -> advisor.feedback(u, new AssignmentFeedback(teamId, 11L, true)),
                u -> windows.recommend(u, new MaintenanceWindowRequest(teamId, "VM", "vm1", 60, 7)));
    }

    private void stubData() {
        when(members.findByTeamIdWithUser(1L)).thenReturn(List.of(new TeamMember(teamA, memberA, "t")));
        when(members.findByTeamIdWithUser(2L)).thenReturn(List.of(new TeamMember(teamB, memberB, "t")));
        when(auditRepo.searchForExport(any(), anyInt())).thenReturn(List.of());
    }

    @Test
    void leaderOfTeamACannotTriggerAnyFeatureForTeamBAndTheOtherWayRound() {
        stubData();
        for (Consumer<AppUser> f : featuresFor(2)) {
            assertThatThrownBy(() -> f.accept(leadA)).isInstanceOf(AccessDeniedException.class);
        }
        for (Consumer<AppUser> f : featuresFor(1)) {
            assertThatThrownBy(() -> f.accept(leadB)).isInstanceOf(AccessDeniedException.class);
        }
        verify(client, never()).complete(any(), any(), any());
        verify(auditRepo, never()).searchForExport(any(), anyInt());
        verify(tasks, never()).findAllMatching(any(), anyInt());
        verify(prom, never()).promRange(any(), org.mockito.ArgumentMatchers.anyLong(), org.mockito.ArgumentMatchers.anyLong(), org.mockito.ArgumentMatchers.anyLong());
        verify(teamService, never()).summary(any(), any());
    }

    @Test
    void aPlainTechnicianCannotUseAnyFeatureOnAnyTeam() {
        stubData();
        for (long team : new long[]{1, 2}) {
            for (Consumer<AppUser> f : featuresFor(team)) {
                assertThatThrownBy(() -> f.accept(tech)).isInstanceOf(AccessDeniedException.class);
            }
        }
        verify(client, never()).complete(any(), any(), any());
    }

    @Test
    void aMemberOfATeamWhoIsNotItsLeaderHasNoAccessEither() {
        stubData();
        for (Consumer<AppUser> f : featuresFor(1)) {
            assertThatThrownBy(() -> f.accept(memberA)).isInstanceOf(AccessDeniedException.class);
        }
    }

    @Test
    void leadersAndAdminsPassTheirOwnPerimeterOnEveryFeature() {
        stubData();
        when(teamService.summary(any(), any())).thenReturn(new com.orange.pfe.backend.dto.team.TeamResponses.TeamSummaryResponse(1L, "Équipe A",
                new com.orange.pfe.backend.dto.team.TeamResponses.TaskSummaryResponse(0, java.util.Map.of(), 0, 0.0), List.of()));
        when(maintenances.findOverlapping(any(), any(), any(), any(), any())).thenReturn(List.of());
        for (Consumer<AppUser> f : featuresFor(1)) {
            f.accept(leadA);          // aucune exception : son équipe
            f.accept(admin);          // aucune exception : toutes les équipes
        }
        for (Consumer<AppUser> f : featuresFor(2)) {
            f.accept(admin);
        }
        assertThat(true).isTrue();
    }

    @Test
    void theGlobalViewsAreReservedToAdministrators() {
        stubData();
        AuditAnomalyService anomalies = new AuditAnomalyService(guard, auditRepo, members);
        MaintenanceWindowAdvisor windows = new MaintenanceWindowAdvisor(guard, prom, maintenances);
        AiSummaryService summaries = new AiSummaryService(guard, reports, teamService);

        assertThatThrownBy(() -> anomalies.detect(leadA, null, 7)).isInstanceOf(AccessDeniedException.class);
        assertThatThrownBy(() -> windows.recommend(leadA, new MaintenanceWindowRequest(null, "VM", "vm1", 60, 7))).isInstanceOf(AccessDeniedException.class);
        assertThatThrownBy(() -> summaries.reportSummary(leadA, 5L)).isInstanceOf(AccessDeniedException.class);
        assertThat(anomalies.detect(admin, null, 7).anomalies()).isEmpty();      // l'administrateur, lui, peut
    }
}
