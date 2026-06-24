package com.orange.pfe.backend.dto;

import com.orange.pfe.backend.model.human.TaskDecision;
import java.time.LocalDateTime;

public class HumanTaskResponse {

    private Long id;
    private String taskName;
    private String name;
    private TaskDecision decision;
    private String status;
    private String workflowId;
    private LocalDateTime createdAt;
    private Long incidentId;
    private String incidentTitle;
    private String incidentStatus;

    public HumanTaskResponse(Long id, String taskName, TaskDecision decision,
                             String workflowId, LocalDateTime createdAt,
                             Long incidentId, String incidentTitle, String incidentStatus) {
        this.id = id;
        this.taskName = taskName;
        this.name = taskName;
        this.decision = decision;
        this.status = decision != null ? decision.name() : "PENDING";
        this.workflowId = workflowId;
        this.createdAt = createdAt;
        this.incidentId = incidentId;
        this.incidentTitle = incidentTitle;
        this.incidentStatus = incidentStatus;
    }

    public Long getId() { return id; }
    public String getTaskName() { return taskName; }
    public String getName() { return name; }
    public TaskDecision getDecision() { return decision; }
    public String getStatus() { return status; }
    public String getWorkflowId() { return workflowId; }
    public LocalDateTime getCreatedAt() { return createdAt; }
    public Long getIncidentId() { return incidentId; }
    public String getIncidentTitle() { return incidentTitle; }
    public String getIncidentStatus() { return incidentStatus; }
}
