package com.orange.pfe.backend.service.remediation;

public record RecoveryVerificationResult(
        boolean workloadHealthy,
        boolean aiStillSeesIncident,
        String message
) {
    public boolean resolved() {
        return workloadHealthy && !aiStillSeesIncident;
    }
}
