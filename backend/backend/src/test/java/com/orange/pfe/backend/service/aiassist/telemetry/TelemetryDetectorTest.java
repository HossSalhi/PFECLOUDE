package com.orange.pfe.backend.service.aiassist.telemetry;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ArrayNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import com.orange.pfe.backend.model.team.ResourceScope;
import com.orange.pfe.backend.service.aiassist.telemetry.TelemetryModels.Detection;
import com.orange.pfe.backend.service.aiassist.telemetry.TelemetryModels.Finding;
import com.orange.pfe.backend.service.aiassist.telemetry.TelemetryModels.QueryTrace;
import com.orange.pfe.backend.service.supervision.SupervisionClient;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

import java.time.Instant;
import java.util.EnumSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.CompletionException;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyInt;
import static org.mockito.ArgumentMatchers.anyLong;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

/**
 * Détecteur avec de faux Prometheus et Loki. Règle vérifiée partout : une absence de MESURE (source en panne, couverture
 * inférieure à 80 %, machine ou source de logs silencieuse) ne produit JAMAIS « Aucune anomalie ».
 */
class TelemetryDetectorTest {

    private static final ObjectMapper JSON = new ObjectMapper();
    private static final Instant NOW = Instant.parse("2026-11-27T12:00:00Z");
    private static final long END = NOW.getEpochSecond();
    private static final String SHA = "a".repeat(64);
    private static final List<String> IPS = List.of("192.168.148.153", "192.168.148.155", "192.168.148.158", "192.168.148.160", "192.168.148.161", "192.168.148.162");
    private static final String PROM_DOWN = "Prometheus est injoignable";
    private static final String LOKI_DOWN = "Loki est injoignable";

    private SupervisionClient client;
    private TelemetryDetector detector;

    @BeforeEach
    void setUp() {
        client = mock(SupervisionClient.class);
        detector = new TelemetryDetector(client);
        healthyMetrics();
        healthyLogs();
    }

    // ── fabriques de données ─────────────────────────────────────────────────────────────────────────────────────

    /** Série minute par minute : {@code base} avant {@code spikeFrom}, {@code spike} ensuite ; seulement {@code windowPoints} mesures dans la fenêtre. */
    private static ObjectNode machine(String ip, long spikeFrom, double base, double spike, int windowPoints) {
        ObjectNode series = JSON.createObjectNode();
        series.putObject("metric").put("instance", ip + ":9100");
        ArrayNode values = series.putArray("values");
        long windowStart = END - 3600;
        for (long t = END - 7200; t < windowStart; t += 60) {                       // période de référence : toujours complète
            values.addArray().add(t).add(String.valueOf(t >= spikeFrom ? spike : base));
        }
        for (int i = 0; i < windowPoints; i++) {                                    // fenêtre : éventuellement incomplète
            long t = windowStart + i * 60L;
            values.addArray().add(t).add(String.valueOf(t >= spikeFrom ? spike : base));
        }
        return series;
    }

    private static ArrayNode hosts(java.util.function.IntFunction<ObjectNode> maker) {
        ArrayNode a = JSON.createArrayNode();
        for (int i = 0; i < IPS.size(); i++) {
            a.add(maker.apply(i));
        }
        return a;
    }

    private void metrics(String ql, ArrayNode result) {
        when(client.promRange(eq(ql), anyLong(), anyLong(), anyLong())).thenReturn(result);
    }

    private void healthyMetrics() {
        metrics(TelemetryDetector.CPU_QL, hosts(i -> machine(IPS.get(i), END + 1, 20, 20, 61)));
        metrics(TelemetryDetector.MEM_QL, hosts(i -> machine(IPS.get(i), END + 1, 45, 45, 61)));
        metrics(TelemetryDetector.DISK_QL, hosts(i -> machine(IPS.get(i), END + 1, 50, 50, 61)));
    }

    /** Une série Loki : trois points horaires (début de plage, début de fenêtre, fin). */
    private static ObjectNode lokiSeries(String label, String value, double c1, double c2, double c3) {
        ObjectNode s = JSON.createObjectNode();
        s.putObject("metric").put(label, value);
        ArrayNode v = s.putArray("values");
        v.addArray().add(END - 7200).add(String.valueOf(c1));
        v.addArray().add(END - 3600).add(String.valueOf(c2));
        v.addArray().add(END).add(String.valueOf(c3));
        return s;
    }

