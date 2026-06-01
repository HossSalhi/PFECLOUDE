package com.orange.pfe.backend.service.remediation;

import com.orange.pfe.backend.model.action.ActionType;
import com.orange.pfe.backend.model.action.AutomationMode;
import com.orange.pfe.backend.model.action.RemediationTargetKind;

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
        String rationale
) {
}
