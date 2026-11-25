package com.orange.pfe.backend.config;

import com.sun.net.httpserver.HttpServer;
import org.junit.jupiter.api.Test;
import org.springframework.http.HttpEntity;
import org.springframework.http.HttpHeaders;
import org.springframework.http.MediaType;
import org.springframework.web.client.RestTemplate;
import org.springframework.http.client.SimpleClientHttpRequestFactory;

import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.atomic.AtomicReference;

import static org.junit.jupiter.api.Assertions.*;

class BridgeClientUnbufferedTransportProbeTest {

    @Test
    void recordsHeadersFromOriginalUnbufferedConfiguration() throws Exception {
        AtomicReference<String> contentLength = new AtomicReference<>();
        AtomicReference<String> transferEncoding = new AtomicReference<>();

        HttpServer server = HttpServer.create(
                new InetSocketAddress("127.0.0.1", 0), 0);

        server.createContext("/probe", exchange -> {
            try {
                contentLength.set(
                        exchange.getRequestHeaders().getFirst("Content-Length"));
                transferEncoding.set(
                        exchange.getRequestHeaders().getFirst("Transfer-Encoding"));

                exchange.getRequestBody().readAllBytes();

                byte[] response = "{\"ok\":true}".getBytes(StandardCharsets.UTF_8);
                exchange.getResponseHeaders().set(
                        "Content-Type", "application/json");
                exchange.sendResponseHeaders(200, response.length);
                exchange.getResponseBody().write(response);
            } finally {
                exchange.close();
            }
        });

        server.start();

        try {
            SimpleClientHttpRequestFactory factory =
                    new SimpleClientHttpRequestFactory();
            factory.setConnectTimeout(2000);
            factory.setReadTimeout(2000);

            // Reproduit l'ancienne configuration avant BufferingClientHttpRequestFactory.
            RestTemplate restTemplate = new RestTemplate(factory);

            HttpHeaders headers = new HttpHeaders();
            headers.setContentType(MediaType.APPLICATION_JSON);
            headers.setAccept(List.of(MediaType.APPLICATION_JSON));

            Map<String, Object> payload = new LinkedHashMap<>();
            payload.put("action", "HOST_SERVICE_CPU_QUOTA");
            payload.put("type", "NODE_HIGH_CPU");
            payload.put("ns", "k8s-nodes");
            payload.put("cible", "k8s-worker1");
            payload.put("noeud", "k8s-worker1");
            payload.put("service", "helios-cpuload.service");
            payload.put("quota_pct", 25);
            payload.put("duree_s", 240);

            String url = "http://127.0.0.1:"
                    + server.getAddress().getPort() + "/probe";

            var response = restTemplate.postForEntity(
                    url, new HttpEntity<>(payload, headers), String.class);

            assertEquals(200, response.getStatusCode().value());

            System.out.println("BASELINE_CONTENT_LENGTH=" + contentLength.get());
            System.out.println("BASELINE_TRANSFER_ENCODING=" + transferEncoding.get());

            assertTrue(
                    contentLength.get() == null
                            || contentLength.get().isBlank(),
                    "La configuration originale devrait illustrer le corps sans longueur connue");

            assertNotNull(transferEncoding.get(),
                    "Le mode de transfert doit être observable");
            assertTrue(
                    transferEncoding.get().toLowerCase().contains("chunked"),
                    "Le test doit confirmer le mode chunked pour établir la différence");
        } finally {
            server.stop(0);
        }
    }
}