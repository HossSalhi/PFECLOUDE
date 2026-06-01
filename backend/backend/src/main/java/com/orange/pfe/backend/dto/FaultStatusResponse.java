package com.orange.pfe.backend.dto;

import java.util.Map;

public class FaultStatusResponse {

    private Map<String, Boolean> states;

    public FaultStatusResponse() {
    }

    public FaultStatusResponse(Map<String, Boolean> states) {
        this.states = states;
    }

    public Map<String, Boolean> getStates() {
        return states;
    }

    public void setStates(Map<String, Boolean> states) {
        this.states = states;
    }
}