package com.orange.pfe.backend.service.supervision;

import com.orange.pfe.backend.config.InfraHealthProperties;
import com.orange.pfe.backend.dto.supervision.SupervisionDtos.Correlation;
import com.orange.pfe.backend.dto.supervision.SupervisionDtos.HostsOverview;
import com.orange.pfe.backend.dto.supervision.SupervisionDtos.LogStats;
import com.orange.pfe.backend.dto.supervision.SupervisionDtos.Metric;
import com.orange.pfe.backend.dto.supervision.SupervisionDtos.SeriesResponse;
import com.orange.pfe.backend.dto.supervision.SupervisionDtos.TopErrors;
import com.orange.pfe.backend.model.core.Incident;
import com.orange.pfe.backend.model.enums.IncidentSeverity;
import com.orange.pfe.backend.model.enums.IncidentStatus;
import com.orange.pfe.backend.repository.SupervisionIncidentReadRepository;
import com.sun.net.httpserver.HttpServer;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.test.util.ReflectionTestUtils;

import java.io.IOException;
import java.net.InetSocketAddress;
import java.net.URLDecoder;
import java.nio.charset.StandardCharsets;
import java.time.LocalDateTime;
import java.util.ArrayList;
import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

/** Supervision contre de FAUX Prometheus et Loki (vrai HTTP local) : aucune dépendance au réseau réel. */
class SupervisionServiceTest {

    private HttpServer server;
    private final List<String> received = new ArrayList<>();
    private SupervisionIncidentReadRepository incidents;
    private SupervisionService service;

    private static String vector(String... samples) {
        return "{\"status\":\"success\",\"data\":{\"resultType\":\"vector\",\"result\":[" + String.join(",", samples) + "]}}";
    }

    private static String sample(String labels, String value) {
        return "{\"metric\":{" + labels + "},\"value\":[1795745103.6,\"" + value + "\"]}";
    }

    private String respond(String path, String q, String raw) {
        if (path.equals("/api/v1/query")) {
            if (q.equals("up")) {
                return vector(sample("\"job\":\"kubelet\",\"instance\":\"192.168.148.153:10250\"", "1"),
                        sample("\"job\":\"kubelet\",\"instance\":\"192.168.148.155:10250\"", "0"),
                        sample("\"job\":\"etcd\",\"instance\":\"192.168.148.153:2381\"", "1"));
            }
            if (q.startsWith("up{")) {
                return vector(sample("\"instance\":\"192.168.148.153:9100\"", "1"), sample("\"instance\":\"192.168.148.155:9100\"", "0"));
            }
            if (q.contains("node_cpu_seconds_total")) {
                return vector(sample("\"instance\":\"192.168.148.153:9100\"", "16.5"), sample("\"instance\":\"192.168.148.155:9100\"", "88.2"));
            }
            if (q.contains("node_memory")) {
                return vector(sample("\"instance\":\"192.168.148.153:9100\"", "68.0"));
            }
            if (q.contains("node_filesystem")) {
                return vector(sample("\"instance\":\"192.168.148.153:9100\"", "67.3"));
            }
        }
        if (path.equals("/api/v1/query_range")) {
            String values = "[[1795745000,\"10\"],[1795745060,\"20\"],[1795745120,\"NaN\"]]";
            return "{\"status\":\"success\",\"data\":{\"resultType\":\"matrix\",\"result\":["
                    + "{\"metric\":{\"instance\":\"192.168.148.153:9100\"},\"values\":" + values + "}]}}";
        }
        if (path.equals("/loki/api/v1/query")) {
            if (q.startsWith("sum by (host)")) {
                String n = q.contains("level=(error") ? "7" : q.contains("warn") ? "3" : "100";
                return vector(sample("\"host\":\"worker1\"", n));
            }
            if (q.startsWith("sum by (filename)")) {
                String n = q.contains("level=(error") ? "5" : q.contains("warn") ? "2" : "50";
                return vector(sample("\"filename\":\"/var/log/pods/helios_backend-abc_uid1/backend/0.log\"", n),
                        sample("\"filename\":\"/var/log/pods/helios_backend-def_uid2/backend/0.log\"", n));
            }
            String n = q.contains("level=(error") ? "40" : q.contains("warn") ? "10" : "1000";
            return vector(sample("", n));
        }
        if (path.equals("/loki/api/v1/query_range")) {
            if (raw.contains("limit=1000")) {
                String line1 = "2026-11-27T02:05:02+00:00 k8s-w1 kubelet[812]: connection refused to 10.0.0.1:6443 after 30s";
                String line2 = "2026-11-27T02:06:02+00:00 k8s-w1 kubelet[812]: connection refused to 10.0.0.2:6443 after 45s";
                String line3 = "2026-11-27T02:07:02+00:00 k8s-w2 kernel: Out of memory: Killed process 4242";
                return "{\"status\":\"success\",\"data\":{\"resultType\":\"streams\",\"result\":["
                        + "{\"stream\":{\"host\":\"worker1\"},\"values\":[[\"1\",\"" + line1 + "\"],[\"2\",\"" + line2 + "\"]]},"
                        + "{\"stream\":{\"host\":\"worker2\"},\"values\":[[\"3\",\"" + line3 + "\"]]}]}}";
            }
            long now = System.currentTimeMillis() / 1000;
            return "{\"status\":\"success\",\"data\":{\"resultType\":\"matrix\",\"result\":[{\"metric\":{},\"values\":["
                    + "[" + (now - 60) + ",\"5\"],[" + now + ",\"9\"]]}]}}";
        }
        return "{\"status\":\"error\"}";
    }

