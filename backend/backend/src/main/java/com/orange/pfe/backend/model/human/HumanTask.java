package com.orange.pfe.backend.model.human;

import com.orange.pfe.backend.model.common.BaseEntity;
import com.orange.pfe.backend.model.workflow.WorkflowInstance;
import jakarta.persistence.*;

import java.time.LocalDateTime;

@Entity
@Table(name = "human_task")
public class HumanTask extends BaseEntity {

    @ManyToOne(optional = false)
    @JoinColumn(name = "workflow_instance_id", nullable = false)
    private WorkflowInstance workflowInstance;

    private String taskName;

    private String assignedTo;

    @Enumerated(EnumType.STRING)
    private TaskDecision decision = TaskDecision.PENDING;

    private LocalDateTime completedAt;

    // Getters / Setters
    public WorkflowInstance getWorkflowInstance() {
        return workflowInstance;
    }

    public void setWorkflowInstance(WorkflowInstance workflowInstance) {
        this.workflowInstance = workflowInstance;
    }

    public String getTaskName() {
        return taskName;
    }

    public void setTaskName(String taskName) {
        this.taskName = taskName;
    }

    public String getAssignedTo() {
        return assignedTo;
    }

    public void setAssignedTo(String assignedTo) {
        this.assignedTo = assignedTo;
    }

    public TaskDecision getDecision() {
        return decision;
    }

    public void setDecision(TaskDecision decision) {
        this.decision = decision;
    }

    public LocalDateTime getCompletedAt() {
        return completedAt;
    }

    public void setCompletedAt(LocalDateTime completedAt) {
        this.completedAt = completedAt;
    }
}
