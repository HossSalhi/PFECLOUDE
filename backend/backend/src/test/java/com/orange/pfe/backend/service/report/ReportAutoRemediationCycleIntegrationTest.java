package com.orange.pfe.backend.service.report;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.orange.pfe.backend.dto.report.ReportDtos.GenerateRequest;
import com.orange.pfe.backend.dto.report.ReportDtos.ReportItem;
import com.orange.pfe.backend.model.action.ActionStatus;
import com.orange.pfe.backend.model.action.ActionType;
import com.orange.pfe.backend.model.action.AutomationMode;
import com.orange.pfe.backend.model.action.IncidentAction;
import com.orange.pfe.backend.model.core.Incident;
import com.orange.pfe.backend.model.core.IncidentHistory;
import com.orange.pfe.backend.model.core.IncidentTerminalEvidence;
import com.orange.pfe.backend.model.core.IncidentVerification;
import com.orange.pfe.backend.model.enums.IncidentSeverity;
import com.orange.pfe.backend.model.enums.IncidentSource;
import com.orange.pfe.backend.model.enums.IncidentStatus;
import com.orange.pfe.backend.model.enums.TerminalEvidencePhase;
import com.orange.pfe.backend.model.human.HumanTask;
import com.orange.pfe.backend.model.report.ReportType;
import com.orange.pfe.backend.model.team.TaskSourceEvent;
import com.orange.pfe.backend.model.team.TaskStatus;
import com.orange.pfe.backend.model.team.TeamTask;
import com.orange.pfe.backend.model.user.AppUser;
import com.orange.pfe.backend.model.user.UserRole;
import com.orange.pfe.backend.model.workflow.WorkflowInstance;
import com.orange.pfe.backend.model.workflow.WorkflowStatus;
import com.orange.pfe.backend.repository.AppUserRepository;
import com.orange.pfe.backend.service.org.OrgTestSupport;
import com.orange.pfe.backend.service.report.ReportService;
import jakarta.persistence.EntityManager;
import jakarta.persistence.PersistenceContext;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.transaction.annotation.Transactional;

import java.time.LocalDateTime;
import java.util.UUID;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * Rapport « cycle d'auto-remédiation » sur de vraies données : détection → action → vérification → résolution, avec des
 * horodatages connus, les cycles bloqués à chaque étape et la distinction résolution automatique / intervention humaine.
 * Tout est recalculé à la demande : aucune écriture dans le journal d'audit.
 */
@SpringBootTest(properties = OrgTestSupport.JWT_TEST_PROPERTY)
@Transactional
class ReportAutoRemediationCycleIntegrationTest {

    private static final ObjectMapper JSON = new ObjectMapper();

    @Autowired private ReportService reports;
    @Autowired private AppUserRepository users;
    @PersistenceContext private EntityManager em;

    private AppUser admin;
    private LocalDateTime base;
    private LocalDateTime from;
    private LocalDateTime to;

    @BeforeEach
    void setUp() {
        admin = OrgTestSupport.user(users, "adm-" + UUID.randomUUID().toString().substring(0, 8), UserRole.ADMIN);
        base = LocalDateTime.now().minusHours(3).withNano(0);
        from = LocalDateTime.now().minusDays(1);
        to = LocalDateTime.now().plusDays(5);
    }

    // ── Données ───────────────────────────────────────────────────────────────

    private void setCreatedAt(String table, Long id, LocalDateTime at) {
        em.flush();
        em.createNativeQuery("update " + table + " set created_at = :t where id = :id")
                .setParameter("t", at).setParameter("id", id).executeUpdate();
    }

    private Incident incident(String title, LocalDateTime at) {
        Incident i = new Incident();
        i.setTitle(title);
        i.setDescription("d");
        i.setSeverity(IncidentSeverity.HIGH);
        i.setStatus(IncidentStatus.OPEN);
        i.setSource(IncidentSource.MANUAL);
        i.setIncidentType("POD_CRASHLOOP");
        em.persist(i);
        em.flush();
        setCreatedAt("incident", i.getId(), at);
        return i;
    }

