package com.orange.pfe.backend.model.maintenance;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.EnumType;
import jakarta.persistence.Enumerated;
import jakarta.persistence.FetchType;
import jakarta.persistence.GeneratedValue;
import jakarta.persistence.GenerationType;
import jakarta.persistence.Id;
import jakarta.persistence.JoinColumn;
import jakarta.persistence.ManyToOne;
import jakarta.persistence.PrePersist;
import jakarta.persistence.Table;

import java.time.LocalDateTime;

/** Entrée de l'historique des changements d'une maintenance (ajout seul). */
@Entity
@Table(name = "maintenance_history")
public class MaintenanceHistory {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @ManyToOne(fetch = FetchType.LAZY, optional = false)
    @JoinColumn(name = "maintenance_id", nullable = false)
    private Maintenance maintenance;

    @Column(name = "changed_at", nullable = false, updatable = false)
    private LocalDateTime changedAt;

    @Column(name = "changed_by_id", updatable = false)
    private Long changedById;

    @Column(name = "changed_by_username", length = 50, updatable = false)
    private String changedByUsername;

    @Enumerated(EnumType.STRING)
    @Column(name = "change_type", nullable = false, length = 24, updatable = false)
    private MaintenanceChangeType changeType;

    @Column(columnDefinition = "TEXT", updatable = false)
    private String details;

    protected MaintenanceHistory() {
        // JPA
    }

    public MaintenanceHistory(Maintenance maintenance, Long changedById, String changedByUsername,
                              MaintenanceChangeType changeType, String details) {
        this.maintenance = maintenance;
        this.changedById = changedById;
        this.changedByUsername = changedByUsername;
        this.changeType = changeType;
        this.details = details;
    }

    @PrePersist
    protected void onCreate() {
        this.changedAt = LocalDateTime.now();
    }

    public Long getId() { return id; }
    public Maintenance getMaintenance() { return maintenance; }
    public LocalDateTime getChangedAt() { return changedAt; }
    public Long getChangedById() { return changedById; }
    public String getChangedByUsername() { return changedByUsername; }
    public MaintenanceChangeType getChangeType() { return changeType; }
    public String getDetails() { return details; }
}
