package com.orange.pfe.backend.service.remediation;

import com.orange.pfe.backend.config.RemediationProperties;
import com.orange.pfe.backend.dto.AIOpsAnalyzeRequest;
import com.orange.pfe.backend.dto.AIPredictResponse;
import com.orange.pfe.backend.model.action.ActionType;
import com.orange.pfe.backend.model.action.AutomationMode;
import com.orange.pfe.backend.model.action.RemediationTargetKind;
import com.orange.pfe.backend.model.enums.IncidentSeverity;
import com.orange.pfe.backend.repository.IncidentActionRepository;
import org.springframework.stereotype.Service;

import java.time.LocalDateTime;
import java.util.Locale;
import java.util.Set;

@Service
public class RemediationPolicyService {

    private final RemediationProperties properties;
    private final IncidentActionRepository actionRepository;

    public RemediationPolicyService(
            RemediationProperties properties,
            IncidentActionRepository actionRepository
    ) {
        this.properties = properties;
        this.actionRepository = actionRepository;
    }

    public RemediationPlan buildPlan(AIOpsAnalyzeRequest request, AIPredictResponse prediction) {
        boolean recommendationOnly = isRecommendationOnly(prediction);
        ActionType actionType = recommendationOnly
                ? null
                : mapActionType(preferredExecutableAction(prediction), prediction.getIncidentType());
        // Auto-démarrage VM : RESTART_VM UNIQUEMENT pour le vrai incident nœud — namespace
        // canonique "k8s-nodes" où app = nom du nœud (k8s-worker1). Un node-down crée aussi des
        // incidents pour les apps affectées (ex. pfe/podinfo) ; ceux-là NE doivent PAS recevoir
        // RESTART_VM (sinon target=app, pas le nœud -> l'agent rejette). Le nom de nœud (app) sert
        // de target pour l'agent vmrun.
        if (!recommendationOnly && "k8s-nodes".equalsIgnoreCase(request.getNamespace())
                && prediction.getIncidentType() != null
                && prediction.getIncidentType().toUpperCase(Locale.ROOT).contains("NODE")) {
            actionType = ActionType.RESTART_VM;
        }
        String targetNamespace = request.getNamespace();
        String targetName = request.getApp();
        RemediationTargetKind targetKind = mapTargetKind(actionType);
        boolean dbRelated = isDbRelated(prediction);
        boolean cooldownBlocked = isCooldownBlocked(targetNamespace, targetName, actionType, prediction.getIncidentType());
        boolean confidenceEligible = isConfidenceEligible(actionType, prediction.getConfidence());
        boolean severityEligible = isSeverityEligible(prediction.getSeverity());
        boolean appAllowlisted = isAppAllowlisted(request.getApp());
        boolean namespaceAllowlisted = isNamespaceAllowlisted(request.getNamespace());
        // Safe actions can execute automatically without human approval
        boolean safeAction = isSafeAction(actionType);
        boolean allowedAction = actionType != null && properties.getAllowedActions().contains(actionType);
        boolean autoExecutable = properties.isAutomationEnabled()
                && actionType != null
                && safeAction
                && allowedAction
                && appAllowlisted
                && namespaceAllowlisted
                && confidenceEligible
                && severityEligible
                && !dbRelated
                && !cooldownBlocked;
        boolean manualFollowUp = recommendationOnly
                || actionType == null
                || actionType == ActionType.INVESTIGATION
                || actionType == ActionType.ESCALATION
                || dbRelated;
        boolean approvalRequired = !recommendationOnly && !autoExecutable && prediction.isIncident();
        // Incident NŒUD = récupération 100% automatique par le watchdog hôte (rallume la
        // VM), quel que soit le namespace de détection (k8s-nodes, kube-system, ...). On
        // affiche donc le mode AUTO (pas d'action manuelle attendue de l'opérateur).
        boolean nodeIncident = isNodeIncident(prediction.getIncidentType(), request.getApp());
        AutomationMode automationMode = (autoExecutable || nodeIncident)
                ? AutomationMode.AUTO : AutomationMode.MANUAL;
        String rationale = buildRationale(
                actionType, autoExecutable, approvalRequired, cooldownBlocked, dbRelated,
                confidenceEligible, severityEligible, appAllowlisted, namespaceAllowlisted,
                recommendationOnly, prediction.getRecommendationOnlyReason()
        );

        return new RemediationPlan(
                actionType,
                automationMode,
                approvalRequired,
                autoExecutable,
                manualFollowUp,
                cooldownBlocked,
                dbRelated,
                targetNamespace,
                targetKind,
                targetName,
                rationale
        );
    }

