package com.orange.pfe.backend.service.report;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.orange.pfe.backend.dto.audit.AuditSearchCriteria;
import com.orange.pfe.backend.dto.maintenance.MaintenanceDtos.MaintenanceRequest;
import com.orange.pfe.backend.dto.maintenance.MaintenanceDtos.MaintenanceResponse;
import com.orange.pfe.backend.dto.report.ReportDtos.GenerateRequest;
import com.orange.pfe.backend.dto.report.ReportDtos.ReportDetail;
import com.orange.pfe.backend.dto.report.ReportDtos.ReportItem;
import com.orange.pfe.backend.dto.report.ReportDtos.ScheduleRequest;
import com.orange.pfe.backend.dto.report.ReportDtos.ScheduleResponse;
import com.orange.pfe.backend.dto.team.TeamRequest;
import com.orange.pfe.backend.dto.team.TeamRequests.TaskRequest;
import com.orange.pfe.backend.dto.team.TeamResponses.TaskResponse;
import com.orange.pfe.backend.dto.team.TeamResponses.TeamResponse;
import com.orange.pfe.backend.model.audit.AuditAction;
import com.orange.pfe.backend.model.audit.AuditModule;
import com.orange.pfe.backend.model.core.Incident;
import com.orange.pfe.backend.model.core.IncidentHistory;
import com.orange.pfe.backend.model.enums.IncidentSeverity;
import com.orange.pfe.backend.model.enums.IncidentSource;
import com.orange.pfe.backend.model.enums.IncidentStatus;
import com.orange.pfe.backend.model.maintenance.MaintenanceResourceType;
import com.orange.pfe.backend.model.report.Report;
import com.orange.pfe.backend.model.report.ReportFrequency;
import com.orange.pfe.backend.model.report.ReportSchedule;
import com.orange.pfe.backend.model.report.ReportStatus;
import com.orange.pfe.backend.model.report.ReportTrigger;
import com.orange.pfe.backend.model.report.ReportType;
import com.orange.pfe.backend.model.team.TaskPriority;
import com.orange.pfe.backend.model.team.TaskStatus;
import com.orange.pfe.backend.model.user.AppUser;
import com.orange.pfe.backend.model.user.UserRole;
import com.orange.pfe.backend.repository.AppUserRepository;
import com.orange.pfe.backend.repository.IncidentHistoryRepository;
import com.orange.pfe.backend.repository.IncidentRepository;
import com.orange.pfe.backend.repository.ReportRepository;
import com.orange.pfe.backend.repository.ReportScheduleRepository;
import com.orange.pfe.backend.service.audit.AuditEvent;
import com.orange.pfe.backend.service.audit.AuditTrailQueryService;
import com.orange.pfe.backend.service.audit.AuditTrailService;
import com.orange.pfe.backend.service.maintenance.MaintenanceService;
import com.orange.pfe.backend.service.org.OrgTestSupport;
import com.orange.pfe.backend.service.team.TeamService;
import com.orange.pfe.backend.service.team.TeamTaskService;
import jakarta.persistence.EntityManager;
import jakarta.persistence.PersistenceContext;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.dao.DataIntegrityViolationException;
import org.springframework.data.domain.Page;
import org.springframework.security.access.AccessDeniedException;
import org.springframework.transaction.annotation.Transactional;

import java.nio.charset.Charset;
import java.nio.charset.StandardCharsets;
import java.time.LocalDate;
import java.time.LocalDateTime;
import java.util.List;
import java.util.Map;
import java.util.UUID;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

/**
 * Module Rapports avec une vraie base : chaque statistique est recalculée à partir des données réellement présentes
 * (audit, maintenances, tâches, incidents), comparaison de périodes, exports PDF/CSV, plannings sans doublon.
 */
@SpringBootTest(properties = OrgTestSupport.JWT_TEST_PROPERTY)
@Transactional
class ReportModuleIntegrationTest {

    private static final ObjectMapper JSON = new ObjectMapper();

    @Autowired private ReportService reports;
    @Autowired private ReportScheduler scheduler;
    @Autowired private TeamService teams;
    @Autowired private TeamTaskService tasks;
    @Autowired private MaintenanceService maintenances;
    @Autowired private AuditTrailService auditTrail;
    @Autowired private AuditTrailQueryService auditQuery;
    @Autowired private AppUserRepository users;
    @Autowired private IncidentRepository incidents;
    @Autowired private IncidentHistoryRepository incidentHistory;
    @Autowired private ReportRepository reportRepository;
    @Autowired private ReportScheduleRepository scheduleRepository;
    @PersistenceContext private EntityManager em;

