package com.orange.pfe.backend.model.fault;

import org.springframework.stereotype.Component;

@Component
public class FaultState {

    private FaultScenario scenario;
    private boolean enabled;

    public FaultState() {
    }

    public FaultState(FaultScenario scenario, boolean enabled) {
        this.scenario = scenario;
        this.enabled = enabled;
    }

    public FaultScenario getScenario() {
        return scenario;
    }

    public void setScenario(FaultScenario scenario) {
        this.scenario = scenario;
    }

    public boolean isEnabled() {
        return enabled;
    }

    public void setEnabled(boolean enabled) {
        this.enabled = enabled;
    }
}