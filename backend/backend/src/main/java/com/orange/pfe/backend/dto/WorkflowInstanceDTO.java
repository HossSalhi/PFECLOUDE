package com.orange.pfe.backend.dto;

import com.orange.pfe.backend.model.workflow.WorkflowStatus;
import com.orange.pfe.backend.model.workflow.WorkflowOrchestrationState;
import java.time.LocalDateTime;
import java.util.List;

public class WorkflowInstanceDTO {

    private Long id;
    private WorkflowStatus status;
    private WorkflowOrchestrationState orchestrationState;
    private String camundaProcessInstanceKey;
    private LocalDateTime startedAt;
    private LocalDateTime endedAt;
    private List<WorkflowLogDTO> logs;

    public WorkflowInstanceDTO() {}

    public WorkflowInstanceDTO(Long id, WorkflowStatus status, WorkflowOrchestrationState orchestrationState,
                               String camundaProcessInstanceKey, LocalDateTime startedAt,
                               LocalDateTime endedAt, List<WorkflowLogDTO> logs) {
        this.id = id;
        this.status = status;
        this.orchestrationState = orchestrationState;
        this.camundaProcessInstanceKey = camundaProcessInstanceKey;
        this.startedAt = startedAt;
        this.endedAt = endedAt;
        this.logs = logs;
    }

    public Long getId() { return id; }
    public void setId(Long id) { this.id = id; }

    public WorkflowStatus getStatus() { return status; }
    public void setStatus(WorkflowStatus status) { this.status = status; }

    public WorkflowOrchestrationState getOrchestrationState() { return orchestrationState; }
    public void setOrchestrationState(WorkflowOrchestrationState orchestrationState) { this.orchestrationState = orchestrationState; }

    public String getCamundaProcessInstanceKey() { return camundaProcessInstanceKey; }
    public void setCamundaProcessInstanceKey(String camundaProcessInstanceKey) { this.camundaProcessInstanceKey = camundaProcessInstanceKey; }

    public LocalDateTime getStartedAt() { return startedAt; }
    public void setStartedAt(LocalDateTime startedAt) { this.startedAt = startedAt; }

    public LocalDateTime getEndedAt() { return endedAt; }
    public void setEndedAt(LocalDateTime endedAt) { this.endedAt = endedAt; }

    public List<WorkflowLogDTO> getLogs() { return logs; }
    public void setLogs(List<WorkflowLogDTO> logs) { this.logs = logs; }
}
