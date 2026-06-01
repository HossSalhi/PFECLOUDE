package com.orange.pfe.backend.service;

import com.orange.pfe.backend.model.fault.FaultScenario;

import java.util.Map;

public interface FaultInjectionService {

    boolean isEnabled(FaultScenario scenario);

    void setFault(FaultScenario scenario, boolean enabled);

    Map<String, Boolean> getAllStates();

    void resetAll();
}