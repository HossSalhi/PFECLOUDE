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
import java.util.Set;

@Service
public class HostMemoryVerificationService {

    private static final Logger log =
            LoggerFactory.getLogger(HostMemoryVerificationService.class);

    private static final Map<String, String> TARGET_INSTANCES = Map.of(
            "k8s-worker1", "192.168.148.158:9100",
            "k8s-worker2", "192.168.148.155:9100"
    );

    private static final Set<String> SUPPORTED_TYPES = Set.of(
            "NODE_HIGH_MEMORY",
            "NODE_MEMORY_PRESSURE"
    );

    private final String prometheusUrl;
    private final double recoveryThresholdPercent;
    private final HttpClient httpClient;
    private final ObjectMapper mapper;

    @Autowired
    public HostMemoryVerificationService(
            @Value("${infra.health.prometheus-url:http://192.168.148.160:9090}")
            String prometheusUrl,
            @Value("${helios.host-memory-verification.recovery-threshold-percent:92.0}")
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

    HostMemoryVerificationService(
            String prometheusUrl,
            double recoveryThresholdPercent,
            HttpClient httpClient,
            ObjectMapper mapper) {

        if (prometheusUrl == null || prometheusUrl.isBlank()) {
            throw new IllegalArgumentException("Prometheus URL is required");
        }
        if (!Double.isFinite(recoveryThresholdPercent)
                || recoveryThresholdPercent < 0.0
                || recoveryThresholdPercent > 100.0) {
            throw new IllegalArgumentException("Invalid memory recovery threshold");
        }

        this.prometheusUrl = prometheusUrl.replaceAll("/+$", "");
        this.recoveryThresholdPercent = recoveryThresholdPercent;
        this.httpClient = httpClient;
        this.mapper = mapper;
    }

    public RecoveryVerificationResult verify(
            String targetName,
            String incidentType) {

        String target = canonicalTarget(targetName);
        if (target == null) {
            return inconclusive(
                    "RAM verification refused: target is not an allowlisted worker");
        }

        String type = incidentType == null
                ? ""
                : incidentType.trim().toUpperCase(Locale.ROOT);

        if (!SUPPORTED_TYPES.contains(type)) {
            return inconclusive(
                    "RAM verification refused: unsupported incident type");
        }

        String query = buildQuery(type, target);

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
                    request,
                    HttpResponse.BodyHandlers.ofString());

            if (response.statusCode() < 200 || response.statusCode() >= 300) {
                return inconclusive(
                        "Prometheus returned HTTP " + response.statusCode());
            }

            return parseResponse(
                    response.body(),
                    type,
                    target,
                    TARGET_INSTANCES.get(target),
                    recoveryThresholdPercent,
                    mapper);

        } catch (InterruptedException ex) {
            Thread.currentThread().interrupt();
            return inconclusive("RAM verification was interrupted");
        } catch (Exception ex) {
            log.warn(
                    "Host RAM verification failed for {}: {}",
                    target,
                    ex.getClass().getSimpleName());

            return inconclusive(
                    "Prometheus query failed; RAM recovery cannot be confirmed");
        }
    }

    static String canonicalTarget(String targetName) {
        if (targetName == null || targetName.isBlank()) {
            return null;
        }

        String normalized = targetName.trim().toLowerCase(Locale.ROOT);
        return TARGET_INSTANCES.containsKey(normalized) ? normalized : null;
    }

    private static String buildQuery(String type, String target) {
        String instance = TARGET_INSTANCES.get(target);

        return switch (type) {
            case "NODE_HIGH_MEMORY" ->
                    "max by (instance) ((1 - ("
                    + "node_memory_MemAvailable_bytes{instance=\""
                    + instance + "\"} / "
                    + "node_memory_MemTotal_bytes{instance=\""
                    + instance + "\"})) * 100)";

            case "NODE_MEMORY_PRESSURE" ->
                    "kube_node_status_condition{node=\""
                    + target
                    + "\",condition=\"MemoryPressure\",status=\"true\"}";

            default -> throw new IllegalArgumentException(
                    "Unsupported RAM incident type");
        };
    }

    static RecoveryVerificationResult parseResponse(
            String body,
            String incidentType,
            String expectedTarget,
            String expectedInstance,
            double threshold,
            ObjectMapper mapper) {

        try {
            if (body == null || body.isBlank()) {
                return inconclusive("Prometheus returned an empty RAM response");
            }

            JsonNode root = mapper.readTree(body);

            if (!"success".equals(root.path("status").asText())) {
                return inconclusive("Prometheus query status was not successful");
            }

            JsonNode results = root.path("data").path("result");
            if (!results.isArray() || results.size() != 1) {
                return inconclusive(
                        "RAM metric missing or ambiguous for " + expectedTarget);
            }

            JsonNode result = results.get(0);
            JsonNode metric = result.path("metric");
            JsonNode value = result.path("value");

            if (!value.isArray() || value.size() < 2) {
                return inconclusive("Prometheus RAM sample has an invalid format");
            }

            double measured = Double.parseDouble(value.get(1).asText());
            if (!Double.isFinite(measured)) {
                return inconclusive("Prometheus returned a non-finite RAM value");
            }

            if ("NODE_MEMORY_PRESSURE".equals(incidentType)) {
                String actualNode = metric.path("node").asText("");
                String condition = metric.path("condition").asText("");
                String status = metric.path("status").asText("");

                if (!expectedTarget.equals(actualNode)
                        || !"MemoryPressure".equals(condition)
                        || !"true".equals(status)) {
                    return inconclusive(
                            "Prometheus returned an unexpected MemoryPressure series");
                }

                if (measured != 0.0 && measured != 1.0) {
                    return inconclusive(
                            "MemoryPressure metric is outside the expected 0/1 range");
                }

                boolean healthy = measured == 0.0;
                return new RecoveryVerificationResult(
                        healthy,
                        !healthy,
                        "node=" + expectedTarget
                                + ", condition=MemoryPressure"
                                + ", measured=" + measured
                                + ", recovered=" + healthy);
            }

            String actualInstance = metric.path("instance").asText("");
            if (!expectedInstance.equals(actualInstance)) {
                return inconclusive(
                        "Prometheus returned an unexpected RAM metric instance");
            }

            if (measured < 0.0 || measured > 100.0) {
                return inconclusive("RAM percentage is outside the valid range");
            }

            boolean healthy = measured <= threshold;

            return new RecoveryVerificationResult(
                    healthy,
                    !healthy,
                    "node=" + expectedTarget
                            + ", instance=" + expectedInstance
                            + ", memory-used-percent="
                            + String.format(Locale.ROOT, "%.2f", measured)
                            + ", recoveryThreshold="
                            + String.format(Locale.ROOT, "%.2f", threshold)
                            + ", recovered=" + healthy);

        } catch (Exception ex) {
            return inconclusive("Could not parse the Prometheus RAM response");
        }
    }

    private static RecoveryVerificationResult inconclusive(String message) {
        return new RecoveryVerificationResult(false, true, message);
    }
}