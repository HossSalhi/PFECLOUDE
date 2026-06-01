package com.orange.pfe.backend.service.remediation;

public record RemediationExecutionResult(
        boolean success,
        String targetName,
        String message
) {
}
