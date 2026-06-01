package com.orange.pfe.backend.dto;

import com.fasterxml.jackson.annotation.JsonProperty;

public class AIPredictEvidence {

    @JsonProperty("log_score")
    private Double logScore;

    @JsonProperty("metric_score")
    private Double metricScore;

    @JsonProperty("rule_score")
    private Double ruleScore;

    @JsonProperty("model_score")
    private Double modelScore;

    public Double getLogScore() {
        return logScore;
    }

    public void setLogScore(Double logScore) {
        this.logScore = logScore;
    }

    public Double getMetricScore() {
        return metricScore;
    }

    public void setMetricScore(Double metricScore) {
        this.metricScore = metricScore;
    }

    public Double getRuleScore() {
        return ruleScore;
    }

    public void setRuleScore(Double ruleScore) {
        this.ruleScore = ruleScore;
    }

    public Double getModelScore() {
        return modelScore;
    }

    public void setModelScore(Double modelScore) {
        this.modelScore = modelScore;
    }
}
