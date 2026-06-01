package com.orange.pfe.backend.dto;

import com.fasterxml.jackson.annotation.JsonProperty;

import java.time.OffsetDateTime;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

public class AIPredictResponse {

    private boolean incident;
    private boolean noise;

    @JsonProperty("incident_type")
    private String incidentType;

    private String severity;

    @JsonProperty("recommended_action")
    private String recommendedAction;

    private Double confidence;
    private String summary;

    @JsonProperty("decision_explanation")
    private String decisionExplanation;

    @JsonProperty("chosen_plan_id")
    private String chosenPlanId;

    @JsonProperty("final_executable_action")
    private String finalExecutableAction;

    @JsonProperty("execution_mode")
    private String executionMode;

    @JsonProperty("recommendation_only_reason")
    private String recommendationOnlyReason;

    @JsonProperty("top_recommended_plans")
    private List<AIPredictPlanOption> topRecommendedPlans = new ArrayList<>();

    private AIPredictEvidence evidence;
    private Map<String, Double> features;

    @JsonProperty("decision_context")
    private Map<String, Object> decisionContext = new LinkedHashMap<>();

    @JsonProperty("window_start")
    private OffsetDateTime windowStart;

    @JsonProperty("window_end")
    private OffsetDateTime windowEnd;

    public boolean isIncident() {
        return incident;
    }

    public void setIncident(boolean incident) {
        this.incident = incident;
    }

    public boolean isNoise() {
        return noise;
    }

    public void setNoise(boolean noise) {
        this.noise = noise;
    }

    public String getIncidentType() {
        return incidentType;
    }

    public void setIncidentType(String incidentType) {
        this.incidentType = incidentType;
    }

    public String getSeverity() {
        return severity;
    }

    public void setSeverity(String severity) {
        this.severity = severity;
    }

    public String getRecommendedAction() {
        return recommendedAction;
    }

    public void setRecommendedAction(String recommendedAction) {
        this.recommendedAction = recommendedAction;
    }

    public Double getConfidence() {
        return confidence;
    }

    public void setConfidence(Double confidence) {
        this.confidence = confidence;
    }

    public String getSummary() {
        return summary;
    }

    public void setSummary(String summary) {
        this.summary = summary;
    }

    public String getDecisionExplanation() {
        return decisionExplanation;
    }

    public void setDecisionExplanation(String decisionExplanation) {
        this.decisionExplanation = decisionExplanation;
    }

    public String getChosenPlanId() {
        return chosenPlanId;
    }

    public void setChosenPlanId(String chosenPlanId) {
        this.chosenPlanId = chosenPlanId;
    }

    public List<AIPredictPlanOption> getTopRecommendedPlans() {
        return topRecommendedPlans;
    }

    public void setTopRecommendedPlans(List<AIPredictPlanOption> topRecommendedPlans) {
        this.topRecommendedPlans = topRecommendedPlans == null ? new ArrayList<>() : new ArrayList<>(topRecommendedPlans);
    }

    public String getFinalExecutableAction() {
        return finalExecutableAction;
    }

    public void setFinalExecutableAction(String finalExecutableAction) {
        this.finalExecutableAction = finalExecutableAction;
    }

    public String getExecutionMode() {
        return executionMode;
    }

    public void setExecutionMode(String executionMode) {
        this.executionMode = executionMode;
    }

    public String getRecommendationOnlyReason() {
        return recommendationOnlyReason;
    }

    public void setRecommendationOnlyReason(String recommendationOnlyReason) {
        this.recommendationOnlyReason = recommendationOnlyReason;
    }

    public AIPredictEvidence getEvidence() {
        return evidence;
    }

    public void setEvidence(AIPredictEvidence evidence) {
        this.evidence = evidence;
    }

    public Map<String, Double> getFeatures() {
        return features;
    }

    public void setFeatures(Map<String, Double> features) {
        this.features = features;
    }

    public Map<String, Object> getDecisionContext() {
        return decisionContext;
    }

    public void setDecisionContext(Map<String, Object> decisionContext) {
        this.decisionContext = decisionContext == null ? new LinkedHashMap<>() : new LinkedHashMap<>(decisionContext);
    }

    public OffsetDateTime getWindowStart() {
        return windowStart;
    }

    public void setWindowStart(OffsetDateTime windowStart) {
        this.windowStart = windowStart;
    }

    public OffsetDateTime getWindowEnd() {
        return windowEnd;
    }

    public void setWindowEnd(OffsetDateTime windowEnd) {
        this.windowEnd = windowEnd;
    }
}
