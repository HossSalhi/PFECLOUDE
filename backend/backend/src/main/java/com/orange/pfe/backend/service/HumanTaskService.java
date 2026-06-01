package com.orange.pfe.backend.service;

import com.orange.pfe.backend.dto.HumanTaskResponse;
import com.orange.pfe.backend.exception.HumanTaskNotFoundException;
import com.orange.pfe.backend.exception.InvalidIncidentStateException;
import com.orange.pfe.backend.model.action.ActionStatus;
import com.orange.pfe.backend.model.action.ActionType;
import com.orange.pfe.backend.model.action.AutomationMode;
import com.orange.pfe.backend.model.action.IncidentAction;
import com.orange.pfe.backend.model.action.RemediationTargetKind;
import com.orange.pfe.backend.model.core.Incident;
import com.orange.pfe.backend.model.enums.IncidentStatus;
import com.orange.pfe.backend.model.human.HumanTask;
import com.orange.pfe.backend.model.human.TaskDecision;
import com.orange.pfe.backend.model.workflow.WorkflowInstance;
import com.orange.pfe.backend.model.workflow.WorkflowOrchestrationState;
import com.orange.pfe.backend.model.workflow.WorkflowStatus;
import com.orange.pfe.backend.repository.HumanTaskRepository;
import com.orange.pfe.backend.repository.IncidentActionRepository;
import org.springframework.security.core.Authentication;
import org.springframework.security.core.context.SecurityContextHolder;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.LocalDateTime;
import java.util.List;
import java.util.stream.Collectors;

@Service
public class HumanTaskService {

    private final HumanTaskRepository repo;
    private final ActionService actionService;
    private final IncidentHistoryService historyService;
    private final WorkflowService workflowService;
    private final CamundaWorkflowService camundaWorkflowService;
    private final IncidentActionRepository incidentActionRepository;
    private final NotificationService notificationService;

    public HumanTaskService(
            HumanTaskRepository repo,
            ActionService actionService,
            IncidentHistoryService historyService,
            WorkflowService workflowService,
            CamundaWorkflowService camundaWorkflowService,
            IncidentActionRepository incidentActionRepository,
            NotificationService notificationService
    ) {
        this.repo = repo;
        this.actionService = actionService;
        this.historyService = historyService;
        this.workflowService = workflowService;
        this.camundaWorkflowService = camundaWorkflowService;
        this.incidentActionRepository = incidentActionRepository;
        this.notificationService = notificationService;
    }

    public HumanTask createTask(String taskName, WorkflowInstance workflowInstance) {
        HumanTask task = new HumanTask();
        task.setTaskName(taskName);
        task.setWorkflowInstance(workflowInstance);
        task.setDecision(TaskDecision.PENDING);
        HumanTask saved = repo.save(task);
        notificationService.notifyHumanTaskCreated(saved);
        return saved;
    }

    @Transactional
    public HumanTaskResponse decideTask(Long taskId, TaskDecision decision) {
        HumanTask task = repo.findById(taskId)
                .orElseThrow(() -> new HumanTaskNotFoundException(taskId));

        if (task.getDecision() != TaskDecision.PENDING) {
            throw new InvalidIncidentStateException(
                    "HumanTask already decided with status: " + task.getDecision()
            );
        }

        task.setDecision(decision);
        task.setCompletedAt(LocalDateTime.now());
        repo.save(task);

        WorkflowInstance workflow = task.getWorkflowInstance();
        Incident incident = workflow.getIncident();
        String username = getCurrentUsername();
        boolean orchestrationManaged = workflow.getCamundaProcessInstanceKey() != null
                && !workflow.getCamundaProcessInstanceKey().isBlank();

        switch (decision) {
            case APPROVED -> {
                Long actionId = null;
                if (orchestrationManaged) {
                    IncidentAction action = prepareActionForApprovedDecision(incident, workflow, username);
                    actionId = action != null ? action.getId() : null;
                    workflowService.updateOrchestrationState(
                            workflow,
                            WorkflowOrchestrationState.APPROVED,
                            "Human operator approved remediation"
                    );
                    camundaWorkflowService.publishDecision(workflow, decision, actionId);
                } else {
                    actionService.createAction(incident, ActionType.REMEDIATION, "Remediation approved by human operator", username);
                }
                incident.setStatus(IncidentStatus.IN_PROGRESS);
                historyService.record(incident, IncidentStatus.IN_PROGRESS, "Human task approved");
            }
            case REJECTED -> {
                if (orchestrationManaged) {
                    workflowService.finalizeWorkflow(
                            workflow,
                            WorkflowOrchestrationState.REJECTED,
                            WorkflowStatus.CANCELLED,
                            IncidentStatus.RESOLVED,
                            "Human operator rejected remediation"
                    );
                    camundaWorkflowService.publishDecision(workflow, decision, null);
                } else {
                    actionService.createAction(incident, ActionType.NOTIFICATION, "Remediation rejected by human operator", username);
                }
                incident.setStatus(IncidentStatus.RESOLVED);
                historyService.record(incident, IncidentStatus.RESOLVED, "Human task rejected");
            }
            case ESCALATED -> {
                if (orchestrationManaged) {
                    workflowService.finalizeWorkflow(
                            workflow,
                            WorkflowOrchestrationState.ESCALATED,
                            WorkflowStatus.FAILED,
                            IncidentStatus.WAITING_FOR_APPROVAL,
                            "Human operator escalated workflow for manual handling"
                    );
                    camundaWorkflowService.publishDecision(workflow, decision, null);
                } else {
                    actionService.createAction(incident, ActionType.ESCALATION, "Incident escalated to senior operator", username);
                }
                incident.setStatus(IncidentStatus.WAITING_FOR_APPROVAL);
                historyService.record(incident, IncidentStatus.WAITING_FOR_APPROVAL, "Human task escalated");
            }
            default -> throw new InvalidIncidentStateException("Unsupported decision: " + decision);
        }

        notificationService.notifyHumanTaskDecided(task, username);

        return map(task);
    }