    // ── Safe action classification ────────────────────────────────────────────

    /**
     * Safe actions are low-risk enough to execute automatically without human approval.
     * Rollbacks, node operations, and drains always require approval for safety.
     */
    private boolean isSafeAction(ActionType actionType) {
        return actionType == ActionType.RESTART_POD
                || actionType == ActionType.SCALE_DEPLOYMENT
                || actionType == ActionType.SCALE_DOWN
                || actionType == ActionType.DELETE_CRASHLOOPING_PODS
                || actionType == ActionType.REMOVE_NETWORK_POLICY
                || actionType == ActionType.FORCE_RECREATE_DEPLOYMENT
                // RESTART_VM auto-exécutable : power-on d'un worker tombé via l'agent hôte
                // (ne fait que démarrer la VM, jamais l'éteindre -> sûr).
                || actionType == ActionType.RESTART_VM;
    }

    // Un incident est "noeud" si le type contient NODE ou si la cible est un noeud K8s
    // (k8s-worker / k8s-master). Ces incidents sont recuperes automatiquement par le
    // watchdog hote -> affiches en mode AUTO quel que soit le namespace de detection.
    private boolean isNodeIncident(String incidentType, String app) {
        boolean typeNode = incidentType != null
                && incidentType.toUpperCase(Locale.ROOT).contains("NODE");
        boolean appNode = app != null
                && (app.toLowerCase(Locale.ROOT).startsWith("k8s-worker")
                 || app.toLowerCase(Locale.ROOT).startsWith("k8s-master"));
        return typeNode || appNode;
    }

    // ── Allowlist checks (support "*" wildcard) ───────────────────────────────

    private boolean isAppAllowlisted(String app) {
        if (app == null || app.isBlank()) {
            return false;
        }
        Set<String> allowlist = properties.getAllowlistedApps();
        if (allowlist.contains("*")) {
            return true;
        }
        return allowlist.stream().anyMatch(v -> v.equalsIgnoreCase(app));
    }

    private boolean isNamespaceAllowlisted(String namespace) {
        if (namespace == null || namespace.isBlank()) {
            return false;
        }
        Set<String> allowlist = properties.getAllowlistedNamespaces();
        if (allowlist.contains("*")) {
            return true;
        }
        return allowlist.stream().anyMatch(v -> v.equalsIgnoreCase(namespace));
    }

    // ── Action type mapping ───────────────────────────────────────────────────

    private boolean isRecommendationOnly(AIPredictResponse prediction) {
        if (prediction == null) return true;
        double confidence = prediction.getConfidence() != null ? prediction.getConfidence() : 0.0;
        // confidence >= approvalThreshold → at least WAITING_FOR_APPROVAL, ignore AI's RECOMMEND_ONLY
        if (confidence >= properties.getApprovalConfidenceThreshold()) {
            return false;
        }
        // below threshold → respect AI decision
        return "RECOMMEND_ONLY".equalsIgnoreCase(prediction.getExecutionMode());
    }

    private String preferredExecutableAction(AIPredictResponse prediction) {
        if (prediction == null) return null;
        if (prediction.getFinalExecutableAction() != null && !prediction.getFinalExecutableAction().isBlank()) {
            return prediction.getFinalExecutableAction();
        }
        return prediction.getRecommendedAction();
    }