    private AppUser admin;
    private AppUser alice;
    private String tag;
    private LocalDateTime from;
    private LocalDateTime to;

    @BeforeEach
    void setUp() {
        tag = UUID.randomUUID().toString().substring(0, 8);
        admin = OrgTestSupport.user(users, "adm-" + tag, UserRole.ADMIN);
        alice = OrgTestSupport.user(users, "ali-" + tag, UserRole.USER);
        LocalDateTime now = LocalDateTime.now();
        from = now.minusDays(1);
        to = now.plusDays(5);
    }

    private ReportItem generate(ReportType type, boolean compare) {
        return reports.generate(admin, new GenerateRequest(type, from, to, compare));
    }

    private JsonNode content(ReportItem item) throws Exception {
        ReportDetail detail = reports.get(admin, item.id());
        assertThat(detail.content()).isNotBlank();
        return JSON.readTree(detail.content());
    }

    private static JsonNode metric(JsonNode root, String label) {
        for (JsonNode m : root.path("summary")) {
            if (label.equals(m.path("label").asText())) {
                return m;
            }
        }
        throw new AssertionError("indicateur absent : " + label);
    }

    /** Comme {@link #row} mais renvoie {@code null} si la ligne est absente (la section, elle, doit exister). */
    private static JsonNode rowOrNull(JsonNode root, String sectionTitle, String firstCell) {
        for (JsonNode s : root.path("sections")) {
            if (sectionTitle.equals(s.path("title").asText())) {
                for (JsonNode r : s.path("rows")) {
                    if (firstCell.equals(r.get(0).asText())) {
                        return r;
                    }
                }
                return null;
            }
        }
        throw new AssertionError("section absente : " + sectionTitle);
    }

    private static JsonNode row(JsonNode root, String sectionTitle, String firstCell) {
        for (JsonNode s : root.path("sections")) {
            if (sectionTitle.equals(s.path("title").asText())) {
                for (JsonNode r : s.path("rows")) {
                    if (firstCell.equals(r.get(0).asText())) {
                        return r;
                    }
                }
                throw new AssertionError("ligne absente (" + firstCell + ") dans " + sectionTitle + " : " + s.path("rows"));
            }
        }
        throw new AssertionError("section absente : " + sectionTitle);
    }

    // ── Statistiques calculées sur les données réelles ────────────────────────

    @Test
    void userActivityReportCountsTheRealAuditActivityOfEachUser() throws Exception {
        for (int i = 0; i < 3; i++) {
            auditTrail.record(AuditEvent.of(AuditModule.AUTH, AuditAction.LOGIN).actor(alice));
        }
        auditTrail.record(AuditEvent.of(AuditModule.AUTH, AuditAction.LOGIN_FAILED).actorUsername(alice.getUsername()));
        auditTrail.record(AuditEvent.of(AuditModule.TEAMS, AuditAction.CREATE).actor(alice).object("Team", 1));
        auditTrail.record(AuditEvent.of(AuditModule.TEAMS, AuditAction.UPDATE).actor(alice).object("Team", 1));
        auditTrail.record(AuditEvent.of(AuditModule.AUDIT, AuditAction.EXPORT).actor(alice));

        JsonNode root = content(generate(ReportType.USER_ACTIVITY, false));

        // [Utilisateur, Total, Connexions, Échecs, Modifications, Exports et rapports, Refus d'accès]
        JsonNode r = row(root, "Activité par utilisateur", alice.getUsername());
        assertThat(r.get(1).asLong()).isEqualTo(7);
        assertThat(r.get(2).asLong()).isEqualTo(3);
        assertThat(r.get(3).asLong()).isEqualTo(1);
        assertThat(r.get(4).asLong()).isEqualTo(2);
        assertThat(r.get(5).asLong()).isEqualTo(1);
        assertThat(r.get(6).asLong()).isZero();
        assertThat(metric(root, "Connexions réussies").get("value").asLong()).isGreaterThanOrEqualTo(3);
        assertThat(metric(root, "Utilisateurs actifs").get("value").asLong()).isGreaterThanOrEqualTo(1);
        assertThat(row(root, "Comptes par rôle (état actuel)", "USER").get(1).asLong()).isGreaterThanOrEqualTo(1);
        assertThat(root.path("charts")).isNotEmpty();
    }

