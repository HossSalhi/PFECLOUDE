package com.orange.pfe.backend.service.audit;

import com.orange.pfe.backend.dto.audit.SelfHealingDtos.ActionLogs;
import com.orange.pfe.backend.dto.audit.SelfHealingDtos.Listing;
import com.orange.pfe.backend.dto.audit.SelfHealingDtos.Trace;
import com.orange.pfe.backend.exception.ResourceNotFoundException;
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
import com.orange.pfe.backend.model.user.AppUser;
import com.orange.pfe.backend.model.user.UserRole;
import com.orange.pfe.backend.repository.AppUserRepository;
import com.orange.pfe.backend.service.org.OrgTestSupport;
import com.sun.net.httpserver.HttpServer;
import jakarta.persistence.EntityManager;
import jakarta.persistence.PersistenceContext;
import org.junit.jupiter.api.AfterAll;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.security.access.AccessDeniedException;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;
import org.springframework.transaction.annotation.Transactional;

import java.io.IOException;
import java.net.InetSocketAddress;
import java.net.URLDecoder;
import java.nio.charset.StandardCharsets;
import java.time.LocalDateTime;
import java.time.ZoneId;
import java.util.ArrayList;
import java.util.List;
import java.util.UUID;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

/**
 * Trace d'auto-remédiation de l'Audit-trail sur une vraie base et un FAUX Loki (HTTP local) : liste, chronologie, logs de la
 * VM autour d'une action, dégradation, droits, et absence de toute écriture dans le journal d'audit.
 */
@SpringBootTest(properties = OrgTestSupport.JWT_TEST_PROPERTY)
@Transactional
class SelfHealingTraceIntegrationTest {

    private static HttpServer loki;
    private static final List<String> received = new ArrayList<>();
    private static volatile boolean lokiDown = false;

    @DynamicPropertySource
    static void props(DynamicPropertyRegistry registry) throws IOException {
        if (loki == null) {
            loki = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
            loki.createContext("/loki/api/v1/query_range", ex -> {
                String raw = ex.getRequestURI().getRawQuery();
                synchronized (received) {
                    received.add(URLDecoder.decode(raw, StandardCharsets.UTF_8));
                }
                if (lokiDown) {
                    ex.sendResponseHeaders(503, -1);
                    ex.close();
                    return;
                }
                String q = URLDecoder.decode(raw, StandardCharsets.UTF_8);
                StringBuilder values = new StringBuilder();
                long base = System.currentTimeMillis() / 1000 * 1_000_000_000L;
                int count = q.contains("host=\"worker2\"") ? 100 : 3;
                for (int i = 0; i < count; i++) {
                    String line = i == 1 ? "kubelet[1]: connection refused to 10.0.0.1:6443"
                            : "systemd[1]: ligne normale " + i;
                    values.append(i == 0 ? "" : ",").append("[\"").append(base - (count - i) * 1_000_000_000L).append("\",\"")
                            .append(line).append("\"]");
                }
                byte[] body = ("{\"status\":\"success\",\"data\":{\"resultType\":\"streams\",\"result\":[{\"stream\":{},\"values\":["
                        + values + "]}]}}").getBytes(StandardCharsets.UTF_8);
                ex.getResponseHeaders().add("Content-Type", "application/json");
                ex.sendResponseHeaders(200, body.length);
                ex.getResponseBody().write(body);
                ex.close();
            });
            loki.start();
        }
        registry.add("infra.health.loki-url", () -> "http://127.0.0.1:" + loki.getAddress().getPort());
    }

    @AfterAll
    static void stopLoki() {
        if (loki != null) {
            loki.stop(0);
            loki = null;
        }
    }

    @Autowired private SelfHealingTraceService service;
    @Autowired private AppUserRepository users;
    @PersistenceContext private EntityManager em;

    private AppUser admin;
    private AppUser user;
    private LocalDateTime base;

    @BeforeEach
    void setUp() {
        String tag = UUID.randomUUID().toString().substring(0, 8);
        admin = OrgTestSupport.user(users, "adm-" + tag, UserRole.ADMIN);
        user = OrgTestSupport.user(users, "usr-" + tag, UserRole.USER);
        base = LocalDateTime.now().minusHours(2).withNano(0);
        lokiDown = false;
        synchronized (received) {
            received.clear();
        }
    }

    // ── Données ───────────────────────────────────────────────────────────────

    private void setCreatedAt(String table, Long id, LocalDateTime at) {
        em.flush();
        em.createNativeQuery("update " + table + " set created_at = :t where id = :id").setParameter("t", at).setParameter("id", id).executeUpdate();
    }

