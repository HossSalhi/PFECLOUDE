package com.orange.pfe.backend.service.supervision;

import com.orange.pfe.backend.config.InfraHealthProperties;
import com.orange.pfe.backend.dto.supervision.SupervisionDtos.LogLines;
import com.orange.pfe.backend.dto.supervision.SupervisionDtos.LogSources;
import com.sun.net.httpserver.HttpServer;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

import java.io.IOException;
import java.net.InetSocketAddress;
import java.net.URLDecoder;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

/** Visualiseur de logs contre un FAUX Loki : sources, filtres, échappement, masquage des secrets, bornes, panne. */
class LogViewerServiceTest {

    private HttpServer server;
    private final List<String> received = new ArrayList<>();
    private volatile boolean down;
    private LogViewerService service;

    private static String line(long secondsAgo, String text) {
        long ns = (System.currentTimeMillis() / 1000 - secondsAgo) * 1_000_000_000L;
        return "[\"" + ns + "\",\"" + text.replace("\\", "\\\\").replace("\"", "\\\"") + "\"]";
    }

    @BeforeEach
    void start() throws IOException {
        down = false;
        server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        server.createContext("/", ex -> {
            String q = ex.getRequestURI().getRawQuery() == null ? "" : URLDecoder.decode(ex.getRequestURI().getRawQuery(), StandardCharsets.UTF_8);
            String path = ex.getRequestURI().getPath();
            synchronized (received) {
                received.add(path + "?" + q);
            }
            String body;
            int status = 200;
            if (down) {
                status = 503;
                body = "";
            } else if (path.equals("/loki/api/v1/label/host/values")) {
                body = "{\"status\":\"success\",\"data\":[\"master\",\"worker1\",\"k8s-monitoring\",\"evil\\\"host\",\"inconnue\"]}";
            } else if (path.equals("/loki/api/v1/label/filename/values")) {
                body = "{\"status\":\"success\",\"data\":[\"/var/log/containers/backend-856754768d-lvmr8_pfe_backend-"
                        + "68e25b8ecb824f989ce1d1674cf6440b2f16bb965ca37c73ef011ec621f28dde.log\",\"/var/log/syslog\","
                        + "\"/var/log/containers/etcd-k8s-master_kube-system_etcd-021776e8a61835f2bf4dd1a249f1b5a2130b481db5eafe1e7a30066facf94df8.log\"]}";
            } else if (path.equals("/loki/api/v1/query_range")) {
                body = "{\"status\":\"success\",\"data\":{\"resultType\":\"streams\",\"result\":[{\"stream\":{},\"values\":["
                        + line(30, "app: tout va bien") + "," + line(20, "kubelet: connection refused to 10.0.0.1") + ","
                        + line(10, "api: Authorization: Bearer abcdef123456 appelé") + "," + line(5, "level=warn msg=lent password=hunter2secret")
                        + "]}]}}";
            } else {
                status = 404;
                body = "{}";
            }
            byte[] out = body.getBytes(StandardCharsets.UTF_8);
            ex.sendResponseHeaders(status, out.length == 0 ? -1 : out.length);
            if (out.length > 0) {
                ex.getResponseBody().write(out);
            }
            ex.close();
        });
        server.start();
        InfraHealthProperties props = new InfraHealthProperties();
        props.setLokiUrl("http://127.0.0.1:" + server.getAddress().getPort());
        service = new LogViewerService(new SupervisionClient(props));
    }

    @AfterEach
    void stop() {
        server.stop(0);
    }

    private String lastQuery() {
        synchronized (received) {
            return received.get(received.size() - 1);
        }
    }

    // ── Sources ───────────────────────────────────────────────────────────────

    @Test
    void sourcesListOnlyKnownMachinesAndParsedApplications() {
        LogSources s = service.sources();

        assertThat(s.available()).isTrue();
        assertThat(s.sources()).extracting(x -> x.key()).containsExactly(
                "host:k8s-monitoring", "host:master", "host:worker1", "app:kube-system/etcd", "app:pfe/backend");
        assertThat(s.sources().get(0).kind()).isEqualTo("HOST");
        assertThat(s.sources().get(3).kind()).isEqualTo("APP");
        // la valeur piégée et la machine inconnue ne sont jamais proposées
        assertThat(s.sources()).noneMatch(x -> x.key().contains("evil") || x.key().contains("inconnue"));
    }

    @Test
    void sourcesAreCachedBriefly() {
        service.sources();
        int after;
        synchronized (received) {
            after = received.size();
        }
        service.sources();

        synchronized (received) {
            assertThat(received).hasSize(after);
        }
    }

    // ── Lignes ────────────────────────────────────────────────────────────────

    @Test
    void linesAreReturnedNewestFirstWithTheirLevelAndMeaning() {
        LogLines l = service.lines("host:worker1", "ALL", null, 30, 100);

        assertThat(l.available()).isTrue();
        assertThat(l.count()).isEqualTo(4);
        assertThat(l.lines()).extracting(x -> x.at()).isSortedAccordingTo(java.util.Comparator.reverseOrder());
        var refused = l.lines().stream().filter(x -> x.text().contains("connection refused")).findFirst().orElseThrow();
        assertThat(refused.level()).isEqualTo("ERROR");
        assertThat(refused.category()).isEqualTo("RESEAU");
        assertThat(l.lines().stream().filter(x -> x.text().contains("lent")).findFirst().orElseThrow().level()).isEqualTo("WARN");
        assertThat(l.lines().stream().filter(x -> x.text().contains("tout va bien")).findFirst().orElseThrow().level()).isEqualTo("INFO");
    }