    public List<HumanTaskResponse> getAllTasks() {
        return repo.findAll().stream().map(this::map).collect(Collectors.toList());
    }

    public List<HumanTaskResponse> getPendingTasks() {
        return repo.findByDecision(TaskDecision.PENDING).stream().map(this::map).collect(Collectors.toList());
    }

    private HumanTaskResponse map(HumanTask task) {
        WorkflowInstance wi = task.getWorkflowInstance();
        String workflowId = wi != null && wi.getCamundaProcessInstanceKey() != null
                ? wi.getCamundaProcessInstanceKey()
                : (wi != null ? String.valueOf(wi.getId()) : null);
        Long incidentId = null;
        String incidentTitle = null;
        if (wi != null && wi.getIncident() != null) {
            incidentId = wi.getIncident().getId();
            incidentTitle = wi.getIncident().getTitle();
        }
        return new HumanTaskResponse(
                task.getId(), task.getTaskName(), task.getDecision(),
                workflowId, task.getCreatedAt(), incidentId, incidentTitle
        );
    }

    private String getCurrentUsername() {
        Authentication auth = SecurityContextHolder.getContext().getAuthentication();
        if (auth != null && auth.isAuthenticated()) {
            return auth.getName();
        }
        return "SYSTEM";
    }

    private IncidentAction prepareActionForApprovedDecision(
            Incident incident,
            WorkflowInstance workflow,
            String username
    ) {
        IncidentAction latestAction = incidentActionRepository.findTopByIncidentIdOrderByCreatedAtDesc(incident.getId())
                .orElse(null);

        if (latestAction != null && latestAction.getStatus() == ActionStatus.PENDING) {
            return latestAction;
        }

        ActionType actionType = latestAction != null && latestAction.getType() != null
                ? latestAction.getType()
                : ActionType.REMEDIATION;
        String description = latestAction != null && latestAction.getDescription() != null
                ? "Retry approved by human operator | " + latestAction.getDescription()
                : "Remediation approved by human operator";
        String targetNamespace = latestAction != null && latestAction.getTargetNamespace() != null
                ? latestAction.getTargetNamespace()
                : workflow.getTargetNamespace();
        RemediationTargetKind targetKind = latestAction != null && latestAction.getTargetKind() != null
                ? latestAction.getTargetKind()
                : RemediationTargetKind.DEPLOYMENT;
        String targetName = latestAction != null && latestAction.getTargetName() != null
                ? latestAction.getTargetName()
                : workflow.getTargetApp();

        if (latestAction == null || latestAction.getStatus() == ActionStatus.COMPLETED || latestAction.getStatus() == ActionStatus.FAILED) {
            return actionService.createAction(
                    incident,
                    actionType,
                    description,
                    username,
                    targetNamespace,
                    targetKind,
                    targetName,
                    AutomationMode.MANUAL
            );
        }

        return latestAction;
    }
}
