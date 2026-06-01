package com.orange.pfe.backend.dto;

import java.time.LocalDateTime;

/**
 * DTO représentant un log de workflow.
 */
public class WorkflowLogDTO {

    private Long id;
    private String message;
    private LocalDateTime createdAt;

    // Constructeurs
    public WorkflowLogDTO() {}

    public WorkflowLogDTO(Long id, String message, LocalDateTime createdAt) {
        this.id = id;
        this.message = message;
        this.createdAt = createdAt;
    }

    // Getters & Setters
    public Long getId() { return id; }
    public void setId(Long id) { this.id = id; }

    public String getMessage() { return message; }
    public void setMessage(String message) { this.message = message; }

    public LocalDateTime getCreatedAt() { return createdAt; }
    public void setCreatedAt(LocalDateTime createdAt) { this.createdAt = createdAt; }
}
