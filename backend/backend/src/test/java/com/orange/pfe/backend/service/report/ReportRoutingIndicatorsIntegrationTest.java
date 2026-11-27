package com.orange.pfe.backend.service.report;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.orange.pfe.backend.dto.maintenance.MaintenanceDtos.MaintenanceRequest;
import com.orange.pfe.backend.dto.maintenance.MaintenanceDtos.MaintenanceResponse;
import com.orange.pfe.backend.dto.report.ReportDtos.GenerateRequest;
import com.orange.pfe.backend.dto.report.ReportDtos.ReportItem;
import com.orange.pfe.backend.dto.routing.RoutingDtos.Decision;
import com.orange.pfe.backend.dto.routing.RoutingDtos.DecisionRequest;
import com.orange.pfe.backend.dto.team.TeamRequest;
import com.orange.pfe.backend.dto.team.TeamRequests.TaskRequest;
import com.orange.pfe.backend.model.core.Incident;
import com.orange.pfe.backend.model.enums.IncidentSeverity;
import com.orange.pfe.backend.model.enums.IncidentSource;
import com.orange.pfe.backend.model.enums.IncidentStatus;
import com.orange.pfe.backend.model.maintenance.Maintenance;
import com.orange.pfe.backend.model.maintenance.MaintenanceResourceType;
import com.orange.pfe.backend.model.report.ReportType;
import com.orange.pfe.backend.model.team.ResourceScope;
import com.orange.pfe.backend.model.team.TaskPriority;
import com.orange.pfe.backend.model.team.TaskSourceEvent;
import com.orange.pfe.backend.model.team.TeamTask;
import com.orange.pfe.backend.model.user.AppUser;
import com.orange.pfe.backend.model.user.UserRole;
import com.orange.pfe.backend.repository.AppUserRepository;
import com.orange.pfe.backend.repository.IncidentRepository;
import com.orange.pfe.backend.repository.MaintenanceRepository;
import com.orange.pfe.backend.repository.TeamTaskRepository;
import com.orange.pfe.backend.service.EmailNotificationService;
import com.orange.pfe.backend.service.maintenance.MaintenanceService;
import com.orange.pfe.backend.service.org.OrgTestSupport;
import com.orange.pfe.backend.service.routing.IncidentTeamRoutingService;
import com.orange.pfe.backend.service.team.TeamService;
import com.orange.pfe.backend.service.team.TeamTaskService;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.springframework.transaction.annotation.Transactional;

import java.time.LocalDate;
import java.time.LocalDateTime;
import java.util.UUID;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * Indicateurs des rapports liés aux suspensions de maintenance et au routage incident → équipe (étape 7), calculés sur de
 * vraies données produites par les vrais services : pause, reprise, routage, décision du responsable.
 */
@SpringBootTest(properties = OrgTestSupport.JWT_TEST_PROPERTY)
@Transactional
class ReportRoutingIndicatorsIntegrationTest {

    private static final ObjectMapper JSON = new ObjectMapper();

    @Autowired private ReportService reports;
    @Autowired private TeamService teams;
    @Autowired private TeamTaskService tasks;
    @Autowired private MaintenanceService maintenances;
    @Autowired private MaintenanceRepository maintenanceRepository;
    @Autowired private IncidentTeamRoutingService routing;
    @Autowired private AppUserRepository users;
    @Autowired private IncidentRepository incidents;
    @Autowired private TeamTaskRepository taskRepository;
    @MockitoBean private EmailNotificationService email;

    private AppUser admin;
    private AppUser lead;
    private String tag;
    private LocalDateTime from;
    private LocalDateTime to;

    @BeforeEach
    void setUp() {
        tag = UUID.randomUUID().toString().substring(0, 8);
        admin = OrgTestSupport.user(users, "adm-" + tag, UserRole.ADMIN);
        lead = OrgTestSupport.user(users, "lead-" + tag, UserRole.USER);
        from = LocalDateTime.now().minusDays(1);
        to = LocalDateTime.now().plusDays(5);
    }

    // ── Données ───────────────────────────────────────────────────────────────

    private void vmTeam() {
        teams.create(admin, new TeamRequest("Infra-VM-" + tag, null, lead.getId(), ResourceScope.VM));
    }

