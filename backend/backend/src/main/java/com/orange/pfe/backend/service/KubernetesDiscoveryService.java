package com.orange.pfe.backend.service;

import com.orange.pfe.backend.config.AiopsPollingProperties;
import io.kubernetes.client.openapi.ApiClient;
import io.kubernetes.client.openapi.apis.AppsV1Api;
import io.kubernetes.client.openapi.models.V1Deployment;
import io.kubernetes.client.openapi.models.V1DeploymentList;
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

    private static final String LABEL_APP_NAME = "aiops.io/app";

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

            V1DeploymentList deployments = appsApi.listDeploymentForAllNamespaces().execute();

            for (V1Deployment d : deployments.getItems()) {
                String ns   = d.getMetadata() != null ? d.getMetadata().getNamespace() : null;
                String name = d.getMetadata() != null ? d.getMetadata().getName()      : null;

                if (ns == null || name == null) continue;
                if (EXCLUDED_NAMESPACES.contains(ns)) continue;

                Map<String, String> labels = d.getMetadata().getLabels();
                String appName = (labels != null && labels.containsKey(LABEL_APP_NAME))
                        ? labels.get(LABEL_APP_NAME)
                        : name;

                discovered.add(new AiopsPollingProperties.Target(ns, appName));
                log.debug("Auto-discovered target: {}/{}", ns, appName);
            }

            if (!discovered.isEmpty()) {
                log.info("K8s auto-discovery found {} target(s): {}", discovered.size(), discovered);
            }

        } catch (Exception ex) {
            log.debug("K8s discovery unavailable (expected outside cluster): {}", ex.getMessage());
        }

        return discovered;
    }

    private ApiClient buildClient() throws Exception {
        try {
            return Config.fromCluster();
        } catch (Exception ex) {
            return Config.defaultClient();
        }
    }
}