    private IncidentAction action(Incident i, ActionType type, ActionStatus status, AutomationMode mode, int attempts,
                                  LocalDateTime createdAt, LocalDateTime executedAt) {
        IncidentAction a = new IncidentAction();
        a.setIncident(i);
        a.setType(type);
        a.setStatus(status);
        a.setAutomationMode(mode);
        a.setAttemptCount(attempts);
        a.setExecutedAt(executedAt);
        a.setDescription("d");
        em.persist(a);
        em.flush();
        setCreatedAt("incident_action", a.getId(), createdAt);
        return a;
    }

    private void verification(Incident i, String result, LocalDateTime at) {
        IncidentVerification v = new IncidentVerification();
        v.setIncidentId(i.getId());
        v.setResult(result);
        v.setCriteria("pods prêts");
        v.setVerifiedAt(at);
        v.setAvailable(true);
        em.persist(v);
    }

    private void history(Incident i, IncidentStatus status, LocalDateTime at) {
        IncidentHistory h = new IncidentHistory();
        h.setIncident(i);
        h.setStatus(status);
        h.setComment("test");
        em.persist(h);
        em.flush();
        setCreatedAt("incident_history", h.getId(), at);
    }

    private void evidence(Incident i, TerminalEvidencePhase phase, boolean available) {
        IncidentTerminalEvidence e = new IncidentTerminalEvidence();
        e.setIncidentId(i.getId());
        e.setPhase(phase);
        e.setVm("k8s-worker1");
        e.setCommand("uptime");
        e.setAvailable(available);
        e.setCapturedAt(base);
        em.persist(e);
    }

    private JsonNode report(boolean compare) throws Exception {
        return report(compare, from, to);
    }

