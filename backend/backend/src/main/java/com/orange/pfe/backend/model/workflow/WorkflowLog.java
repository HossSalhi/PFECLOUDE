package com.orange.pfe.backend.model.workflow;

import jakarta.persistence.*;
import java.time.LocalDateTime;

@Entity
@Table(name = "workflow_logs")
public class WorkflowLog {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    private String message;

    private LocalDateTime createdAt = LocalDateTime.now();

    @ManyToOne(fetch = FetchType.LAZY)
    @JoinColumn(name = "workflow_id", nullable = false)
    private WorkflowInstance workflow;

    public WorkflowLog() {}

    public WorkflowLog(String message, WorkflowInstance workflow) {
        this.message = message;
        this.workflow = workflow;
        this.createdAt = LocalDateTime.now();
    }

    // 🔹 Getters & Setters
    public Long getId() { return id; }
    public void setId(Long id) { this.id = id; }

    public String getMessage() { return message; }
    public void setMessage(String message) { this.message = message; }

    public LocalDateTime getCreatedAt() { return createdAt; }
    public void setCreatedAt(LocalDateTime createdAt) { this.createdAt = createdAt; }

    public WorkflowInstance getWorkflow() { return workflow; }
    public void setWorkflow(WorkflowInstance workflow) { this.workflow = workflow; }
}