    private static ArrayNode arr(ObjectNode... nodes) {
        ArrayNode a = JSON.createArrayNode();
        for (ObjectNode n : nodes) {
            a.add(n);
        }
        return a;
    }

    private static ObjectNode pod(String namespace, String container, double c1, double c2, double c3) {
        return lokiSeries("filename", "/var/log/containers/" + container + "-5d8f_" + namespace + "_" + container + "-" + SHA + ".log", c1, c2, c3);
    }

    private void loki(String ql, ArrayNode result) {
        when(client.lokiRange(eq(ql), anyLong(), anyLong(), anyLong(), anyInt())).thenReturn(result);
    }

    private void healthyLogs() {
        loki(TelemetryDetector.LOG_HOSTS_QL, JSON.createArrayNode());
        loki(TelemetryDetector.LOG_PODS_QL, JSON.createArrayNode());
        loki(TelemetryDetector.LOG_ALL_HOSTS_QL, arr(lokiSeries("host", "master", 500, 500, 500), lokiSeries("host", "worker1", 500, 500, 500),
                lokiSeries("host", "worker2", 500, 500, 500)));
        loki(TelemetryDetector.LOG_ALL_PODS_QL, arr(pod("pfe", "backend", 300, 300, 300)));
    }

    private static QueryTrace query(Detection d, String id) {
        return d.trace().queries().stream().filter(q -> q.id().equals(id)).findFirst().orElseThrow();
    }

    private void promFails(String ql, RuntimeException e) {
        when(client.promRange(eq(ql), anyLong(), anyLong(), anyLong())).thenThrow(e);
    }

    private void lokiFails(String ql, RuntimeException e) {
        when(client.lokiRange(eq(ql), anyLong(), anyLong(), anyLong(), anyInt())).thenThrow(e);
    }

    private static SupervisionClient.UnavailableException down(String message) {
        return new SupervisionClient.UnavailableException(message, null);
    }

    // ── cas nominaux ─────────────────────────────────────────────────────────────────────────────────────────────

    @Test
    void onlyCompleteDataWithoutFindingsGivesNoAnomalyAndTheTraceKeepsEveryParameter() {
        Detection d = detector.detect(NOW, 60, null);

        assertThat(d.verdict()).isEqualTo(TelemetryModels.NO_ANOMALY);
        assertThat(d.dataQuality()).isEqualTo(TelemetryModels.DATA_COMPLETE);
        assertThat(d.findings()).isEmpty();
        assertThat(d.quality()).hasSize(2).allMatch(q -> q.available());

        assertThat(d.trace().rulesVersion()).isEqualTo(TelemetryModels.RULES_VERSION);
        assertThat(d.trace().windowStart()).isEqualTo(d.windowStart());
        assertThat(d.trace().windowEnd()).isEqualTo(d.windowEnd());
        assertThat(d.trace().baselineStart()).isEqualTo(d.windowStart().minusHours(1));
        assertThat(d.trace().baselineEnd()).isEqualTo(d.windowStart());
        assertThat(d.trace().metricsStepSeconds()).isEqualTo(60L);
        assertThat(d.trace().logsStepSeconds()).isEqualTo(3600L);
        assertThat(d.trace().thresholds()).containsKeys("cpu.high/critical", "coverage.complete.from", "logs.minErrorsPerHour");
        assertThat(d.trace().queries()).extracting(QueryTrace::id).containsExactly("PROM_CPU", "PROM_MEMORY", "PROM_DISK",
                "LOKI_ERRORS_HOSTS", "LOKI_ERRORS_PODS", "LOKI_LINES_HOSTS", "LOKI_LINES_PODS");
        assertThat(d.trace().queries()).allMatch(q -> q.status().equals("OK") && q.reason() == null && q.start() != null && q.end() != null && q.stepSeconds() != null);
        assertThat(query(d, "PROM_CPU").query()).isEqualTo(TelemetryDetector.CPU_QL);          // la requête EXACTE
        assertThat(query(d, "LOKI_ERRORS_PODS").query()).isEqualTo(TelemetryDetector.LOG_PODS_QL);
        assertThat(d.trace().coverage()).anyMatch(c -> c.entity().equals("k8s-lb2") && c.coverage() >= 0.8);
        assertThat(d.entities()).isGreaterThanOrEqualTo(6);
    }

