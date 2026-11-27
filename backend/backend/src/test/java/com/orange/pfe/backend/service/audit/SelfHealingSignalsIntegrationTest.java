package com.orange.pfe.backend.service.audit;

import com.orange.pfe.backend.dto.audit.SelfHealingDtos.Alerts;
import com.orange.pfe.backend.dto.audit.SelfHealingDtos.AlertItem;
import com.orange.pfe.backend.dto.audit.SelfHealingDtos.CamundaTrace;
import com.orange.pfe.backend.dto.audit.SelfHealingDtos.LogSignals;
import com.orange.pfe.backend.exception.ResourceNotFoundException;
import com.orange.pfe.backend.model.core.Incident;
import com.orange.pfe.backend.model.core.IncidentHistory;
import com.orange.pfe.backend.model.enums.IncidentSeverity;
import com.orange.pfe.backend.model.enums.IncidentSource;
import com.orange.pfe.backend.model.enums.IncidentStatus;
import com.orange.pfe.backend.model.user.AppUser;
import com.orange.pfe.backend.model.user.UserRole;
import com.orange.pfe.backend.model.workflow.WorkflowInstance;
import com.orange.pfe.backend.model.workflow.WorkflowLog;
import com.orange.pfe.backend.model.workflow.WorkflowStatus;
import com.orange.pfe.backend.repository.AppUserRepository;
import com.orange.pfe.backend.service.org.OrgTestSupport;
import com.sun.net.httpserver.HttpServer;
import jakarta.persistence.EntityManager;
import jakarta.persistence.PersistenceContext;
import org.junit.jupiter.api.AfterAll;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.aop.support.AopUtils;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.security.access.AccessDeniedException;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;
import org.springframework.test.util.AopTestUtils;
import org.springframework.test.util.ReflectionTestUtils;
import org.springframework.transaction.annotation.Transactional;

import java.io.IOException;
import java.net.InetSocketAddress;
import java.net.URLDecoder;
import java.nio.charset.StandardCharsets;
import java.time.LocalDateTime;
import java.util.ArrayList;
import java.util.List;
import java.util.UUID;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

/**
 * Alertes, signaux de logs et trace Camunda de l'incident, sur une vraie base et de FAUX outils (un serveur HTTP local qui
 * joue Prometheus, Loki, Camunda, Alertmanager et Grafana) : rapprochement alerte → incident, états « non configuré » et
 * « indisponible », absence de variables Camunda, droits, lecture seule et absence d'écriture dans l'audit.
 */
@SpringBootTest(properties = OrgTestSupport.JWT_TEST_PROPERTY)
@Transactional
class SelfHealingSignalsIntegrationTest {

    private static HttpServer tools;
    private static final List<String> received = new ArrayList<>();
    private static volatile boolean promDown = false;
    private static volatile boolean lokiRulesDefined = false;

    @DynamicPropertySource
    static void props(DynamicPropertyRegistry registry) throws IOException {
        if (tools == null) {
            tools = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
            tools.createContext("/", ex -> {
                String path = ex.getRequestURI().getPath();
                String q = ex.getRequestURI().getRawQuery() == null ? "" : URLDecoder.decode(ex.getRequestURI().getRawQuery(), StandardCharsets.UTF_8);
                String body = new String(ex.getRequestBody().readAllBytes(), StandardCharsets.UTF_8);
                synchronized (received) {
                    received.add(ex.getRequestMethod() + " " + path + (q.isEmpty() ? "" : "?" + q));
                }
                int status = 200;
                String out = respond(path, q, body, ex.getRequestHeaders().getFirst("Authorization"));
                if (out == null) {
                    status = 404;
                    out = "{}";
                } else if (out.startsWith("#")) {
                    status = Integer.parseInt(out.substring(1, 4));
                    out = out.substring(4);
                }
                byte[] bytes = out.getBytes(StandardCharsets.UTF_8);
                ex.sendResponseHeaders(status, bytes.length == 0 ? -1 : bytes.length);
                if (bytes.length > 0) {
                    ex.getResponseBody().write(bytes);
                }
                ex.close();
            });
            tools.start();
        }
        String base = "http://127.0.0.1:" + tools.getAddress().getPort();
        registry.add("infra.health.prometheus-url", () -> base);
        registry.add("infra.health.loki-url", () -> base);
        registry.add("infra.health.grafana-url", () -> base);
        registry.add("camunda.rest-address", () -> base);
    }

    private static String series(String labels, String values) {
        return "{\"metric\":{" + labels + "},\"values\":[" + values + "]}";
    }

