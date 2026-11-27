package com.orange.pfe.backend.service.aiassist.telemetry;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ArrayNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import com.orange.pfe.backend.model.team.ResourceScope;
import com.orange.pfe.backend.service.aiassist.telemetry.TelemetryModels.Finding;
import com.orange.pfe.backend.service.supervision.SupervisionClient;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

import java.util.EnumSet;
import java.util.LinkedHashMap;
import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

/** Sonde de la stack de supervision : cibles, alertes, déploiements, pods, santé des outils. Lecture seule, panne par panne. */
class TelemetryStackProbeTest {

    private static final ObjectMapper JSON = new ObjectMapper();
    private static final long END = 1_795_000_000L;
    private SupervisionClient client;
    private TelemetryStackProbe probe;

    @BeforeEach
    void setUp() {
        client = mock(SupervisionClient.class);
        probe = new TelemetryStackProbe(client);
        when(client.promInstant(anyString())).thenReturn(JSON.createArrayNode());          // tout va bien par défaut
        when(client.promAlerts()).thenReturn(JSON.createArrayNode());
        Map<String, String> ok = new LinkedHashMap<>();
        ok.put("Prometheus", null);
        ok.put("Loki", null);
        ok.put("Grafana", null);
        when(client.probeReady()).thenReturn(ok);
    }

    private static ArrayNode series(String value, String... kv) {
        ArrayNode a = JSON.createArrayNode();
        ObjectNode s = a.addObject();
        ObjectNode m = s.putObject("metric");
        for (int i = 0; i < kv.length; i += 2) {
            m.put(kv[i], kv[i + 1]);
        }
        s.putArray("value").add(END).add(value);
        return a;
    }

    private TelemetryStackProbe.Result run(java.util.Set<ResourceScope> scopes) {
        return probe.probe(END - 3600, END, scopes);
    }

    @Test
    void aHealthyStackYieldsNoFindingAndAllProbesAnswered() {
        TelemetryStackProbe.Result r = run(null);

        assertThat(r.findings()).isEmpty();
        assertThat(r.failures()).isEmpty();
        assertThat(r.anyOk()).isTrue();
        assertThat(r.queries()).hasSize(8).allMatch(q -> q.status().equals("OK"));
        assertThat(r.queries()).extracting(q -> q.id()).containsExactly("PROBE_TARGETS_DOWN", "PROBE_TARGETS_UNSTABLE", "PROBE_DEPLOYMENTS", "PROBE_RESTARTS",
                "PROBE_PODS_NOT_RUNNING", "PROBE_PODS_STUCK", "PROBE_ALERTS", "PROBE_TOOLS_READY");
        assertThat(r.coverage()).singleElement().satisfies(c -> assertThat(c.coverage()).isEqualTo(1.0));
    }

    @Test
    void aDownTargetIsClassifiedByItsJobAndNamedReadably() {
        ArrayNode down = JSON.createArrayNode();
        down.addAll(series("0", "job", "vm-node-exporters", "instance", "192.168.148.162:9100"));
        down.addAll(series("0", "job", "loki", "instance", "192.168.148.160:3100"));
        down.addAll(series("0", "job", "backend-direct", "instance", "10.0.0.5:8080"));
        when(client.promInstant(TelemetryStackProbe.TARGETS_DOWN_QL)).thenReturn(down);

        var f = run(null).findings();

        assertThat(f).extracting(Finding::rule).containsOnly("TARGET_DOWN");
        assertThat(f).extracting(Finding::scope, Finding::entity, Finding::severity).containsExactlyInAnyOrder(
                org.assertj.core.groups.Tuple.tuple("VM", "k8s-lb2", "HIGH"),
                org.assertj.core.groups.Tuple.tuple("SERVICE", "service loki (k8s-monitoring)", "HIGH"),
                org.assertj.core.groups.Tuple.tuple("APPLICATION", "service backend-direct (10.0.0.5)", "HIGH"));
        assertThat(f.get(0).evidence()).startsWith("PromQL : up == 0");
        assertThat(f.get(0).queryRef()).isEqualTo("PROBE_TARGETS_DOWN");
    }

    @Test
    void anUnstableTargetReportsItsAvailabilityPercentage() {
        when(client.promInstant("avg_over_time(up[3600s]) < 0.95")).thenReturn(series("0.8", "job", "grafana", "instance", "192.168.148.160:3000"));

        var f = run(null).findings();

        assertThat(f).singleElement().satisfies(x -> {
            assertThat(x.rule()).isEqualTo("TARGET_UNSTABLE");
            assertThat(x.observed()).isEqualTo(80.0);
            assertThat(x.detail()).contains("80.0 %");
        });
    }

    @Test
    void applicationWorkloadsAreSeparatedFromTheInfrastructureOnes() {
        when(client.promInstant(TelemetryStackProbe.DEPLOY_QL)).thenReturn(series("1", "namespace", "pfe", "deployment", "backend"));
        when(client.promInstant("sum by (namespace, container) (increase(kube_pod_container_status_restarts_total[3600s])) >= 1"))
                .thenReturn(series("7", "namespace", "kube-system", "container", "etcd"));
        when(client.promInstant(TelemetryStackProbe.NOT_RUNNING_QL)).thenReturn(series("1", "namespace", "pfe", "pod", "frontend-558445cb9-n2bpm", "phase", "Pending"));
        when(client.promInstant(TelemetryStackProbe.STUCK_QL)).thenReturn(series("1", "namespace", "pfe", "container", "backend", "reason", "CrashLoopBackOff"));

        var f = run(null).findings();

        assertThat(f).extracting(Finding::rule, Finding::scope, Finding::entity, Finding::severity).containsExactlyInAnyOrder(
                org.assertj.core.groups.Tuple.tuple("DEPLOYMENT_UNAVAILABLE", "APPLICATION", "pfe/backend", "HIGH"),
                org.assertj.core.groups.Tuple.tuple("POD_RESTARTS", "CLUSTER", "kube-system/etcd", "HIGH"),
                org.assertj.core.groups.Tuple.tuple("POD_NOT_RUNNING", "APPLICATION", "pfe/frontend", "MEDIUM"),
                org.assertj.core.groups.Tuple.tuple("POD_STUCK", "APPLICATION", "pfe/backend", "HIGH"));
        assertThat(f).extracting(x -> TelemetryModels.sideOf(x.scope())).containsExactlyInAnyOrder("APPLICATION", "INFRASTRUCTURE", "APPLICATION", "APPLICATION");
    }

