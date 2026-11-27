package com.orange.pfe.backend.service.aiassist;

import com.orange.pfe.backend.config.AiAssistProperties;
import com.orange.pfe.backend.dto.aiassist.AiAssistDtos.Capabilities;
import com.orange.pfe.backend.model.team.Team;
import com.orange.pfe.backend.model.user.AppUser;
import com.orange.pfe.backend.model.user.UserRole;
import com.orange.pfe.backend.repository.TeamRepository;
import com.orange.pfe.backend.service.audit.AuditTrailService;
import com.orange.pfe.backend.service.org.OrgAccessService;
import org.junit.jupiter.api.Test;
import org.springframework.context.event.EventListener;
import org.springframework.scheduling.annotation.Scheduled;

import java.lang.reflect.Method;
import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.anyCollection;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

/** Capacités affichées à l'utilisateur, alias anonymes, et garantie qu'aucun appel IA n'est déclenché automatiquement. */
class AiCapabilitiesAndSafetyTest {

    private AiAssistProperties props(boolean global, boolean all, String key) {
        AiAssistProperties p = new AiAssistProperties();
        p.setEnabled(global);
        p.getReportsSummary().setEnabled(all);
        p.getAuditAnomalies().setEnabled(all);
        p.getTaskAssignment().setEnabled(all);
        p.getMaintenanceWindow().setEnabled(all);
        p.getTelemetryAnalysis().setEnabled(all);
        p.setApiKey(key);
        return p;
    }

    private Capabilities capabilities(AppUser who, AiAssistProperties p, List<Team> led) {
        TeamRepository teams = mock(TeamRepository.class);
        when(teams.findActiveByLeadIds(anyCollection())).thenReturn(led);
        return new AiCapabilitiesService(new OrgAccessService(mock(AuditTrailService.class)), teams, new AiCompletionClient(p)).capabilities(who);
    }

    @Test
    void everythingIsOffByDefaultForEveryone() {
        AppUser admin = AiTestSupport.user(1, "root", "A", "B", UserRole.ADMIN);

        Capabilities c = capabilities(admin, new AiAssistProperties(), List.of());

        assertThat(c.admin()).isTrue();
        for (var f : List.of(c.reportsSummary(), c.teamSummary(), c.auditAnomalies(), c.taskAssignment(), c.maintenanceWindow(), c.telemetryAnalysis())) {
            assertThat(f.enabled()).isFalse();
            assertThat(f.ready()).isFalse();
        }
    }

    @Test
    void adminSeesAllFourFunctionsWhenEnabledAndConfigured() {
        Capabilities c = capabilities(AiTestSupport.user(1, "root", "A", "B", UserRole.SUPER_ADMIN), props(true, true, "k"), List.of());

        for (var f : List.of(c.reportsSummary(), c.teamSummary(), c.auditAnomalies(), c.taskAssignment(), c.maintenanceWindow())) {
            assertThat(f.enabled()).isTrue();
            assertThat(f.ready()).isTrue();
        }
    }

    @Test
    void teamLeaderSeesAllButTheGlobalReportSummaryAndKnowsHisTeams() {
        AppUser lead = AiTestSupport.user(10, "leada", "L", "A", UserRole.USER);
        Team t = AiTestSupport.team(7, "Réseau", lead, null);

        Capabilities c = capabilities(lead, props(true, true, "k"), List.of(t));

        assertThat(c.admin()).isFalse();
        assertThat(c.allowed()).isTrue();
        assertThat(c.ledTeams()).extracting(l -> l.id() + ":" + l.name()).containsExactly("7:Réseau");
        assertThat(c.teamSummary().ready()).isTrue();
        assertThat(c.reportsSummary().enabled()).isFalse();
        assertThat(c.auditAnomalies().ready()).isTrue();
        assertThat(c.taskAssignment().ready()).isTrue();
        assertThat(c.maintenanceWindow().ready()).isTrue();
    }

    @Test
    void aPlainTechnicianSeesNothing() {
        Capabilities c = capabilities(AiTestSupport.user(30, "simple", "S", "P", UserRole.USER), props(true, true, "k"), List.of());

        assertThat(c.allowed()).isFalse();
        assertThat(c.auditAnomalies().enabled()).isFalse();
        assertThat(c.taskAssignment().enabled()).isFalse();
        assertThat(c.maintenanceWindow().enabled()).isFalse();
        assertThat(c.teamSummary().enabled()).isFalse();
    }

    @Test
    void enabledButWithoutKeyIsShownAsNotConfigured() {
        Capabilities c = capabilities(AiTestSupport.user(1, "root", "A", "B", UserRole.ADMIN), props(true, true, ""), List.of());

        assertThat(c.auditAnomalies().enabled()).isTrue();
        assertThat(c.auditAnomalies().ready()).isFalse();
        assertThat(c.auditAnomalies().message()).contains("clé API");
    }

    @Test
    void featureFlagsAreIndependent() {
        AiAssistProperties p = props(true, false, "k");
        p.getAuditAnomalies().setEnabled(true);

        Capabilities c = capabilities(AiTestSupport.user(1, "root", "A", "B", UserRole.ADMIN), p, List.of());

        assertThat(c.auditAnomalies().ready()).isTrue();
        assertThat(c.taskAssignment().enabled()).isFalse();
        assertThat(c.maintenanceWindow().enabled()).isFalse();
        assertThat(c.reportsSummary().enabled()).isFalse();
    }

    @Test
    void aliasesAreStableAndRestoredLongestFirst() {
        AiNames names = new AiNames();
        String first = names.alias("Sami");
        for (int i = 0; i < 26; i++) {
            names.alias("Personne " + i);
        }

        assertThat(first).isEqualTo("Technicien A");
        assertThat(names.alias("Sami")).isEqualTo("Technicien A");
        assertThat(names.alias("Personne 25")).isEqualTo("Technicien AA");      // 27e personne
        assertThat(names.restore("Technicien AA et Technicien A")).isEqualTo("Personne 25 et Sami");
        assertThat(names.restore(null)).isNull();
    }

    @Test
    void noAiCallCanBeTriggeredAutomatically() {
        // aucune méthode des services IA n'est planifiée ni branchée sur un événement ou un démarrage
        List<Class<?>> classes = List.of(com.orange.pfe.backend.service.aiassist.telemetry.TelemetryAnalysisService.class,
                com.orange.pfe.backend.service.aiassist.telemetry.TelemetryDetector.class, AiCompletionClient.class, AiSummaryService.class, AuditAnomalyService.class,
                TaskAssignmentAdvisor.class, MaintenanceWindowAdvisor.class, AiCapabilitiesService.class, AiAssistGuard.class);
        for (Class<?> c : classes) {
            for (Method m : c.getDeclaredMethods()) {
                assertThat(m.isAnnotationPresent(Scheduled.class)).as(c.getSimpleName() + "." + m.getName() + " @Scheduled").isFalse();
                assertThat(m.isAnnotationPresent(EventListener.class)).as(c.getSimpleName() + "." + m.getName() + " @EventListener").isFalse();
                assertThat(m.isAnnotationPresent(jakarta.annotation.PostConstruct.class)).as(c.getSimpleName() + "." + m.getName() + " @PostConstruct").isFalse();
            }
        }
    }
}