    @Test
    void aSustainedCpuOverrunIsReportedWithItsEvidenceAndKeepsCompleteDataQuality() {
        metrics(TelemetryDetector.CPU_QL, hosts(i -> machine(IPS.get(i), i == 2 ? END - 2400 : END + 1, 20, 96, 61)));

        Detection d = detector.detect(NOW, 60, null);

        assertThat(d.verdict()).isEqualTo(TelemetryModels.ANOMALIES);
        assertThat(d.dataQuality()).isEqualTo(TelemetryModels.DATA_COMPLETE);
        assertThat(d.findings()).singleElement().satisfies(f -> {
            assertThat(f.entity()).isEqualTo("k8s-worker1");
            assertThat(f.signal()).isEqualTo("CPU");
            assertThat(f.severity()).isEqualTo("HIGH");
            assertThat(f.scope()).isEqualTo("VM");
            assertThat(f.evidence()).startsWith("PromQL : ").contains("node_cpu_seconds_total");
            assertThat(f.queryRef()).isEqualTo("PROM_CPU");
            assertThat(f.observed()).isEqualTo(96.0);
            assertThat(f.baseline()).isEqualTo(20.0);
        });
    }

    @Test
    void aLogErrorSpikeIsDetectedWithItsQueryReferenceAndOrderedAfterHigherSeverities() {
        metrics(TelemetryDetector.CPU_QL, hosts(i -> machine(IPS.get(i), i == 2 ? END - 2400 : END + 1, 20, 96, 61)));
        loki(TelemetryDetector.LOG_PODS_QL, arr(pod("pfe", "backend", 2, 3, 60)));

        Detection d = detector.detect(NOW, 60, null);

        assertThat(d.findings()).extracting(Finding::severity).containsExactly("HIGH", "MEDIUM");
        Finding logs = d.findings().get(1);
        assertThat(logs.entity()).isEqualTo("pfe/backend");
        assertThat(logs.scope()).isEqualTo("APPLICATION");
        assertThat(logs.rule()).isEqualTo("ERROR_SPIKE");
        assertThat(logs.queryRef()).isEqualTo("LOKI_ERRORS_PODS");
        assertThat(logs.evidence()).startsWith("LogQL : ");
    }

    // ── couverture : jamais de vert sans mesure ──────────────────────────────────────────────────────────────────

    @Test
    void aHostBelowEightyPercentCoverageMakesTheVerdictPartialNeverAllClear() {
        metrics(TelemetryDetector.CPU_QL, hosts(i -> machine(IPS.get(i), END + 1, 20, 20, i == 1 ? 36 : 61)));        // k8s-worker2 : 36/60 = 60 %

        Detection d = detector.detect(NOW, 60, null);

        assertThat(d.findings()).isEmpty();
        assertThat(d.verdict()).isEqualTo(TelemetryModels.PARTIAL_COVERAGE);
        assertThat(d.dataQuality()).isEqualTo(TelemetryModels.DATA_PARTIAL);
        assertThat(d.quality().get(0).note()).contains("Couverture inférieure à 80 %").contains("k8s-worker2 (CPU 60 %)").contains("jamais comptées comme anomalie");
        assertThat(d.trace().coverage()).anyMatch(c -> c.entity().equals("k8s-worker2") && c.signal().equals("CPU") && c.coverage() == 0.6);
    }

    @Test
    void justUnderEightyPercentIsPartialAndExactlyEightyPercentIsComplete() {
        metrics(TelemetryDetector.CPU_QL, hosts(i -> machine(IPS.get(i), END + 1, 20, 20, i == 0 ? 47 : 61)));        // 47/60 = 78 %
        assertThat(detector.detect(NOW, 60, null).verdict()).isEqualTo(TelemetryModels.PARTIAL_COVERAGE);

        metrics(TelemetryDetector.CPU_QL, hosts(i -> machine(IPS.get(i), END + 1, 20, 20, i == 0 ? 48 : 61)));        // 48/60 = 80 %
        assertThat(detector.detect(NOW, 60, null).verdict()).isEqualTo(TelemetryModels.NO_ANOMALY);
    }

