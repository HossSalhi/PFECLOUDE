package com.orange.pfe.backend.dto;

import com.orange.pfe.backend.model.workflow.WorkflowStatus;
import java.time.LocalDateTime;

public class WorkflowResponse {

    private Long id;
    private WorkflowStatus status;
    private LocalDateTime startedAt;
    private LocalDateTime endedAt;

    public WorkflowResponse(Long id, WorkflowStatus status, LocalDateTime startedAt, LocalDateTime endedAt) {
        this.id = id;
        this.status = status;
        this.startedAt = startedAt;
        this.endedAt = endedAt;
    }

    public Long getId() { return id; }
    public WorkflowStatus getStatus() { return status; }
    public LocalDateTime getStartedAt() { return startedAt; }
    public LocalDateTime getEndedAt() { return endedAt; }
}
