package com.orange.pfe.backend.service.remediation;

public interface RemediationExecutor {

    RemediationExecutionResult execute(RemediationPlan plan);

    boolean isWorkloadHealthy(String namespace, String deploymentName);

    /** True if the given Kubernetes NODE exists and its Ready condition is True. Used to verify recovery
     *  of a NODE_NOT_READY incident (the "workload" is a node, not a deployment). */
    boolean isNodeReady(String nodeName);
}
