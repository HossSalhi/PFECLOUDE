package com.orange.pfe.backend.service.remediation;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.sun.net.httpserver.HttpServer;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Value;

import java.io.IOException;
import java.io.OutputStream;
import java.lang.reflect.Constructor;
import java.net.InetSocketAddress;
import java.net.http.HttpClient;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicReference;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * DISK-E2E-158-02 : le seuil de recuperation de 77 % est LIMITE au scenario DISK_E2E_158 (drapeau dedie actif +
 * k8s-worker1 + NODE_DISK_FULL). Toute autre combinaison garde sa regle d'avant : seuil configurable (defaut 85) pour
 * NODE_DISK_FULL sur k8s-worker2, seuil de 85 pour NODE_INODES_FULL, metrique 0/1 pour NODE_DISK_PRESSURE.
 * Aucun reseau hors boucle locale : Prometheus est un serveur local.
 */
class HostDiskVerificationDiskE2eThresholdTest {

    private static final String W1 = "192.168.148.158:9100";
    private static final String W2 = "192.168.148.155:9100";

    private HttpServer server;
    private AtomicReference<String> responseBody;
    private AtomicInteger requestCount;

    @BeforeEach
    void setUp() throws IOException {
        responseBody = new AtomicReference<>(response(W1, "50.0"));
        requestCount = new AtomicInteger();
        server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        server.createContext("/api/v1/query", exchange -> {
            requestCount.incrementAndGet();
            byte[] bytes = responseBody.get().getBytes(StandardCharsets.UTF_8);
            exchange.getResponseHeaders().set("Content-Type", "application/json");
            exchange.sendResponseHeaders(200, bytes.length);
            try (OutputStream output = exchange.getResponseBody()) {
                output.write(bytes);
            }
        });
        server.start();
    }

    @AfterEach
    void tearDown() {
        if (server != null) {
            server.stop(0);
        }
    }

    private HostDiskVerificationService service(boolean scenarioEnabled) {
        return new HostDiskVerificationService(
                "http://127.0.0.1:" + server.getAddress().getPort(),
                85.0,
                scenarioEnabled,
                HttpClient.newBuilder().connectTimeout(Duration.ofSeconds(2)).build(),
                new ObjectMapper());
    }

    private boolean resolved(HostDiskVerificationService service, String target, String type, String instance, String used) {
        responseBody.set(response(instance, used));
        return service.verify(target, type).resolved();
    }

    // ------------------------------------------------------------------------------------------ valeurs par defaut

    @Test
    void legacyDefaultThresholdIsRestoredTo85AndTheScenarioFlagDefaultsToFalse() throws NoSuchMethodException {
        Constructor<HostDiskVerificationService> constructor = HostDiskVerificationService.class
                .getConstructor(String.class, double.class, boolean.class);

        Value legacy = constructor.getParameters()[1].getAnnotation(Value.class);
        Value scenario = constructor.getParameters()[2].getAnnotation(Value.class);

        assertEquals("${helios.host-disk-verification.recovery-threshold-percent:85.0}", legacy.value());
        assertEquals("${remediation.host-disk-e2e158-enabled:false}", scenario.value());
    }

    @Test
    void theScenarioThresholdConstantIs77() {
        assertEquals(77.0, HostDiskScenario.RECOVERY_THRESHOLD_PERCENT);
    }

    // ------------------------------------------------------------------------------------------ scenario : 77 %

    @Test
    void scenarioDiskFullOnWorker1IsRecoveredAtOrBelow77Percent() {
        HostDiskVerificationService service = service(true);
        for (String used : new String[] {"50.0", "76.99", "77.0"}) {
            assertTrue(resolved(service, "k8s-worker1", "NODE_DISK_FULL", W1, used), used);
        }
    }

    @Test
    void scenarioDiskFullOnWorker1IsNotRecoveredAbove77Percent() {
        HostDiskVerificationService service = service(true);
        for (String used : new String[] {"77.01", "78.0", "80.5", "84.0", "85.0", "96.0"}) {
            assertFalse(resolved(service, "k8s-worker1", "NODE_DISK_FULL", W1, used), used);
        }
    }

    @Test
    void theScenarioCanonicalizesTheTargetAndTheTypeButOnlyForTheExactPair() {
        HostDiskVerificationService service = service(true);
        assertFalse(resolved(service, " K8S-Worker1 ", "node_disk_full", W1, "80.0"), "80 % > 77 % : meme avec une autre casse");
        assertTrue(resolved(service, " K8S-Worker1 ", "node_disk_full", W1, "70.0"));
    }

