package com.orange.pfe.backend.dto;

import com.fasterxml.jackson.annotation.JsonProperty;

import java.time.OffsetDateTime;
import java.util.LinkedHashMap;
import java.util.Map;

public class AIPredictRequest {

    private String namespace;
    private String app;

    @JsonProperty("window_minutes")
    private Integer windowMinutes;

    @JsonProperty("end_time")
    private OffsetDateTime endTime;

    @JsonProperty("feature_overrides")
    private Map<String, Double> featureOverrides = new LinkedHashMap<>();

    @JsonProperty("remediation_context")
    private Map<String, Object> remediationContext = new LinkedHashMap<>();

    @JsonProperty("skip_llm")
    private boolean skipLlm = false;

    public boolean isSkipLlm() {
        return skipLlm;
    }

    public void setSkipLlm(boolean skipLlm) {
        this.skipLlm = skipLlm;
    }

    public String getNamespace() {
        return namespace;
    }

    public void setNamespace(String namespace) {
        this.namespace = namespace;
    }

    public String getApp() {
        return app;
    }

    public void setApp(String app) {
        this.app = app;
    }

    public Integer getWindowMinutes() {
        return windowMinutes;
    }

    public void setWindowMinutes(Integer windowMinutes) {
        this.windowMinutes = windowMinutes;
    }

    public OffsetDateTime getEndTime() {
        return endTime;
    }

    public void setEndTime(OffsetDateTime endTime) {
        this.endTime = endTime;
    }

    public Map<String, Double> getFeatureOverrides() {
        return featureOverrides;
    }

    public void setFeatureOverrides(Map<String, Double> featureOverrides) {
        this.featureOverrides = featureOverrides == null ? new LinkedHashMap<>() : new LinkedHashMap<>(featureOverrides);
    }

    public Map<String, Object> getRemediationContext() {
        return remediationContext;
    }

    public void setRemediationContext(Map<String, Object> remediationContext) {
        this.remediationContext = remediationContext == null ? new LinkedHashMap<>() : new LinkedHashMap<>(remediationContext);
    }
}
