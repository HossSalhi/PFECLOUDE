package com.orange.pfe.backend.service.remediation;

import com.orange.pfe.backend.config.RemediationProperties;
import com.orange.pfe.backend.dto.AIPredictRequest;
import com.orange.pfe.backend.dto.AIPredictResponse;
import com.orange.pfe.backend.model.action.ActionType;
import com.orange.pfe.backend.model.action.AutomationMode;
import com.orange.pfe.backend.model.action.RemediationTargetKind;
import com.orange.pfe.backend.service.AIOpsClientService;
import com.orange.pfe.backend.service.KubernetesSignalEnrichmentService;
import com.orange.pfe.backend.service.RemediationHistoryContextService;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;

import java.util.Locale;
import java.util.function.Consumer;

@Service
public class RecoveryVerificationService {

    private static final Logger logger = LoggerFactory.getLogger(RecoveryVerificationService.class);

    private final RemediationExecutor remediationExecutor;
    private final AIOpsClientService aiOpsClientService;
    private final KubernetesSignalEnrichmentService kubernetesSignalEnrichmentService;
    private final RemediationHistoryContextService remediationHistoryContextService;
    private final RemediationProperties properties;

    public RecoveryVerificationService(
            RemediationExecutor remediationExecutor,
            AIOpsClientService aiOpsClientService,
            KubernetesSignalEnrichmentService kubernetesSignalEnrichmentService,
            RemediationHistoryContextService remediationHistoryContextService,
            RemediationProperties properties
    ) {
        this.remediationExecutor = remediationExecutor;
        this.aiOpsClientService = aiOpsClientService;
        this.kubernetesSignalEnrichmentService = kubernetesSignalEnrichmentService;
        this.remediationHistoryContextService = remediationHistoryContextService;
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
     * @param scaleMode AUTOSCALING/SCALE_DEPLOYMENT case: the success criterion is "extra capacity is up
     *                  and all pods Ready" — the AI continuing to see high load is EXPECTED (sustained
     *                  load is exactly why we scaled), so it must NOT count as an unresolved incident.
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
                logger.info("Recovery verification still sees an incident for {}/{}; retrying in {}s",
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
                        + "autoscaling RÉSOLU (la charge soutenue est attendue, c'est la raison du scale-up)",
                        attempt, total);
            }
            return String.format(Locale.ROOT,
                    "✓ Vérification %d/%d — OK : pods tous prêts ET l'IA ne détecte plus d'anomalie → incident RÉSOLU",
                    attempt, total);
        }
        if (scaleMode) {
            return String.format(Locale.ROOT,
                    "✗ Vérification %d/%d — les nouvelles répliques ne sont pas encore toutes prêtes",
                    attempt, total);
        }
        // Parse aiConfidence from the raw message ("...aiConfidence=0.8765,...")
        String conf = "?";
        for (String part : result.message().split(",")) {
            String p = part.trim();
            if (p.startsWith("aiConfidence=")) {
                conf = p.substring("aiConfidence=".length());
                break;
            }
        }
        // Deux conditions doivent être vraies pour résoudre : workload sain ET IA ne voit plus rien.
        String workloadPart = result.workloadHealthy()
                ? "pods prêts=OUI"
                : "pods prêts=NON (déploiement pas encore disponible)";
        String aiPart = result.aiStillSeesIncident()
                ? "IA détecte encore l'anomalie"
                : "IA ne voit plus rien";
        return String.format(Locale.ROOT,
                "✗ Vérification %d/%d — pas encore résolu : %s, %s (confiance IA %s)",
                attempt, total, workloadPart, aiPart, conf);
    }

    /**
     * Verify recovery and automatically execute a compensating action if the remediation
     * worsened the situation (AI confidence increased by more than {@code degradationThresholdRatio}).
     *
     * @param namespace           target namespace
     * @param app                 target application name
     * @param lastAction          the action that was just executed (used to pick the right compensation)
     * @param preActionConfidence AI confidence score captured before the remediation ran (0 = unknown)
     */
    public VerificationWithCompensationResult verifyWithAutoRollback(
            String namespace,
            String app,
            ActionType lastAction,
            double preActionConfidence
    ) throws InterruptedException {

        RecoveryVerificationResult result = verify(namespace, app);

        if (result.resolved()) {
            return new VerificationWithCompensationResult(result, false, "recovery confirmed — no compensation needed");
        }

        if (!properties.isAutoRollbackEnabled() || preActionConfidence <= 0) {
            return new VerificationWithCompensationResult(result, false, "auto-rollback disabled or pre-action confidence unknown");
        }

        // Query current AI state one final time to measure post-remediation confidence
        AIPredictRequest check = new AIPredictRequest();
        check.setNamespace(namespace);
        check.setApp(app);
        check.setWindowMinutes(Math.max(1, properties.getVerificationWindowMinutes()));
        check.setFeatureOverrides(kubernetesSignalEnrichmentService.collectFeatureOverrides(namespace, app, false));
        check.setRemediationContext(remediationHistoryContextService.buildContext(namespace, app));

        AIPredictResponse currentState = aiOpsClientService.predict(check);
        double postConfidence = currentState.getConfidence() == null ? 0.0 : currentState.getConfidence();

        boolean degraded = postConfidence > preActionConfidence * properties.getDegradationThresholdRatio();

        if (!degraded) {
            logger.info("No degradation for {}/{}: preConfidence={} postConfidence={}", namespace, app,
                    preActionConfidence, postConfidence);
            return new VerificationWithCompensationResult(result, false,
                    String.format(Locale.ROOT, "no degradation detected (pre=%.3f post=%.3f)", preActionConfidence, postConfidence));
        }

        logger.warn("Degradation detected for {}/{}: confidence {:.3f} → {:.3f} (threshold={}x). Triggering auto-compensation.",
                namespace, app, preActionConfidence, postConfidence, properties.getDegradationThresholdRatio());

        String compensationMsg = executeCompensation(namespace, app, lastAction, preActionConfidence, postConfidence);

        return new VerificationWithCompensationResult(result, true, compensationMsg);
    }

    // ── Private helpers ───────────────────────────────────────────────────────

    private String executeCompensation(
            String namespace, String app,
            ActionType lastAction,
            double preConfidence, double postConfidence
    ) {
        String rationale = String.format(Locale.ROOT,
                "Auto-rollback: remediation worsened incident (confidence %.3f → %.3f)", preConfidence, postConfidence);

        if (lastAction == ActionType.SCALE_DEPLOYMENT) {
            // Scale was applied and things got worse — scale back down first
            RemediationExecutionResult scaleDown = remediationExecutor.execute(
                    compensationPlan(ActionType.SCALE_DOWN, namespace, app, rationale + " — scaling down first"));
            logger.info("Auto scale-down result for {}/{}: {}", namespace, app, scaleDown.message());

            // Then roll back to the previous image/config
            RemediationExecutionResult rollback = remediationExecutor.execute(
                    compensationPlan(ActionType.ROLLBACK_DEPLOYMENT, namespace, app, rationale + " — rolling back deployment"));
            logger.info("Auto rollback result for {}/{}: {}", namespace, app, rollback.message());

            return "scale-down=[" + scaleDown.message() + "] rollback=[" + rollback.message() + "]";
        }

        if (lastAction == ActionType.FORCE_RECREATE_DEPLOYMENT
                || lastAction == ActionType.RESTART_POD
                || lastAction == ActionType.DELETE_CRASHLOOPING_PODS) {
            // Restart made things worse — roll back to previous image
            RemediationExecutionResult rollback = remediationExecutor.execute(
                    compensationPlan(ActionType.ROLLBACK_DEPLOYMENT, namespace, app, rationale));
            logger.info("Auto rollback result for {}/{}: {}", namespace, app, rollback.message());
            return "rollback=[" + rollback.message() + "]";
        }

        // For other actions log and escalate — do not attempt further automated changes
        logger.warn("Auto-rollback: no compensation strategy for lastAction={} on {}/{}", lastAction, namespace, app);
        return "no automated compensation for action=" + lastAction + "; requires manual intervention";
    }

    private RemediationPlan compensationPlan(ActionType actionType, String namespace, String app, String rationale) {
        return new RemediationPlan(
                actionType,
                AutomationMode.AUTO,
                false,   // approvalRequired
                true,    // autoExecutable
                false,   // manualFollowUp
                false,   // cooldownBlocked
                false,   // dbRelated
                namespace,
                RemediationTargetKind.DEPLOYMENT,
                app,
                rationale
        );
    }

    private RecoveryVerificationResult verifyOnce(String namespace, String app, int attempt, int totalAttempts,
                                                  boolean scaleMode) {
        boolean workloadHealthy = remediationExecutor.isWorkloadHealthy(namespace, app);

        AIPredictRequest request = new AIPredictRequest();
        request.setNamespace(namespace);
        request.setApp(app);
        request.setWindowMinutes(Math.max(1, properties.getVerificationWindowMinutes()));
        // includePodKillSignal=false: the remediation just killed/recreated the pod (expected),
        // so the historical pod-kill signal must NOT keep the incident "active" during verification.
        java.util.Map<String, Double> overrides =
                kubernetesSignalEnrichmentService.collectFeatureOverrides(namespace, app, false);
        // Reachability marker: cAdvisor reports normal metrics for a partitioned pod, so the AI alone
        // would wrongly verify "resolved". Treat "still unreachable" as the incident persisting.
        boolean stillUnreachable = overrides.remove("service_unreachable_detected") != null;
        request.setFeatureOverrides(overrides);
        request.setRemediationContext(remediationHistoryContextService.buildContext(namespace, app));

        AIPredictResponse followUp = aiOpsClientService.predict(request);
        double followUpConfidence = followUp.getConfidence() == null ? 0.0d : followUp.getConfidence();
        boolean rawIncidentSignal = followUp.isIncident();
        boolean confidencePersistent = followUpConfidence >= properties.getVerificationIncidentConfidenceThreshold();
        boolean aiStillSeesIncident = stillUnreachable || (rawIncidentSignal && confidencePersistent);
        // AUTOSCALING: the remediation is "add capacity". Once the extra replicas are Ready the response
        // has succeeded; the AI legitimately still reads high load (that is WHY we scaled), so we must not
        // treat that as an unresolved incident — resolution hinges on workload health alone.
        if (scaleMode) {
            aiStillSeesIncident = false;
        }

        String message = String.format(
                Locale.ROOT,
                "attempt=%d/%d, workloadHealthy=%s, aiStillSeesIncident=%s, rawAiIncident=%s, aiIncidentType=%s, aiConfidence=%s, verificationThreshold=%.2f",
                attempt,
                totalAttempts,
                workloadHealthy,
                aiStillSeesIncident,
                rawIncidentSignal,
                safeValue(followUp.getIncidentType()),
                followUp.getConfidence() == null ? "n/a" : String.format(Locale.ROOT, "%.4f", followUp.getConfidence()),
                properties.getVerificationIncidentConfidenceThreshold()
        );

        logger.info("Recovery verification for {}/{} -> {}", namespace, app, message);
        return new RecoveryVerificationResult(workloadHealthy, aiStillSeesIncident, message);
    }

    private String safeValue(String value) {
        return value == null || value.isBlank() ? "n/a" : value;
    }
}