    @Test
    void maintenanceReportReflectsTheMaintenancesOfThePeriod() throws Exception {
        String resource = "node-" + tag;
        LocalDateTime base = LocalDateTime.now().plusDays(1).withNano(0);
        MaintenanceResponse first = maintenances.create(admin, new MaintenanceRequest("Courte", null, null,
                MaintenanceResourceType.NODE, resource, base, base.plusHours(2), null));
        maintenances.create(admin, new MaintenanceRequest("Longue", null, null,
                MaintenanceResourceType.NODE, resource, base.plusHours(3), base.plusHours(7), null));
        maintenances.start(admin, first.id());
        maintenances.complete(admin, first.id());

        JsonNode root = content(generate(ReportType.MAINTENANCE, false));

        assertThat(metric(root, "Maintenances planifiées dans la période").get("value").asLong()).isEqualTo(2);
        assertThat(metric(root, "Maintenances exécutées dans la période").get("value").asLong()).isEqualTo(1);       // seule la première a été démarrée
        assertThat(metric(root, "Terminées").get("value").asLong()).isEqualTo(1);
        assertThat(metric(root, "Terminées dans les temps").get("value").asLong()).isEqualTo(1);
        JsonNode r = row(root, "Par ressource", "NODE — " + resource);
        assertThat(r.get(1).asLong()).isEqualTo(2);
        assertThat(r.get(2).asDouble()).isEqualTo(6.0);       // 2 h + 4 h planifiées
        assertThat(row(root, "Détail des maintenances planifiées", String.valueOf(first.id())).get(5).asText()).isEqualTo("COMPLETED");
    }

    @Test
    void teamsAndTasksReportCountsTasksAndNeutralisesHostileNamesInCsv() throws Exception {
        TeamResponse team = teams.create(admin, new TeamRequest("=SUM(1)-" + tag, "Nom hostile", admin.getId()));
        teams.addMember(admin, team.id(), alice.getId());
        TaskResponse done = tasks.create(admin, new TaskRequest("A", null, team.id(), alice.getId(), TaskPriority.HIGH, LocalDate.now().plusDays(3)));
        tasks.create(admin, new TaskRequest("B", null, team.id(), alice.getId(), TaskPriority.LOW, null));
        tasks.create(admin, new TaskRequest("C", null, team.id(), admin.getId(), TaskPriority.MEDIUM, null));
        tasks.changeStatus(admin, done.id(), TaskStatus.DONE);

        ReportItem item = generate(ReportType.TEAMS_TASKS, false);
        JsonNode root = content(item);

        assertThat(metric(root, "Tâches créées").get("value").asLong()).isEqualTo(3);
        assertThat(metric(root, "Tâches terminées dans la période").get("value").asLong()).isEqualTo(1);
        assertThat(metric(root, "Taux d'achèvement des tâches créées").get("value").asDouble()).isEqualTo(33.3);
        JsonNode teamRow = row(root, "Par équipe (tâches créées dans la période)", "=SUM(1)-" + tag);
        assertThat(teamRow.get(1).asLong()).isEqualTo(3);   // créées
        assertThat(teamRow.get(2).asLong()).isEqualTo(1);   // terminées
        assertThat(teamRow.get(3).asLong()).isEqualTo(2);   // ouvertes
        assertThat(row(root, "Par membre (tâches créées dans la période)", "Prénom-" + alice.getUsername() + " Nom").get(1).asLong())
                .isEqualTo(2);

        String csv = new String(reports.export(admin, item.id(), "csv").bytes(), StandardCharsets.UTF_8);
        assertThat(csv).contains("'=SUM(1)-" + tag).doesNotContain(";=SUM(1)").doesNotContain("\r\n=SUM(1)");
    }

