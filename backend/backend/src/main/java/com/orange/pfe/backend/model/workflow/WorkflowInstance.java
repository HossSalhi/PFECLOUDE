package com.orange.pfe.backend.model.workflow;

import com.orange.pfe.backend.model.common.BaseEntity;
import com.orange.pfe.backend.model.core.Incident;
import jakarta.persistence.*;
import java.time.LocalDateTime;
import java.util.ArrayList;
import java.util.List;

@Entity
@Table(name = "workflow_instance")
public class WorkflowInstance extends BaseEntity {

    @ManyToOne
    @JoinColumn(name = "incident_id", nullable = false)
    private Incident incident;

    @Enumerated(EnumType.STRING)
    private WorkflowStatus status;

    private String workflowName;

    @Column(name = "camunda_process_definition_id")
    private String camundaProcessDefinitionId;

    @Column(name = "camunda_process_instance_key")
    private String camundaProcessInstanceKey;

    @Column(name = "correlation_key")
    private String correlationKey;

    @Enumerated(EnumType.STRING)
    @Column(name = "orchestration_state")
    private WorkflowOrchestrationState orchestrationState = WorkflowOrchestrationState.CREATED;

    @Column(name = "target_namespace")
    private String targetNamespace;

    @Column(name = "target_app")
    private String targetApp;

    private LocalDateTime startedAt;
    private LocalDateTime endedAt;

    private int currentStep = 0;
    private int totalSteps = 1;

    @OneToMany(mappedBy = "workflow", cascade = CascadeType.ALL, orphanRemoval = true)
    private List<WorkflowLog> logs = new ArrayList<>();

    // =========================
    // Méthodes métier
    // =========================

    public void startWorkflow() {
        this.startedAt = LocalDateTime.now();
        this.status = WorkflowStatus.RUNNING;
        addLog("Workflow started");
    }

    public void endWorkflow(WorkflowStatus finalStatus) {
        if (this.status != WorkflowStatus.COMPLETED) {
            this.status = finalStatus;
            this.endedAt = LocalDateTime.now();
            addLog("Workflow ended with status " + finalStatus);
        }
    }

    public void advanceStep() {
        if (status == WorkflowStatus.COMPLETED) return;

        currentStep++;
        addLog("Advanced to step " + currentStep + " at " + LocalDateTime.now());

        if (currentStep >= totalSteps) {
            endWorkflow(WorkflowStatus.COMPLETED);
            addLog("Workflow completed automatically");
        }
    }

    public void addLog(String message) {
        if (message != null && !message.isEmpty()) {
            WorkflowLog log = new WorkflowLog(message, this);
            logs.add(log);
        }
    }

    // =========================
    // Getters & Setters
    // =========================

    public Incident getIncident() { return incident; }
    public void setIncident(Incident incident) { this.incident = incident; }

    public WorkflowStatus getStatus() { return status; }
    public void setStatus(WorkflowStatus status) { this.status = status; }

    public String getWorkflowName() { return workflowName; }
    public void setWorkflowName(String workflowName) { this.workflowName = workflowName; }

    public String getCamundaProcessDefinitionId() { return camundaProcessDefinitionId; }
    public void setCamundaProcessDefinitionId(String camundaProcessDefinitionId) { this.camundaProcessDefinitionId = camundaProcessDefinitionId; }

    public String getCamundaProcessInstanceKey() { return camundaProcessInstanceKey; }
    public void setCamundaProcessInstanceKey(String camundaProcessInstanceKey) { this.camundaProcessInstanceKey = camundaProcessInstanceKey; }

    public String getCorrelationKey() { return correlationKey; }
    public void setCorrelationKey(String correlationKey) { this.correlationKey = correlationKey; }

    public WorkflowOrchestrationState getOrchestrationState() { return orchestrationState; }
    public void setOrchestrationState(WorkflowOrchestrationState orchestrationState) { this.orchestrationState = orchestrationState; }

    public String getTargetNamespace() { return targetNamespace; }
    public void setTargetNamespace(String targetNamespace) { this.targetNamespace = targetNamespace; }

    public String getTargetApp() { return targetApp; }
    public void setTargetApp(String targetApp) { this.targetApp = targetApp; }

    public LocalDateTime getStartedAt() { return startedAt; }
    public LocalDateTime getEndedAt() { return endedAt; }

    public int getCurrentStep() { return currentStep; }
    public void setCurrentStep(int currentStep) { this.currentStep = currentStep; }

    public int getTotalSteps() { return totalSteps; }
    public void setTotalSteps(int totalSteps) { this.totalSteps = totalSteps; }

    public List<WorkflowLog> getLogs() { return logs; }
    public void setLogs(List<WorkflowLog> logs) { this.logs = logs; }
}
