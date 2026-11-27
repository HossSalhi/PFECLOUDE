package com.orange.pfe.backend.model.maintenance;

import com.orange.pfe.backend.model.team.Team;
import com.orange.pfe.backend.model.user.AppUser;
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
import jakarta.persistence.PreUpdate;
import jakarta.persistence.Table;

import java.time.LocalDateTime;

/**
 * Opération de maintenance planifiée sur une ressource. Entité DÉCLARATIVE : elle n'agit sur aucun mécanisme
 * existant (supervision, remédiation, drapeau de maintenance de l'hôte lu par le watchdog).
 */
@Entity
@Table(name = "maintenance")
public class Maintenance {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @Column(nullable = false, length = 200)
    private String title;

    @Column(columnDefinition = "TEXT")
    private String description;

    @Column(length = 500)
    private String reason;

    @Enumerated(EnumType.STRING)
    @Column(name = "resource_type", nullable = false, length = 20)
    private MaintenanceResourceType resourceType;

    @Column(name = "resource_name", nullable = false, length = 200)
    private String resourceName;

    @Column(name = "start_at", nullable = false)
    private LocalDateTime startAt;

    @Column(name = "end_at", nullable = false)
    private LocalDateTime endAt;

    @Enumerated(EnumType.STRING)
    @Column(nullable = false, length = 16)
    private MaintenanceStatus status = MaintenanceStatus.PLANNED;

    @ManyToOne(fetch = FetchType.LAZY)
    @JoinColumn(name = "created_by_id")
    private AppUser createdBy;

    @Column(name = "created_by_username", length = 50)
    private String createdByUsername;

    @ManyToOne(fetch = FetchType.LAZY)
    @JoinColumn(name = "responsible_id")
    private AppUser responsible;

    /** Équipe porteuse de la maintenance (optionnelle : les maintenances antérieures n'en ont pas). */
    @ManyToOne(fetch = FetchType.LAZY)
    @JoinColumn(name = "team_id")
    private Team team;

    /** Suspension en cours (renseignés uniquement si le statut est SUSPENDED ; l'historique conserve les précédentes). */
    @Column(name = "suspended_at")
    private LocalDateTime suspendedAt;

    @Column(name = "suspend_reason", length = 500)
    private String suspendReason;

    /** Incident déclencheur de la suspension (référence simple : l'incident peut être purgé sans toucher à la maintenance). */
    @Column(name = "suspend_incident_id")
    private Long suspendIncidentId;

    @Column(name = "actual_start_at")
    private LocalDateTime actualStartAt;

    @Column(name = "actual_end_at")
    private LocalDateTime actualEndAt;

    @Column(name = "cancel_reason", length = 500)
    private String cancelReason;

    @Column(name = "created_at", nullable = false, updatable = false)
    private LocalDateTime createdAt;

    @Column(name = "updated_at", nullable = false)
    private LocalDateTime updatedAt;

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

    public Long getId() { return id; }

    public String getTitle() { return title; }
    public void setTitle(String title) { this.title = title; }

    public String getDescription() { return description; }
    public void setDescription(String description) { this.description = description; }

    public String getReason() { return reason; }
    public void setReason(String reason) { this.reason = reason; }

    public MaintenanceResourceType getResourceType() { return resourceType; }
    public void setResourceType(MaintenanceResourceType resourceType) { this.resourceType = resourceType; }

    public String getResourceName() { return resourceName; }
    public void setResourceName(String resourceName) { this.resourceName = resourceName; }

    public LocalDateTime getStartAt() { return startAt; }
    public void setStartAt(LocalDateTime startAt) { this.startAt = startAt; }

    public LocalDateTime getEndAt() { return endAt; }
    public void setEndAt(LocalDateTime endAt) { this.endAt = endAt; }

    public MaintenanceStatus getStatus() { return status; }
    public void setStatus(MaintenanceStatus status) { this.status = status; }

    public AppUser getCreatedBy() { return createdBy; }
    public void setCreatedBy(AppUser createdBy) { this.createdBy = createdBy; }

    public String getCreatedByUsername() { return createdByUsername; }
    public void setCreatedByUsername(String createdByUsername) { this.createdByUsername = createdByUsername; }

    public AppUser getResponsible() { return responsible; }
    public void setResponsible(AppUser responsible) { this.responsible = responsible; }

    public Team getTeam() { return team; }
    public void setTeam(Team team) { this.team = team; }

    public LocalDateTime getSuspendedAt() { return suspendedAt; }
    public String getSuspendReason() { return suspendReason; }
    public Long getSuspendIncidentId() { return suspendIncidentId; }

    /** Enregistre une suspension (réservé au service : transition IN_PROGRESS → SUSPENDED). */
    public void markSuspended(LocalDateTime at, String reason, Long incidentId) {
        this.suspendedAt = at;
        this.suspendReason = reason;
        this.suspendIncidentId = incidentId;
    }

    /** Efface la suspension en cours (reprise ou annulation) ; la trace reste dans l'historique et l'audit. */
    public void clearSuspension() {
        this.suspendedAt = null;
        this.suspendReason = null;
        this.suspendIncidentId = null;
    }

    public LocalDateTime getActualStartAt() { return actualStartAt; }
    public void setActualStartAt(LocalDateTime actualStartAt) { this.actualStartAt = actualStartAt; }

    public LocalDateTime getActualEndAt() { return actualEndAt; }
    public void setActualEndAt(LocalDateTime actualEndAt) { this.actualEndAt = actualEndAt; }

    public String getCancelReason() { return cancelReason; }
    public void setCancelReason(String cancelReason) { this.cancelReason = cancelReason; }

    public LocalDateTime getCreatedAt() { return createdAt; }
    public LocalDateTime getUpdatedAt() { return updatedAt; }
}
