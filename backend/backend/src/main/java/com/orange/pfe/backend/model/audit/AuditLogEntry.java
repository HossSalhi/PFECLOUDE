package com.orange.pfe.backend.model.audit;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.EnumType;
import jakarta.persistence.Enumerated;
import jakarta.persistence.GeneratedValue;
import jakarta.persistence.GenerationType;
import jakarta.persistence.Id;
import jakarta.persistence.Table;
import org.hibernate.annotations.Immutable;

import java.time.LocalDateTime;

/**
 * Entrée du journal d'audit. Entité IMMUABLE : aucun setter, colonnes non modifiables, et le repository associé
 * n'expose aucune opération de modification ni de suppression. L'acteur est conservé par identifiant ET par nom :
 * la trace survit à la suppression d'un utilisateur (aucune clé étrangère vers app_user).
 */
@Entity
@Immutable
@Table(name = "audit_log")
public class AuditLogEntry {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @Column(name = "occurred_at", nullable = false, updatable = false)
    private LocalDateTime occurredAt;

    @Column(name = "actor_user_id", updatable = false)
    private Long actorUserId;

    @Column(name = "actor_username", length = 50, updatable = false)
    private String actorUsername;

    @Column(name = "actor_role", length = 20, updatable = false)
    private String actorRole;

    @Enumerated(EnumType.STRING)
    @Column(nullable = false, length = 30, updatable = false)
    private AuditModule module;

    @Enumerated(EnumType.STRING)
    @Column(nullable = false, length = 40, updatable = false)
    private AuditAction action;

    @Column(name = "object_type", length = 60, updatable = false)
    private String objectType;

    @Column(name = "object_id", length = 64, updatable = false)
    private String objectId;

    @Column(length = 500, updatable = false)
    private String summary;

    @Column(name = "old_values", columnDefinition = "TEXT", updatable = false)
    private String oldValues;

    @Column(name = "new_values", columnDefinition = "TEXT", updatable = false)
    private String newValues;

    @Enumerated(EnumType.STRING)
    @Column(nullable = false, length = 12, updatable = false)
    private AuditResult result = AuditResult.SUCCESS;

    @Column(name = "client_ip", length = 45, updatable = false)
    private String clientIp;

    @Column(name = "request_id", length = 64, updatable = false)
    private String requestId;

    protected AuditLogEntry() {
        // JPA
    }

    public AuditLogEntry(LocalDateTime occurredAt, Long actorUserId, String actorUsername, String actorRole,
                         AuditModule module, AuditAction action, String objectType, String objectId,
                         String summary, String oldValues, String newValues, AuditResult result,
                         String clientIp, String requestId) {
        this.occurredAt = occurredAt;
        this.actorUserId = actorUserId;
        this.actorUsername = actorUsername;
        this.actorRole = actorRole;
        this.module = module;
        this.action = action;
        this.objectType = objectType;
        this.objectId = objectId;
        this.summary = summary;
        this.oldValues = oldValues;
        this.newValues = newValues;
        this.result = result;
        this.clientIp = clientIp;
        this.requestId = requestId;
    }

    public Long getId() { return id; }
    public LocalDateTime getOccurredAt() { return occurredAt; }
    public Long getActorUserId() { return actorUserId; }
    public String getActorUsername() { return actorUsername; }
    public String getActorRole() { return actorRole; }
    public AuditModule getModule() { return module; }
    public AuditAction getAction() { return action; }
    public String getObjectType() { return objectType; }
    public String getObjectId() { return objectId; }
    public String getSummary() { return summary; }
    public String getOldValues() { return oldValues; }
    public String getNewValues() { return newValues; }
    public AuditResult getResult() { return result; }
    public String getClientIp() { return clientIp; }
    public String getRequestId() { return requestId; }
}
