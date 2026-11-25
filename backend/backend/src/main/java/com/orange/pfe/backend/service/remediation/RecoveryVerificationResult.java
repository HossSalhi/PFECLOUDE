package com.orange.pfe.backend.service.remediation;

/**
 * Résultat d'une vérification de récupération. {@code faultStillPresent} : un marqueur d'état Kubernetes de panne
 * est encore présent (retrait de l'IA, 11 nov. 2026 : ce champ s'appelait {@code aiStillSeesIncident}).
 */
public record RecoveryVerificationResult(
        boolean workloadHealthy,
        boolean faultStillPresent,
        String message
) {
    public boolean resolved() {
        return workloadHealthy && !faultStillPresent;
    }
}