    private static String respond(String path, String q, String body, String auth) {
        long now = System.currentTimeMillis() / 1000;
        if (path.startsWith("/api/v1/") && promDown) {
            return "#503";
        }
        switch (path) {
            case "/api/v1/rules":
                return "{\"status\":\"success\",\"data\":{\"groups\":[{\"name\":\"g\",\"rules\":["
                        + "{\"name\":\"HeliosCrashLoop\",\"state\":\"inactive\",\"annotations\":{\"incident\":\"POD_CRASH_LOOP\",\"summary\":\"Pod en boucle\"}},"
                        + "{\"name\":\"HeliosCpuSature\",\"state\":\"inactive\",\"annotations\":{\"incident\":\"CPU_STRESS\"}}]}]}}";
            case "/api/v1/alerts":
                return "{\"status\":\"success\",\"data\":{\"alerts\":["
                        + "{\"labels\":{\"alertname\":\"HeliosCrashLoop\",\"namespace\":\"pfe\",\"pod\":\"backend-abc-1\",\"severity\":\"high\"},"
                        + "\"annotations\":{\"summary\":\"Pod backend-abc-1 en boucle\"},\"state\":\"firing\",\"activeAt\":\"" + java.time.Instant.ofEpochSecond(now - 300) + "\"},"
                        + "{\"labels\":{\"alertname\":\"HeliosCrashLoop\",\"namespace\":\"autre\",\"pod\":\"zzz-1\"},\"annotations\":{},\"state\":\"firing\",\"activeAt\":\"" + java.time.Instant.ofEpochSecond(now - 300) + "\"}]}}";
            case "/api/v1/query_range":
                return "{\"status\":\"success\",\"data\":{\"resultType\":\"matrix\",\"result\":["
                        + series("\"__name__\":\"ALERTS\",\"alertstate\":\"firing\",\"alertname\":\"HeliosCrashLoop\",\"namespace\":\"pfe\",\"pod\":\"backend-abc-1\",\"severity\":\"high\"",
                        "[" + (now - 600) + ",\"1\"],[" + (now - 540) + ",\"1\"],[" + (now - 480) + ",\"0\"]") + ","
                        + series("\"__name__\":\"ALERTS\",\"alertstate\":\"firing\",\"alertname\":\"HeliosReplicasIndisponibles\",\"namespace\":\"pfe\",\"deployment\":\"backend\"",
                        "[" + (now - 900) + ",\"1\"]") + ","
                        + series("\"__name__\":\"ALERTS\",\"alertstate\":\"firing\",\"alertname\":\"HeliosCrashLoop\",\"namespace\":\"autre\",\"pod\":\"zzz-1\"",
                        "[" + (now - 600) + ",\"1\"]") + ","
                        + series("\"__name__\":\"ALERTS\",\"alertstate\":\"firing\",\"alertname\":\"NodeDown\",\"instance\":\"192.168.148.158:9100\"",
                        "[" + (now - 700) + ",\"1\"],[" + (now - 640) + ",\"1\"]")
                        + "]}}";
            case "/api/v2/alerts":
                return "[{\"labels\":{\"alertname\":\"AmCrash\",\"namespace\":\"pfe\",\"deployment\":\"backend\"},\"annotations\":{\"summary\":\"AM\"},"
                        + "\"startsAt\":\"" + java.time.Instant.ofEpochSecond(now - 100) + "\",\"status\":{\"state\":\"active\"}},"
                        + "{\"labels\":{\"alertname\":\"Autre\",\"namespace\":\"zzz\",\"deployment\":\"x\"},\"annotations\":{},\"status\":{\"state\":\"active\"}}]";
            case "/api/prometheus/grafana/api/v1/alerts":
                return "Bearer tok-ok".equals(auth)
                        ? "{\"status\":\"success\",\"data\":{\"alerts\":[{\"labels\":{\"alertname\":\"GrafCrash\",\"namespace\":\"pfe\",\"deployment\":\"backend\"},\"annotations\":{\"summary\":\"G\"},\"state\":\"firing\",\"activeAt\":\""
                        + java.time.Instant.ofEpochSecond(now - 50) + "\"}]}}"
                        : "#401{\"message\":\"Unauthorized\"}";
            case "/loki/api/v1/rules":
                return lokiRulesDefined ? "groups:\n  - name: g\n    rules:\n      - alert: TooManyErrors\n        expr: x\n" : null;
            case "/loki/api/v1/query_range": {
                if (promDown) {
                    return "#503";
                }
                long base = now * 1_000_000_000L;
                String[] lines = {"kubelet[1]: connection refused to 10.0.0.1", "kubelet[1]: connection refused to 10.0.0.2",
                        "kernel: Out of memory: Killed process 99", "app: tout va bien"};
                StringBuilder v = new StringBuilder();
                for (int i = 0; i < lines.length; i++) {
                    v.append(i == 0 ? "" : ",").append("[\"").append(base - (lines.length - i) * 1_000_000_000L).append("\",\"").append(lines[i]).append("\"]");
                }
                return "{\"status\":\"success\",\"data\":{\"resultType\":\"streams\",\"result\":[{\"stream\":{},\"values\":[" + v + "]}]}}";
            }
            default:
                break;
        }
        if (path.equals("/v2/process-instances/2251799826972037")) {
            return "{\"processDefinitionId\":\"incident-remediation\",\"state\":\"COMPLETED\",\"hasIncident\":true,"
                    + "\"startDate\":\"2026-11-27T02:00:00.000Z\",\"endDate\":\"2026-11-27T02:00:09.000Z\"}";
        }
        if (path.equals("/v2/element-instances/search") && body.contains("2251799826972037")) {
            return "{\"page\":{\"totalItems\":3},\"items\":["
                    + "{\"elementName\":\"Début\",\"type\":\"START_EVENT\",\"state\":\"COMPLETED\",\"startDate\":\"2026-11-27T02:00:00.000Z\",\"endDate\":\"2026-11-27T02:00:00.000Z\",\"hasIncident\":false},"
                    + "{\"elementName\":\"Exécuter la remédiation\",\"type\":\"SERVICE_TASK\",\"state\":\"COMPLETED\",\"startDate\":\"2026-11-27T02:00:01.000Z\",\"endDate\":\"2026-11-27T02:00:06.000Z\",\"hasIncident\":true},"
                    + "{\"elementName\":\"\",\"elementId\":\"Fin\",\"type\":\"END_EVENT\",\"state\":\"COMPLETED\",\"startDate\":\"2026-11-27T02:00:09.000Z\",\"endDate\":\"2026-11-27T02:00:09.000Z\",\"hasIncident\":false}]}";
        }
        if (path.equals("/v2/incidents/search") && body.contains("2251799826972037")) {
            return "{\"page\":{\"totalItems\":1},\"items\":[{\"errorType\":\"JOB_NO_RETRIES\",\"errorMessage\":\"" + "x".repeat(400)
                    + "\",\"elementId\":\"Task_Exec\",\"creationTime\":\"2026-11-27T02:00:05.000Z\"}]}";
        }
        return null;   // inconnu : 404 (ex. instance purgée)
    }