    @Test
    void performanceReportComputesTheResolutionTimeFromTheRealIncidentHistory() throws Exception {
        Incident incident = new Incident();
        incident.setTitle("CPU saturé " + tag);
        incident.setDescription("Saturation CPU du nœud");
        incident.setSeverity(IncidentSeverity.HIGH);
        incident.setStatus(IncidentStatus.RESOLVED);
        incident.setSource(IncidentSource.MANUAL);
        incident = incidents.saveAndFlush(incident);
        IncidentHistory resolved = new IncidentHistory();
        resolved.setIncident(incident);
        resolved.setStatus(IncidentStatus.RESOLVED);
        resolved.setComment("Remédiation réussie");
        incidentHistory.saveAndFlush(resolved);
        // l'incident a été créé il y a 2 h : la résolution (horodatage de l'historique) arrive 120 minutes plus tard
        em.createNativeQuery("update incident set created_at = :t where id = :id")
                .setParameter("t", LocalDateTime.now().minusMinutes(120)).setParameter("id", incident.getId()).executeUpdate();
        em.clear();

        JsonNode root = content(generate(ReportType.PERFORMANCE, false));

        assertThat(metric(root, "Incidents créés").get("value").asLong()).isEqualTo(1);
        assertThat(metric(root, "Incidents résolus (parmi les créés)").get("value").asLong()).isEqualTo(1);
        assertThat(metric(root, "Taux de résolution").get("value").asDouble()).isEqualTo(100.0);
        assertThat(metric(root, "Temps de résolution moyen").get("value").asDouble()).isBetween(119.0, 121.0);
        assertThat(metric(root, "Temps de résolution moyen").get("unit").asText()).isEqualTo("min");
        JsonNode high = row(root, "Par sévérité", "HIGH");
        assertThat(high.get(1).asLong()).isEqualTo(1);
        assertThat(high.get(2).asLong()).isEqualTo(1);
    }

    @Test
    void anEmptyPeriodGivesZerosAndEmptyTablesNeverInventedFigures() throws Exception {
        LocalDateTime past = LocalDateTime.now().minusYears(5);
        ReportItem item = reports.generate(admin, new GenerateRequest(ReportType.PERFORMANCE, past, past.plusDays(1), false));
        JsonNode root = content(item);

        assertThat(metric(root, "Incidents créés").get("value").asLong()).isZero();
        assertThat(metric(root, "Taux de résolution").get("value").asDouble()).isZero();
        assertThat(metric(root, "Temps de résolution moyen").get("value").asDouble()).isZero();
        assertThat(root.path("sections").get(0).path("rows")).isEmpty();

        JsonNode tasksRoot = JSON.readTree(reports.get(admin, reports.generate(admin,
                new GenerateRequest(ReportType.TEAMS_TASKS, past, past.plusDays(1), false)).id()).content());
        assertThat(metric(tasksRoot, "Tâches créées").get("value").asLong()).isZero();
    }

    // ── Comparaison, liste, droits, exports ───────────────────────────────────

    @Test
    void comparingPeriodsAddsPreviousValuesAndVariation() throws Exception {
        tasks.create(admin, new TaskRequest("Récente", null, null, admin.getId(), null, null));

        JsonNode root = content(generate(ReportType.TEAMS_TASKS, true));

        assertThat(root.has("previousFrom")).isTrue();
        JsonNode created = metric(root, "Tâches créées");
        assertThat(created.get("value").asLong()).isEqualTo(1);
        assertThat(created.get("previous").asLong()).isZero();
        assertThat(created.get("delta").asDouble()).isEqualTo(1.0);
        assertThat(created.get("deltaPercent").isNull()).isTrue();   // division par zéro évitée
        assertThat(reportRepository.findAll()).anyMatch(Report::isComparePrevious);
    }

    @Test
    void generatedReportsAreListedFilteredAndReadableLater() {
        ReportItem a = generate(ReportType.USER_ACTIVITY, false);
        ReportItem b = generate(ReportType.PERFORMANCE, false);

        Page<ReportItem> all = reports.list(admin, null, 0, 50);
        assertThat(all.getContent()).extracting(ReportItem::id).contains(a.id(), b.id());
        assertThat(reports.list(admin, ReportType.PERFORMANCE, 0, 50).getContent())
                .extracting(ReportItem::type).containsOnly(ReportType.PERFORMANCE);

        ReportDetail detail = reports.get(admin, a.id());
        assertThat(detail.report().generatedBy()).isEqualTo(admin.getUsername());
        assertThat(detail.report().trigger()).isEqualTo(ReportTrigger.MANUAL);
        assertThat(detail.report().status()).isEqualTo(ReportStatus.COMPLETED);
        assertThat(detail.report().periodStart()).isEqualTo(from);
        assertThat(detail.report().summary()).isNotBlank();
        assertThat(detail.report().title()).contains("Activité des utilisateurs");
    }