    @BeforeEach
    void start() throws IOException {
        server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        server.createContext("/", ex -> {
            String raw = ex.getRequestURI().getRawQuery() == null ? "" : ex.getRequestURI().getRawQuery();
            String q = "";
            for (String kv : raw.split("&")) {
                if (kv.startsWith("query=")) {
                    q = URLDecoder.decode(kv.substring(6), StandardCharsets.UTF_8);
                }
            }
            synchronized (received) {
                received.add(ex.getRequestMethod() + " " + ex.getRequestURI().getPath() + " " + q);
            }
            byte[] body = respond(ex.getRequestURI().getPath(), q, raw).getBytes(StandardCharsets.UTF_8);
            ex.getResponseHeaders().add("Content-Type", "application/json");
            ex.sendResponseHeaders(200, body.length);
            ex.getResponseBody().write(body);
            ex.close();
        });
        server.start();
        String base = "http://127.0.0.1:" + server.getAddress().getPort();
        InfraHealthProperties props = new InfraHealthProperties();
        props.setPrometheusUrl(base);
        props.setLokiUrl(base);
        incidents = mock(SupervisionIncidentReadRepository.class);
        service = new SupervisionService(new SupervisionClient(props), incidents);
    }

    @AfterEach
    void stop() {
        server.stop(0);
    }

    @Test
    void hostsGivesCpuMemoryDiskAndTargetsWithFriendlyNames() {
        HostsOverview h = service.hosts();

        assertThat(h.available()).isTrue();
        assertThat(h.hosts()).extracting(x -> x.name()).containsExactly("k8s-master", "k8s-worker2");
        assertThat(h.hosts().get(0).cpuPercent()).isEqualTo(16.5);
        assertThat(h.hosts().get(0).memoryPercent()).isEqualTo(68.0);
        assertThat(h.hosts().get(0).diskPercent()).isEqualTo(67.3);
        assertThat(h.hosts().get(1).up()).isFalse();
        assertThat(h.targets()).extracting(t -> t.job() + ":" + t.up() + "/" + t.total()).containsExactly("etcd:1/1", "kubelet:1/2");
    }

    @Test
    void seriesParsesPointsAndDropsNaN() {
        SeriesResponse s = service.series(Metric.CPU, 60);

        assertThat(s.available()).isTrue();
        assertThat(s.unit()).isEqualTo("%");
        assertThat(s.lines()).hasSize(1);
        assertThat(s.lines().get(0).name()).isEqualTo("k8s-master");
        assertThat(s.lines().get(0).points()).extracting(p -> p.v()).containsExactly(10.0, 20.0);
        assertThat(s.stepSeconds()).isEqualTo(30);
    }

    @Test
    void theRequestedPeriodIsClampedAndTheStepIsBounded() {
        assertThat(SupervisionService.clamp(1)).isEqualTo(5);
        assertThat(SupervisionService.clamp(99999)).isEqualTo(1440);
        assertThat(SupervisionService.clampLogs(99999)).isEqualTo(180);   // Loki : plafonné à 3 h
        assertThat(SupervisionService.clampLogs(60)).isEqualTo(60);
        assertThat(SupervisionService.stepFor(5)).isEqualTo(15);
        assertThat(SupervisionService.stepFor(1440)).isEqualTo(600);
    }

    @Test
    void logStatsCountsLevelsPerHostAndPerApplication() {
        LogStats s = service.logStats(60);

        assertThat(s.available()).isTrue();
        assertThat(s.total()).isEqualTo(1000);
        assertThat(s.errors()).isEqualTo(40);
        assertThat(s.warnings()).isEqualTo(10);
        assertThat(s.byHost()).singleElement().satisfies(g -> {
            assertThat(g.key()).isEqualTo("worker1");
            assertThat(g.total()).isEqualTo(100);
            assertThat(g.errors()).isEqualTo(7);
            assertThat(g.warnings()).isEqualTo(3);
        });
        // les deux pods du même conteneur sont regroupés sous « namespace/conteneur »
        assertThat(s.byApplication()).singleElement().satisfies(g -> {
            assertThat(g.key()).isEqualTo("helios/backend");
            assertThat(g.errors()).isEqualTo(10);
        });
    }

