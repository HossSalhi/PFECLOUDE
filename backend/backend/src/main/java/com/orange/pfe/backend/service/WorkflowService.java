package com.orange.pfe.backend.service;

import com.orange.pfe.backend.dto.WorkflowInstanceDTO;
import com.orange.pfe.backend.dto.WorkflowLogDTO;
import com.orange.pfe.backend.exception.InvalidWorkflowStateException;
import com.orange.pfe.backend.exception.WorkflowNotFoundException;
import com.orange.pfe.backend.model.core.Incident;
import com.orange.pfe.backend.model.enums.IncidentStatus;
import com.orange.pfe.backend.model.workflow.WorkflowInstance;
import com.orange.pfe.backend.model.workflow.WorkflowOrchestrationState;
import com.orange.pfe.backend.model.workflow.WorkflowStatus;
import com.orange.pfe.backend.repository.IncidentRepository;
import com.orange.pfe.backend.repository.WorkflowRepository;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.LocalDateTime;
import java.util.Comparator;
import java.util.List;
import java.util.stream.Collectors;

@Service
public class WorkflowService {

    private final WorkflowRepository repo;
    private final IncidentRepository incidentRepo;

    public WorkflowService(WorkflowRepository repo, IncidentRepository incidentRepo) {
        this.repo = repo;
        this.incidentRepo = incidentRepo;
    }

    @Transactional
    public WorkflowInstance startWorkflow(Incident incident) {
        return startWorkflow(incident, "Incident-Auto-Workflow", null, null);
    }

    @Transactional
    public WorkflowInstance startWorkflow(
            Incident incident,
            String workflowName,
            String targetNamespace,
            String targetApp
    ) {
        WorkflowInstance workflow = new WorkflowInstance();
        workflow.setIncident(incident);
        workflow.setWorkflowName(workflowName == null || workflowName.isBlank() ? "Incident-Auto-Workflow" : workflowName);
        workflow.setTargetNamespace(targetNamespace);
        workflow.setTargetApp(targetApp);
        workflow.setOrchestrationState(WorkflowOrchestrationState.CREATED);
        workflow.startWorkflow();

        WorkflowInstance saved = repo.save(workflow);
        saved.setCorrelationKey(String.valueOf(saved.getId()));
        saved.addLog("Correlation key assigned: " + saved.getCorrelationKey());
        // saved est gere par la transaction : le dirty checking persiste les changements.
        return saved;
    }

    @Transactional(readOnly = true)
    public WorkflowInstanceDTO getWorkflow(Long id) {
        return mapToDTO(getWorkflowInstance(id));
    }

    @Transactional(readOnly = true)
    public List<WorkflowInstanceDTO> getAllWorkflows() {
        return repo.findAllWithLogs().stream()
                .map(this::mapToDTO)
                .collect(Collectors.toList());
    }

    @Transactional(readOnly = true)
    public WorkflowInstance getWorkflowInstance(Long id) {
        return repo.findByIdWithLogs(id)
                .orElseThrow(() -> new WorkflowNotFoundException(id));
    }

    @Transactional
    public WorkflowInstance completeWorkflow(Long workflowId) {
        WorkflowInstance workflow = getWorkflowInstance(workflowId);
        if (workflow.getStatus() == WorkflowStatus.COMPLETED) {
            throw new InvalidWorkflowStateException("Workflow already completed");
        }

        return finalizeWorkflow(
                workflow,
                WorkflowOrchestrationState.RESOLVED,
                WorkflowStatus.COMPLETED,
                IncidentStatus.RESOLVED,
                "Workflow resolved by backend orchestration"
        );
    }

    @Transactional
    public WorkflowInstance nextStep(WorkflowInstance workflow) {
        WorkflowInstance managed = requireManagedWorkflow(workflow);
        if (managed.getStatus() == WorkflowStatus.COMPLETED) {
            throw new InvalidWorkflowStateException("Cannot advance a completed workflow");
        }

        managed.advanceStep();
        managed.addLog("Advanced to step " + managed.getCurrentStep() + " at " + LocalDateTime.now());
        return managed;
    }