    // ------------------------------------------------------------------------------------------ hors scenario : regles d'avant

    @Test
    void withoutTheScenarioFlagWorker1DiskFullKeepsTheOldThresholdOf85() {
        HostDiskVerificationService service = service(false);
        assertTrue(resolved(service, "k8s-worker1", "NODE_DISK_FULL", W1, "85.0"));
        assertTrue(resolved(service, "k8s-worker1", "NODE_DISK_FULL", W1, "80.5"));
        assertFalse(resolved(service, "k8s-worker1", "NODE_DISK_FULL", W1, "85.1"));
    }

    @Test
    void worker2DiskFullKeepsTheOldThresholdOf85EvenWhenTheScenarioIsEnabled() {
        HostDiskVerificationService service = service(true);
        assertTrue(resolved(service, "k8s-worker2", "NODE_DISK_FULL", W2, "85.0"), "85 % : regle d'avant, pas 77 %");
        assertTrue(resolved(service, "k8s-worker2", "NODE_DISK_FULL", W2, "80.5"));
        assertFalse(resolved(service, "k8s-worker2", "NODE_DISK_FULL", W2, "85.1"));
    }

    @Test
    void inodesKeepThe85PercentThresholdWhateverTheScenarioFlag() {
        for (boolean scenario : new boolean[] {false, true}) {
            HostDiskVerificationService service = service(scenario);
            for (String[] target : new String[][] {{"k8s-worker1", W1}, {"k8s-worker2", W2}}) {
                assertTrue(resolved(service, target[0], "NODE_INODES_FULL", target[1], "84.0"), target[0] + " " + scenario);
                assertTrue(resolved(service, target[0], "NODE_INODES_FULL", target[1], "85.0"), target[0] + " " + scenario);
                assertFalse(resolved(service, target[0], "NODE_INODES_FULL", target[1], "85.1"), target[0] + " " + scenario);
            }
        }
    }

    @Test
    void diskPressureKeepsItsZeroOrOneMetricWhateverTheScenarioFlag() {
        for (boolean scenario : new boolean[] {false, true}) {
            HostDiskVerificationService service = service(scenario);
            responseBody.set(pressureResponse("k8s-worker1", "0"));
            assertTrue(service.verify("k8s-worker1", "NODE_DISK_PRESSURE").resolved());
            responseBody.set(pressureResponse("k8s-worker1", "1"));
            assertFalse(service.verify("k8s-worker1", "NODE_DISK_PRESSURE").resolved());
        }
    }

    // ------------------------------------------------------------------------------------------ surete

    @Test
    void anAmbiguousOrMissingMeasureIsNeverRecoveredInTheScenario() {
        HostDiskVerificationService service = service(true);
        responseBody.set("{\"status\":\"success\",\"data\":{\"resultType\":\"vector\",\"result\":[]}}");
        assertFalse(service.verify("k8s-worker1", "NODE_DISK_FULL").resolved());
    }

    @Test
    void aWrongInstanceIsNeverRecoveredInTheScenario() {
        HostDiskVerificationService service = service(true);
        assertFalse(resolved(service, "k8s-worker1", "NODE_DISK_FULL", W2, "10.0"),
                "la serie d'un autre noeud ne prouve rien pour k8s-worker1");
    }

    @Test
    void unknownTargetsNeverQueryPrometheus() {
        HostDiskVerificationService service = service(true);
        assertFalse(service.verify("k8s-master", "NODE_DISK_FULL").resolved());
        assertFalse(service.verify("monitoring", "NODE_DISK_FULL").resolved());
        assertEquals(0, requestCount.get());
    }

    private static String response(String instance, String usedPercent) {
        return "{\"status\":\"success\",\"data\":{\"resultType\":\"vector\","
                + "\"result\":[{\"metric\":{\"instance\":\"" + instance + "\"},"
                + "\"value\":[1792231746.2,\"" + usedPercent + "\"]}]}}";
    }

    private static String pressureResponse(String node, String value) {
        return "{\"status\":\"success\",\"data\":{\"resultType\":\"vector\","
                + "\"result\":[{\"metric\":{\"node\":\"" + node
                + "\",\"condition\":\"DiskPressure\",\"status\":\"true\"},"
                + "\"value\":[1792231746.2,\"" + value + "\"]}]}}";
    }
}