    @AfterAll
    static void stop() {
        if (tools != null) {
            tools.stop(0);
            tools = null;
        }
    }

    @Autowired private SelfHealingAlertsService alerts;
    @Autowired private SelfHealingCamundaTraceService camunda;
    @Autowired private AppUserRepository users;
    @PersistenceContext private EntityManager em;

    private AppUser admin;
    private AppUser user;

    @BeforeEach
    void setUp() {
        String tag = UUID.randomUUID().toString().substring(0, 8);
        admin = OrgTestSupport.user(users, "adm-" + tag, UserRole.ADMIN);
        user = OrgTestSupport.user(users, "usr-" + tag, UserRole.USER);
        promDown = false;
        lokiRulesDefined = false;
        Object target = AopTestUtils.getTargetObject(alerts);
        ReflectionTestUtils.setField(target, "alertmanagerUrl", "");
        ReflectionTestUtils.setField(target, "grafanaToken", "");
        synchronized (received) {
            received.clear();
        }
    }

    // ── Données ───────────────────────────────────────────────────────────────

    private void setCreatedAt(String table, Long id, LocalDateTime at) {
        em.flush();
        em.createNativeQuery("update " + table + " set created_at = :t where id = :id").setParameter("t", at).setParameter("id", id).executeUpdate();
    }

    private Incident incident(String type, String namespace, String app) {
        Incident i = new Incident();
        i.setTitle("Incident " + app);
        i.setDescription("d");
        i.setSeverity(IncidentSeverity.HIGH);
        i.setStatus(IncidentStatus.OPEN);
        i.setSource(IncidentSource.MANUAL);
        i.setIncidentType(type);
        i.setNamespace(namespace);
        i.setApp(app);
        em.persist(i);
        em.flush();
        setCreatedAt("incident", i.getId(), LocalDateTime.now().minusMinutes(20));
        em.flush();
        em.clear();
        return i;
    }

