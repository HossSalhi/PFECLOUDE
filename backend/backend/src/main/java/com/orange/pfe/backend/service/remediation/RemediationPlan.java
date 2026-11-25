package com.orange.pfe.backend.service.remediation;

import com.orange.pfe.backend.model.action.ActionType;
import com.orange.pfe.backend.model.action.AutomationMode;
import com.orange.pfe.backend.model.action.RemediationTargetKind;

/**
 * Plan de remédiation. {@code incidentId} (VMDOWN-POLICY-20261105) est l'incident qui justifie l'action : il sert de
 * clé d'idempotence ({@code X-Helios-Incident}) pour la mise sous tension d'une VM par le watchdog hôte. Il est
 * optionnel (null) pour toutes les autres actions ; un {@code RESTART_VM} sans incident est refusé par l'exécuteur.
 */
public record RemediationPlan(
        ActionType actionType,
        AutomationMode automationMode,
        boolean approvalRequired,
        boolean autoExecutable,
        boolean manualFollowUp,
        boolean cooldownBlocked,
        boolean dbRelated,
        String targetNamespace,
        RemediationTargetKind targetKind,
        String targetName,
        String rationale,
        String incidentType,
        Long incidentId
) {
    // Compatibilite avec les appels existants a 12 parametres (sans incident).
    public RemediationPlan(
            ActionType actionType,
            AutomationMode automationMode,
            boolean approvalRequired,
            boolean autoExecutable,
            boolean manualFollowUp,
            boolean cooldownBlocked,
            boolean dbRelated,
            String targetNamespace,
            RemediationTargetKind targetKind,
            String targetName,
            String rationale,
            String incidentType
    ) {
        this(actionType, automationMode, approvalRequired, autoExecutable,
                manualFollowUp, cooldownBlocked, dbRelated, targetNamespace,
                targetKind, targetName, rationale, incidentType, null);
    }

    // Compatibilite avec les appels existants a 11 parametres.
    public RemediationPlan(
            ActionType actionType,
            AutomationMode automationMode,
            boolean approvalRequired,
            boolean autoExecutable,
            boolean manualFollowUp,
            boolean cooldownBlocked,
            boolean dbRelated,
            String targetNamespace,
            RemediationTargetKind targetKind,
            String targetName,
            String rationale
    ) {
        this(actionType, automationMode, approvalRequired, autoExecutable,
                manualFollowUp, cooldownBlocked, dbRelated, targetNamespace,
                targetKind, targetName, rationale, null, null);
    }

    /** Copie du plan rattachée à l'incident qui la justifie. */
    public RemediationPlan withIncidentId(Long incidentId) {
        return new RemediationPlan(actionType, automationMode, approvalRequired, autoExecutable, manualFollowUp,
                cooldownBlocked, dbRelated, targetNamespace, targetKind, targetName, rationale, incidentType,
                incidentId);
    }
}
