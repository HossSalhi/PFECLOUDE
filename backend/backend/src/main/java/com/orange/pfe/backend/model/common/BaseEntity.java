package com.orange.pfe.backend.model.common;

import jakarta.persistence.*;
import java.time.LocalDateTime;

@MappedSuperclass   // ⭐⭐⭐ OBLIGATOIRE
public abstract class BaseEntity {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    protected Long id;

    @Embedded
    protected AuditActor auditActor;

    @Column(name = "created_at", updatable = false, nullable = false)
    protected LocalDateTime createdAt;

    @Column(name = "updated_at", nullable = false)
    protected LocalDateTime updatedAt;

    /* =======================
       LIFECYCLE CALLBACKS
       ======================= */

    @PrePersist
    protected void onCreate() {
        LocalDateTime now = LocalDateTime.now();
        this.createdAt = now;
        this.updatedAt = now;
    }

    @PreUpdate
    protected void onUpdate() {
        this.updatedAt = LocalDateTime.now();
    }

    /* =======================
       GETTERS UNIQUEMENT
       ======================= */

    public Long getId() {
        return id;
    }

    public LocalDateTime getCreatedAt() {
        return createdAt;
    }

    public LocalDateTime getUpdatedAt() {
        return updatedAt;
    }
}