    private Incident incident(String title, String namespace, String app, LocalDateTime at) {
        Incident i = new Incident();
        i.setTitle(title);
        i.setDescription("d");
        i.setSeverity(IncidentSeverity.HIGH);
        i.setStatus(IncidentStatus.IN_PROGRESS);
        i.setSource(IncidentSource.MANUAL);
        i.setIncidentType("NODE_HIGH_CPU");
        i.setNamespace(namespace);
        i.setApp(app);
        em.persist(i);
        em.flush();
        setCreatedAt("incident", i.getId(), at);
        return i;
    }

    private IncidentAction action(Incident i, ActionType type, ActionStatus status, LocalDateTime createdAt, LocalDateTime executedAt) {
        IncidentAction a = new IncidentAction();
        a.setIncident(i);
        a.setType(type);
        a.setStatus(status);
        a.setAutomationMode(AutomationMode.AUTO);
        a.setAttemptCount(1);
        a.setExecutedAt(executedAt);
        a.setDescription("Description " + type);
        em.persist(a);
        em.flush();
        setCreatedAt("incident_action", a.getId(), createdAt);
        return a;
    }

    private void history(Incident i, IncidentStatus status, String comment, LocalDateTime at) {
        IncidentHistory h = new IncidentHistory();
        h.setIncident(i);
        h.setStatus(status);
        h.setComment(comment);
        em.persist(h);
        em.flush();
        setCreatedAt("incident_history", h.getId(), at);
    }

    private void resolve() {
        em.flush();
        em.clear();
    }

    private Incident hostIncident(String node) {
        return incident("CPU " + node, "k8s-nodes", node, base);
    }

    // ── Liste ─────────────────────────────────────────────────────────────────

    @Test
    void listShowsIncidentsWithARealRemediationInThePeriodAndIgnoresNotificationsOnly() {
        Incident healed = hostIncident("k8s-worker1");
        action(healed, ActionType.HOST_SERVICE_CPU_QUOTA, ActionStatus.COMPLETED, base.plusMinutes(5), base.plusMinutes(6));
        action(healed, ActionType.RESTART_POD, ActionStatus.FAILED, base.plusMinutes(8), null);
        Incident notified = hostIncident("k8s-worker2");
        action(notified, ActionType.NOTIFICATION, ActionStatus.COMPLETED, base.plusMinutes(5), null);
        Incident old = incident("Ancien", "pfe", "backend", base.minusDays(5));
        action(old, ActionType.RESTART_POD, ActionStatus.COMPLETED, base.minusDays(5).plusMinutes(1), null);
        resolve();

        Listing l = service.list(admin, base.minusHours(1), base.plusHours(3), 50);

        assertThat(l.items()).extracting(x -> x.incidentId()).containsExactly(healed.getId());
        var item = l.items().get(0);
        assertThat(item.actions()).isEqualTo(2);
        assertThat(item.failedActions()).isEqualTo(1);
        assertThat(item.lastActionType()).isEqualTo("RESTART_POD");
        assertThat(item.resource()).isEqualTo("k8s-nodes/k8s-worker1");
        assertThat(l.truncated()).isFalse();
    }

    @Test
    void listIsBoundedAndSaysWhenItIsTruncated() {
        for (int n = 0; n < 3; n++) {
            Incident i = hostIncident("k8s-worker" + (n + 1));
            action(i, ActionType.RESTART_POD, ActionStatus.COMPLETED, base.plusMinutes(5), null);
        }
        resolve();

        Listing l = service.list(admin, base.minusHours(1), base.plusHours(3), 2);

        assertThat(l.items()).hasSize(2);
        assertThat(l.truncated()).isTrue();
        assertThat(service.list(admin, base.minusHours(1), base.plusHours(3), 99999).items()).hasSize(3);
    }

    @Test
    void listRefusesAnInvalidOrTooLongPeriod() {
        assertThatThrownBy(() -> service.list(admin, base, base.minusHours(1), 10)).isInstanceOf(IllegalArgumentException.class);
        assertThatThrownBy(() -> service.list(admin, base.minusDays(40), base, 10)).isInstanceOf(IllegalArgumentException.class);
    }

    // ── Trace ─────────────────────────────────────────────────────────────────

