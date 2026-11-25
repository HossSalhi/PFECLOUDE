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
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;

/**
 * Performs health checks on every infrastructure component:
 * Kubernetes nodes, VM Monitoring (Prometheus/Loki/Grafana),
 * and VM IaCamunda (AI service, Camunda, Ollama).
 */
@Service
public class InfrastructureHealthService {

    private static final Logger log = LoggerFactory.getLogger(InfrastructureHealthService.class);

    public record ComponentStatus(String namespace, String app, boolean up, String detail) {}

    /** Dernier résultat complet de {@link #checkAll()} (VMDOWN-POLICY-20261105 : l'API n'attend plus 30 s). */
    public record Snapshot(List<ComponentStatus> statuses, long generatedAtMs) {}

    private volatile Snapshot lastSnapshot;
    private final ExecutorService pool;

    private final InfraHealthProperties props;
    private final HttpClient http;

    public InfrastructureHealthService(InfraHealthProperties props) {
        this.props = props;
        this.http = HttpClient.newBuilder()
                .connectTimeout(Duration.ofSeconds(props.getTimeoutSeconds()))
                .build();
        AtomicInteger n = new AtomicInteger();
        this.pool = Executors.newFixedThreadPool(7, r -> {
            Thread t = new Thread(r, "infra-health-" + n.incrementAndGet());
            t.setDaemon(true);
            return t;
        });
    }

    /** Dernier snapshot s'il a moins de {@code maxAgeMs}, sinon un nouveau contrôle complet (parallèle). */
    public Snapshot freshSnapshot(long maxAgeMs) {
        Snapshot s = lastSnapshot;
        if (s != null && System.currentTimeMillis() - s.generatedAtMs() <= maxAgeMs) {
            return s;
        }
        checkAll();
        return lastSnapshot;
    }

    /**
     * Checks all infrastructure components and returns their status.
     * Called by InfrastructureHealthScheduler every N seconds.
     */
    public List<ComponentStatus> checkAll() {
        // VMDOWN-POLICY-20261105 : contrôles PARALLÈLES à délai borné. Avant, ils étaient séquentiels : une VM
        // muette (.160) bloquait chaque appel ~30 s (3 services x 10 s), donc aussi l'API /api/infrastructure/health.
        Future<List<ComponentStatus>> nodes = pool.submit(this::checkKubernetesNodes);
        Future<List<ComponentStatus>> pressures = pool.submit(this::checkNodePressures);
        Future<ComponentStatus> dns = pool.submit(this::checkDnsResolution);
        // namespace="infra" : c'est le namespace attendu par l'UI Infrastructure (filtre getVmComponents ->
        // namespace === 'infra') et par buildDescription (case "infra").
        Future<ComponentStatus> prometheus = pool.submit(() -> checkVmService("infra", "prometheus", props.getPrometheusUrl()));
        Future<ComponentStatus> grafana = pool.submit(() -> checkVmService("infra", "grafana", props.getGrafanaUrl()));
        Future<ComponentStatus> loki = pool.submit(() -> checkVmService("infra", "loki", props.getLokiUrl()));
        // DÉTERMINISATION HELIOS : composants IA (ai-service, ollama) RETIRÉS de la supervision infra. camunda reste.
        Future<ComponentStatus> camunda = pool.submit(() -> checkVmService("infra", "camunda", props.getCamundaUrl()));

        List<ComponentStatus> results = new ArrayList<>();
        results.addAll(await(nodes, List.of()));
        results.addAll(await(pressures, List.of()));
        results.add(await(dns, new ComponentStatus("cluster", "coredns", true, "DNS check inconclusive: délai dépassé")));
        results.add(await(prometheus, timedOut("prometheus")));
        results.add(await(grafana, timedOut("grafana")));
        results.add(await(loki, timedOut("loki")));
        results.add(await(camunda, timedOut("camunda")));
        lastSnapshot = new Snapshot(List.copyOf(results), System.currentTimeMillis());
        return results;
    }

    private static ComponentStatus timedOut(String app) {
        return new ComponentStatus("infra", app, false, "Injoignable: délai dépassé");
    }