    @Test
    void ordinaryUsersCannotListGenerateOrExportButAdminsCan() {
        ReportItem item = generate(ReportType.USER_ACTIVITY, false);

        assertThatThrownBy(() -> reports.list(alice, null, 0, 10)).isInstanceOf(AccessDeniedException.class);
        assertThatThrownBy(() -> reports.get(alice, item.id())).isInstanceOf(AccessDeniedException.class);
        assertThatThrownBy(() -> reports.generate(alice, new GenerateRequest(ReportType.PERFORMANCE, from, to, false)))
                .isInstanceOf(AccessDeniedException.class);
        assertThatThrownBy(() -> reports.export(alice, item.id(), "csv")).isInstanceOf(AccessDeniedException.class);
        assertThatThrownBy(() -> reports.statistics(alice, from, to, false)).isInstanceOf(AccessDeniedException.class);
        assertThat(reports.types(admin)).hasSize(ReportType.values().length);
    }

    @Test
    void exportsPdfAndCsvFromTheStoredContentAndTracesEachExport() {
        ReportItem item = generate(ReportType.PLATFORM_ACTIVITY, false);

        var pdf = reports.export(admin, item.id(), "PDF");
        assertThat(pdf.contentType()).isEqualTo("application/pdf");
        assertThat(new String(pdf.bytes(), Charset.forName("windows-1252"))).startsWith("%PDF-1.4").contains("Activités de la plateforme");

        var csv = reports.export(admin, item.id(), "csv");
        assertThat(csv.filename()).endsWith(".csv");
        assertThat(new String(csv.bytes(), StandardCharsets.UTF_8)).contains("Indicateurs").contains("Activités d'audit");

        Page<?> exports = auditQuery.search(admin, new AuditSearchCriteria(admin.getId(), null, AuditModule.REPORTS,
                AuditAction.EXPORT, "Report", String.valueOf(item.id()), null, null, null, null), 0, 10);
        assertThat(exports.getTotalElements()).isEqualTo(2);
        assertThat(auditQuery.search(admin, new AuditSearchCriteria(admin.getId(), null, AuditModule.REPORTS,
                AuditAction.GENERATE, "Report", String.valueOf(item.id()), null, null, null, null), 0, 10).getTotalElements())
                .isEqualTo(1);
    }

    @Test
    void liveStatisticsAreComputedWithoutStoringAReport() {
        long before = reportRepository.count();
        auditTrail.record(AuditEvent.of(AuditModule.TEAMS, AuditAction.CREATE).actor(alice).object("Team", 9));

        Map<String, Object> stats = reports.statistics(admin, from, to, true);

        assertThat(stats.get("type")).isEqualTo("PLATFORM_ACTIVITY");
        assertThat((List<?>) stats.get("summary")).isNotEmpty();
        assertThat(stats).containsKey("previousFrom");
        assertThat(reportRepository.count()).isEqualTo(before);
    }

    // ── Rapports périodiques ──────────────────────────────────────────────────

    @Test
    void aScheduleProducesExactlyOneReportPerPeriodNeverTwo() {
        scheduleRepository.deleteAll();
        ScheduleResponse schedule = reports.createSchedule(admin, new ScheduleRequest(ReportType.USER_ACTIVITY, ReportFrequency.DAILY, true));
        assertThatThrownBy(() -> reports.createSchedule(admin, new ScheduleRequest(ReportType.USER_ACTIVITY, ReportFrequency.DAILY, true)))
                .isInstanceOf(IllegalStateException.class).hasMessageContaining("existe déjà");
        assertThatThrownBy(() -> reports.createSchedule(alice, new ScheduleRequest(ReportType.PERFORMANCE, ReportFrequency.DAILY, true)))
                .isInstanceOf(AccessDeniedException.class);

        LocalDateTime now = LocalDateTime.of(2026, 11, 25, 10, 30);
        assertThat(scheduler.runOnce(now)).isEqualTo(1);
        assertThat(scheduler.runOnce(now)).isZero();                     // deuxième passage : rien de plus
        assertThat(scheduler.runOnce(now.plusHours(5))).isZero();        // même jour : même période
        assertThat(scheduler.runOnce(now.plusDays(1))).isEqualTo(1);     // jour suivant : nouvelle période

        List<Report> produced = reportRepository.findAll().stream()
                .filter(r -> r.getSchedule() != null && r.getSchedule().getId().equals(schedule.id())).toList();
        assertThat(produced).hasSize(2).allSatisfy(r -> {
            assertThat(r.getTrigger()).isEqualTo(ReportTrigger.SCHEDULED);
            assertThat(r.isComparePrevious()).isTrue();
            assertThat(r.getStatus()).isEqualTo(ReportStatus.COMPLETED);
            assertThat(r.getGeneratedByUsername()).isEqualTo(ReportService.SCHEDULER_ACTOR);
        });
        assertThat(produced).extracting(Report::getPeriodStart)
                .containsExactlyInAnyOrder(LocalDateTime.of(2026, 11, 24, 0, 0), LocalDateTime.of(2026, 11, 25, 0, 0));
    }

