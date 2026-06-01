package com.orange.pfe.backend.dto;

import com.fasterxml.jackson.annotation.JsonProperty;

import java.util.Map;

public class AILearnRequest {

    private Map<String, Double> features;

    @JsonProperty("incident_type")
    private String incidentType;

    private String severity;
    private String action;
    private String executable;
    private boolean success;

    @JsonProperty("minutes_to_resolve")
    private int minutesToResolve;

    private String app;
    private String namespace;
    private double confidence;

    public AILearnRequest() {}

    public Map<String, Double> getFeatures() { return features; }
    public void setFeatures(Map<String, Double> features) { this.features = features; }

    public String getIncidentType() { return incidentType; }
    public void setIncidentType(String incidentType) { this.incidentType = incidentType; }

    public String getSeverity() { return severity; }
    public void setSeverity(String severity) { this.severity = severity; }

    public String getAction() { return action; }
    public void setAction(String action) { this.action = action; }

    public String getExecutable() { return executable; }
    public void setExecutable(String executable) { this.executable = executable; }

    public boolean isSuccess() { return success; }
    public void setSuccess(boolean success) { this.success = success; }

    public int getMinutesToResolve() { return minutesToResolve; }
    public void setMinutesToResolve(int minutesToResolve) { this.minutesToResolve = minutesToResolve; }

    public String getApp() { return app; }
    public void setApp(String app) { this.app = app; }

    public String getNamespace() { return namespace; }
    public void setNamespace(String namespace) { this.namespace = namespace; }

    public double getConfidence() { return confidence; }
    public void setConfidence(double confidence) { this.confidence = confidence; }
}
