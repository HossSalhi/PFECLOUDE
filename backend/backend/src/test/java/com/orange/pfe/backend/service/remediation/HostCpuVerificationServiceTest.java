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

import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

class HostCpuVerificationServiceTest {

    private HttpServer server;
    private AtomicReference<String> responseBody;
    private AtomicReference<String> receivedQuery;
    private AtomicInteger responseStatus;
    private AtomicInteger requestCount;
    private HostCpuVerificationService service;

    @BeforeEach
    void setUp() throws IOException {
        responseBody = new AtomicReference<>(
                vectorResponse("192.168.148.158:9100", "45.0"));
        receivedQuery = new AtomicReference<>("");
        responseStatus = new AtomicInteger(200);
        requestCount = new AtomicInteger(0);

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

            byte[] bytes =
                    responseBody.get().getBytes(StandardCharsets.UTF_8);
            exchange.getResponseHeaders().set(
                    "Content-Type", "application/json");
            exchange.sendResponseHeaders(responseStatus.get(), bytes.length);

            try (OutputStream output = exchange.getResponseBody()) {
                output.write(bytes);
            }
        });
        server.start();

        String url = "http://127.0.0.1:" + server.getAddress().getPort();
        // CPU-05B : seuil de test aligne sur le nouveau defaut 85.0 (< detection 90).
        service = new HostCpuVerificationService(
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
    void shouldResolveWhenMeasuredCpuIsAtThreshold() {
        // Valeur limite : cpu == seuil (85.0) => resolu (comparaison cpuPercent <= threshold).
        responseBody.set(
                vectorResponse("192.168.148.158:9100", "85.0"));

        RecoveryVerificationResult result =
                service.verify("k8s-worker1");

        assertTrue(result.resolved());
        assertTrue(receivedQuery.get().contains(
                "instance=\"192.168.148.158:9100\""));
        assertTrue(receivedQuery.get().contains("[3m]"));
    }

    @Test
    void shouldResolveJustBelowThreshold() {
        // CPU-05B : juste sous le seuil 85 => resolu.
        responseBody.set(vectorResponse("192.168.148.158:9100", "84.9"));
        assertTrue(service.verify("k8s-worker1").resolved());
    }

    @Test
    void shouldNotResolveInDetectionBandAboveEightyFive() {
        // CPU-05B : avec seuil=85, aucune valeur de la bande de detection ([90,96] et au-dela,
        // ainsi que 85.1) ne doit etre declaree RESOLVED. Prouve que le RESOLVED premature
        // (ancien defaut 96 >= detection 90) est supprime.
        for (String cpu : new String[] {"85.1", "89.9", "90.0", "90.1", "95.0", "96.0"}) {
            responseBody.set(vectorResponse("192.168.148.158:9100", cpu));
            RecoveryVerificationResult r = service.verify("k8s-worker1");
            assertFalse(r.resolved(),
                    "cpu=" + cpu + "% ne doit PAS etre resolu avec un seuil de 85% (message=" + r.message() + ")");
        }
    }

    @Test
    void shouldNotResolveWhileCpuIsAboveThreshold() {
        responseBody.set(
                vectorResponse("192.168.148.158:9100", "97.2"));

        RecoveryVerificationResult result =
                service.verify("k8s-worker1");

        assertFalse(result.resolved());
    }

    @Test
    void shouldNotResolveWhenMetricIsMissing() {
        responseBody.set(
                "{\"status\":\"success\",\"data\":{\"resultType\":\"vector\",\"result\":[]}}");

        RecoveryVerificationResult result =
                service.verify("k8s-worker1");

        assertFalse(result.resolved());
    }

    @Test
    void shouldNotQueryPrometheusForUnknownTarget() {
        RecoveryVerificationResult result =
                service.verify("unknown-node");

        assertFalse(result.resolved());
        assertTrue(requestCount.get() == 0);
    }

    @Test
    void shouldNotResolveWhenPrometheusReturnsHttpError() {
        responseStatus.set(503);

        RecoveryVerificationResult result =
                service.verify("k8s-worker1");

        assertFalse(result.resolved());
    }

    private static String vectorResponse(
            String instance, String cpuPercent) {
        return "{\"status\":\"success\",\"data\":{\"resultType\":\"vector\","
                + "\"result\":[{\"metric\":{\"instance\":\"" + instance
                + "\"},\"value\":[1792231746.2,\"" + cpuPercent + "\"]}]}}";
    }
}