    @Test
    void aDisabledScheduleProducesNothingAndDeletingItKeepsItsReports() {
        scheduleRepository.deleteAll();
        ScheduleResponse schedule = reports.createSchedule(admin, new ScheduleRequest(ReportType.PERFORMANCE, ReportFrequency.WEEKLY, true));
        LocalDateTime now = LocalDateTime.of(2026, 11, 25, 10, 30);

        reports.toggleSchedule(admin, schedule.id(), false);
        assertThat(scheduler.runOnce(now)).isZero();

        reports.toggleSchedule(admin, schedule.id(), true);
        assertThat(scheduler.runOnce(now)).isEqualTo(1);
        long reportCount = reportRepository.count();

        reports.deleteSchedule(admin, schedule.id());
        assertThat(reports.schedules(admin)).isEmpty();
        assertThat(reportRepository.count()).isEqualTo(reportCount);
        assertThat(reportRepository.findAll()).allMatch(r -> r.getSchedule() == null || !r.getSchedule().getId().equals(schedule.id()));
    }

    @Test
    void theDatabaseItselfRefusesTwoReportsForTheSameSchedulePeriod() {
        scheduleRepository.deleteAll();
        ReportSchedule schedule = new ReportSchedule();
        schedule.setReportType(ReportType.MAINTENANCE);
        schedule.setFrequency(ReportFrequency.MONTHLY);
        schedule = scheduleRepository.saveAndFlush(schedule);

        LocalDateTime start = LocalDateTime.of(2026, 10, 1, 0, 0);
        reportRepository.saveAndFlush(scheduled(schedule, start));

        ReportSchedule finalSchedule = schedule;
        assertThatThrownBy(() -> reportRepository.saveAndFlush(scheduled(finalSchedule, start)))
                .isInstanceOf(DataIntegrityViolationException.class);
    }

    private static Report scheduled(ReportSchedule schedule, LocalDateTime start) {
        Report r = new Report();
        r.setReportType(schedule.getReportType());
        r.setTitle("Doublon");
        r.setPeriodStart(start);
        r.setPeriodEnd(start.plusMonths(1));
        r.setTrigger(ReportTrigger.SCHEDULED);
        r.setSchedule(schedule);
        r.setStatus(ReportStatus.COMPLETED);
        r.setGeneratedAt(LocalDateTime.now());
        return r;
    }

    // ── option C : deux compteurs distincts, dates PLANIFIÉES d'un côté, dates RÉELLES de l'autre ─────────────────────────

    private JsonNode maintenanceReportFor(LocalDateTime periodFrom, LocalDateTime periodTo) throws Exception {
        return content(reports.generate(admin, new GenerateRequest(ReportType.MAINTENANCE, periodFrom, periodTo, false)));
    }

    @Test
    void aMaintenanceRunEarlyIsCountedAsExecutedTodayAndNotAsPlannedToday() throws Exception {
        String resource = "early-" + tag;
        LocalDateTime tomorrow = LocalDateTime.now().plusDays(1).withNano(0);
        MaintenanceResponse m = maintenances.create(admin, new MaintenanceRequest("Executee en avance", null, null,
                MaintenanceResourceType.NODE, resource, tomorrow, tomorrow.plusHours(1), null));
        maintenances.start(admin, m.id());
        maintenances.complete(admin, m.id());
        LocalDateTime dayStart = LocalDate.now().atStartOfDay();

        JsonNode today = maintenanceReportFor(dayStart, dayStart.plusDays(1));
        JsonNode wide = maintenanceReportFor(dayStart, dayStart.plusDays(3));      // une période ne peut pas commencer dans le futur : on l'étend

        // aujourd'hui : rien de PLANIFIÉ pour cette ressource, mais une maintenance EXÉCUTÉE et TERMINÉE
        assertThat(rowOrNull(today, "Détail des maintenances planifiées", String.valueOf(m.id()))).isNull();
        assertThat(row(today, "Maintenances exécutées dans la période", String.valueOf(m.id())).get(5).asText()).isEqualTo("COMPLETED");
        assertThat(metric(today, "Maintenances exécutées dans la période").get("value").asLong()).isGreaterThanOrEqualTo(1);
        assertThat(metric(today, "Terminées dans la période (heure réelle)").get("value").asLong()).isGreaterThanOrEqualTo(1);
        // période élargie jusqu'à après-demain : la même maintenance est alors AUSSI comptée comme planifiée
        assertThat(row(wide, "Détail des maintenances planifiées", String.valueOf(m.id())).get(5).asText()).isEqualTo("COMPLETED");
        assertThat(row(wide, "Maintenances exécutées dans la période", String.valueOf(m.id())).get(5).asText()).isEqualTo("COMPLETED");
    }

