package com.orange.pfe.backend.service.remediation;

public record VerificationWithCompensationResult(
        RecoveryVerificationResult verificationResult,
        boolean compensationTriggered,
        String compensationMessage
) {
    public boolean resolved() {
        return verificationResult.resolved();
    }
}