    @Test
    void theTraceMergesHistoryActionsVerificationAndVmEvidenceInChronologicalOrder() {
        Incident i = hostIncident("k8s-worker1");
        history(i, IncidentStatus.IN_PROGRESS, "Remédiation démarrée", base.plusMinutes(4));
        action(i, ActionType.HOST_SERVICE_CPU_QUOTA, ActionStatus.COMPLETED, base.plusMinutes(5), base.plusMinutes(7));
        IncidentTerminalEvidence e = new IncidentTerminalEvidence();
        e.setIncidentId(i.getId());
        e.setPhase(TerminalEvidencePhase.BEFORE);
        e.setVm("k8s-worker1");
        e.setCommand("uptime");
        e.setStdout("SECRET-OUTPUT-NE-DOIT-PAS-FUIR");
        e.setExitCode(0);
        e.setAvailable(true);
        e.setCapturedAt(base.plusMinutes(3));
        em.persist(e);
        IncidentVerification v = new IncidentVerification();
        v.setIncidentId(i.getId());
        v.setResult("PASSED");
        v.setCriteria("CPU < 80 %");
        v.setVerifiedAt(base.plusMinutes(10));
        v.setAvailable(true);
        em.persist(v);
        history(i, IncidentStatus.RESOLVED, "Résolu", base.plusMinutes(12));
        resolve();

        Trace t = service.trace(admin, i.getId());

        assertThat(t.incident().type()).isEqualTo("NODE_HIGH_CPU");
        assertThat(t.timeline()).extracting(x -> x.kind()).containsExactly(
                "INCIDENT", "EVIDENCE", "HISTORY", "ACTION", "ACTION", "VERIFICATION", "HISTORY");
        assertThat(t.timeline()).extracting(x -> x.at()).isSorted();
        assertThat(t.actions()).singleElement().satisfies(a -> {
            assertThat(a.type()).isEqualTo("HOST_SERVICE_CPU_QUOTA");
            assertThat(a.remediation()).isTrue();
            assertThat(a.logSource()).isEqualTo("VM worker1");
        });
        assertThat(t.verifications()).singleElement().satisfies(x -> assertThat(x.result()).isEqualTo("PASSED"));
        assertThat(t.evidence()).singleElement().satisfies(x -> {
            assertThat(x.vm()).isEqualTo("k8s-worker1");
            assertThat(x.phase()).isEqualTo("BEFORE");
            assertThat(x.exitCode()).isZero();
        });
        // la sortie des commandes n'est volontairement jamais reprise dans la trace
        assertThat(t.toString()).doesNotContain("SECRET-OUTPUT");
    }

    @Test
    void anUnknownIncidentIsNotFound() {
        assertThatThrownBy(() -> service.trace(admin, 999_999_999L)).isInstanceOf(ResourceNotFoundException.class);
    }

    // ── Logs autour d'une action ──────────────────────────────────────────────

    @Test
    void logsOfAHostIncidentComeFromTheRightVmWithinFiveMinutesOfTheAction() {
        Incident i = hostIncident("k8s-worker1");
        LocalDateTime executed = base.plusMinutes(7);
        IncidentAction a = action(i, ActionType.HOST_SERVICE_CPU_QUOTA, ActionStatus.COMPLETED, base.plusMinutes(5), executed);
        resolve();

        ActionLogs logs = service.actionLogs(admin, i.getId(), a.getId());

        assertThat(logs.available()).isTrue();
        assertThat(logs.source()).isEqualTo("VM worker1");
        assertThat(logs.from()).isEqualTo(executed.minusMinutes(5));
        assertThat(logs.to()).isEqualTo(executed.plusMinutes(5));
        assertThat(logs.lines()).hasSize(3);
        assertThat(logs.lines()).extracting(x -> x.at()).isSorted();
        assertThat(logs.lines().get(1).error()).isTrue();
        assertThat(logs.lines().get(1).category()).isEqualTo("RESEAU");
        assertThat(logs.lines().get(0).error()).isFalse();
        assertThat(logs.truncated()).isFalse();
        String query;
        synchronized (received) {
            query = received.get(received.size() - 1);
        }
        assertThat(query).contains("host=\"worker1\"").contains("limit=100");
        long zoneStart = executed.minusMinutes(5).atZone(ZoneId.systemDefault()).toEpochSecond();
        assertThat(query).contains("start=" + zoneStart + "000000000");
    }

    @Test
    void anActionWithoutExecutionTimeUsesItsCreationTime() {
        Incident i = hostIncident("k8s-worker1");
        IncidentAction a = action(i, ActionType.RESTART_POD, ActionStatus.PENDING, base.plusMinutes(5), null);
        resolve();

        ActionLogs logs = service.actionLogs(admin, i.getId(), a.getId());

        assertThat(logs.from()).isEqualTo(base.plusMinutes(5).minusMinutes(5));
    }

    @Test
    void whenTheLogLimitIsReachedTheAnswerSaysSo() {
        Incident i = hostIncident("k8s-worker2");
        IncidentAction a = action(i, ActionType.RESTART_POD, ActionStatus.COMPLETED, base.plusMinutes(5), null);
        resolve();

        ActionLogs logs = service.actionLogs(admin, i.getId(), a.getId());

        assertThat(logs.lines()).hasSize(100);
        assertThat(logs.truncated()).isTrue();
    }

