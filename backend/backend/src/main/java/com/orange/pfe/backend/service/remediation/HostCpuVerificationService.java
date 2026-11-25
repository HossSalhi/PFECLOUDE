package com.orange.pfe.backend.service.remediation;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;

import java.net.URI;
import java.net.URLEncoder;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.Locale;
import java.util.Map;

@Service
public class HostCpuVerificationService {

    private static final Logger logger =
            LoggerFactory.getLogger(HostCpuVerificationService.class);

    private static final Map<String, String> TARGET_INSTANCES = Map.of(
            "k8s-master", "192.168.148.153:9100",
            "k8s-worker1", "192.168.148.158:9100",
            "k8s-worker2", "192.168.148.155:9100"
    );

    private final String prometheusUrl;
    private final double recoveryThresholdPercent;
    private final HttpClient httpClient;
    private final ObjectMapper mapper;

    @Autowired
    public HostCpuVerificationService(
            @Value("${infra.health.prometheus-url:http://192.168.148.160:9090}")
            String prometheusUrl,
            // CPU-05B : seuil de recuperation abaisse de 96.0 a 85.0 pour rester STRICTEMENT
            // SOUS le seuil de DETECTION du detecteur (.160 detecter.py l.148 : NODE_HIGH_CPU si
            // min_over_time[5m:30s] > 90). Avec 96 (>90), un incident pouvait etre declare RESOLVED
            // alors que le CPU restait au-dessus du seuil de detection (bande [90,96]) => re-detection
            // / flapping. 85 laisse une marge de 5 pts sous 90 et s'aligne sur le /verifier du Bridge.
            // Surchargeable via la propriete helios.host-cpu-verification.recovery-threshold-percent.
            @Value("${helios.host-cpu-verification.recovery-threshold-percent:85.0}")
            double recoveryThresholdPercent) {

        this(
                prometheusUrl,
                recoveryThresholdPercent,
                HttpClient.newBuilder()
                        .connectTimeout(Duration.ofSeconds(5))
                        .build(),
                new ObjectMapper()
        );
    }

    HostCpuVerificationService(
            String prometheusUrl,
            double recoveryThresholdPercent,
            HttpClient httpClient,
            ObjectMapper mapper) {

        if (!Double.isFinite(recoveryThresholdPercent)
                || recoveryThresholdPercent < 0.0
                || recoveryThresholdPercent > 100.0) {
            throw new IllegalArgumentException("Invalid CPU recovery threshold");
        }

        this.prometheusUrl = prometheusUrl.replaceAll("/+$", "");
        this.recoveryThresholdPercent = recoveryThresholdPercent;
        this.httpClient = httpClient;
        this.mapper = mapper;
    }

    public RecoveryVerificationResult verify(String targetName) {
        String instance = instanceForTarget(targetName);

        if (instance == null) {
            return inconclusive("Target is not an allowlisted Kubernetes node");
        }

        // Same CPU formula and three-minute rate window as detecter.py.
        String query = "100 - (avg by(instance) "
                + "(rate(node_cpu_seconds_total{instance=\"" + instance
                + "\",mode=\"idle\"}[3m])) * 100)";

        try {
            String encodedQuery =
                    URLEncoder.encode(query, StandardCharsets.UTF_8);
            URI uri = URI.create(
                    prometheusUrl + "/api/v1/query?query=" + encodedQuery);

            HttpRequest request = HttpRequest.newBuilder()
                    .uri(uri)
                    .timeout(Duration.ofSeconds(8))
                    .GET()
                    .build();

            HttpResponse<String> response = httpClient.send(
                    request, HttpResponse.BodyHandlers.ofString());

            if (response.statusCode() < 200 || response.statusCode() >= 300) {
                return inconclusive(
                        "Prometheus returned HTTP " + response.statusCode());
            }

            return parseResponse(
                    response.body(), instance, recoveryThresholdPercent, mapper);

        } catch (InterruptedException ex) {
            Thread.currentThread().interrupt();
            return inconclusive("Prometheus verification was interrupted");
        } catch (Exception ex) {
            logger.warn("Host CPU verification failed for {}: {}",
                    targetName, ex.getClass().getSimpleName());
            return inconclusive(
                    "Prometheus query failed; CPU recovery cannot be confirmed");
        }
    }

    static String instanceForTarget(String targetName) {
        if (targetName == null || targetName.isBlank()) {
            return null;
        }
        return TARGET_INSTANCES.get(targetName.trim().toLowerCase(Locale.ROOT));
    }

    static RecoveryVerificationResult parseResponse(
            String body,
            String expectedInstance,
            double threshold,
            ObjectMapper mapper) {

        try {
            if (body == null || body.isBlank()) {
                return inconclusive("Prometheus returned an empty response");
            }

            JsonNode root = mapper.readTree(body);

            if (!"success".equals(root.path("status").asText())) {
                return inconclusive("Prometheus query status was not successful");
            }

            JsonNode results = root.path("data").path("result");

            if (!results.isArray() || results.size() != 1) {
                return inconclusive(
                        "CPU metric missing or ambiguous for " + expectedInstance);
            }

            JsonNode result = results.get(0);
            String actualInstance =
                    result.path("metric").path("instance").asText("");

            if (!expectedInstance.equals(actualInstance)) {
                return inconclusive(
                        "Prometheus returned an unexpected CPU instance");
            }

            JsonNode value = result.path("value");

            if (!value.isArray() || value.size() < 2) {
                return inconclusive(
                        "Prometheus CPU sample has an invalid format");
            }

            double cpuPercent =
                    Double.parseDouble(value.get(1).asText());

            if (!Double.isFinite(cpuPercent)
                    || cpuPercent < 0.0
                    || cpuPercent > 100.0) {
                return inconclusive(
                        "Prometheus CPU sample is outside the valid range");
            }

            boolean healthy = cpuPercent <= threshold;

            String message = String.format(
                    Locale.ROOT,
                    "instance=%s, cpu=%.2f%%, recoveryThreshold=%.2f%%, window=3m",
                    expectedInstance, cpuPercent, threshold);

            return new RecoveryVerificationResult(
                    healthy, !healthy, message);

        } catch (Exception ex) {
            return inconclusive(
                    "Could not parse the Prometheus CPU response");
        }
    }

    private static RecoveryVerificationResult inconclusive(String message) {
        return new RecoveryVerificationResult(false, true, message);
    }
}