    private MaintenanceResponse running(String node) {
        LocalDateTime start = LocalDateTime.now().plusMinutes(1).withNano(0);
        MaintenanceResponse m = maintenances.create(admin, new MaintenanceRequest("Patch " + node, null, "Motif",
                MaintenanceResourceType.NODE, node, start, start.plusHours(4), null));
        return maintenances.start(admin, m.id());
    }

    private Incident hostIncident(String node) {
        Incident i = new Incident();
        i.setTitle("CPU " + node);
        i.setDescription("d");
        i.setSeverity(IncidentSeverity.HIGH);
        i.setStatus(IncidentStatus.OPEN);
        i.setSource(IncidentSource.MANUAL);
        i.setIncidentType("NODE_HIGH_CPU");
        i.setNamespace("k8s-nodes");
        i.setApp(node);
        return incidents.saveAndFlush(i);
    }

    private JsonNode report(ReportType type, boolean compare) throws Exception {
        return report(type, compare, from, to);
    }

    private JsonNode report(ReportType type, boolean compare, LocalDateTime f, LocalDateTime t) throws Exception {
        ReportItem item = reports.generate(admin, new GenerateRequest(type, f, t, compare));
        return JSON.readTree(reports.get(admin, item.id()).content());
    }

    private static JsonNode metric(JsonNode root, String label) {
        for (JsonNode m : root.path("summary")) {
            if (label.equals(m.path("label").asText())) {
                return m;
            }
        }
        throw new AssertionError("indicateur absent : " + label);
    }

    private static JsonNode section(JsonNode root, String titleStart) {
        for (JsonNode s : root.path("sections")) {
            if (s.path("title").asText().startsWith(titleStart)) {
                return s;
            }
        }
        throw new AssertionError("section absente : " + titleStart);
    }

    // ── 1. Rapport MAINTENANCE ────────────────────────────────────────────────

    @Test
    void maintenanceReportCountsSuspensionsByCauseAndTheirAverageDuration() throws Exception {
        MaintenanceResponse byIncident = running("n1-" + tag);
        MaintenanceResponse manual = running("n2-" + tag);
        Incident incident = hostIncident("n1-" + tag);
        maintenances.pause(admin, byIncident.id(), incident.getId(), null);
        maintenances.pause(admin, manual.id(), null, "Fenêtre de changement");
        // la suspension de la maintenance déclenchée par l'incident date d'il y a 90 minutes
        Maintenance m = maintenanceRepository.findById(byIncident.id()).orElseThrow();
        m.markSuspended(LocalDateTime.now().minusMinutes(90), null, incident.getId());
        maintenanceRepository.saveAndFlush(m);
        maintenances.resume(admin, byIncident.id());

        JsonNode root = report(ReportType.MAINTENANCE, false);

        assertThat(metric(root, "Maintenances suspendues").get("value").asLong()).isEqualTo(2);
        assertThat(metric(root, "Suspendues à cause d'un incident").get("value").asLong()).isEqualTo(1);
        assertThat(metric(root, "Suspendues par décision manuelle").get("value").asLong()).isEqualTo(1);
        assertThat(metric(root, "Durée moyenne de suspension").get("value").asDouble()).isBetween(89.0, 91.0);
        assertThat(metric(root, "Durée moyenne de suspension").get("unit").asText()).isEqualTo("min");
        assertThat(metric(root, "Maintenances suspendues en ce moment (état actuel)").get("value").asLong()).isEqualTo(1);   // la manuelle
        JsonNode rows = section(root, "Suspensions").get("rows");
        assertThat(rows).hasSize(2);
        assertThat(rows.toString()).contains("Incident #" + incident.getId()).contains("Décision manuelle").contains("Fenêtre de changement");
    }

    @Test
    void anOpenSuspensionIsCountedButHasNoDurationYet() throws Exception {
        MaintenanceResponse m = running("n3-" + tag);
        maintenances.pause(admin, m.id(), null, "Attente d'une pièce");

        JsonNode root = report(ReportType.MAINTENANCE, false);

        assertThat(metric(root, "Maintenances suspendues").get("value").asLong()).isEqualTo(1);
        assertThat(metric(root, "Durée moyenne de suspension").get("value").asDouble()).isZero();
    }