    @Test
    void aKnownMachineWithoutAnyMeasurementIsNotMistakenForAHealthyOne() {
        ArrayNode fiveHosts = JSON.createArrayNode();
        for (int i = 0; i < 5; i++) {
            fiveHosts.add(machine(IPS.get(i), END + 1, 20, 20, 61));                                              // k8s-lb2 (.162) ne répond plus
        }
        metrics(TelemetryDetector.CPU_QL, fiveHosts);

        Detection d = detector.detect(NOW, 60, null);

        assertThat(d.verdict()).isEqualTo(TelemetryModels.PARTIAL_COVERAGE);
        assertThat(d.quality().get(0).note()).contains("Aucune mesure reçue").contains("absence de mesure, pas absence d'anomalie").contains("k8s-lb2 (CPU)");
        assertThat(d.trace().coverage()).anyMatch(c -> c.entity().equals("k8s-lb2") && c.signal().equals("CPU") && c.coverage() == 0.0 && c.note().contains("aucune mesure"));
    }

    @Test
    void findingsWithPartialCoverageAreKeptButCarryAPartialDataQualityWarning() {
        metrics(TelemetryDetector.CPU_QL, hosts(i -> machine(IPS.get(i), i == 2 ? END - 2400 : END + 1, 20, 96, i == 1 ? 36 : 61)));

        Detection d = detector.detect(NOW, 60, null);

        assertThat(d.verdict()).isEqualTo(TelemetryModels.ANOMALIES);
        assertThat(d.dataQuality()).isEqualTo(TelemetryModels.DATA_PARTIAL);
        assertThat(d.findings()).hasSize(1);
    }

    @Test
    void aHostWithAlmostNoMeasurementIsNotAnalysedYetNeverYieldsAGreenVerdict() {
        metrics(TelemetryDetector.CPU_QL, hosts(i -> machine(IPS.get(i), END - 3000, 20, 99, i == 3 ? 6 : 61)));      // 6/60 = 10 %, valeurs à 99 %

        Detection d = detector.detect(NOW, 60, null);

        assertThat(d.findings()).noneMatch(f -> f.entity().equals("k8s-monitoring"));                                // aucune conclusion tirée de 6 mesures
        assertThat(d.quality().get(0).note()).contains("k8s-monitoring (CPU 10 %)");
        assertThat(d.dataQuality()).isNotEqualTo(TelemetryModels.DATA_COMPLETE);
    }

    @Test
    void aLogSourceThatWentSilentCannotBeMistakenForAQuietOne() {
        loki(TelemetryDetector.LOG_ALL_HOSTS_QL, arr(lokiSeries("host", "master", 500, 500, 500), lokiSeries("host", "worker1", 500, 500, 0),
                lokiSeries("host", "worker2", 500, 500, 500)));

        Detection d = detector.detect(NOW, 60, null);

        assertThat(d.findings()).isEmpty();
        assertThat(d.verdict()).isEqualTo(TelemetryModels.PARTIAL_COVERAGE);
        assertThat(d.quality().get(1).note()).contains("silencieuse(s)").contains("hôte worker1");
        assertThat(d.trace().coverage()).anyMatch(c -> c.entity().equals("hôte worker1") && c.coverage() == 0.0);
    }

    @Test
    void quietContainersDoNotDegradeTheCoverageOfAnOtherwiseCompleteAnalysis() {
        loki(TelemetryDetector.LOG_ALL_PODS_QL, arr(pod("pfe", "backend", 300, 300, 300), pod("pfe", "postgres-backup", 2, 2, 0)));   // tâche planifiée terminée

        Detection d = detector.detect(NOW, 60, null);

        assertThat(d.verdict()).isEqualTo(TelemetryModels.NO_ANOMALY);
        assertThat(d.trace().coverage()).noneMatch(c -> c.entity().contains("postgres-backup"));
    }

    @Test
    void noLogLineAtAllInTheScopeIsNeverReportedAsNoAnomaly() {
        loki(TelemetryDetector.LOG_ALL_HOSTS_QL, JSON.createArrayNode());
        loki(TelemetryDetector.LOG_ALL_PODS_QL, JSON.createArrayNode());

        Detection d = detector.detect(NOW, 60, EnumSet.of(ResourceScope.APPLICATION));

        assertThat(d.verdict()).isEqualTo(TelemetryModels.INSUFFICIENT_DATA);
        assertThat(d.quality().get(0).note()).contains("Aucune ligne de log reçue").contains("pas absence d'anomalie");
    }