    public ActionType mapActionType(String recommendedAction, String incidentType) {
        if (recommendedAction == null || recommendedAction.isBlank()) {
            return ActionType.FORCE_RECREATE_DEPLOYMENT; // concrete default, never passive investigation
        }

        String normalized = recommendedAction.trim().toUpperCase(Locale.ROOT);

        switch (normalized) {
            case "NO_ACTION":
                return null;

            // ── Rollback actions ──────────────────────────────────────────────
            case "ROLLBACK_RECENT_CHANGE_AND_RESTART":
            case "ROLLBACK_LAST_RELEASE_AND_SHED_TRAFFIC":
            case "SHIFT_TRAFFIC_TO_STABLE_REVISION_AND_INVESTIGATE":
            case "ROLLBACK_DEPLOYMENT":
                return ActionType.ROLLBACK_DEPLOYMENT;

            // ── Scale-down actions ────────────────────────────────────────────
            case "SCALE_DOWN":
            case "SCALE_DOWN_AND_INVESTIGATE":
            case "REDUCE_REPLICAS":
                return ActionType.SCALE_DOWN;

            // ── Crashloop cleanup ─────────────────────────────────────────────
            case "DELETE_CRASHLOOPING_PODS":
            case "CLEANUP_CRASHLOOPING_PODS":
            case "CLEAN_CRASHLOOP_PODS":
                return ActionType.DELETE_CRASHLOOPING_PODS;

            // ── Force recreate ────────────────────────────────────────────────
            case "FORCE_RECREATE_DEPLOYMENT":
            case "FORCE_RESTART_ALL_PODS":
            case "ROLLING_RESTART":
                return ActionType.FORCE_RECREATE_DEPLOYMENT;

            // ── Cordon / drain ────────────────────────────────────────────────
            case "CORDON_NODE":
            case "CORDON_AND_INVESTIGATE":
                return ActionType.CORDON_NODE;
            case "DRAIN_NODE":
            case "DRAIN_FAILING_NODE":
            case "CORDON_AND_DRAIN_NODE":
                return ActionType.DRAIN_NODE;

            // ── Restart actions ───────────────────────────────────────────────
            case "RESTART_OR_SCALE":
            case "RESTART_POD_AND_INVESTIGATE":
            case "RESTART_FAILED_WORKLOAD_AND_REBALANCE":
            case "RESTART_POD_AND_CAPTURE_CPU_PROFILE":
            case "SCALE_DEPLOYMENT_AND_CAPTURE_CPU_PROFILE":
            case "RESTART_POD_AND_TUNE_MEMORY_LIMITS":
            case "RESTART_POD_AND_SCALE_MEMORY_LIMITS":
            case "SCALE_MEMORY_LIMITS_AND_RESTART_POD":
            case "CAPTURE_DIAGNOSTICS_AND_INVESTIGATE":
            case "INVESTIGATE_DEPENDENCY_LATENCY":
            case "TRACE_DEPENDENCY_LATENCY_AND_INVESTIGATE":
            case "CHECK_DB_CONNECTIVITY_AND_RESTART":
            case "CHECK_DATABASE_CONNECTION_POOL_AND_RESTART":
            case "FAILOVER_DATABASE_AND_RESTART_WORKLOAD":
                // Rolling restart of the WHOLE deployment (all replicas) — clears a runaway process
                // on EVERY pod (a single-pod restart leaves the stress on the other replica). Real
                // command "kubectl rollout restart deployment/X".
                return ActionType.FORCE_RECREATE_DEPLOYMENT;

            // ── Network policy removal (partition / DNS / service unreachable) ─────
            case "REMOVE_NETWORK_POLICY":
            case "REMOVE_BLOCKING_NETWORK_POLICY":
            case "DELETE_NETWORK_POLICY":
                return ActionType.REMOVE_NETWORK_POLICY;

            // ── Scale-up actions ──────────────────────────────────────────────
            case "ESCALATE_AND_RECOVER_REPLICAS":
            case "SCALE_DEPLOYMENT_AND_REBALANCE":
            case "SHIFT_TRAFFIC_AND_SCALE_DEPLOYMENT":
            case "REBALANCE_REPLICAS_AND_SCALE_DEPLOYMENT":
                return ActionType.SCALE_DEPLOYMENT;

            // ── VM / node escalation ──────────────────────────────────────────
            case "ESCALATE_VM_OUTAGE_AND_FAILOVER":
            case "ESCALATE_NODE_OUTAGE_AND_REBALANCE":
            case "ESCALATE_MULTI_VM_OUTAGE_AND_FREEZE_AUTOMATION":
            case "ESCALATE_NODE_OUTAGE_AND_FAILOVER_WORKLOAD":
                return ActionType.ESCALATION;

            // No more passive INVESTIGATION: an INVESTIGATE recommendation becomes a concrete, safe
            // rolling restart (the standard first remediation that resolves most transient faults).
            case "INVESTIGATE":
                return ActionType.FORCE_RECREATE_DEPLOYMENT;

            default:
                break;
        }

        // Fallback keyword matching
        if (normalized.contains("NETWORK")) return ActionType.REMOVE_NETWORK_POLICY;
        if (normalized.contains("ROLLBACK")) return ActionType.ROLLBACK_DEPLOYMENT;
        if (normalized.contains("CRASHLOOP")) return ActionType.DELETE_CRASHLOOPING_PODS;
        if (normalized.contains("DRAIN")) return ActionType.DRAIN_NODE;
        if (normalized.contains("CORDON")) return ActionType.CORDON_NODE;
        if (normalized.contains("RESTART")) return ActionType.FORCE_RECREATE_DEPLOYMENT;
        if (normalized.contains("SCALE_DOWN") || normalized.contains("REDUCE")) return ActionType.SCALE_DOWN;
        if (normalized.contains("SCALE") || normalized.contains("REPLICA")) return ActionType.SCALE_DEPLOYMENT;
        if (normalized.contains("ESCALATE")) return ActionType.ESCALATION;

        // Database / latency / anything else on an app: a rolling restart is the concrete default
        // (never a passive INVESTIGATION). Node/VM incidents are routed to ESCALATION/RESTART_VM above.
        return ActionType.FORCE_RECREATE_DEPLOYMENT;
    }

