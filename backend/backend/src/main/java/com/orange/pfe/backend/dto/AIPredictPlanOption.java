package com.orange.pfe.backend.dto;

import com.fasterxml.jackson.annotation.JsonProperty;

import java.util.ArrayList;
import java.util.List;

public class AIPredictPlanOption {

    private Integer rank;

    @JsonProperty("plan_id")
    private String planId;

    private String title;
    private String strategy;

    @JsonProperty("recommended_action")
    private String recommendedAction;

    @JsonProperty("executable_action")
    private String executableAction;

    @JsonProperty("safe_to_execute")
    private boolean safeToExecute;

    @JsonProperty("execution_mode")
    private String executionMode;

    private String rationale;
    private List<String> reasons = new ArrayList<>();
    private List<String> advantages = new ArrayList<>();
    private List<String> tradeoffs = new ArrayList<>();

    @JsonProperty("risk_score")
    private Double riskScore;

    @JsonProperty("cost_score")
    private Double costScore;

    @JsonProperty("blast_radius_score")
    private Double blastRadiusScore;

    @JsonProperty("historical_effectiveness")
    private Double historicalEffectiveness;

    @JsonProperty("novelty_score")
    private Double noveltyScore;

    @JsonProperty("priority_score")
    private Double priorityScore;

    @JsonProperty("requires_approval")
    private boolean requiresApproval;

    public Integer getRank() {
        return rank;
    }

    public void setRank(Integer rank) {
        this.rank = rank;
    }

    public String getPlanId() {
        return planId;
    }

    public void setPlanId(String planId) {
        this.planId = planId;
    }

    public String getTitle() {
        return title;
    }

    public void setTitle(String title) {
        this.title = title;
    }

    public String getStrategy() {
        return strategy;
    }

    public void setStrategy(String strategy) {
        this.strategy = strategy;
    }

    public String getRecommendedAction() {
        return recommendedAction;
    }

    public void setRecommendedAction(String recommendedAction) {
        this.recommendedAction = recommendedAction;
    }

    public String getExecutableAction() {
        return executableAction;
    }

    public void setExecutableAction(String executableAction) {
        this.executableAction = executableAction;
    }

    public String getRationale() {
        return rationale;
    }

    public void setRationale(String rationale) {
        this.rationale = rationale;
    }

    public boolean isSafeToExecute() {
        return safeToExecute;
    }

    public void setSafeToExecute(boolean safeToExecute) {
        this.safeToExecute = safeToExecute;
    }

    public String getExecutionMode() {
        return executionMode;
    }

    public void setExecutionMode(String executionMode) {
        this.executionMode = executionMode;
    }

    public List<String> getReasons() {
        return reasons;
    }

    public void setReasons(List<String> reasons) {
        this.reasons = reasons == null ? new ArrayList<>() : new ArrayList<>(reasons);
    }

    public List<String> getAdvantages() {
        return advantages;
    }

    public void setAdvantages(List<String> advantages) {
        this.advantages = advantages == null ? new ArrayList<>() : new ArrayList<>(advantages);
    }

    public List<String> getTradeoffs() {
        return tradeoffs;
    }

    public void setTradeoffs(List<String> tradeoffs) {
        this.tradeoffs = tradeoffs == null ? new ArrayList<>() : new ArrayList<>(tradeoffs);
    }

    public Double getRiskScore() {
        return riskScore;
    }

    public void setRiskScore(Double riskScore) {
        this.riskScore = riskScore;
    }

    public Double getCostScore() {
        return costScore;
    }

    public void setCostScore(Double costScore) {
        this.costScore = costScore;
    }

    public Double getBlastRadiusScore() {
        return blastRadiusScore;
    }

    public void setBlastRadiusScore(Double blastRadiusScore) {
        this.blastRadiusScore = blastRadiusScore;
    }

    public Double getHistoricalEffectiveness() {
        return historicalEffectiveness;
    }

    public void setHistoricalEffectiveness(Double historicalEffectiveness) {
        this.historicalEffectiveness = historicalEffectiveness;
    }

    public Double getNoveltyScore() {
        return noveltyScore;
    }

    public void setNoveltyScore(Double noveltyScore) {
        this.noveltyScore = noveltyScore;
    }

    public Double getPriorityScore() {
        return priorityScore;
    }

    public void setPriorityScore(Double priorityScore) {
        this.priorityScore = priorityScore;
    }

    public boolean isRequiresApproval() {
        return requiresApproval;
    }

    public void setRequiresApproval(boolean requiresApproval) {
        this.requiresApproval = requiresApproval;
    }
}