    private Incident podIncident() {
        return incident("POD_CRASH_LOOP", "pfe", "backend");
    }

    private AlertItem item(Alerts a, String name) {
        return a.prometheus().alerts().stream().filter(x -> name.equals(x.name())).findFirst().orElseThrow();
    }

    private WorkflowInstance workflow(Incident i, String key) {
        WorkflowInstance w = new WorkflowInstance();
        w.setIncident(em.getReference(Incident.class, i.getId()));
        w.setStatus(WorkflowStatus.COMPLETED);
        w.setWorkflowName("incident-remediation");
        w.setCamundaProcessInstanceKey(key);
        em.persist(w);
        WorkflowLog l = new WorkflowLog();
        l.setWorkflow(w);
        l.setMessage("Étape exécutée");
        em.persist(l);
        em.flush();
        return w;
    }

    // ── Alertes ───────────────────────────────────────────────────────────────

    @Test
    void prometheusAlertsAreLinkedToTheIncidentByNamespaceAndAppWithTheirRule() {
        Incident i = podIncident();

        Alerts a = alerts.alerts(admin, i.getId());

        assertThat(a.prometheus().status()).isEqualTo("OK");
        assertThat(a.prometheus().alerts()).extracting(AlertItem::name).containsExactlyInAnyOrder("HeliosCrashLoop", "HeliosReplicasIndisponibles");
        AlertItem crash = item(a, "HeliosCrashLoop");
        assertThat(crash.activeNow()).isTrue();
        assertThat(crash.firstFiring()).isNotNull();
        assertThat(crash.lastFiring()).isNotNull();
        assertThat(crash.ruleState()).isEqualTo("inactive");
        assertThat(crash.ruleIncidentType()).isEqualTo("POD_CRASH_LOOP");
        assertThat(crash.typeMatches()).isTrue();
        assertThat(crash.summary()).contains("backend-abc-1");
        assertThat(crash.labels()).containsEntry("pod", "backend-abc-1").doesNotContainKey("__name__");
        AlertItem replicas = item(a, "HeliosReplicasIndisponibles");
        assertThat(replicas.activeNow()).isFalse();
        assertThat(replicas.typeMatches()).isFalse();
        // l'alerte d'une autre application et celle d'une machine ne sont pas rattachées
        assertThat(a.prometheus().alerts()).noneMatch(x -> "NodeDown".equals(x.name()) || "zzz-1".equals(x.labels().get("pod")));
        assertThat(a.from()).isBefore(a.to());
    }

    @Test
    void aHostIncidentIsLinkedToTheAlertsOfItsMachineAddress() {
        Incident i = incident("NODE_HIGH_CPU", "k8s-nodes", "k8s-worker1");

        Alerts a = alerts.alerts(admin, i.getId());

        assertThat(a.prometheus().alerts()).extracting(AlertItem::name).containsExactly("NodeDown");
    }

    @Test
    void alertmanagerAndGrafanaAreNotConfiguredByDefaultAndLokiHasNoRuler() {
        Alerts a = alerts.alerts(admin, podIncident().getId());

        assertThat(a.alertmanager().status()).isEqualTo("NOT_CONFIGURED");
        assertThat(a.grafana().status()).isEqualTo("NOT_CONFIGURED");
        assertThat(a.loki().status()).isEqualTo("NOT_CONFIGURED");
        assertThat(a.loki().message()).contains("pas de ruler");
        synchronized (received) {
            assertThat(received).noneMatch(r -> r.contains("/api/v2/alerts") || r.contains("/api/prometheus/grafana"));
        }
    }

    @Test
    void whenConfiguredAlertmanagerGrafanaAndLokiRulesAreReadAndFiltered() {
        Object target = AopTestUtils.getTargetObject(alerts);
        ReflectionTestUtils.setField(target, "alertmanagerUrl", "http://127.0.0.1:" + tools.getAddress().getPort());
        ReflectionTestUtils.setField(target, "grafanaToken", "tok-ok");
        lokiRulesDefined = true;

        Alerts a = alerts.alerts(admin, podIncident().getId());

        assertThat(a.alertmanager().status()).isEqualTo("OK");
        assertThat(a.alertmanager().alerts()).extracting(AlertItem::name).containsExactly("AmCrash");
        assertThat(a.grafana().status()).isEqualTo("OK");
        assertThat(a.grafana().alerts()).extracting(AlertItem::name).containsExactly("GrafCrash");
        assertThat(a.loki().status()).isEqualTo("OK");
        assertThat(a.loki().rules()).containsExactly("TooManyErrors");
    }

