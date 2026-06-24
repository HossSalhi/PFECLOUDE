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
        return repo.save(saved);
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
        if (workflow.getStatus() == WorkflowStatus.COMPLETED) {
            throw new InvalidWorkflowStateException("Cannot advance a completed workflow");
        }

        workflow.advanceStep();
        workflow.addLog("Advanced to step " + workflow.getCurrentStep() + " at " + LocalDateTime.now());
        return repo.save(workflow);
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
        workflow.setOrchestrationState(state);
        if (incidentStatus != null) {
            workflow.getIncident().setStatus(incidentStatus);
        }
        if (logMessage != null && !logMessage.isBlank()) {
            workflow.addLog(logMessage);
        }
        return repo.save(workflow);
    }

    @Transactional
    public WorkflowInstance attachCamundaProcess(
            WorkflowInstance workflow,
            String processDefinitionId,
            String processInstanceKey
    ) {
        workflow.setCamundaProcessDefinitionId(processDefinitionId);
        workflow.setCamundaProcessInstanceKey(processInstanceKey);
        workflow.addLog("Camunda process attached: " + processInstanceKey);
        return repo.save(workflow);
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
        if (workflow == null) {
            throw new InvalidWorkflowStateException("Workflow is required");
        }

        workflow.setOrchestrationState(orchestrationState);
        if (incidentStatus != null) {
            workflow.getIncident().setStatus(incidentStatus);
            incidentRepo.save(workflow.getIncident());
        }
        if (logMessage != null && !logMessage.isBlank()) {
            workflow.addLog(logMessage);
        }
        workflow.endWorkflow(workflowStatus);
        return repo.save(workflow);
    }

    @Transactional
    public void addWorkflowLog(Long workflowId, String message) {
        repo.findByIdWithLogs(workflowId).ifPresent(wf -> {
            wf.addLog(message);
            repo.save(wf);
        });
    }

    @Transactional(readOnly = true)
    public List<WorkflowLogDTO> getWorkflowLogsForIncident(Long incidentId) {
        return repo.findByIncidentIdWithLogs(incidentId).stream()
                .flatMap(wf -> wf.getLogs().stream())
                .sorted(Comparator.comparing(log -> log.getCreatedAt()))
                .map(log -> new WorkflowLogDTO(log.getId(), log.getMessage(), log.getCreatedAt()))
                .collect(Collectors.toList());
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
