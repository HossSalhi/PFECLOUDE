package com.orange.pfe.backend.dto;

import com.orange.pfe.backend.model.fault.FaultScenario;
import jakarta.validation.constraints.NotNull;

public class FaultCommandRequest {

    @NotNull
    private FaultScenario scenario;

    @NotNull
    private Boolean enabled;

    public FaultScenario getScenario() {
        return scenario;
    }

    public void setScenario(FaultScenario scenario) {
        this.scenario = scenario;
    }

    public Boolean getEnabled() {
        return enabled;
    }

    public void setEnabled(Boolean enabled) {
        this.enabled = enabled;
    }
}