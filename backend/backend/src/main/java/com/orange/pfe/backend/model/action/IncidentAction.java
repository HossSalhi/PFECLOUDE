package com.orange.pfe.backend.model.action;

import com.orange.pfe.backend.model.common.BaseEntity;
import com.orange.pfe.backend.model.core.Incident;
import jakarta.persistence.*;

import java.time.LocalDateTime;

@Entity
@Table(name = "incident_action")
public class IncidentAction extends BaseEntity {

    @ManyToOne
    @JoinColumn(name = "incident_id", nullable = false)
    private Incident incident;

    @Enumerated(EnumType.STRING)
    private ActionType type;

    @Enumerated(EnumType.STRING)
    private ActionStatus status;

    @Column(length = 1000)
    private String description;

    @Column(name = "target_namespace")
    private String targetNamespace;

    @Enumerated(EnumType.STRING)
    @Column(name = "target_kind")
    private RemediationTargetKind targetKind;

    @Column(name = "target_name")
    private String targetName;

    @Enumerated(EnumType.STRING)
    @Column(name = "automation_mode")
    private AutomationMode automationMode = AutomationMode.MANUAL;

    @Column(name = "attempt_count", nullable = false)
    private int attemptCount = 0;

    @Column(name = "executor_result", length = 1000)
    private String executorResult;

    private String executedBy;

    private LocalDateTime executedAt;

    @Column(length = 1000)
    private String failedReason;

    public IncidentAction() {}

    // ------------------ Getters & Setters ------------------
    public Incident getIncident() { return incident; }
    public void setIncident(Incident incident) { this.incident = incident; }

    public ActionType getType() { return type; }
    public void setType(ActionType type) { this.type = type; }

    public ActionStatus getStatus() { return status; }
    public void setStatus(ActionStatus status) { this.status = status; }

    public String getDescription() { return description; }
    public void setDescription(String description) { this.description = description; }

    public String getTargetNamespace() { return targetNamespace; }
    public void setTargetNamespace(String targetNamespace) { this.targetNamespace = targetNamespace; }

    public RemediationTargetKind getTargetKind() { return targetKind; }
    public void setTargetKind(RemediationTargetKind targetKind) { this.targetKind = targetKind; }

    public String getTargetName() { return targetName; }
    public void setTargetName(String targetName) { this.targetName = targetName; }

    public AutomationMode getAutomationMode() { return automationMode; }
    public void setAutomationMode(AutomationMode automationMode) { this.automationMode = automationMode; }

    public int getAttemptCount() { return attemptCount; }
    public void setAttemptCount(int attemptCount) { this.attemptCount = attemptCount; }

    public String getExecutorResult() { return executorResult; }
    public void setExecutorResult(String executorResult) { this.executorResult = executorResult; }

    public String getExecutedBy() { return executedBy; }
    public void setExecutedBy(String executedBy) { this.executedBy = executedBy; }

    public LocalDateTime getExecutedAt() { return executedAt; }
    public void setExecutedAt(LocalDateTime executedAt) { this.executedAt = executedAt; }

    public String getFailedReason() { return failedReason; }
    public void setFailedReason(String failedReason) { this.failedReason = failedReason; }

    // ------------------ Méthodes utilitaires ------------------
    public void markCompleted(String user) {
        this.status = ActionStatus.COMPLETED;
        this.executedBy = user;
        this.executedAt = LocalDateTime.now();
        this.failedReason = null; // réinitialiser si précédemment FAILED
    }

    public void markFailed(String user, String reason) {
        this.status = ActionStatus.FAILED;
        this.executedBy = user;
        this.executedAt = LocalDateTime.now();
        this.failedReason = reason;
    }

    public void incrementAttemptCount() {
        this.attemptCount++;
    }
}