    @Transactional
    public WorkflowInstance updateOrchestrationState(
            WorkflowInstance workflow,
            WorkflowOrchestrationState state,
            String logMessage
    ) {
        return updateOrchestrationState(workflow, state, null, logMessage);
    }

    @Transactional
    public WorkflowInstance updateOrchestrationState(
            WorkflowInstance workflow,
            WorkflowOrchestrationState state,
            IncidentStatus incidentStatus,
            String logMessage
    ) {
        WorkflowInstance managed = requireManagedWorkflow(workflow);
        managed.setOrchestrationState(state);

        if (incidentStatus != null) {
            managed.getIncident().setStatus(incidentStatus);
        }
        if (logMessage != null && !logMessage.isBlank()) {
            managed.addLog(logMessage);
        }

        // Ne pas fusionner une seconde fois le graphe workflow/logs.
        return managed;
    }

    @Transactional
    public WorkflowInstance attachCamundaProcess(
            WorkflowInstance workflow,
            String processDefinitionId,
            String processInstanceKey
    ) {
        WorkflowInstance managed = requireManagedWorkflow(workflow);
        managed.setCamundaProcessDefinitionId(processDefinitionId);
        managed.setCamundaProcessInstanceKey(processInstanceKey);
        managed.addLog("Camunda process attached: " + processInstanceKey);
        return managed;
    }

    @Transactional
    public WorkflowInstance failWorkflow(WorkflowInstance workflow, String reason) {
        return finalizeWorkflow(
                workflow,
                WorkflowOrchestrationState.FAILED,
                WorkflowStatus.FAILED,
                workflow.getIncident().getStatus(),
                reason == null ? "Workflow failed" : reason
        );
    }

    @Transactional
    public WorkflowInstance finalizeWorkflow(
            WorkflowInstance workflow,
            WorkflowOrchestrationState orchestrationState,
            WorkflowStatus workflowStatus,
            IncidentStatus incidentStatus,
            String logMessage
    ) {
        WorkflowInstance managed = requireManagedWorkflow(workflow);
        managed.setOrchestrationState(orchestrationState);

        if (incidentStatus != null) {
            // L'incident charge appartient au contexte transactionnel courant.
            managed.getIncident().setStatus(incidentStatus);
        }
        if (logMessage != null && !logMessage.isBlank()) {
            managed.addLog(logMessage);
        }

        managed.endWorkflow(workflowStatus);
        return managed;
    }

    @Transactional
    public void addWorkflowLog(Long workflowId, String message) {
        repo.findByIdWithLogs(workflowId).ifPresent(wf -> wf.addLog(message));
    }

    @Transactional(readOnly = true)
    public List<WorkflowLogDTO> getWorkflowLogsForIncident(Long incidentId) {
        return repo.findByIncidentIdWithLogs(incidentId).stream()
                .flatMap(wf -> wf.getLogs().stream())
                .sorted(Comparator.comparing(log -> log.getCreatedAt()))
                .map(log -> new WorkflowLogDTO(log.getId(), log.getMessage(), log.getCreatedAt()))
                .collect(Collectors.toList());
    }

    private WorkflowInstance requireManagedWorkflow(WorkflowInstance workflow) {
        if (workflow == null || workflow.getId() == null) {
            throw new InvalidWorkflowStateException("A persisted workflow is required");
        }

        Long workflowId = workflow.getId();
        return repo.findByIdWithLogs(workflowId)
                .orElseThrow(() -> new WorkflowNotFoundException(workflowId));
    }
    private WorkflowInstanceDTO mapToDTO(WorkflowInstance workflow) {
        List<WorkflowLogDTO> logs = workflow.getLogs().stream()
                .map(log -> new WorkflowLogDTO(log.getId(), log.getMessage(), log.getCreatedAt()))
                .collect(Collectors.toList());

        return new WorkflowInstanceDTO(
                workflow.getId(),
                workflow.getStatus(),
                workflow.getOrchestrationState(),
                workflow.getCamundaProcessInstanceKey(),
                workflow.getStartedAt(),
                workflow.getEndedAt(),
                logs
        );
    }
}