    @Test
    void suspensionsOutsideThePeriodAreNotCountedAndThePreviousPeriodIsCompared() throws Exception {
        MaintenanceResponse m = running("n4-" + tag);
        maintenances.pause(admin, m.id(), null, "Motif");

        // une période entièrement passée ne contient pas la pause d'aujourd'hui
        JsonNode earlier = report(ReportType.MAINTENANCE, false, LocalDateTime.now().minusDays(10), LocalDateTime.now().minusDays(8));
        assertThat(metric(earlier, "Maintenances suspendues").get("value").asLong()).isZero();
        assertThat(section(earlier, "Suspensions").get("rows")).isEmpty();

        // période courante avec comparaison : 1 pause maintenant, 0 dans la période précédente de même durée
        JsonNode compared = report(ReportType.MAINTENANCE, true, LocalDateTime.now().minusDays(2), LocalDateTime.now().plusDays(1));
        assertThat(metric(compared, "Maintenances suspendues").get("value").asLong()).isEqualTo(1);
        assertThat(metric(compared, "Maintenances suspendues").get("previous").asLong()).isZero();
    }

    @Test
    void withoutAnySuspensionTheIndicatorsAreZeroNotMissing() throws Exception {
        JsonNode root = report(ReportType.MAINTENANCE, false);

        assertThat(metric(root, "Maintenances suspendues").get("value").asLong()).isZero();
        assertThat(metric(root, "Durée moyenne de suspension").get("value").asDouble()).isZero();
        assertThat(section(root, "Suspensions").get("rows")).isEmpty();
    }

    // ── 2. Rapport TEAMS_TASKS ────────────────────────────────────────────────

    @Test
    void teamsTasksReportSeparatesAutomaticRoutingTasksFromManualOnes() throws Exception {
        vmTeam();
        var team = teams.create(admin, new TeamRequest("Manuelle-" + tag, null, lead.getId()));
        tasks.create(admin, new TaskRequest("Tâche manuelle", null, team.id(), null, TaskPriority.LOW, LocalDate.now().plusDays(2)));
        running("n5-" + tag);
        Incident incident = hostIncident("n5-" + tag);
        routing.routeIncident(incident.getId());

        JsonNode root = report(ReportType.TEAMS_TASKS, false);

        assertThat(metric(root, "Tâches créées").get("value").asLong()).isEqualTo(2);
        assertThat(metric(root, "Tâches créées automatiquement (routage incident)").get("value").asLong()).isEqualTo(1);
        assertThat(metric(root, "Tâches créées manuellement").get("value").asLong()).isEqualTo(1);
        assertThat(metric(root, "Tâches de décision en attente (état actuel)").get("value").asLong()).isEqualTo(1);
        JsonNode decision = section(root, "Tâches automatiques du routage").get("rows").get(0);
        assertThat(decision.get(0).asText()).startsWith("Décision");
        assertThat(decision.get(1).asLong()).isEqualTo(1);   // créées
        assertThat(decision.get(3).asLong()).isEqualTo(1);   // ouvertes
    }

    @Test
    void aDecisionTaskIsNoLongerPendingOnceTheLeadHasAnswered() throws Exception {
        vmTeam();
        MaintenanceResponse m = running("n6-" + tag);
        Incident incident = hostIncident("n6-" + tag);
        routing.routeIncident(incident.getId());
        routing.decide(lead, incident.getId(), new DecisionRequest(m.id(), Decision.CONTINUE));

        JsonNode root = report(ReportType.TEAMS_TASKS, false);

        assertThat(metric(root, "Tâches de décision en attente (état actuel)").get("value").asLong()).isZero();
        JsonNode decision = section(root, "Tâches automatiques du routage").get("rows").get(0);
        assertThat(decision.get(2).asLong()).isEqualTo(1);   // terminées
        assertThat(decision.get(3).asLong()).isZero();       // ouvertes
    }