    @Test
    void logsOfAPodIncidentAreReadFromTheContainerLogsOfTheRightNamespaceAndApp() {
        Incident i = incident("Crash backend", "pfe", "backend", base);
        IncidentAction a = action(i, ActionType.RESTART_POD, ActionStatus.COMPLETED, base.plusMinutes(5), null);
        resolve();

        ActionLogs logs = service.actionLogs(admin, i.getId(), a.getId());

        assertThat(logs.available()).isTrue();
        assertThat(logs.source()).isEqualTo("pods pfe/backend");
        synchronized (received) {
            assertThat(received.get(received.size() - 1)).contains("job=\"k8s-containers\"").contains("_pfe_");
        }
    }

    @Test
    void aVmThatLokiDoesNotCollectIsReportedWithoutCallingLoki() {
        Incident i = hostIncident("k8s-lb2");
        IncidentAction a = action(i, ActionType.RESTART_VM, ActionStatus.COMPLETED, base.plusMinutes(5), null);
        resolve();

        ActionLogs logs = service.actionLogs(admin, i.getId(), a.getId());

        assertThat(logs.available()).isFalse();
        assertThat(logs.reason()).contains("pas collectés");
        synchronized (received) {
            assertThat(received).isEmpty();
        }
    }

    @Test
    void anUnsafeNamespaceOrAppNeverReachesTheLogQuery() {
        Incident i = incident("Piège", "pfe\" or {job=~\".+\"} #", "backend", base);
        IncidentAction a = action(i, ActionType.RESTART_POD, ActionStatus.COMPLETED, base.plusMinutes(5), null);
        resolve();

        ActionLogs logs = service.actionLogs(admin, i.getId(), a.getId());

        assertThat(logs.available()).isFalse();
        synchronized (received) {
            assertThat(received).isEmpty();
        }
    }

    @Test
    void anActionOfAnotherIncidentIsNotFound() {
        Incident one = hostIncident("k8s-worker1");
        Incident two = hostIncident("k8s-worker2");
        IncidentAction other = action(two, ActionType.RESTART_POD, ActionStatus.COMPLETED, base.plusMinutes(5), null);
        resolve();

        assertThatThrownBy(() -> service.actionLogs(admin, one.getId(), other.getId())).isInstanceOf(ResourceNotFoundException.class);
    }

    @Test
    void whenLokiIsDownTheAnswerIsUnavailableNotAnError() {
        Incident i = hostIncident("k8s-worker1");
        IncidentAction a = action(i, ActionType.RESTART_POD, ActionStatus.COMPLETED, base.plusMinutes(5), null);
        resolve();
        lokiDown = true;   // le faux Loki répond HTTP 503 (le serveur partagé reste actif pour les autres tests)

        ActionLogs logs = service.actionLogs(admin, i.getId(), a.getId());

        assertThat(logs.available()).isFalse();
        assertThat(logs.reason()).contains("Loki");
        assertThat(logs.source()).isEqualTo("VM worker1");
        assertThat(logs.lines()).isEmpty();
    }

    // ── Droits et absence d'écriture ──────────────────────────────────────────

    @Test
    void onlyAdministratorsWithFullAuditAccessCanUseTheTrace() {
        Incident i = hostIncident("k8s-worker1");
        IncidentAction a = action(i, ActionType.RESTART_POD, ActionStatus.COMPLETED, base.plusMinutes(5), null);
        resolve();

        assertThatThrownBy(() -> service.list(user, null, null, 10)).isInstanceOf(AccessDeniedException.class);
        assertThatThrownBy(() -> service.trace(user, i.getId())).isInstanceOf(AccessDeniedException.class);
        assertThatThrownBy(() -> service.actionLogs(user, i.getId(), a.getId())).isInstanceOf(AccessDeniedException.class);
    }

    @Test
    void consultingTheTraceWritesNothingToTheAuditLog() {
        Incident i = hostIncident("k8s-worker1");
        IncidentAction a = action(i, ActionType.RESTART_POD, ActionStatus.COMPLETED, base.plusMinutes(5), null);
        resolve();
        long before = em.createQuery("select count(e) from AuditLogEntry e", Long.class).getSingleResult();

        service.list(admin, base.minusHours(1), base.plusHours(3), 10);
        service.trace(admin, i.getId());
        service.actionLogs(admin, i.getId(), a.getId());
        em.flush();

        assertThat(em.createQuery("select count(e) from AuditLogEntry e", Long.class).getSingleResult()).isEqualTo(before);
    }
}
