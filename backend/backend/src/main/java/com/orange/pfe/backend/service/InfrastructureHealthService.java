package com.orange.pfe.backend.service;

import com.orange.pfe.backend.config.InfraHealthProperties;
import io.kubernetes.client.openapi.ApiClient;
import io.kubernetes.client.openapi.apis.CoreV1Api;
import io.kubernetes.client.openapi.models.V1Node;
import io.kubernetes.client.openapi.models.V1NodeCondition;
import io.kubernetes.client.openapi.models.V1NodeList;
import io.kubernetes.client.util.Config;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;

import java.io.IOException;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.time.Duration;
import java.util.ArrayList;
import java.util.List;

/**
 * Performs health checks on every infrastructure component:
 * Kubernetes nodes, VM Monitoring (Prometheus/Loki/Grafana),
 * and VM IaCamunda (AI service, Camunda, Ollama).
 */
@Service
public class InfrastructureHealthService {

    private static final Logger log = LoggerFactory.getLogger(InfrastructureHealthService.class);

    public record ComponentStatus(String namespace, String app, boolean up, String detail) {}

    private final InfraHealthProperties props;
    private final HttpClient http;

    public InfrastructureHealthService(InfraHealthProperties props) {
        this.props = props;
        this.http = HttpClient.newBuilder()
                .connectTimeout(Duration.ofSeconds(props.getTimeoutSeconds()))
                .build();
    }

    /**
     * Checks all infrastructure components and returns their status.
     * Called by InfrastructureHealthScheduler every N seconds.
     */
    public List<ComponentStatus> checkAll() {
        List<ComponentStatus> results = new ArrayList<>();

        // --- VM Monitoring ---
        results.add(checkHttp("infra", "prometheus", props.getPrometheusUrl() + "/-/healthy"));
        results.add(checkHttp("infra", "loki",       props.getLokiUrl()       + "/loki/api/v1/labels"));
        results.add(checkHttp("infra", "grafana",    props.getGrafanaUrl()    + "/api/health"));

        // --- VM IaCamunda ---
        results.add(checkHttp("infra", "ai-service", props.getAiServiceUrl()  + "/health"));
        results.add(checkHttp("infra", "camunda",    props.getCamundaUrl()    + "/v2/topology"));
        results.add(checkHttp("infra", "ollama",     props.getOllamaUrl()     + "/api/tags"));

        // --- Kubernetes nodes ---
        results.addAll(checkKubernetesNodes());

        return results;
    }

    // -------------------------------------------------------------------------

    private ComponentStatus checkHttp(String namespace, String app, String url) {
        try {
            HttpRequest req = HttpRequest.newBuilder()
                    .uri(URI.create(url))
                    .timeout(Duration.ofSeconds(props.getTimeoutSeconds()))
                    .GET()
                    .build();
            HttpResponse<String> resp = http.send(req, HttpResponse.BodyHandlers.ofString());
            boolean up = resp.statusCode() >= 200 && resp.statusCode() < 400;
            log.debug("Health check {}/{} → {} ({})", namespace, app, up ? "UP" : "DOWN", resp.statusCode());
            return new ComponentStatus(namespace, app, up,
                    up ? "HTTP " + resp.statusCode() : "HTTP " + resp.statusCode() + " — " + truncate(resp.body()));
        } catch (Exception ex) {
            log.debug("Health check {}/{} → DOWN: {}", namespace, app, ex.getMessage());
            return new ComponentStatus(namespace, app, false, "Unreachable: " + ex.getMessage());
        }
    }

    private List<ComponentStatus> checkKubernetesNodes() {
        List<ComponentStatus> nodeStatuses = new ArrayList<>();
        try {
            ApiClient client = buildK8sClient();
            CoreV1Api coreApi = new CoreV1Api(client);
            V1NodeList nodeList = coreApi.listNode().execute();

            for (V1Node node : nodeList.getItems()) {
                String nodeName = node.getMetadata() != null ? node.getMetadata().getName() : "unknown";
                boolean ready = isNodeReady(node);
                String detail = ready ? "Ready" : "NotReady — conditions: " + summarizeConditions(node);
                nodeStatuses.add(new ComponentStatus("k8s-nodes", nodeName, ready, detail));
                log.debug("Node {} → {}", nodeName, ready ? "Ready" : "NotReady");
            }
        } catch (Exception ex) {
            log.debug("Cannot check K8s nodes: {}", ex.getMessage());
        }
        return nodeStatuses;
    }

    private boolean isNodeReady(V1Node node) {
        if (node.getStatus() == null || node.getStatus().getConditions() == null) return false;
        return node.getStatus().getConditions().stream()
                .filter(c -> "Ready".equals(c.getType()))
                .anyMatch(c -> "True".equals(c.getStatus()));
    }

    private String summarizeConditions(V1Node node) {
        if (node.getStatus() == null || node.getStatus().getConditions() == null) return "none";
        StringBuilder sb = new StringBuilder();
        for (V1NodeCondition cond : node.getStatus().getConditions()) {
            if (!"True".equals(cond.getStatus()) && !"False".equals(cond.getStatus())) continue;
            if ("Ready".equals(cond.getType()) && "True".equals(cond.getStatus())) continue;
            sb.append(cond.getType()).append("=").append(cond.getStatus()).append(" ");
        }
        return sb.toString().trim();
    }

    private ApiClient buildK8sClient() throws IOException {
        try {
            return Config.fromCluster();
        } catch (Exception ex) {
            return Config.defaultClient();
        }
    }

    private String truncate(String s) {
        if (s == null) return "";
        return s.length() > 120 ? s.substring(0, 120) + "..." : s;
    }
}
