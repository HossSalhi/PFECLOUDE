package com.orange.pfe.backend.service.remediation;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.sun.net.httpserver.HttpServer;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

import java.io.IOException;
import java.io.OutputStream;
import java.net.InetSocketAddress;
import java.net.URLDecoder;
import java.net.http.HttpClient;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicReference;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

class HostDiskVerificationServiceTest {

    private HttpServer server;
    private AtomicReference<String> responseBody;
    private AtomicReference<String> receivedQuery;
    private AtomicInteger responseStatus;
    private AtomicInteger requestCount;
    private HostDiskVerificationService service;

    @BeforeEach
    void setUp() throws IOException {
        responseBody = new AtomicReference<>(
                filesystemResponse("192.168.148.158:9100", "50.0"));
        receivedQuery = new AtomicReference<>("");
        responseStatus = new AtomicInteger(200);
        requestCount = new AtomicInteger();

        server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        server.createContext("/api/v1/query", exchange -> {
            requestCount.incrementAndGet();

            String rawQuery = exchange.getRequestURI().getRawQuery();
            if (rawQuery != null) {
                for (String part : rawQuery.split("&")) {
                    if (part.startsWith("query=")) {
                        receivedQuery.set(URLDecoder.decode(
                                part.substring("query=".length()),
                                StandardCharsets.UTF_8));
                    }
                }
            }

            byte[] bytes = responseBody.get().getBytes(StandardCharsets.UTF_8);
            exchange.getResponseHeaders().set(
                    "Content-Type", "application/json");
            exchange.sendResponseHeaders(responseStatus.get(), bytes.length);

            try (OutputStream output = exchange.getResponseBody()) {
                output.write(bytes);
            }
        });
        server.start();

        String url = "http://127.0.0.1:" + server.getAddress().getPort();
        service = new HostDiskVerificationService(
                url,
                85.0,
                HttpClient.newBuilder()
                        .connectTimeout(Duration.ofSeconds(2))
                        .build(),
                new ObjectMapper());
    }

    @AfterEach
    void tearDown() {
        if (server != null) {
            server.stop(0);
        }
    }

    @Test
    void diskFullResolvesAtTheDetectorThreshold() {
        responseBody.set(filesystemResponse(
                "192.168.148.158:9100", "85.0"));

        RecoveryVerificationResult result =
                service.verify("k8s-worker1", "NODE_DISK_FULL");

        assertTrue(result.resolved());
        assertTrue(receivedQuery.get().contains("node_filesystem_avail_bytes"));
        assertTrue(receivedQuery.get().contains("mountpoint=\"/\""));
        assertTrue(receivedQuery.get().contains("192.168.148.158:9100"));
    }

    @Test
    void diskFullDoesNotResolveAboveThreshold() {
        responseBody.set(filesystemResponse(
                "192.168.148.158:9100", "85.1"));

        RecoveryVerificationResult result =
                service.verify("k8s-worker1", "NODE_DISK_FULL");

        assertFalse(result.resolved());
    }

    @Test
    void inodesUseTheInodeMetricAndCanResolveBelowThreshold() {
        responseBody.set(filesystemResponse(
                "192.168.148.158:9100", "60.0"));

        RecoveryVerificationResult result =
                service.verify("k8s-worker1", "NODE_INODES_FULL");

        assertTrue(result.resolved());
        assertTrue(receivedQuery.get().contains("node_filesystem_files_free"));
        assertTrue(receivedQuery.get().contains("node_filesystem_files{"));
    }

    @Test
    void diskPressureResolvesOnlyWhenTheConditionIsFalse() {
        responseBody.set(pressureResponse("k8s-worker1", "0"));

        RecoveryVerificationResult result =
                service.verify("k8s-worker1", "NODE_DISK_PRESSURE");

        assertTrue(result.resolved());
        assertTrue(receivedQuery.get().contains(
                "condition=\"DiskPressure\""));
        assertTrue(receivedQuery.get().contains("status=\"true\""));
    }

    @Test
    void activeDiskPressureDoesNotResolve() {
        responseBody.set(pressureResponse("k8s-worker1", "1"));

        RecoveryVerificationResult result =
                service.verify("k8s-worker1", "NODE_DISK_PRESSURE");

        assertFalse(result.resolved());
    }

    @Test
    void missingMetricCannotBeConsideredRecovered() {
        responseBody.set(
                "{\"status\":\"success\",\"data\":{\"resultType\":\"vector\",\"result\":[]}}");

        RecoveryVerificationResult result =
                service.verify("k8s-worker1", "NODE_DISK_FULL");

        assertFalse(result.resolved());
        assertTrue(result.message().contains("missing or ambiguous"));
    }

    @Test
    void unknownTargetDoesNotQueryPrometheus() {
        RecoveryVerificationResult result =
                service.verify("k8s-master", "NODE_DISK_FULL");

        assertFalse(result.resolved());
        assertEquals(0, requestCount.get());
    }

    @Test
    void unsupportedIncidentTypeDoesNotQueryPrometheus() {
        RecoveryVerificationResult result =
                service.verify("k8s-worker1", "NODE_HIGH_CPU");

        assertFalse(result.resolved());
        assertEquals(0, requestCount.get());
    }

    @Test
    void prometheusHttpErrorCannotBeConsideredRecovered() {
        responseStatus.set(503);

        RecoveryVerificationResult result =
                service.verify("k8s-worker1", "NODE_DISK_FULL");

        assertFalse(result.resolved());
    }

    private static String filesystemResponse(
            String instance, String usedPercent) {
        return "{\"status\":\"success\",\"data\":{\"resultType\":\"vector\","
                + "\"result\":[{\"metric\":{\"instance\":\"" + instance
                + "\"},\"value\":[1792231746.2,\"" + usedPercent + "\"]}]}}";
    }

    private static String pressureResponse(String node, String value) {
        return "{\"status\":\"success\",\"data\":{\"resultType\":\"vector\","
                + "\"result\":[{\"metric\":{\"node\":\"" + node
                + "\",\"condition\":\"DiskPressure\",\"status\":\"true\"},"
                + "\"value\":[1792231746.2,\"" + value + "\"]}]}}";
    }
}