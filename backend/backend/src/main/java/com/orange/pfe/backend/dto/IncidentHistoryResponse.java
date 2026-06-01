package com.orange.pfe.backend.dto;

import java.time.LocalDateTime;

public class IncidentHistoryResponse {

    private Long id;
    private String status;
    private String comment;
    private LocalDateTime createdAt;

    public IncidentHistoryResponse(Long id, String status, String comment, LocalDateTime createdAt) {
        this.id = id;
        this.status = status;
        this.comment = comment;
        this.createdAt = createdAt;
    }

    public Long getId() { return id; }
    public String getStatus() { return status; }
    public String getComment() { return comment; }
    public LocalDateTime getCreatedAt() { return createdAt; }
}
