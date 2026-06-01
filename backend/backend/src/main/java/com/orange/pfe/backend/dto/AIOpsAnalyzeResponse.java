package com.orange.pfe.backend.dto;

import com.orange.pfe.backend.model.enums.IncidentStatus;

public class AIOpsAnalyzeResponse {

    private AIPredictResponse prediction;
    private boolean incidentCreated;
    private Long incidentId;
    private Long workflowId;
    private IncidentStatus incidentStatus;
    private String message;

    public AIPredictResponse getPrediction() {
        return prediction;
    }

    public void setPrediction(AIPredictResponse prediction) {
        this.prediction = prediction;
    }

    public boolean isIncidentCreated() {
        return incidentCreated;
    }

    public void setIncidentCreated(boolean incidentCreated) {
        this.incidentCreated = incidentCreated;
    }

    public Long getIncidentId() {
        return incidentId;
    }

    public void setIncidentId(Long incidentId) {
        this.incidentId = incidentId;
    }

    public Long getWorkflowId() {
        return workflowId;
    }

    public void setWorkflowId(Long workflowId) {
        this.workflowId = workflowId;
    }

    public IncidentStatus getIncidentStatus() {
        return incidentStatus;
    }

    public void setIncidentStatus(IncidentStatus incidentStatus) {
        this.incidentStatus = incidentStatus;
    }

    public String getMessage() {
        return message;
    }

    public void setMessage(String message) {
        this.message = message;
    }
}