    @Test
    void secretsAreMaskedBeforeLeavingTheServer() {
        LogLines l = service.lines("host:worker1", "ALL", null, 30, 100);

        String all = l.lines().toString();
        assertThat(all).doesNotContain("abcdef123456").doesNotContain("hunter2secret");
        assertThat(all).contains("***");
        assertThat(LogViewerService.mask("eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.abcdefghijk")).isEqualTo("***");
        assertThat(LogViewerService.mask("api_key=SUPERSECRET123 ok")).isEqualTo("api_key=*** ok");
    }

    @Test
    void aMachineQueryUsesTheFixedSelectorAndAnApplicationQueryTheContainerFile() {
        service.lines("host:worker1", "ALL", null, 30, 100);
        assertThat(lastQuery()).contains("host=\"worker1\"").contains("vm-syslog|node-syslog").contains("limit=100");

        service.lines("app:pfe/backend", "ALL", null, 30, 100);
        assertThat(lastQuery()).contains("job=\"k8s-containers\"").contains("_pfe_backend-[0-9a-f]{64}");
    }

    @Test
    void theLevelFilterAddsAFixedLineFilter() {
        service.lines("host:worker1", "ERROR", null, 30, 100);
        assertThat(lastQuery()).contains("level=(error|fatal|crit)").contains("!= \"level=info\"");

        service.lines("host:worker1", "WARN", null, 30, 100);
        assertThat(lastQuery()).contains("level=warn");

        service.lines("host:worker1", "ALL", null, 30, 100);
        assertThat(lastQuery()).doesNotContain("|~");
    }

    @Test
    void theSearchedTextIsAlwaysLiteralNeverARegexNorALogqlInjection() {
        service.lines("host:worker1", "ALL", "a.b(c)|\"d\"\\e", 30, 100);

        String q = lastQuery();
        assertThat(q).contains("(?i)a\\\\.b\\\\(c\\\\)\\\\|\\\"d\\\"\\\\\\\\e");   // . ( ) | " \ échappés, jamais interprétés
        assertThat(LogViewerService.regexEscape("a.b")).isEqualTo("a\\.b");
        assertThat(LogViewerService.logqlString("\"x\\")).isEqualTo("\\\"x\\\\");
    }

    @Test
    void invalidSourcesLevelsAndTextsAreRefusedBeforeAnyRequest() {
        assertThatThrownBy(() -> service.lines("host:inconnue", "ALL", null, 30, 100)).isInstanceOf(IllegalArgumentException.class);
        assertThatThrownBy(() -> service.lines("host:master\"} or {job=~\".+", "ALL", null, 30, 100)).isInstanceOf(IllegalArgumentException.class);
        assertThatThrownBy(() -> service.lines("app:pfe/back end", "ALL", null, 30, 100)).isInstanceOf(IllegalArgumentException.class);
        assertThatThrownBy(() -> service.lines("{job=~\".+\"}", "ALL", null, 30, 100)).isInstanceOf(IllegalArgumentException.class);
        assertThatThrownBy(() -> service.lines(null, "ALL", null, 30, 100)).isInstanceOf(IllegalArgumentException.class);
        assertThatThrownBy(() -> service.lines("host:master", "DEBUG", null, 30, 100)).isInstanceOf(IllegalArgumentException.class);
        assertThatThrownBy(() -> service.lines("host:master", "ALL", "x".repeat(81), 30, 100)).isInstanceOf(IllegalArgumentException.class);
        assertThatThrownBy(() -> service.lines("host:master", "ALL", "a\nb", 30, 100)).isInstanceOf(IllegalArgumentException.class);
        synchronized (received) {
            assertThat(received).isEmpty();
        }
    }

    @Test
    void periodAndLineCountAreBounded() {
        LogLines l = service.lines("host:worker1", "ALL", null, 99999, 99999);

        assertThat(l.minutes()).isEqualTo(180);
        assertThat(lastQuery()).contains("limit=200");
        assertThat(service.lines("host:worker1", "ALL", null, 1, 0).minutes()).isEqualTo(5);
        assertThat(lastQuery()).contains("limit=1");
    }

    @Test
    void whenLokiIsDownTheViewerAnswersUnavailable() {
        down = true;

        LogLines l = service.lines("host:worker1", "ALL", null, 30, 100);
        LogSources s = new LogViewerService(new SupervisionClient(unreachable())).sources();

        assertThat(l.available()).isFalse();
        assertThat(l.lines()).isEmpty();
        assertThat(l.error()).contains("Loki");
        assertThat(s.available()).isFalse();
    }

    private InfraHealthProperties unreachable() {
        InfraHealthProperties p = new InfraHealthProperties();
        p.setLokiUrl("http://127.0.0.1:1");
        return p;
    }

    @Test
    void onlyReadRequestsAreSent() {
        service.sources();
        service.lines("host:worker1", "ERROR", "x", 30, 50);

        synchronized (received) {
            assertThat(received).isNotEmpty().allMatch(r -> r.startsWith("/loki/api/v1/"));
        }
    }
}