    @Test
    void ifIngestionCannotBeCheckedTheAnalysisCannotBeAllClear() {
        lokiFails(TelemetryDetector.LOG_ALL_HOSTS_QL, down(LOKI_DOWN));
        lokiFails(TelemetryDetector.LOG_ALL_PODS_QL, down(LOKI_DOWN));

        Detection d = detector.detect(NOW, 60, null);

        assertThat(d.verdict()).isEqualTo(TelemetryModels.PARTIAL_COVERAGE);
        assertThat(d.dataQuality()).isEqualTo(TelemetryModels.DATA_PARTIAL);
    }

    // ── chaque panne, individuellement et simultanément ──────────────────────────────────────────────────────────

    private static final Map<String, String> PROM = Map.of("PROM_CPU", TelemetryDetector.CPU_QL, "PROM_MEMORY", TelemetryDetector.MEM_QL, "PROM_DISK", TelemetryDetector.DISK_QL);
    private static final Map<String, String> LOKI = Map.of("LOKI_ERRORS_HOSTS", TelemetryDetector.LOG_HOSTS_QL, "LOKI_ERRORS_PODS", TelemetryDetector.LOG_PODS_QL,
            "LOKI_LINES_HOSTS", TelemetryDetector.LOG_ALL_HOSTS_QL, "LOKI_LINES_PODS", TelemetryDetector.LOG_ALL_PODS_QL);

    @Test
    void eachPrometheusQueryFailingAloneIsNamedWithItsReasonAndNeverAllClear() {
        for (Map.Entry<String, String> e : PROM.entrySet()) {
            setUp();
            promFails(e.getValue(), down(PROM_DOWN));

            Detection d = detector.detect(NOW, 60, null);

            String signal = e.getKey().substring("PROM_".length());
            QueryTrace failed = query(d, e.getKey());
            assertThat(failed.status()).as(e.getKey()).isEqualTo("FAILED");
            assertThat(failed.reason()).isEqualTo(PROM_DOWN);
            assertThat(failed.query()).isEqualTo(e.getValue());
            assertThat(d.trace().queries().stream().filter(q -> q.status().equals("FAILED"))).hasSize(1);
            assertThat(d.quality().get(0).note()).contains("Requête(s) en échec : " + signal + " (" + PROM_DOWN + ")");
            assertThat(d.verdict()).as(e.getKey()).isEqualTo(TelemetryModels.PARTIAL_COVERAGE);
            assertThat(d.dataQuality()).isEqualTo(TelemetryModels.DATA_PARTIAL);
            assertThat(d.quality().get(0).available()).isTrue();               // les deux autres requêtes ont répondu
        }
    }

    @Test
    void allPrometheusQueriesFailingTogetherLeavesLogsUsableButNeverAllClear() {
        PROM.values().forEach(ql -> promFails(ql, down(PROM_DOWN)));

        Detection d = detector.detect(NOW, 60, null);

        assertThat(d.trace().queries().stream().filter(q -> q.source().equals("Prometheus") && q.status().equals("FAILED"))).hasSize(3);
        assertThat(d.quality().get(0).available()).isFalse();
        assertThat(d.quality().get(0).note()).contains("CPU (" + PROM_DOWN + ")").contains("MEMORY (" + PROM_DOWN + ")").contains("DISK (" + PROM_DOWN + ")");
        assertThat(d.verdict()).isEqualTo(TelemetryModels.PARTIAL_COVERAGE);
        assertThat(d.trace().coverage()).anyMatch(c -> c.source().equals("Prometheus") && c.coverage() == 0.0 && c.note().contains("requête en échec"));
    }

