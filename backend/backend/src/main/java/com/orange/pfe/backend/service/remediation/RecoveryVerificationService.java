package com.orange.pfe.backend.service.remediation;

import com.orange.pfe.backend.config.RemediationProperties;
import com.orange.pfe.backend.service.KubernetesSignalEnrichmentService;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;

import java.util.Locale;
import java.util.Map;
import java.util.function.Consumer;

/**
 * Vérification de la récupération après une remédiation d'application.
 *
 * Retrait de l'IA (11 nov. 2026) : le critère est DÉTERMINISTE — le workload est sain
 * ({@link RemediationExecutor#isWorkloadHealthy}) ET aucun marqueur d'état Kubernetes de panne (hors marqueurs nœud)
 * n'est encore présent. Aucune prédiction ni score de confiance n'intervient.
 */
@Service
public class RecoveryVerificationService {

    private static final Logger logger = LoggerFactory.getLogger(RecoveryVerificationService.class);

    private final RemediationExecutor remediationExecutor;
    private final KubernetesSignalEnrichmentService kubernetesSignalEnrichmentService;
    private final RemediationProperties properties;

    public RecoveryVerificationService(
            RemediationExecutor remediationExecutor,
            KubernetesSignalEnrichmentService kubernetesSignalEnrichmentService,
            RemediationProperties properties
    ) {
        this.remediationExecutor = remediationExecutor;
        this.kubernetesSignalEnrichmentService = kubernetesSignalEnrichmentService;
        this.properties = properties;
    }

    /**
     * Verify recovery after remediation. Returns when resolved or all retries exhausted.
     */
    public RecoveryVerificationResult verify(String namespace, String app) throws InterruptedException {
        return verify(namespace, app, msg -> {});
    }

    /**
     * Same as {@link #verify(String, String)} but calls {@code perAttemptLog} after each check
     * so callers can write per-attempt entries to the workflow trace.
     */
    public RecoveryVerificationResult verify(String namespace, String app, Consumer<String> perAttemptLog)
            throws InterruptedException {
        return verify(namespace, app, perAttemptLog, false);
    }

    /**
     * @param scaleMode SCALE_DEPLOYMENT case: the success criterion is "extra capacity is up and all pods
     *                  Ready" — a sustained load is EXPECTED (it is exactly why we scaled), so a persisting
     *                  marker must NOT count as an unresolved incident.
     */
    public RecoveryVerificationResult verify(String namespace, String app, Consumer<String> perAttemptLog,
                                             boolean scaleMode) throws InterruptedException {
        int attempts = Math.max(1, properties.getVerificationRetryAttempts());
        int retryDelaySeconds = Math.max(0, properties.getVerificationRetryDelaySeconds());
        RecoveryVerificationResult lastResult = null;

        for (int attempt = 1; attempt <= attempts; attempt++) {
            lastResult = verifyOnce(namespace, app, attempt, attempts, scaleMode);
            perAttemptLog.accept(buildVerificationSummary(attempt, attempts, lastResult, scaleMode));
            if (lastResult.resolved() || attempt == attempts) {
                return lastResult;
            }
            if (retryDelaySeconds > 0) {
                logger.info("Recovery verification still sees a fault for {}/{}; retrying in {}s",
                        namespace, app, retryDelaySeconds);
                Thread.sleep(retryDelaySeconds * 1000L);
            }
        }

        return lastResult;
    }

    private String buildVerificationSummary(int attempt, int total, RecoveryVerificationResult result, boolean scaleMode) {
        if (result.resolved()) {
            if (scaleMode) {
                return String.format(Locale.ROOT,
                        "✓ Vérification %d/%d — OK : capacité ajoutée, tous les pods (répliques) sont prêts → "
                        + "scale-up RÉSOLU (la charge soutenue est attendue, c'est la raison du scale-up)",
                        attempt, total);
            }
            return String.format(Locale.ROOT,
                    "✓ Vérification %d/%d — OK : pods tous prêts ET plus aucun marqueur de panne Kubernetes → incident RÉSOLU",
                    attempt, total);
        }
        if (scaleMode) {
            return String.format(Locale.ROOT,
                    "✗ Vérification %d/%d — les nouvelles répliques ne sont pas encore toutes prêtes",
                    attempt, total);
        }
        // Deux conditions doivent être vraies pour résoudre : workload sain ET plus aucun marqueur de panne K8s.
        String workloadPart = result.workloadHealthy()
                ? "pods prêts=OUI"
                : "pods prêts=NON (déploiement pas encore disponible)";
        String markerPart = result.faultStillPresent()
                ? "un marqueur de panne Kubernetes est encore présent"
                : "plus aucun marqueur de panne Kubernetes";
        return String.format(Locale.ROOT,
                "✗ Vérification %d/%d — pas encore résolu : %s, %s",
                attempt, total, workloadPart, markerPart);
    }

    private RecoveryVerificationResult verifyOnce(String namespace, String app, int attempt, int totalAttempts,
                                                  boolean scaleMode) {
        boolean workloadHealthy = remediationExecutor.isWorkloadHealthy(namespace, app);

        // includePodKillSignal=false: the remediation just killed/recreated the pod (expected),
        // so the historical pod-kill signal must NOT keep the incident "active" during verification.
        Map<String, Double> overrides =
                kubernetesSignalEnrichmentService.collectFeatureOverrides(namespace, app, false);
        // Marqueurs d'état Kubernetes de panne encore présents (hors marqueurs nœud). « Toujours injoignable »
        // (service_unreachable_detected) en fait partie : cAdvisor rapporte des métriques normales pour un pod isolé.
        boolean faultStillPresent = overrides.keySet().stream().anyMatch(key -> !key.contains("node")
                && (key.endsWith("_detected") || key.equals("dns_blocked") || key.equals("db_blocked")
                || key.equals("network_partition")));
        // SCALE_DEPLOYMENT: the remediation is "add capacity". Once the extra replicas are Ready the response
        // has succeeded; resolution hinges on workload health alone.
        if (scaleMode) {
            faultStillPresent = false;
        }

        String message = String.format(
                Locale.ROOT,
                "attempt=%d/%d, workloadHealthy=%s, faultStillPresent=%s",
                attempt,
                totalAttempts,
                workloadHealthy,
                faultStillPresent
        );

        logger.info("Recovery verification for {}/{} -> {}", namespace, app, message);
        return new RecoveryVerificationResult(workloadHealthy, faultStillPresent, message);
    }
}
