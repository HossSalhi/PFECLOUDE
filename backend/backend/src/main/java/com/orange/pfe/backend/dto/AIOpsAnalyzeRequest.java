package com.orange.pfe.backend.dto;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Positive;

import java.time.OffsetDateTime;

public class AIOpsAnalyzeRequest {

    @NotBlank(message = "namespace required")
    private String namespace;

    @NotBlank(message = "app required")
    private String app;

    @Positive(message = "windowMinutes must be > 0")
    private Integer windowMinutes = 5;
    private OffsetDateTime endTime;
    private boolean createIncident = true;

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

    public boolean isCreateIncident() {
        return createIncident;
    }

    public void setCreateIncident(boolean createIncident) {
        this.createIncident = createIncident;
    }
}
