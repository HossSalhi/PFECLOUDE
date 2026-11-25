package com.orange.pfe.backend.service.remediation;

public interface RemediationExecutor {

    RemediationExecutionResult execute(RemediationPlan plan);

    boolean isWorkloadHealthy(String namespace, String deploymentName);

    /** True if the given Kubernetes NODE exists and its Ready condition is True. Used to verify recovery
     *  of a NODE_NOT_READY incident (the "workload" is a node, not a deployment). */
    boolean isNodeReady(String nodeName);

    /**
     * Contrôle détaillé de l'état Ready.
     * Les implémentations historiques échouent de manière conservatrice :
     * un false ne permet pas de confirmer la disponibilité de l'API.
     */
    default NodeReadinessObservation inspectNodeReadiness(String nodeName) {
        boolean ready = isNodeReady(nodeName);

        return new NodeReadinessObservation(
                ready,
                ready,
                ready ? "True" : "UNKNOWN",
                ready
                        ? "Node " + nodeName + " is Ready"
                        : "Node readiness is not confirmed by the executor"
        );
    }

    /**
     * Lecture des pods d'un ensemble étiqueté (ex. {@code cnpg.io/cluster=postgres}). Défaut CONSERVATEUR : un
     * exécuteur qui n'implémente pas ce contrat ne prouve rien ({@link PodSetObservation#unavailable()}).
     */
    default PodSetObservation inspectPods(String namespace, String labelSelector) {
        return PodSetObservation.unavailable();
    }

    /**
     * Arrête le générateur de charge CPU de LABORATOIRE (helios-cpuload.service) après une récupération
     * CPU confirmée (vérif OK + incident RESOLVED). Strictement borné : nœuds de labo uniquement, via
     * l'action dédiée STOP_CPU_LOAD_SERVICE du Bridge (la cible/service est résolue côté serveur, jamais
     * fournie par l'appelant). Une seule tentative, best-effort, jamais de redémarrage. Fail-closed par
     * défaut : un exécuteur qui n'implémente pas ce contrat ne fait rien et signale l'échec.
     */
    default RemediationExecutionResult stopCpuLoadServiceAfterRecovery(
            String namespace, String node, String incidentType) {
        return new RemediationExecutionResult(false, node,
                "Arrêt de charge CPU non supporté par cet exécuteur.");
    }
}
