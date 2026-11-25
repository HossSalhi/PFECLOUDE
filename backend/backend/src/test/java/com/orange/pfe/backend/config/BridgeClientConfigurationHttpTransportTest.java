package com.orange.pfe.backend.config;

import com.sun.net.httpserver.HttpServer;
import org.junit.jupiter.api.Test;
import org.springframework.http.HttpEntity;
import org.springframework.http.HttpHeaders;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.web.client.RestTemplate;

import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.atomic.AtomicReference;

import static org.junit.jupiter.api.Assertions.*;

class BridgeClientConfigurationHttpTransportTest {

    @Test
    void sendsJsonWithContentLengthAndWithoutChunkedTransfer() throws Exception {
        AtomicReference<String> contentLength = new AtomicReference<>();
        AtomicReference<String> transferEncoding = new AtomicReference<>();
        AtomicReference<String> receivedBody = new AtomicReference<>();

        HttpServer server = HttpServer.create(
                new InetSocketAddress("127.0.0.1", 0), 0);

        server.createContext("/action", exchange -> {
            try {
                contentLength.set(
                        exchange.getRequestHeaders().getFirst("Content-Length"));
                transferEncoding.set(
                        exchange.getRequestHeaders().getFirst("Transfer-Encoding"));
                receivedBody.set(new String(
                        exchange.getRequestBody().readAllBytes(),
                        StandardCharsets.UTF_8));

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
            RestTemplate restTemplate =
                    new BridgeClientConfiguration().heliosBridgeRestTemplate();

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
                    + server.getAddress().getPort() + "/action";

            ResponseEntity<String> response = restTemplate.postForEntity(
                    url, new HttpEntity<>(payload, headers), String.class);

            assertEquals(200, response.getStatusCode().value());
            assertNotNull(receivedBody.get());
            assertTrue(receivedBody.get().contains("HOST_SERVICE_CPU_QUOTA"));

            assertNotNull(contentLength.get(),
                    "Le corps doit avoir un Content-Length connu");
            assertEquals(
                    String.valueOf(receivedBody.get().getBytes(StandardCharsets.UTF_8).length),
                    contentLength.get(),
                    "Content-Length doit correspondre a la longueur du JSON recu");

            assertTrue(transferEncoding.get() == null
                            || !transferEncoding.get().toLowerCase().contains("chunked"),
                    "La requete ne doit pas utiliser Transfer-Encoding: chunked");
        } finally {
            server.stop(0);
        }
    }
}