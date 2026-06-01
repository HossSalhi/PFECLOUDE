package com.orange.pfe.backend.service;

import com.orange.pfe.backend.exception.ActionException;
import com.orange.pfe.backend.exception.ResourceNotFoundException;
import com.orange.pfe.backend.model.action.ActionStatus;
import com.orange.pfe.backend.model.action.ActionType;
import com.orange.pfe.backend.model.action.AutomationMode;
import com.orange.pfe.backend.model.action.IncidentAction;
import com.orange.pfe.backend.model.action.RemediationTargetKind;
import com.orange.pfe.backend.model.core.Incident;
import com.orange.pfe.backend.model.enums.IncidentStatus;
import com.orange.pfe.backend.repository.IncidentActionRepository;
import com.orange.pfe.backend.repository.IncidentRepository;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

@Service
public class ActionServiceImpl implements ActionService {

    private static final Logger logger = LoggerFactory.getLogger(ActionServiceImpl.class);

    private final IncidentActionRepository actionRepository;
    private final IncidentRepository incidentRepository;

    public ActionServiceImpl(
            IncidentActionRepository actionRepository,
            IncidentRepository incidentRepository
    ) {
        this.actionRepository = actionRepository;
        this.incidentRepository = incidentRepository;
    }

    @Override
    @Transactional
    public IncidentAction createAction(Incident incident, ActionType type, String description, String username) {
        return createAction(
                incident,
                type,
                description,
                username,
                null,
                RemediationTargetKind.UNKNOWN,
                null,
                AutomationMode.MANUAL
        );
    }

    @Override
    @Transactional
    public IncidentAction createAction(
            Incident incident,
            ActionType type,
            String description,
            String username,
            String targetNamespace,
            RemediationTargetKind targetKind,
            String targetName,
            AutomationMode automationMode
    ) {
        if (incident == null || incident.getId() == null) {
            throw new ResourceNotFoundException("Incident introuvable");
        }

        if (type == null) {
            throw new ActionException("Type d'action invalide");
        }

        if (incident.getStatus() == IncidentStatus.ARCHIVED || incident.getStatus() == IncidentStatus.CLOSED) {
            throw new ActionException(
                    String.format(
                            "Impossible de creer une action pour l'incident [%d] dont le statut est '%s'",
                            incident.getId(),
                            incident.getStatus()
                    )
            );
        }

        incidentRepository.findById(incident.getId())
                .orElseThrow(() -> new ResourceNotFoundException("Incident introuvable"));

        IncidentAction action = new IncidentAction();
        action.setIncident(incident);
        action.setType(type);
        action.setStatus(ActionStatus.PENDING);
        action.setDescription(description);
        action.setTargetNamespace(targetNamespace);
        action.setTargetKind(targetKind == null ? RemediationTargetKind.UNKNOWN : targetKind);
        action.setTargetName(targetName);
        action.setAutomationMode(automationMode == null ? AutomationMode.MANUAL : automationMode);
        action.setExecutedBy(username);
        action.setFailedReason(null);

        logger.info("Creation action [{}] pour incident [{}] par [{}]", type, incident.getId(), username);

        return actionRepository.save(action);
    }

    @Override
    @Transactional
    public void markActionCompleted(Long actionId, String username) {
        markActionCompleted(actionId, username, null);
    }

    @Override
    @Transactional
    public void markActionCompleted(Long actionId, String username, String executorResult) {
        IncidentAction action = actionRepository.findById(actionId)
                .orElseThrow(() -> new ResourceNotFoundException("Action non trouvee: " + actionId));

        if (action.getStatus() == ActionStatus.COMPLETED) {
            throw new ActionException("Action deja completee");
        }

        if (action.getStatus() == ActionStatus.FAILED) {
            throw new ActionException("Impossible de completer une action echouee");
        }

        action.markCompleted(username);
        action.setExecutorResult(executorResult);
        actionRepository.save(action);

        logger.info("Action [{}] marquee completee par [{}]", actionId, username);
    }

    @Override
    @Transactional
    public void markActionFailed(Long actionId, String reason, String username) {
        markActionFailed(actionId, reason, username, null);
    }

    @Override
    @Transactional
    public void markActionFailed(Long actionId, String reason, String username, String executorResult) {
        IncidentAction action = actionRepository.findById(actionId)
                .orElseThrow(() -> new ResourceNotFoundException("Action non trouvee: " + actionId));

        if (action.getStatus() == ActionStatus.COMPLETED) {
            throw new ActionException("Impossible d'echouer une action deja completee");
        }

        action.markFailed(username, reason);
        action.setExecutorResult(executorResult);
        actionRepository.save(action);

        logger.info("Action [{}] echouee par [{}], raison: {}", actionId, username, reason);
    }
}