    private JsonNode report(boolean compare, LocalDateTime f, LocalDateTime t) throws Exception {
        em.flush();
        em.clear();   // relire les données telles qu'écrites en base (dates fixées par SQL)
        ReportItem item = reports.generate(admin, new GenerateRequest(ReportType.AUTO_REMEDIATION_CYCLE, f, t, compare));
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

    private static JsonNode section(JsonNode root, String title) {
        for (JsonNode s : root.path("sections")) {
            if (title.equals(s.path("title").asText())) {
                return s;
            }
        }
        throw new AssertionError("section absente : " + title);
    }

    private static JsonNode rowOf(JsonNode root, String section, Long firstCell) {
        for (JsonNode r : section(root, section).get("rows")) {
            if (firstCell.equals(r.get(0).asLong())) {
                return r;
            }
        }
        throw new AssertionError("ligne absente : " + firstCell);
    }

    /** Cycle complet automatique : 10 min, 8 min et 15 min entre les phases. */
    private Incident completeAutomaticCycle(String title) {
        Incident i = incident(title, base);
        action(i, ActionType.RESTART_POD, ActionStatus.COMPLETED, AutomationMode.AUTO, 1, base.plusMinutes(10), base.plusMinutes(12));
        verification(i, "PASSED", base.plusMinutes(20));
        history(i, IncidentStatus.RESOLVED, base.plusMinutes(35));
        return i;
    }

    // ── Cycle complet et durées ───────────────────────────────────────────────

    @Test
    void aCompleteAutomaticCycleGivesTheDurationOfEachPhaseAndCountsAsAutomatic() throws Exception {
        Incident i = completeAutomaticCycle("Crash auto");

        JsonNode root = report(false);

        assertThat(metric(root, "Incidents de la période").get("value").asLong()).isEqualTo(1);
        assertThat(metric(root, "Cycles complets (action, vérification, résolution)").get("value").asLong()).isEqualTo(1);
        assertThat(metric(root, "Cycles incomplets").get("value").asLong()).isZero();
        assertThat(metric(root, "Résolutions automatiques sans intervention humaine").get("value").asLong()).isEqualTo(1);
        assertThat(metric(root, "Taux de résolution automatique").get("value").asDouble()).isEqualTo(100.0);
        assertThat(metric(root, "Durée moyenne détection → action").get("value").asDouble()).isEqualTo(10.0);
        assertThat(metric(root, "Durée moyenne action → vérification").get("value").asDouble()).isEqualTo(8.0);   // 20 − 12
        assertThat(metric(root, "Durée moyenne vérification → résolution").get("value").asDouble()).isEqualTo(15.0);
        assertThat(metric(root, "Durée moyenne détection → action").get("unit").asText()).isEqualTo("min");
        JsonNode row = rowOf(root, "Cycle par incident", i.getId());
        assertThat(row.get(5).asText()).isEqualTo("RESTART_POD · AUTO");
        assertThat(row.get(7).asText()).isEqualTo("PASSED");
        assertThat(row.get(10).asText()).isEqualTo("Cycle complet (automatique)");
    }

    @Test
    void averagesAreComputedOverTheIncidentsThatHaveEachPhase() throws Exception {
        completeAutomaticCycle("A");                                               // 10 / 8 / 15
        Incident b = incident("B", base);
        action(b, ActionType.RESTART_POD, ActionStatus.COMPLETED, AutomationMode.AUTO, 1, base.plusMinutes(30), base.plusMinutes(30));
        verification(b, "PASSED", base.plusMinutes(34));
        history(b, IncidentStatus.RESOLVED, base.plusMinutes(37));                  // 30 / 4 / 3

        JsonNode root = report(false);

        assertThat(metric(root, "Durée moyenne détection → action").get("value").asDouble()).isEqualTo(20.0);
        assertThat(metric(root, "Durée moyenne action → vérification").get("value").asDouble()).isEqualTo(6.0);
        assertThat(metric(root, "Durée moyenne vérification → résolution").get("value").asDouble()).isEqualTo(9.0);
    }

    // ── Cycles incomplets : où ils s'arrêtent ─────────────────────────────────

    @Test
    void incompleteCyclesAreClassifiedByTheStageWhereTheyStopped() throws Exception {
        Incident noAction = incident("Sans action", base);
        Incident noVerif = incident("Action sans vérification", base);
        action(noVerif, ActionType.SCALE_DEPLOYMENT, ActionStatus.COMPLETED, AutomationMode.AUTO, 1, base.plusMinutes(5), null);
        Incident noResolution = incident("Vérifié sans résolution", base);
        action(noResolution, ActionType.RESTART_POD, ActionStatus.COMPLETED, AutomationMode.AUTO, 1, base.plusMinutes(5), null);
        verification(noResolution, "FAILED", base.plusMinutes(9));
        Incident early = incident("Résolu avant la vérification", base);
        action(early, ActionType.RESTART_POD, ActionStatus.COMPLETED, AutomationMode.AUTO, 1, base.plusMinutes(5), null);
        history(early, IncidentStatus.RESOLVED, base.plusMinutes(8));
        verification(early, "PASSED", base.plusMinutes(12));
        completeAutomaticCycle("Complet");

        JsonNode root = report(false);

        assertThat(metric(root, "Incidents de la période").get("value").asLong()).isEqualTo(5);
        assertThat(metric(root, "Cycles complets (action, vérification, résolution)").get("value").asLong()).isEqualTo(1);
        assertThat(metric(root, "Cycles incomplets").get("value").asLong()).isEqualTo(4);
        assertThat(rowOf(root, "Cycle par incident", noAction.getId()).get(10).asText()).isEqualTo("Sans action");
        assertThat(rowOf(root, "Cycle par incident", noVerif.getId()).get(10).asText()).isEqualTo("Action sans vérification");
        assertThat(rowOf(root, "Cycle par incident", noResolution.getId()).get(10).asText()).isEqualTo("Vérifié sans résolution");
        // résolu par des actions automatiques, mais avant la vérification : cycle incomplet, résolution tout de même automatique
        assertThat(rowOf(root, "Cycle par incident", early.getId()).get(10).asText()).isEqualTo("Résolu avant la vérification (automatique)");
        JsonNode stages = section(root, "Où s'arrêtent les cycles").get("rows");
        assertThat(stages.toString()).contains("[\"Sans action\",1]").contains("[\"Action sans vérification\",1]")
                .contains("[\"Vérifié sans résolution\",1]").contains("[\"Résolu avant la vérification\",1]").contains("[\"Cycle complet\",1]");
    }

    @Test
    void notificationsAndEscalationsAreNotRemediationActions() throws Exception {
        Incident i = incident("Seulement notifié", base);
        action(i, ActionType.NOTIFICATION, ActionStatus.COMPLETED, AutomationMode.AUTO, 1, base.plusMinutes(1), null);
        action(i, ActionType.ESCALATION, ActionStatus.COMPLETED, AutomationMode.AUTO, 1, base.plusMinutes(2), null);

        JsonNode root = report(false);

        assertThat(rowOf(root, "Cycle par incident", i.getId()).get(10).asText()).isEqualTo("Sans action");
        assertThat(section(root, "Par type d'action").get("rows")).isEmpty();
    }

    // ── Résolution automatique ou avec intervention humaine ───────────────────

    @Test
    void aManualActionOrAHumanTaskOrATeamDecisionMeansHumanIntervention() throws Exception {
        Incident manual = incident("Manuelle", base);
        action(manual, ActionType.RESTART_POD, ActionStatus.COMPLETED, AutomationMode.MANUAL, 1, base.plusMinutes(5), null);
        verification(manual, "PASSED", base.plusMinutes(8));
        history(manual, IncidentStatus.RESOLVED, base.plusMinutes(9));

        Incident human = incident("Validation humaine", base);
        action(human, ActionType.RESTART_POD, ActionStatus.COMPLETED, AutomationMode.AUTO, 1, base.plusMinutes(5), null);
        verification(human, "PASSED", base.plusMinutes(8));
        history(human, IncidentStatus.RESOLVED, base.plusMinutes(9));
        WorkflowInstance wf = new WorkflowInstance();
        wf.setIncident(human);
        wf.setStatus(WorkflowStatus.COMPLETED);
        wf.setWorkflowName("wf");
        em.persist(wf);
        HumanTask task = new HumanTask();
        task.setWorkflowInstance(wf);
        task.setTaskName("Valider");
        em.persist(task);

        Incident approval = incident("Approbation demandée", base);
        action(approval, ActionType.RESTART_POD, ActionStatus.COMPLETED, AutomationMode.AUTO, 1, base.plusMinutes(5), null);
        verification(approval, "PASSED", base.plusMinutes(8));
        history(approval, IncidentStatus.WAITING_FOR_APPROVAL, base.plusMinutes(2));
        history(approval, IncidentStatus.RESOLVED, base.plusMinutes(9));

        Incident routed = incident("Décision d'équipe", base);
        action(routed, ActionType.RESTART_POD, ActionStatus.COMPLETED, AutomationMode.AUTO, 1, base.plusMinutes(5), null);
        verification(routed, "PASSED", base.plusMinutes(8));
        history(routed, IncidentStatus.RESOLVED, base.plusMinutes(9));
        TeamTask decision = new TeamTask();
        decision.setTitle("Décision");
        decision.setStatus(TaskStatus.DONE);
        decision.setCreatedByUsername("system");
        decision.setCompletedAt(base.plusMinutes(3));
        decision.markFromIncident(routed.getId(), TaskSourceEvent.DECISION);
        em.persist(decision);

        completeAutomaticCycle("Vraiment automatique");

        JsonNode root = report(false);

        assertThat(metric(root, "Résolutions automatiques sans intervention humaine").get("value").asLong()).isEqualTo(1);
        assertThat(metric(root, "Taux de résolution automatique").get("value").asDouble()).isEqualTo(20.0);   // 1 sur 5 résolus
        assertThat(rowOf(root, "Cycle par incident", manual.getId()).get(10).asText()).doesNotContain("automatique");
        assertThat(rowOf(root, "Cycle par incident", human.getId()).get(10).asText()).doesNotContain("automatique");
        assertThat(rowOf(root, "Cycle par incident", approval.getId()).get(10).asText()).doesNotContain("automatique");
        assertThat(rowOf(root, "Cycle par incident", routed.getId()).get(10).asText()).doesNotContain("automatique");
        assertThat(rowOf(root, "Cycle par incident", routed.getId()).get(4).asText()).isNotEqualTo("—");   // décision d'équipe horodatée
    }

    @Test
    void anUnresolvedIncidentIsNeverCountedAsAutomaticallyResolved() throws Exception {
        Incident i = incident("Non résolu", base);
        action(i, ActionType.RESTART_POD, ActionStatus.COMPLETED, AutomationMode.AUTO, 1, base.plusMinutes(5), null);
        verification(i, "FAILED", base.plusMinutes(8));

        JsonNode root = report(false);

        assertThat(metric(root, "Résolutions automatiques sans intervention humaine").get("value").asLong()).isZero();
        assertThat(metric(root, "Taux de résolution automatique").get("value").asDouble()).isZero();
    }

    // ── Actions, échecs, relevés VM ───────────────────────────────────────────

    @Test
    void failedActionsAttemptsAndVmEvidenceAreCounted() throws Exception {
        Incident i = incident("Échec puis succès", base);
        action(i, ActionType.RESTART_POD, ActionStatus.FAILED, AutomationMode.AUTO, 3, base.plusMinutes(5), null);
        action(i, ActionType.FORCE_DELETE_POD, ActionStatus.COMPLETED, AutomationMode.AUTO, 1, base.plusMinutes(10), null);
        evidence(i, TerminalEvidencePhase.BEFORE, true);
        evidence(i, TerminalEvidencePhase.AFTER, true);
        evidence(i, TerminalEvidencePhase.AFTER, true);
        evidence(i, TerminalEvidencePhase.AFTER, false);   // relevé indisponible : non compté

        JsonNode root = report(false);

        assertThat(metric(root, "Actions de remédiation en échec").get("value").asLong()).isEqualTo(1);
        assertThat(metric(root, "Tentatives moyennes par action").get("value").asDouble()).isEqualTo(2.0);
        assertThat(metric(root, "Relevés VM avant remédiation").get("value").asLong()).isEqualTo(1);
        assertThat(metric(root, "Relevés VM après remédiation").get("value").asLong()).isEqualTo(2);
        JsonNode types = section(root, "Par type d'action").get("rows");
        assertThat(types.toString()).contains("\"RESTART_POD\",1,0,1,3.0").contains("\"FORCE_DELETE_POD\",1,1,0,1.0");
    }

    // ── Période, comparaison, absence de données ──────────────────────────────

    @Test
    void incidentsOutsideThePeriodAreIgnoredAndThePreviousPeriodIsCompared() throws Exception {
        completeAutomaticCycle("Récent");
        Incident old = incident("Ancien", LocalDateTime.now().minusDays(20));
        action(old, ActionType.RESTART_POD, ActionStatus.COMPLETED, AutomationMode.AUTO, 1, LocalDateTime.now().minusDays(20).plusMinutes(5), null);

        JsonNode root = report(true, LocalDateTime.now().minusDays(2), LocalDateTime.now().plusDays(1));

        assertThat(metric(root, "Incidents de la période").get("value").asLong()).isEqualTo(1);
        assertThat(metric(root, "Incidents de la période").has("previous")).isTrue();
        assertThat(metric(root, "Incidents de la période").get("previous").asLong()).isZero();
    }

    @Test
    void withoutAnyDataTheIndicatorsAreZeroAndTheTablesAreEmpty() throws Exception {
        JsonNode root = report(false);

        assertThat(metric(root, "Incidents de la période").get("value").asLong()).isZero();
        assertThat(metric(root, "Taux de résolution automatique").get("value").asDouble()).isZero();
        assertThat(metric(root, "Durée moyenne détection → action").get("value").asDouble()).isZero();
        assertThat(section(root, "Cycle par incident").get("rows")).isEmpty();
        assertThat(root.path("charts")).isNotEmpty();
    }

    @Test
    void theReportIsListedAmongTheReportTypes() {
        assertThat(reports.types(admin)).extracting(t -> t.type()).contains(ReportType.AUTO_REMEDIATION_CYCLE);
    }
}
