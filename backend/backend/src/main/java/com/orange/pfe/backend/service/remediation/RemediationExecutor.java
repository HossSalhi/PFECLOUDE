package com.orange.pfe.backend.service.remediation;

public interface RemediationExecutor {

    RemediationExecutionResult execute(RemediationPlan plan);

    boolean isWorkloadHealthy(String namespace, String deploymentName);
}
