package com.orange.pfe.backend.service;

import com.orange.pfe.backend.config.RemediationProperties;
import io.kubernetes.client.openapi.ApiClient;
import io.kubernetes.client.openapi.ApiException;
import io.kubernetes.client.openapi.Configuration;
import io.kubernetes.client.openapi.apis.CoreV1Api;
import io.kubernetes.client.openapi.models.V1Node;
import io.kubernetes.client.openapi.models.V1NodeCondition;
import io.kubernetes.client.openapi.models.V1Pod;
import io.kubernetes.client.util.ClientBuilder;
import io.kubernetes.client.util.Config;
import io.kubernetes.client.util.KubeConfig;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;

import java.io.FileReader;
import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.stream.Collectors;

@Service
public class KubernetesSignalEnrichmentService {

    private static final Logger logger = LoggerFactory.getLogger(KubernetesSignalEnrichmentService.class);

    private final RemediationProperties properties;
    private volatile CoreV1Api coreV1Api;

    public KubernetesSignalEnrichmentService(RemediationProperties properties) {
        this.properties = properties;
    }

    public Map<String, Double> collectFeatureOverrides(String namespace, String app) {
        Map<String, Double> overrides = new LinkedHashMap<>();

        try {
            ensureApi();
            List<V1Pod> pods = coreV1Api.listNamespacedPod(namespace)
                    .labelSelector("app=" + app)
                    .execute()
                    .getItems();

            Set<String> nodeNames = pods.stream()
                    .map(V1Pod::getSpec)
                    .filter(spec -> spec != null && spec.getNodeName() != null && !spec.getNodeName().isBlank())
                    .map(spec -> spec.getNodeName().trim())
                    .collect(Collectors.toSet());

            List<V1Node> nodes = coreV1Api.listNode().execute().getItems();
            double appNodeNotReadyCount = nodes.stream()
                    .filter(node -> node.getMetadata() != null && node.getMetadata().getName() != null)
                    .filter(node -> !nodeNames.isEmpty() && nodeNames.contains(node.getMetadata().getName()))
                    .mapToDouble(node -> isNodeReady(node) ? 0.0d : 1.0d)
                    .sum();
            double workerNodeNotReadyCount = nodes.stream()
                    .filter(this::isWorkerNode)
                    .mapToDouble(node -> isNodeReady(node) ? 0.0d : 1.0d)
                    .sum();

            overrides.put("node_not_ready_count", Math.max(appNodeNotReadyCount, workerNodeNotReadyCount));
        } catch (ApiException | IOException ex) {
            logger.debug(
                    "Failed to enrich AI prediction with Kubernetes signals for {}/{}: {}",
                    namespace,
                    app,
                    ex.getMessage()
            );
        }

        return overrides;
    }

    private boolean isNodeReady(V1Node node) {
        if (node.getStatus() == null || node.getStatus().getConditions() == null) {
            return false;
        }

        for (V1NodeCondition condition : node.getStatus().getConditions()) {
            if (!"Ready".equals(condition.getType())) {
                continue;
            }
            return "True".equalsIgnoreCase(String.valueOf(condition.getStatus()));
        }

        return false;
    }

    private boolean isWorkerNode(V1Node node) {
        if (node.getMetadata() == null || node.getMetadata().getLabels() == null) {
            return true;
        }

        Map<String, String> labels = node.getMetadata().getLabels();
        return !labels.containsKey("node-role.kubernetes.io/control-plane")
                && !labels.containsKey("node-role.kubernetes.io/master");
    }

    private void ensureApi() throws IOException {
        if (coreV1Api != null) {
            return;
        }

        synchronized (this) {
            if (coreV1Api != null) {
                return;
            }

            ApiClient client = buildClient();
            Configuration.setDefaultApiClient(client);
            coreV1Api = new CoreV1Api(client);
        }
    }

    private ApiClient buildClient() throws IOException {
        String kubeconfigPath = properties.getKubeconfigPath();
        if (kubeconfigPath != null && !kubeconfigPath.isBlank() && Files.exists(Path.of(kubeconfigPath))) {
            try (FileReader fileReader = new FileReader(kubeconfigPath)) {
                KubeConfig kubeConfig = KubeConfig.loadKubeConfig(fileReader);
                return ClientBuilder.kubeconfig(kubeConfig).build();
            }
        }
        try {
            return ClientBuilder.cluster().build();
        } catch (RuntimeException ex) {
            logger.info(
                    "In-cluster Kubernetes client configuration unavailable for signal enrichment, falling back to default client discovery"
            );
        }
        return Config.defaultClient();
    }
}