    @Test
    void topErrorsGroupsLinesThatDifferOnlyByNumbersAndAddsTheMeaning() {
        TopErrors t = service.topErrors(60, 10);

        assertThat(t.available()).isTrue();
        assertThat(t.patterns()).hasSize(2);
        var first = t.patterns().get(0);
        assertThat(first.count()).isEqualTo(2);
        assertThat(first.pattern()).contains("connection refused to <ip> after <n>");
        assertThat(first.category()).isEqualTo("RESEAU");
        assertThat(first.host()).isEqualTo("worker1");
        assertThat(first.meaning()).isNotBlank();
        assertThat(t.patterns().get(1).category()).isEqualTo("MEMOIRE");
    }

    @Test
    void onlyReadRequestsAreSentWithFixedQueryTemplates() {
        service.hosts();
        service.logStats(30);
        service.topErrors(30, 5);

        synchronized (received) {
            assertThat(received).isNotEmpty().allMatch(r -> r.startsWith("GET "));
            assertThat(received).noneMatch(r -> r.contains("delete") || r.contains("/admin/"));
        }
    }

    @Test
    void resultsAreCachedBrieflySoSeveralViewersDoNotMultiplyQueries() {
        service.hosts();
        int after = received.size();
        service.hosts();

        assertThat(received).hasSize(after);
    }

    @Test
    void correlationMarksEachIncidentWithTheErrorLogsAroundIt() {
        Incident i = new Incident();
        i.setTitle("CPU");
        i.setApp("worker1");
        i.setIncidentType("NODE_HIGH_CPU");
        i.setSeverity(IncidentSeverity.HIGH);
        i.setStatus(IncidentStatus.OPEN);
        ReflectionTestUtils.setField(i, "id", 77L);
        ReflectionTestUtils.setField(i, "createdAt", LocalDateTime.now().minusSeconds(30));
        when(incidents.findByCreatedAtAfterOrderByCreatedAtAsc(org.mockito.ArgumentMatchers.any())).thenReturn(List.of(i));

        Correlation c = service.correlation(60);

        assertThat(c.available()).isTrue();
        assertThat(c.errorSeries()).hasSize(2);
        assertThat(c.cpuSeries()).isNotEmpty();
        assertThat(c.incidents()).singleElement().satisfies(m -> {
            assertThat(m.id()).isEqualTo(77L);
            assertThat(m.type()).isEqualTo("NODE_HIGH_CPU");
            assertThat(m.errorLogsAround()).isEqualTo(14);   // 5 + 9 dans la fenêtre de ±5 min
        });
    }

    @Test
    void whenTheToolsAreDownEverySectionAnswersUnavailableInsteadOfFailing() {
        server.stop(0);
        when(incidents.findByCreatedAtAfterOrderByCreatedAtAsc(org.mockito.ArgumentMatchers.any())).thenReturn(List.of());

        assertThat(service.hosts().available()).isFalse();
        assertThat(service.hosts().error()).contains("injoignable");
        assertThat(service.series(Metric.MEMORY, 60).available()).isFalse();
        assertThat(service.logStats(60).available()).isFalse();
        assertThat(service.topErrors(60, 5).available()).isFalse();
        assertThat(service.correlation(60).available()).isFalse();
    }

    @Test
    void normalizeRemovesTimestampsIdsAndNumbers() {
        assertThat(SupervisionService.normalize("2026-11-27T02:05:02+00:00 host app[812]: took 30ms for 10.1.2.3:80 id 3f2a9c1d-1111-2222-3333-444455556666"))
                .isEqualTo("host app[<pid>]: took <n> for <ip> id <id>");
    }

    @Test
    void appNameIsDerivedFromThePodLogFileName() {
        assertThat(SupervisionService.appOf("/var/log/pods/cnpg-system_cnpg-abc_1234/manager/0.log")).isEqualTo("cnpg-system/manager");
        assertThat(SupervisionService.appOf("/var/log/syslog")).isEqualTo("/var/log/syslog");
        assertThat(SupervisionService.appOf("/var/log/containers/backend-856754768d-lvmr8_pfe_backend-68e25b8ecb824f989ce1d1674cf6440b2f16bb965ca37c73ef011ec621f28dde.log"))
                .isEqualTo("pfe/backend");
        assertThat(SupervisionService.appOf("/var/log/containers/etcd-k8s-master_kube-system_etcd-021776e8a61835f2bf4dd1a249f1b5a2130b481db5eafe1e7a30066facf94df8.log"))
                .isEqualTo("kube-system/etcd");
    }

    @Test
    void klogLinesThatDifferOnlyByTheirLevelLetterAndDateAreGrouped() {
        assertThat(SupervisionService.normalize("host ksm[12]: E1127 02:05:01.123 reflector.go:1] forbidden"))
                .isEqualTo(SupervisionService.normalize("host ksm[13]: W1128 03:06:02.456 reflector.go:9] forbidden"));
    }
}