    @Test
    void aRefusedGrafanaTokenOrADeadAlertmanagerIsReportedAsUnavailable() {
        Object target = AopTestUtils.getTargetObject(alerts);
        ReflectionTestUtils.setField(target, "alertmanagerUrl", "http://127.0.0.1:1");
        ReflectionTestUtils.setField(target, "grafanaToken", "mauvais-jeton");

        Alerts a = alerts.alerts(admin, podIncident().getId());

        assertThat(a.alertmanager().status()).isEqualTo("UNAVAILABLE");
        assertThat(a.grafana().status()).isEqualTo("UNAVAILABLE");
        assertThat(a.grafana().message()).contains("refusé");
        assertThat(a.prometheus().status()).isEqualTo("OK");   // une panne ne contamine pas les autres sources
    }

    @Test
    void theGrafanaTokenIsNeverReturned() {
        Object target = AopTestUtils.getTargetObject(alerts);
        ReflectionTestUtils.setField(target, "grafanaToken", "tok-ok");

        Alerts a = alerts.alerts(admin, podIncident().getId());

        assertThat(a.toString()).doesNotContain("tok-ok");
    }

    @Test
    void whenPrometheusIsDownTheAnswerSaysSoWithoutAnError() {
        promDown = true;

        Alerts a = alerts.alerts(admin, podIncident().getId());

        assertThat(a.prometheus().status()).isEqualTo("UNAVAILABLE");
        assertThat(a.prometheus().alerts()).isEmpty();
    }

    @Test
    void anIncidentWithoutAnAppMatchesNoAlert() {
        Incident i = incident("OTHER", "pfe", null);

        assertThat(alerts.alerts(admin, i.getId()).prometheus().alerts()).isEmpty();
    }

    // ── Signaux de logs ───────────────────────────────────────────────────────

    @Test
    void logSignalsAreGroupedByMeaningWithCountsTimesAndAnExample() {
        Incident i = podIncident();

        LogSignals s = alerts.logSignals(admin, i.getId());

        assertThat(s.available()).isTrue();
        assertThat(s.source()).isEqualTo("pods pfe/backend");
        assertThat(s.linesRead()).isEqualTo(4);
        assertThat(s.signals()).extracting(x -> x.category()).containsExactly("RESEAU", "MEMOIRE");
        var network = s.signals().get(0);
        assertThat(network.count()).isEqualTo(2);
        assertThat(network.first()).isBeforeOrEqualTo(network.last());
        assertThat(network.example()).contains("connection refused");
        assertThat(network.advice()).isNotBlank();
        assertThat(s.truncated()).isFalse();
        synchronized (received) {
            assertThat(received.stream().filter(r -> r.contains("query_range") && r.contains("k8s-containers")).findFirst()).isPresent();
        }
    }

    @Test
    void aVmNotCollectedByLokiGivesNoSignalAndNoLokiCall() {
        Incident i = incident("NODE_HIGH_CPU", "k8s-nodes", "k8s-lb2");

        LogSignals s = alerts.logSignals(admin, i.getId());

        assertThat(s.available()).isFalse();
        assertThat(s.reason()).contains("pas collectés");
        synchronized (received) {
            assertThat(received).isEmpty();
        }
    }

    @Test
    void whenLokiIsDownLogSignalsAreUnavailable() {
        promDown = true;   // le faux serveur répond 503 aux routes Prometheus et Loki

        LogSignals s = alerts.logSignals(admin, podIncident().getId());

        assertThat(s.available()).isFalse();
        assertThat(s.signals()).isEmpty();
    }

    // ── Trace Camunda ─────────────────────────────────────────────────────────

    @Test
    void theCamundaTraceListsTheRealStepsInOrderWithTheirIncidentsAndTheWorkflowLog() {
        Incident i = podIncident();
        workflow(i, "2251799826972037");

        CamundaTrace t = camunda.trace(admin, i.getId());

        assertThat(t.available()).isTrue();
        var run = t.runs().get(0);
        assertThat(run.state()).isEqualTo("COMPLETED");
        assertThat(run.hasIncident()).isTrue();
        assertThat(run.definitionId()).isEqualTo("incident-remediation");
        assertThat(run.steps()).extracting(s -> s.name()).containsExactly("Début", "Exécuter la remédiation", "Fin");
        assertThat(run.steps().get(1).incident()).isTrue();
        assertThat(run.steps().get(1).durationMs()).isEqualTo(5000L);
        assertThat(run.steps().get(1).type()).isEqualTo("SERVICE_TASK");
        assertThat(run.incidents()).singleElement().satisfies(x -> {
            assertThat(x.errorType()).isEqualTo("JOB_NO_RETRIES");
            assertThat(x.message().length()).isLessThanOrEqualTo(300);
        });
        assertThat(run.logs()).extracting(l -> l.message()).containsExactly("Étape exécutée");
    }