    @Test
    void onlyFiringAlertsAreReportedWithTheirSeverity() {
        ArrayNode alerts = JSON.createArrayNode();
        ObjectNode a = alerts.addObject();
        a.put("state", "firing");
        a.putObject("labels").put("alertname", "NodeDown").put("severity", "critical").put("instance", "192.168.148.153:9100");
        ObjectNode b = alerts.addObject();
        b.put("state", "pending");
        b.putObject("labels").put("alertname", "Slow").put("severity", "warning");
        when(client.promAlerts()).thenReturn(alerts);

        var r = run(null);

        assertThat(r.findings()).singleElement().satisfies(f -> {
            assertThat(f.rule()).isEqualTo("ALERT_FIRING");
            assertThat(f.severity()).isEqualTo("HIGH");
            assertThat(f.entity()).isEqualTo("k8s-master");
            assertThat(f.detail()).contains("NodeDown");
        });
    }

    @Test
    void aMonitoringToolThatIsNotReadyIsReportedWithTheReason() {
        Map<String, String> res = new LinkedHashMap<>();
        res.put("Prometheus", null);
        res.put("Loki", "HTTP 503 (Ingester not ready: waiting for 15s after being ready)");
        res.put("Grafana", "injoignable (ConnectException)");
        when(client.probeReady()).thenReturn(res);

        var f = run(null).findings();

        assertThat(f).extracting(Finding::entity).containsExactly("service Loki", "service Grafana");
        assertThat(f.get(0).scope()).isEqualTo("SERVICE");
        assertThat(f.get(0).detail()).contains("Loki n'est pas prêt").contains("HTTP 503").contains("Ingester not ready");
    }

    @Test
    void eachFailingProbeIsNamedAndNeverHidden() {
        when(client.promInstant(TelemetryStackProbe.DEPLOY_QL)).thenThrow(new SupervisionClient.UnavailableException("Prometheus a répondu HTTP 500", null));
        when(client.promAlerts()).thenThrow(new SupervisionClient.UnavailableException("Prometheus est injoignable", null));
        when(client.probeReady()).thenThrow(new IllegalStateException("détail interne"));

        var r = run(null);

        assertThat(r.failures()).hasSize(3);
        assertThat(r.failures()).anyMatch(x -> x.startsWith("PROBE_DEPLOYMENTS (Prometheus a répondu HTTP 500"));
        assertThat(r.failures()).anyMatch(x -> x.startsWith("PROBE_ALERTS (Prometheus est injoignable"));
        assertThat(r.failures()).anyMatch(x -> x.startsWith("PROBE_TOOLS_READY (erreur inattendue (IllegalStateException)"));
        assertThat(r.queries().stream().filter(q -> q.status().equals("FAILED"))).hasSize(3).allMatch(q -> q.reason() != null && q.query() != null);
        assertThat(r.coverage().get(0).coverage()).isLessThan(1.0);
        assertThat(r.anyOk()).isTrue();                          // les autres sondes ont répondu
    }

    @Test
    void ifEveryProbeFailsNothingIsUsable() {
        when(client.promInstant(anyString())).thenThrow(new SupervisionClient.UnavailableException("Prometheus est injoignable", null));
        when(client.promAlerts()).thenThrow(new SupervisionClient.UnavailableException("Prometheus est injoignable", null));
        when(client.probeReady()).thenThrow(new SupervisionClient.UnavailableException("tout est injoignable", null));

        var r = run(null);

        assertThat(r.failures()).hasSize(8);
        assertThat(r.anyOk()).isFalse();
        assertThat(r.findings()).isEmpty();
    }

    @Test
    void aScopeOnlyKeepsItsOwnFindings() {
        ArrayNode down = JSON.createArrayNode();
        down.addAll(series("0", "job", "vm-node-exporters", "instance", "192.168.148.162:9100"));
        down.addAll(series("0", "job", "backend-direct", "instance", "10.0.0.5:8080"));
        when(client.promInstant(TelemetryStackProbe.TARGETS_DOWN_QL)).thenReturn(down);

        assertThat(run(EnumSet.of(ResourceScope.VM)).findings()).extracting(Finding::entity).containsExactly("k8s-lb2");
        assertThat(run(EnumSet.of(ResourceScope.APPLICATION)).findings()).extracting(Finding::scope).containsOnly("APPLICATION");
        assertThat(run(EnumSet.of(ResourceScope.CLUSTER)).findings()).isEmpty();
    }

    @Test
    void podNamesLoseTheirReplicaSuffix() {
        assertThat(TelemetryStackProbe.podBase("backend-6794f67dbc-2cpxz")).isEqualTo("backend");
        assertThat(TelemetryStackProbe.podBase("postgres-7")).isEqualTo("postgres-7");
        assertThat(TelemetryStackProbe.podBase("calico-node-xk2lq")).isEqualTo("calico-node");
    }
}