    @Test
    void withoutRoutingEveryTaskIsManualAndTheStepTableIsEmptyOfActivity() throws Exception {
        var team = teams.create(admin, new TeamRequest("Seule-" + tag, null, lead.getId()));
        tasks.create(admin, new TaskRequest("A", null, team.id(), null, TaskPriority.LOW, null));

        JsonNode root = report(ReportType.TEAMS_TASKS, false);

        assertThat(metric(root, "Tâches créées automatiquement (routage incident)").get("value").asLong()).isZero();
        assertThat(metric(root, "Tâches créées manuellement").get("value").asLong()).isEqualTo(1);
        assertThat(metric(root, "Tâches de décision en attente (état actuel)").get("value").asLong()).isZero();
        section(root, "Tâches automatiques du routage").get("rows").forEach(r -> assertThat(r.get(1).asLong()).isZero());
    }

    // ── 3. Rapport PERFORMANCE ────────────────────────────────────────────────

    @Test
    void performanceReportCountsRoutedIncidentsDecisionsAndDecisionDelay() throws Exception {
        vmTeam();
        MaintenanceResponse paused = running("n7-" + tag);
        MaintenanceResponse continued = running("n8-" + tag);
        Incident i1 = hostIncident("n7-" + tag);
        Incident i2 = hostIncident("n8-" + tag);
        routing.routeIncident(i1.getId());
        routing.routeIncident(i2.getId());
        routing.decide(lead, i1.getId(), new DecisionRequest(paused.id(), Decision.PAUSE));
        routing.decide(lead, i2.getId(), new DecisionRequest(continued.id(), Decision.CONTINUE));
        // délais de décision connus : 10 min et 20 min après la création des tâches de décision
        setDecisionDelay(i1.getId(), 10);
        setDecisionDelay(i2.getId(), 20);

        JsonNode root = report(ReportType.PERFORMANCE, false);

        assertThat(metric(root, "Incidents routés vers une équipe pendant une maintenance").get("value").asLong()).isEqualTo(2);
        assertThat(metric(root, "Décisions du responsable : suspendre la maintenance").get("value").asLong()).isEqualTo(1);
        assertThat(metric(root, "Décisions du responsable : continuer la maintenance").get("value").asLong()).isEqualTo(1);
        assertThat(metric(root, "Délai moyen de décision du responsable (depuis la notification)").get("value").asDouble()).isEqualTo(15.0);
        assertThat(metric(root, "Délai médian de décision du responsable").get("value").asDouble()).isEqualTo(15.0);
        assertThat(metric(root, "Délai médian de décision du responsable").get("unit").asText()).isEqualTo("min");
    }

    @Test
    void aRoutedIncidentStillWaitingForTheLeadIsCountedButHasNoDecisionDelay() throws Exception {
        vmTeam();
        running("n9-" + tag);
        routing.routeIncident(hostIncident("n9-" + tag).getId());

        JsonNode root = report(ReportType.PERFORMANCE, false);

        assertThat(metric(root, "Incidents routés vers une équipe pendant une maintenance").get("value").asLong()).isEqualTo(1);
        assertThat(metric(root, "Décisions du responsable : suspendre la maintenance").get("value").asLong()).isZero();
        assertThat(metric(root, "Décisions du responsable : continuer la maintenance").get("value").asLong()).isZero();
        assertThat(metric(root, "Délai moyen de décision du responsable (depuis la notification)").get("value").asDouble()).isZero();
    }

    @Test
    void withoutRoutingThePerformanceIndicatorsAreZeroAndComparisonIsAvailable() throws Exception {
        JsonNode root = report(ReportType.PERFORMANCE, true);

        assertThat(metric(root, "Incidents routés vers une équipe pendant une maintenance").get("value").asLong()).isZero();
        assertThat(metric(root, "Incidents routés vers une équipe pendant une maintenance").has("previous")).isTrue();
        assertThat(metric(root, "Délai médian de décision du responsable").get("value").asDouble()).isZero();
    }

    /** Fixe l'heure de décision à « création de la tâche + minutes » pour obtenir un délai connu. */
    private void setDecisionDelay(Long incidentId, int minutes) {
        TeamTask t = taskRepository.findBySourceIncidentIdAndSourceEvent(incidentId, TaskSourceEvent.DECISION).orElseThrow();
        t.setCompletedAt(t.getCreatedAt().plusMinutes(minutes));
        taskRepository.saveAndFlush(t);
    }
}