    @Test
    void eachLokiQueryFailingAloneIsNamedWithItsReasonAndNeverAllClear() {
        for (Map.Entry<String, String> e : LOKI.entrySet()) {
            setUp();
            lokiFails(e.getValue(), down(LOKI_DOWN));

            Detection d = detector.detect(NOW, 60, null);

            QueryTrace failed = query(d, e.getKey());
            assertThat(failed.status()).as(e.getKey()).isEqualTo("FAILED");
            assertThat(failed.reason()).isEqualTo(LOKI_DOWN);
            assertThat(failed.query()).isEqualTo(e.getValue());
            assertThat(d.trace().queries().stream().filter(q -> q.status().equals("FAILED"))).hasSize(1);
            assertThat(d.quality().get(1).note()).contains("Requête(s) en échec : " + e.getKey() + " (" + LOKI_DOWN + ")");
            assertThat(d.verdict()).as(e.getKey()).isEqualTo(TelemetryModels.PARTIAL_COVERAGE);
        }
    }

    @Test
    void allLokiQueriesFailingTogetherLeavesMetricsUsableButNeverAllClear() {
        LOKI.values().forEach(ql -> lokiFails(ql, down(LOKI_DOWN)));

        Detection d = detector.detect(NOW, 60, null);

        assertThat(d.trace().queries().stream().filter(q -> q.source().equals("Loki") && q.status().equals("FAILED"))).hasSize(4);
        assertThat(d.quality().get(1).available()).isFalse();
        assertThat(d.verdict()).isEqualTo(TelemetryModels.PARTIAL_COVERAGE);
        assertThat(d.dataQuality()).isEqualTo(TelemetryModels.DATA_PARTIAL);
    }

    @Test
    void aFailingSourceDoesNotHideTheAnomaliesFoundByTheOtherOne() {
        LOKI.values().forEach(ql -> lokiFails(ql, down(LOKI_DOWN)));
        metrics(TelemetryDetector.CPU_QL, hosts(i -> machine(IPS.get(i), i == 2 ? END - 2400 : END + 1, 20, 96, 61)));

        Detection d = detector.detect(NOW, 60, null);

        assertThat(d.verdict()).isEqualTo(TelemetryModels.ANOMALIES);
        assertThat(d.dataQuality()).isEqualTo(TelemetryModels.DATA_PARTIAL);          // avertissement : Loki n'a pas pu être analysé
    }

    @Test
    void bothSourcesCompletelyDownMeansInsufficientDataWithSevenNamedFailures() {
        PROM.values().forEach(ql -> promFails(ql, down(PROM_DOWN)));
        LOKI.values().forEach(ql -> lokiFails(ql, down(LOKI_DOWN)));

        Detection d = detector.detect(NOW, 60, null);

        assertThat(d.verdict()).isEqualTo(TelemetryModels.INSUFFICIENT_DATA);
        assertThat(d.dataQuality()).isEqualTo(TelemetryModels.DATA_INSUFFICIENT);
        assertThat(d.findings()).isEmpty();
        assertThat(d.quality()).hasSize(2).noneMatch(q -> q.available());
        assertThat(d.trace().queries()).hasSize(7).allMatch(q -> q.status().equals("FAILED") && q.reason() != null && q.query() != null);
    }

    @Test
    void anUnexpectedExceptionIsReportedWithItsTypeButWithoutInternalDetail() {
        promFails(TelemetryDetector.DISK_QL, new IllegalStateException("détail interne secret"));

        Detection d = detector.detect(NOW, 60, null);

        assertThat(query(d, "PROM_DISK").reason()).isEqualTo("erreur inattendue (IllegalStateException)");
        assertThat(d.quality().get(0).note()).doesNotContain("détail interne secret");
        assertThat(TelemetryDetector.reasonOf(new CompletionException(down("Prometheus a répondu HTTP 503")))).isEqualTo("Prometheus a répondu HTTP 503");
    }

    // ── périmètres et fenêtres ───────────────────────────────────────────────────────────────────────────────────

    @Test
    void anApplicationScopeNeverLooksAtMachinesNorQueriesPrometheus() {
        metrics(TelemetryDetector.CPU_QL, hosts(i -> machine(IPS.get(i), END - 2400, 20, 96, 61)));
        loki(TelemetryDetector.LOG_PODS_QL, arr(pod("pfe", "backend", 2, 3, 60)));

        Detection d = detector.detect(NOW, 60, EnumSet.of(ResourceScope.APPLICATION));

        assertThat(d.findings()).extracting(Finding::scope).containsOnly("APPLICATION");
        assertThat(d.verdict()).isEqualTo(TelemetryModels.ANOMALIES);
        verify(client, never()).promRange(any(), anyLong(), anyLong(), anyLong());
        assertThat(d.trace().queries()).noneMatch(q -> q.source().equals("Prometheus"));
        assertThat(d.quality()).hasSize(1);
    }