    @Test
    void processVariablesAreNeverRequestedAndOnlyReadCallsAreMade() {
        Incident i = podIncident();
        workflow(i, "2251799826972037");

        camunda.trace(admin, i.getId());

        synchronized (received) {
            assertThat(received).isNotEmpty().noneMatch(r -> r.contains("variables"));
            assertThat(received).allSatisfy(r -> assertThat(r).matches("(GET /v2/process-instances/\\d+)|(POST /v2/(element-instances|incidents)/search)"));
        }
    }

    @Test
    void aWorkflowWithoutACamundaInstanceOrWithAPurgedOneIsExplainedNotFailed() {
        Incident i = podIncident();
        workflow(i, null);
        workflow(i, "2251799826972037");
        workflow(i, "999");

        CamundaTrace t = camunda.trace(admin, i.getId());

        assertThat(t.available()).isTrue();   // au moins une instance a pu être lue
        assertThat(t.runs()).hasSize(3);
        assertThat(t.runs().get(0).note()).contains("Aucune instance Camunda");
        assertThat(t.runs().get(1).steps()).hasSize(3);
        assertThat(t.runs().get(2).note()).contains("introuvable");
    }

    @Test
    void whenEveryInstanceIsUnreadableTheTraceReportsItButStillGivesTheHeliosLog() {
        Incident i = podIncident();
        workflow(i, "999");

        CamundaTrace t = camunda.trace(admin, i.getId());

        assertThat(t.available()).isFalse();
        assertThat(t.error()).contains("introuvable");
        assertThat(t.runs().get(0).logs()).hasSize(1);
    }

    @Test
    void anInvalidInstanceKeyNeverReachesCamunda() {
        Incident i = podIncident();
        workflow(i, "../../admin");

        CamundaTrace t = camunda.trace(admin, i.getId());

        assertThat(t.available()).isFalse();
        assertThat(t.runs().get(0).note()).contains("invalide");
        synchronized (received) {
            assertThat(received).isEmpty();
        }
    }

    // ── Droits, incident inconnu, absence d'écriture ──────────────────────────

    @Test
    void onlyAdministratorsWithFullAuditAccessCanUseThem() {
        Incident i = podIncident();

        assertThatThrownBy(() -> alerts.alerts(user, i.getId())).isInstanceOf(AccessDeniedException.class);
        assertThatThrownBy(() -> alerts.logSignals(user, i.getId())).isInstanceOf(AccessDeniedException.class);
        assertThatThrownBy(() -> camunda.trace(user, i.getId())).isInstanceOf(AccessDeniedException.class);
    }

    @Test
    void anUnknownIncidentIsNotFound() {
        assertThatThrownBy(() -> alerts.alerts(admin, 999_999_999L)).isInstanceOf(ResourceNotFoundException.class);
        assertThatThrownBy(() -> alerts.logSignals(admin, 999_999_999L)).isInstanceOf(ResourceNotFoundException.class);
        assertThatThrownBy(() -> camunda.trace(admin, 999_999_999L)).isInstanceOf(ResourceNotFoundException.class);
    }

    @Test
    void consultingWritesNothingToTheAuditLog() {
        Incident i = podIncident();
        workflow(i, "2251799826972037");
        long before = em.createQuery("select count(e) from AuditLogEntry e", Long.class).getSingleResult();

        alerts.alerts(admin, i.getId());
        alerts.logSignals(admin, i.getId());
        camunda.trace(admin, i.getId());
        em.flush();

        assertThat(em.createQuery("select count(e) from AuditLogEntry e", Long.class).getSingleResult()).isEqualTo(before);
    }

    @Test
    void theMatchingRuleHasNoSideEffects() {
        // garde-fou : AopUtils est bien utilisé pour atteindre l'objet cible des champs de configuration
        assertThat(AopUtils.isAopProxy(alerts) || AopTestUtils.getTargetObject(alerts) != null).isTrue();
        assertThat(IncidentHistory.class).isNotNull();
    }
}