    @Test
    void aPlannedMaintenanceThatNeverStartedIsPlannedButNotExecuted() throws Exception {
        String resource = "never-" + tag;
        LocalDateTime soon = LocalDateTime.now().plusHours(2).withNano(0);
        MaintenanceResponse m = maintenances.create(admin, new MaintenanceRequest("Jamais demarree", null, null,
                MaintenanceResourceType.NODE, resource, soon, soon.plusHours(1), null));

        JsonNode root = maintenanceReportFor(LocalDate.now().atStartOfDay(), LocalDate.now().plusDays(2).atStartOfDay());

        assertThat(row(root, "Détail des maintenances planifiées", String.valueOf(m.id())).get(5).asText()).isEqualTo("PLANNED");
        assertThat(rowOrNull(root, "Maintenances exécutées dans la période", String.valueOf(m.id()))).isNull();
    }

    @Test
    void aMaintenanceStillInProgressCountsAsExecutedWithOpenEnd() throws Exception {
        String resource = "running-" + tag;
        LocalDateTime now = LocalDateTime.now().withNano(0);
        MaintenanceResponse m = maintenances.create(admin, new MaintenanceRequest("En cours", null, null,
                MaintenanceResourceType.NODE, resource, now.plusDays(3), now.plusDays(3).plusHours(2), null));
        maintenances.start(admin, m.id());

        JsonNode root = maintenanceReportFor(LocalDate.now().atStartOfDay(), LocalDate.now().plusDays(1).atStartOfDay());

        JsonNode r = row(root, "Maintenances exécutées dans la période", String.valueOf(m.id()));
        assertThat(r.get(4).asText()).isEqualTo("en cours");
        assertThat(r.get(5).asText()).isEqualTo("IN_PROGRESS");
    }

    @Test
    void theExecutedCounterAgreesWithTheSuspensionsSectionOfTheSameReport() throws Exception {
        String resource = "susp-" + tag;
        LocalDateTime tomorrow = LocalDateTime.now().plusDays(1).withNano(0);
        MaintenanceResponse m = maintenances.create(admin, new MaintenanceRequest("Suspendue", null, null,
                MaintenanceResourceType.NODE, resource, tomorrow, tomorrow.plusHours(1), null));
        maintenances.start(admin, m.id());
        maintenances.pause(admin, m.id(), null, "test");
        LocalDateTime dayStart = LocalDate.now().atStartOfDay();

        JsonNode root = maintenanceReportFor(dayStart, dayStart.plusDays(1));

        // même horloge (réelle) : la maintenance suspendue aujourd'hui est aussi comptée comme exécutée aujourd'hui
        assertThat(row(root, "Suspensions (pauses de la période)", String.valueOf(m.id()))).isNotNull();
        assertThat(row(root, "Maintenances exécutées dans la période", String.valueOf(m.id()))).isNotNull();
        assertThat(metric(root, "Maintenances exécutées dans la période").get("value").asLong())
                .isGreaterThanOrEqualTo(metric(root, "Maintenances suspendues").get("value").asLong());
    }

    @Test
    void thePlatformActivityReportNamesItsMaintenanceCounterAsPlanned() throws Exception {
        JsonNode root = content(generate(ReportType.PLATFORM_ACTIVITY, false));

        assertThat(metric(root, "Maintenances planifiées dans la période")).isNotNull();
        assertThat(root.toString()).doesNotContain("\"Maintenances de la période\"");
    }
}