    @Test
    void anApplicationScopeWithCompleteLogsAndNoErrorIsAllClearOnItsOwnPerimeter() {
        Detection d = detector.detect(NOW, 60, EnumSet.of(ResourceScope.APPLICATION));

        assertThat(d.verdict()).isEqualTo(TelemetryModels.NO_ANOMALY);
        assertThat(d.dataQuality()).isEqualTo(TelemetryModels.DATA_COMPLETE);
    }

    @Test
    void containersAreClassifiedByNamespaceClusterServiceOrApplication() {
        assertThat(TelemetryDetector.scopeOfApp("kube-system/kube-apiserver")).isEqualTo(ResourceScope.CLUSTER);
        assertThat(TelemetryDetector.scopeOfApp("infra/loki")).isEqualTo(ResourceScope.SERVICE);
        assertThat(TelemetryDetector.scopeOfApp("pfe/prometheus")).isEqualTo(ResourceScope.SERVICE);
        assertThat(TelemetryDetector.scopeOfApp("pfe/backend")).isEqualTo(ResourceScope.APPLICATION);
        assertThat(TelemetryDetector.scopeOfApp("sans-slash")).isEqualTo(ResourceScope.APPLICATION);
    }

    @Test
    void aScopeOnlySeesItsOwnContainers() {
        loki(TelemetryDetector.LOG_PODS_QL, arr(pod("kube-system", "etcd", 1, 1, 80)));
        loki(TelemetryDetector.LOG_ALL_PODS_QL, arr(pod("kube-system", "etcd", 300, 300, 300)));

        assertThat(detector.detect(NOW, 60, EnumSet.of(ResourceScope.CLUSTER)).findings()).singleElement()
                .satisfies(f -> assertThat(f.scope()).isEqualTo("CLUSTER"));
        assertThat(detector.detect(NOW, 60, EnumSet.of(ResourceScope.APPLICATION)).findings()).isEmpty();
        assertThat(detector.detect(NOW, 60, Set.of(ResourceScope.SERVICE)).findings()).isEmpty();
    }

    @Test
    void containerAndHostNamesAreReadableAndStable() {
        assertThat(TelemetryDetector.hostName("192.168.148.153:9100")).isEqualTo("k8s-master");
        assertThat(TelemetryDetector.hostName("10.0.0.9:9100")).isEqualTo("10.0.0.9");
        assertThat(TelemetryDetector.appOf("/var/log/containers/backend-5d8f_pfe_backend-" + SHA + ".log")).isEqualTo("pfe/backend");
        assertThat(TelemetryDetector.appOf("")).isEqualTo("inconnu");
    }

    @Test
    void aSevenDayWindowHasNoBaselineAndLogsAreCappedAtTwentyFourHours() {
        Detection d = detector.detect(NOW, 7 * 24 * 60, null);

        verify(client).promRange(eq(TelemetryDetector.CPU_QL), eq(END - 7 * 86400L), eq(END), eq(2520L));
        assertThat(d.trace().baselineStart()).isNull();
        assertThat(d.trace().metricsStepSeconds()).isEqualTo(2520L);
        assertThat(d.quality().get(0).note()).contains("Pas de période de référence").contains("7 jours");
        assertThat(d.quality().get(1).note()).contains("dernières 24 h").contains("limite de performance");
        assertThat(d.trace().logsWindowStart()).isEqualTo(d.windowEnd().minusHours(24));
    }

    @Test
    void aShortWindowUsesItsOwnPreviousPeriodAsBaselineAndOneHourOfLogs() {
        Detection d = detector.detect(NOW, 30, null);

        verify(client).promRange(eq(TelemetryDetector.CPU_QL), eq(END - 3600), eq(END), eq(60L));      // 30 min de fenêtre + 30 min de référence
        assertThat(d.quality().get(0).note()).contains("Référence : 30 min");
        assertThat(d.quality().get(1).note()).contains("au moins 1 h");
    }
}