    /** Attend un contrôle au plus (timeout + marge) ; au-delà, repli explicite (jamais un blocage). */
    private <T> T await(Future<T> future, T fallback) {
        try {
            return future.get(props.getTimeoutSeconds() + 5L, TimeUnit.SECONDS);
        } catch (InterruptedException ie) {
            Thread.currentThread().interrupt();
            future.cancel(true);
            return fallback;
        } catch (Exception ex) {
            future.cancel(true);
            return fallback;
        }
    }

    /**
     * Vérifie l'accessibilité d'un service VM externe. UP = le service répond (HTTP < 500) ;
     * DOWN = connexion refusée/timeout (process injoignable) ou erreur serveur 5xx. On reste
     * tolérant sur le code (4xx = process up mais chemin /health absent) pour éviter les faux DOWN.
     */
    private ComponentStatus checkVmService(String namespace, String app, String url) {
        if (url == null || url.isBlank()) {
            return new ComponentStatus(namespace, app, false, "URL non configurée");
        }
        try {
            HttpRequest req = HttpRequest.newBuilder()
                    .uri(URI.create(url))
                    .timeout(Duration.ofSeconds(props.getTimeoutSeconds()))
                    .GET()
                    .build();
            HttpResponse<String> resp = http.send(req, HttpResponse.BodyHandlers.ofString());
            boolean up = resp.statusCode() < 500;
            log.debug("VM health {}/{} -> {} (HTTP {})", namespace, app, up ? "UP" : "DOWN", resp.statusCode());
            return new ComponentStatus(namespace, app, up, "HTTP " + resp.statusCode());
        } catch (Exception ex) {
            log.debug("VM health {}/{} -> DOWN: {}", namespace, app, ex.getMessage());
            return new ComponentStatus(namespace, app, false, "Injoignable: " + truncate(ex.getMessage()));
        }
    }

    /**
     * Detects DiskPressure and MemoryPressure conditions on any node.
     * Each pressured node yields one ComponentStatus(namespace="node-pressure", ...).
     */
    public List<ComponentStatus> checkNodePressures() {
        List<ComponentStatus> results = new ArrayList<>();
        try {
            ApiClient client = buildK8sClient();
            CoreV1Api coreApi = new CoreV1Api(client);
            V1NodeList nodeList = coreApi.listNode().execute();
            for (V1Node node : nodeList.getItems()) {
                String nodeName = node.getMetadata() != null ? node.getMetadata().getName() : "unknown";
                if (node.getStatus() == null || node.getStatus().getConditions() == null) continue;
                for (V1NodeCondition cond : node.getStatus().getConditions()) {
                    if (!"DiskPressure".equals(cond.getType()) && !"MemoryPressure".equals(cond.getType())) continue;
                    if (!"True".equals(cond.getStatus())) continue;
                    String detail = cond.getType() + " on " + nodeName
                            + (cond.getMessage() != null ? " — " + truncate(cond.getMessage()) : "");
                    // app name encodes node + condition so each pair has its own incident
                    results.add(new ComponentStatus("node-pressure", nodeName + "-" + cond.getType(), false, detail));
                    log.warn("Node pressure detected: {}/{} — {}", nodeName, cond.getType(), detail);
                }
            }
        } catch (Exception ex) {
            log.debug("Cannot check node pressures: {}", ex.getMessage());
        }
        return results;
    }

    /**
     * Validates in-cluster DNS by resolving a well-known cluster-internal hostname.
     * Returns DOWN when CoreDNS is misconfigured or unreachable.
     */
    public ComponentStatus checkDnsResolution() {
        try {
            java.net.InetAddress.getByName("kubernetes.default.svc.cluster.local");
            return new ComponentStatus("cluster", "coredns", true, "DNS resolution OK");
        } catch (java.net.UnknownHostException ex) {
            return new ComponentStatus("cluster", "coredns", false,
                    "DNS FAILURE — in-cluster resolution failed: " + ex.getMessage());
        } catch (Exception ex) {
            // Other errors (security, interrupted…) → treat as inconclusive, not a failure
            log.debug("DNS check inconclusive: {}", ex.getMessage());
            return new ComponentStatus("cluster", "coredns", true, "DNS check inconclusive: " + ex.getMessage());
        }
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

    List<ComponentStatus> checkKubernetesNodes() {
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