    private RemediationTargetKind mapTargetKind(ActionType actionType) {
        if (actionType == null) return RemediationTargetKind.UNKNOWN;
        return switch (actionType) {
            case RESTART_POD, DELETE_CRASHLOOPING_PODS -> RemediationTargetKind.POD;
            case SCALE_DEPLOYMENT, SCALE_DOWN, ROLLBACK_DEPLOYMENT, FORCE_RECREATE_DEPLOYMENT,
                    REMOVE_NETWORK_POLICY -> RemediationTargetKind.DEPLOYMENT;
            default -> RemediationTargetKind.UNKNOWN;
        };
    }

    private boolean isDbRelated(AIPredictResponse prediction) {
        String combined = (prediction.getIncidentType() + " " + prediction.getRecommendedAction())
                .toUpperCase(Locale.ROOT);
        return combined.contains("DB") || combined.contains("DATABASE") || combined.contains("POSTGRES");
    }

    private boolean isCooldownBlocked(String namespace, String targetName, ActionType actionType, String newIncidentType) {
        if (namespace == null || targetName == null || targetName.isBlank() || actionType == null) {
            return false;
        }
        LocalDateTime cutoff = LocalDateTime.now().minusMinutes(properties.getCooldownMinutes());
        if (actionRepository.findRecentActionsForTarget(namespace, targetName, actionType, cutoff).isEmpty()) {
            return false;
        }
        // Smart cooldown bypass: if the new incident type differs from the last one on this target,
        // the failure has escalated to a new cause — do not block; the situation changed.
        if (newIncidentType != null && !newIncidentType.isBlank()) {
            String lastType = actionRepository.findLastIncidentTypeForTarget(namespace, targetName);
            if (lastType != null && !lastType.equalsIgnoreCase(newIncidentType)) {
                return false;
            }
        }
        return true;
    }

    private boolean isConfidenceEligible(ActionType actionType, Double confidence) {
        double value = confidence == null ? 0.0d : confidence;
        if (actionType == ActionType.SCALE_DEPLOYMENT || actionType == ActionType.SCALE_DOWN) {
            return value >= properties.getScaleConfidenceThreshold();
        }
        if (actionType == ActionType.ROLLBACK_DEPLOYMENT) {
            return value >= properties.getRollbackConfidenceThreshold();
        }
        return value >= properties.getRestartConfidenceThreshold();
    }

    private boolean isSeverityEligible(String severityValue) {
        if (severityValue == null || severityValue.isBlank()) {
            return true;
        }
        try {
            IncidentSeverity severity = IncidentSeverity.valueOf(severityValue.toUpperCase(Locale.ROOT));
            return severity != IncidentSeverity.CRITICAL;
        } catch (IllegalArgumentException ex) {
            return true;
        }
    }

    private String buildRationale(
            ActionType actionType,
            boolean autoExecutable,
            boolean approvalRequired,
            boolean cooldownBlocked,
            boolean dbRelated,
            boolean confidenceEligible,
            boolean severityEligible,
            boolean appAllowlisted,
            boolean namespaceAllowlisted,
            boolean recommendationOnly,
            String recommendationOnlyReason
    ) {
        if (recommendationOnly) {
            return recommendationOnlyReason == null || recommendationOnlyReason.isBlank()
                    ? "AI produced a recommendation-only plan because the action was not safe enough to automate"
                    : recommendationOnlyReason;
        }
        if (actionType == null) return "AI produced an incident with no immediate platform action";
        if (autoExecutable) return "Action is allowlisted for safe automatic execution";
        if (cooldownBlocked) return "Cooldown active for this workload";
        if (dbRelated) return "Database-related incident forced to approval/manual path";
        if (!confidenceEligible) return "Prediction confidence is below the remediation threshold";
        if (!severityEligible) return "Critical severity forced to approval/manual path";
        if (!appAllowlisted) return "Target app is not in the remediation allowlist";
        if (!namespaceAllowlisted) return "Target namespace is not in the remediation allowlist";
        if (actionType == ActionType.ROLLBACK_DEPLOYMENT
                || actionType == ActionType.CORDON_NODE
                || actionType == ActionType.DRAIN_NODE) {
            return "High-impact action requires human approval before execution";
        }
        if (approvalRequired) return "Manual approval is required before remediation";
        return "Manual follow-up required";
    }
}
