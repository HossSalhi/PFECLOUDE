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
public class HostDiskVerificationService {

    private static final Logger logger =
            LoggerFactory.getLogger(HostDiskVerificationService.class);

    private static final Map<String, String> TARGET_INSTANCES = Map.of(
            "k8s-worker1", "192.168.148.158:9100",
            "k8s-worker2", "192.168.148.155:9100"
    );

    private static final Set<String> SUPPORTED_TYPES = Set.of(
            "NODE_DISK_FULL",
            "NODE_DISK_PRESSURE",
            "NODE_INODES_FULL"
    );

    /** Seuil de recuperation des INODES : inchange (DISK-E2E-158-01 ne touche que le remplissage du disque). */
    static final double INODES_RECOVERY_THRESHOLD_PERCENT = 85.0;

    private final String prometheusUrl;
    private final double recoveryThresholdPercent;
    private final boolean scenarioEnabled;
    private final HttpClient httpClient;
    private final ObjectMapper mapper;

    @Autowired
    public HostDiskVerificationService(
            @Value("${infra.health.prometheus-url:http://192.168.148.160:9090}")
            String prometheusUrl,
            // Regle d'avant, INCHANGEE pour tout ce qui n'est pas le scenario DISK_E2E_158 (defaut 85.0).
            @Value("${helios.host-disk-verification.recovery-threshold-percent:85.0}")
            double recoveryThresholdPercent,
            // DISK-E2E-158-02 : drapeau dedie du scenario ; le seuil de 77 % n'existe que lorsqu'il est actif.
            @Value("${remediation.host-disk-e2e158-enabled:false}")
            boolean scenarioEnabled) {

        this(
                prometheusUrl,
                recoveryThresholdPercent,
                scenarioEnabled,
                HttpClient.newBuilder()
                        .connectTimeout(Duration.ofSeconds(5))
                        .build(),
                new ObjectMapper()
        );
    }

    /** Constructeur d'avant (tests existants) : scenario DESACTIVE, donc aucune regle de 77 %. */
    HostDiskVerificationService(
            String prometheusUrl,
            double recoveryThresholdPercent,
            HttpClient httpClient,
            ObjectMapper mapper) {
        this(prometheusUrl, recoveryThresholdPercent, false, httpClient, mapper);
    }

    HostDiskVerificationService(
            String prometheusUrl,
            double recoveryThresholdPercent,
            boolean scenarioEnabled,
            HttpClient httpClient,
            ObjectMapper mapper) {

        if (prometheusUrl == null || prometheusUrl.isBlank()) {
            throw new IllegalArgumentException("Prometheus URL is required");
        }
        if (!Double.isFinite(recoveryThresholdPercent)
                || recoveryThresholdPercent < 0.0
                || recoveryThresholdPercent > 100.0) {
            throw new IllegalArgumentException("Invalid disk recovery threshold");
        }

        this.prometheusUrl = prometheusUrl.replaceAll("/+$", "");
        this.recoveryThresholdPercent = recoveryThresholdPercent;
        this.scenarioEnabled = scenarioEnabled;
        this.httpClient = httpClient;
        this.mapper = mapper;
    }

