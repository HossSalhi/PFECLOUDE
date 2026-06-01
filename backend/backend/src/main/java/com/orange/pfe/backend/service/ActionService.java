package com.orange.pfe.backend.service;

import com.orange.pfe.backend.model.action.ActionType;
import com.orange.pfe.backend.model.action.AutomationMode;
import com.orange.pfe.backend.model.action.IncidentAction;
import com.orange.pfe.backend.model.action.RemediationTargetKind;
import com.orange.pfe.backend.model.core.Incident;

/**
 * Service pour gérer les actions associées aux incidents.
 */
public interface ActionService {

    IncidentAction createAction(Incident incident, ActionType type, String description, String username);

    IncidentAction createAction(
            Incident incident,
            ActionType type,
            String description,
            String username,
            String targetNamespace,
            RemediationTargetKind targetKind,
            String targetName,
            AutomationMode automationMode
    );

    void markActionCompleted(Long actionId, String username);

    void markActionCompleted(Long actionId, String username, String executorResult);

    void markActionFailed(Long actionId, String reason, String username);

    void markActionFailed(Long actionId, String reason, String username, String executorResult);
}
