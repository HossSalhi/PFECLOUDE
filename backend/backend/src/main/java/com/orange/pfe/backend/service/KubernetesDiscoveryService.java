package com.orange.pfe.backend.service;

import com.orange.pfe.backend.config.AiopsPollingProperties;
import io.kubernetes.client.openapi.ApiClient;
import io.kubernetes.client.openapi.apis.AppsV1Api;
import io.kubernetes.client.openapi.models.V1Deployment;
import io.kubernetes.client.openapi.models.V1StatefulSet;
import io.kubernetes.client.util.Config;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.Set;

/**
 * Découvre automatiquement TOUS les Deployments Kubernetes à surveiller.
 * Aucun label requis — toute application déployée est surveillée automatiquement.
 * Les namespaces système sont exclus.
 */
@Service
public class KubernetesDiscoveryService {

    private static final Logger log = LoggerFactory.getLogger(KubernetesDiscoveryService.class);

    private static final String LABEL_AIOPS_APP   = "aiops.io/app";
    private static final String LABEL_K8S_APP     = "app.kubernetes.io/name";
    private static final String LABEL_APP         = "app";
    private static final String LABEL_SKIP        = "helios.io/monitor";

    // Namespaces système exclus de la surveillance automatique
    private static final Set<String> EXCLUDED_NAMESPACES = Set.of(
            "kube-system",
            "kube-public",
            "kube-node-lease",
            "cnpg-system",
            "cert-manager",
            "ingress-nginx",
            "monitoring",
            "local-path-storage"
    );

    private final AiopsPollingProperties properties;

    public KubernetesDiscoveryService(AiopsPollingProperties properties) {
        this.properties = properties;
    }

    /**
     * Retourne la liste de TOUS les Deployments de tous les namespaces utilisateur.
     * Aucun label requis — suffit de déployer l'application.
     */
    public List<AiopsPollingProperties.Target> discoverTargets() {
        if (!properties.isDiscoveryEnabled()) {
            return List.of();
        }

        List<AiopsPollingProperties.Target> discovered = new ArrayList<>();
        try {
            ApiClient client = buildClient();
            AppsV1Api appsApi = new AppsV1Api(client);

            // Deployments
            for (V1Deployment d : appsApi.listDeploymentForAllNamespaces().execute().getItems()) {
                String ns   = d.getMetadata() != null ? d.getMetadata().getNamespace() : null;
                String name = d.getMetadata() != null ? d.getMetadata().getName()      : null;
                if (ns == null || name == null || EXCLUDED_NAMESPACES.contains(ns)) continue;
                Map<String, String> labels = d.getMetadata().getLabels();
                if (isMonitoringDisabled(labels)) continue;
                discovered.add(new AiopsPollingProperties.Target(ns, resolveAppName(labels, name)));
            }

            // StatefulSets (bases de données, brokers, etc.)
            for (V1StatefulSet s : appsApi.listStatefulSetForAllNamespaces().execute().getItems()) {
                String ns   = s.getMetadata() != null ? s.getMetadata().getNamespace() : null;
                String name = s.getMetadata() != null ? s.getMetadata().getName()      : null;
                if (ns == null || name == null || EXCLUDED_NAMESPACES.contains(ns)) continue;
                Map<String, String> labels = s.getMetadata().getLabels();
                if (isMonitoringDisabled(labels)) continue;
                discovered.add(new AiopsPollingProperties.Target(ns, resolveAppName(labels, name)));
            }

            if (!discovered.isEmpty()) {
                log.info("K8s auto-discovery found {} target(s): {}", discovered.size(), discovered);
            }

        } catch (Exception ex) {
            log.debug("K8s discovery unavailable (expected outside cluster): {}", ex.getMessage());
        }

        return discovered;
    }

    /** Priorité : aiops.io/app > app.kubernetes.io/name > app > metadata.name */
    private String resolveAppName(Map<String, String> labels, String fallback) {
        if (labels == null) return fallback;
        if (labels.containsKey(LABEL_AIOPS_APP)) return labels.get(LABEL_AIOPS_APP);
        if (labels.containsKey(LABEL_K8S_APP))   return labels.get(LABEL_K8S_APP);
        if (labels.containsKey(LABEL_APP))        return labels.get(LABEL_APP);
        return fallback;
    }

    /** Retourne true si le label helios.io/monitor=false est positionné (opt-out explicite). */
    private boolean isMonitoringDisabled(Map<String, String> labels) {
        return labels != null && "false".equalsIgnoreCase(labels.get(LABEL_SKIP));
    }

    private ApiClient buildClient() throws Exception {
        try {
            return Config.fromCluster();
        } catch (Exception ex) {
            return Config.defaultClient();
        }
    }
}