    /**
     * Vérifie la récupération mesurée de l'incident sur un worker autorisé.
     * Aucune donnée absente ou ambiguë ne peut être interprétée comme un succès.
     */
    public RecoveryVerificationResult verify(
            String targetName,
            String incidentType) {

        String target = canonicalTarget(targetName);
        if (target == null) {
            return inconclusive("DISK verification refused: target is not an allowlisted worker");
        }

        String type = incidentType == null
                ? ""
                : incidentType.trim().toUpperCase(Locale.ROOT);

        if (!SUPPORTED_TYPES.contains(type)) {
            return inconclusive("DISK verification refused: unsupported incident type");
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

            // DISK-E2E-158-02 : 77 % UNIQUEMENT pour le scenario (drapeau dedie actif + k8s-worker1 + NODE_DISK_FULL).
            // Tout le reste garde sa regle d'avant : INODES 85, k8s-worker2 et autres cas = seuil configurable (defaut 85).
            double threshold;
            if (scenarioEnabled && HostDiskScenario.matchesTargetAndType(target, type)) {
                threshold = HostDiskScenario.RECOVERY_THRESHOLD_PERCENT;
            } else if ("NODE_INODES_FULL".equals(type)) {
                threshold = INODES_RECOVERY_THRESHOLD_PERCENT;
            } else {
                threshold = recoveryThresholdPercent;
            }

            return parseResponse(
                    response.body(),
                    type,
                    target,
                    TARGET_INSTANCES.get(target),
                    threshold,
                    mapper);

        } catch (InterruptedException ex) {
            Thread.currentThread().interrupt();
            return inconclusive("DISK verification was interrupted");
        } catch (Exception ex) {
            logger.warn(
                    "Host DISK verification failed for {}: {}",
                    target,
                    ex.getClass().getSimpleName());
            return inconclusive(
                    "Prometheus query failed; disk recovery cannot be confirmed");
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
            case "NODE_DISK_FULL" -> {
                String available = "node_filesystem_avail_bytes{instance=\""
                        + instance + "\",mountpoint=\"/\",fstype!~\"tmpfs\"}";
                String size = "node_filesystem_size_bytes{instance=\""
                        + instance + "\",mountpoint=\"/\",fstype!~\"tmpfs\"}";
                yield "max by (instance) ((1 - (" + available + " / " + size + ")) * 100)";
            }
            case "NODE_INODES_FULL" -> {
                String free = "node_filesystem_files_free{instance=\""
                        + instance + "\",mountpoint=\"/\",fstype!~\"tmpfs\"}";
                String total = "node_filesystem_files{instance=\""
                        + instance + "\",mountpoint=\"/\",fstype!~\"tmpfs\"}";
                yield "max by (instance) ((1 - (" + free + " / " + total + ")) * 100)";
            }
            case "NODE_DISK_PRESSURE" ->
                    "kube_node_status_condition{node=\"" + target
                            + "\",condition=\"DiskPressure\",status=\"true\"}";
            default -> throw new IllegalArgumentException(
                    "Unsupported DISK incident type");
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
                return inconclusive("Prometheus returned an empty response");
            }

            JsonNode root = mapper.readTree(body);

            if (!"success".equals(root.path("status").asText())) {
                return inconclusive("Prometheus query status was not successful");
            }

            JsonNode results = root.path("data").path("result");
            if (!results.isArray() || results.size() != 1) {
                return inconclusive(
                        "DISK metric missing or ambiguous for " + expectedTarget);
            }

            JsonNode result = results.get(0);
            JsonNode metric = result.path("metric");
            JsonNode value = result.path("value");

            if (!value.isArray() || value.size() < 2) {
                return inconclusive("Prometheus DISK sample has an invalid format");
            }

            double measured = Double.parseDouble(value.get(1).asText());
            if (!Double.isFinite(measured)) {
                return inconclusive("Prometheus returned a non-finite DISK value");
            }

            if ("NODE_DISK_PRESSURE".equals(incidentType)) {
                String actualNode = metric.path("node").asText("");
                String condition = metric.path("condition").asText("");
                String status = metric.path("status").asText("");

                if (!expectedTarget.equals(actualNode)
                        || !"DiskPressure".equals(condition)
                        || !"true".equals(status)) {
                    return inconclusive(
                            "Prometheus returned an unexpected DiskPressure series");
                }

                if (measured != 0.0 && measured != 1.0) {
                    return inconclusive(
                            "DiskPressure metric is outside the expected 0/1 range");
                }

                boolean healthy = measured == 0.0;
                String message = "node=" + expectedTarget
                        + ", condition=DiskPressure"
                        + ", status=true"
                        + ", measured=" + measured
                        + ", recovered=" + healthy;

                return new RecoveryVerificationResult(
                        healthy, !healthy, message);
            }

            String actualInstance = metric.path("instance").asText("");
            if (!expectedInstance.equals(actualInstance)) {
                return inconclusive(
                        "Prometheus returned an unexpected filesystem instance");
            }

            if (measured < 0.0 || measured > 100.0) {
                return inconclusive(
                        "Prometheus DISK percentage is outside the valid range");
            }

            boolean healthy = measured <= threshold;
            String metricName = "NODE_DISK_FULL".equals(incidentType)
                    ? "disk-used-percent"
                    : "inode-used-percent";

            String message = "node=" + expectedTarget
                    + ", instance=" + expectedInstance
                    + ", " + metricName + "="
                    + String.format(Locale.ROOT, "%.2f", measured)
                    + ", recoveryThreshold="
                    + String.format(Locale.ROOT, "%.2f", threshold)
                    + ", recovered=" + healthy;

            return new RecoveryVerificationResult(
                    healthy, !healthy, message);

        } catch (Exception ex) {
            return inconclusive(
                    "Could not parse the Prometheus DISK response");
        }
    }

    private static RecoveryVerificationResult inconclusive(String message) {
        return new RecoveryVerificationResult(false, true, message);
    }
}