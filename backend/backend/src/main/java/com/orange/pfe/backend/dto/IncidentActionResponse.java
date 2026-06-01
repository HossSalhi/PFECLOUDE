package com.orange.pfe.backend.dto;

import com.orange.pfe.backend.model.action.ActionStatus;
import com.orange.pfe.backend.model.action.ActionType;
import com.orange.pfe.backend.model.action.AutomationMode;
import com.orange.pfe.backend.model.action.IncidentAction;
import com.orange.pfe.backend.model.action.RemediationTargetKind;

import java.time.LocalDateTime;

/**
 * DTO pour exposer les informations d'une action via l'API.
 */
public class IncidentActionResponse {

    private Long id;
    private Long incidentId;
    private ActionType type;
    private ActionStatus status;
    private String description;
    private String targetNamespace;
    private RemediationTargetKind targetKind;
    private String targetName;
    private AutomationMode automationMode;
    private int attemptCount;
    private String executorResult;
    private LocalDateTime executedAt;
    private String failedReason;

    // 🔹 Constructeurs
    public IncidentActionResponse() {}

    public IncidentActionResponse(Long id, Long incidentId, ActionType type, ActionStatus status,
                                  String description, String targetNamespace, RemediationTargetKind targetKind,
                                  String targetName, AutomationMode automationMode, int attemptCount,
                                  String executorResult, LocalDateTime executedAt, String failedReason) {
        this.id = id;
        this.incidentId = incidentId;
        this.type = type;
        this.status = status;
        this.description = description;
        this.targetNamespace = targetNamespace;
        this.targetKind = targetKind;
        this.targetName = targetName;
        this.automationMode = automationMode;
        this.attemptCount = attemptCount;
        this.executorResult = executorResult;
        this.executedAt = executedAt;
        this.failedReason = failedReason;
    }

    /**
     * Convertit l'entité IncidentAction en DTO.
     * Le champ failedReason est dérivé de la description si nécessaire.
     */
    public static IncidentActionResponse fromEntity(IncidentAction action) {
        String failedReason = null;

        if (action.getStatus() == ActionStatus.FAILED && action.getDescription() != null) {
            // Extraire la raison d'échec depuis la description si ajoutée lors du markFailed
            String desc = action.getDescription();
            int idx = desc.lastIndexOf("| FAILED:");
            if (idx != -1) {
                failedReason = desc.substring(idx + 9).trim();
            }
        }

        return new IncidentActionResponse(
                action.getId(),
                action.getIncident() != null ? action.getIncident().getId() : null,
                action.getType(),
                action.getStatus(),
                action.getDescription(),
                action.getTargetNamespace(),
                action.getTargetKind(),
                action.getTargetName(),
                action.getAutomationMode(),
                action.getAttemptCount(),
                action.getExecutorResult(),
                action.getExecutedAt(),
                failedReason
        );
    }

    // 🔹 Getters & Setters
    public Long getId() { return id; }
    public void setId(Long id) { this.id = id; }

    public Long getIncidentId() { return incidentId; }
    public void setIncidentId(Long incidentId) { this.incidentId = incidentId; }

    public ActionType getType() { return type; }
    public void setType(ActionType type) { this.type = type; }
    public String getActionType() { return type != null ? type.name() : null; }
    public String getResult() { return executorResult; }
    public boolean isAutomated() { return automationMode != null && automationMode != AutomationMode.MANUAL; }

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

    public LocalDateTime getExecutedAt() { return executedAt; }
    public void setExecutedAt(LocalDateTime executedAt) { this.executedAt = executedAt; }

    public String getFailedReason() { return failedReason; }
    public void setFailedReason(String failedReason) { this.failedReason = failedReason; }
}
