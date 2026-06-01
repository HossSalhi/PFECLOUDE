package com.orange.pfe.backend.service;

import com.orange.pfe.backend.model.fault.FaultScenario;
import jakarta.annotation.PostConstruct;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;

import java.util.EnumMap;
import java.util.LinkedHashMap;
import java.util.Map;

@Service
public class FaultInjectionServiceImpl implements FaultInjectionService {

    // READINESS_FAILURE and LIVENESS_FAILURE auto-expire after 3 minutes so that
    // pods self-heal when the test teardown cannot reach them (Service has 0 endpoints).
    private static final long PROBE_FAULT_EXPIRY_MS = 180_000L;

    private final Map<FaultScenario, Boolean> states = new EnumMap<>(FaultScenario.class);
    private final Map<FaultScenario, Long> expiryTimes = new EnumMap<>(FaultScenario.class);
    private final DbPoolExhaustionService dbPoolExhaustionService;

    @Value("${fault.crash-on-startup:false}")
    private boolean crashOnStartup;

    public FaultInjectionServiceImpl(DbPoolExhaustionService dbPoolExhaustionService) {
        this.dbPoolExhaustionService = dbPoolExhaustionService;
    }

    @PostConstruct
    public void init() {
        for (FaultScenario scenario : FaultScenario.values()) {
            states.put(scenario, false);
        }

        if (crashOnStartup) {
            states.put(FaultScenario.CRASH_ON_STARTUP, true);
            throw new RuntimeException("Injected startup crash: CRASH_ON_STARTUP=true");
        }
    }

    @Override
    public synchronized boolean isEnabled(FaultScenario scenario) {
        boolean enabled = states.getOrDefault(scenario, false);
        if (enabled && isProbeScenario(scenario)) {
            Long expiry = expiryTimes.get(scenario);
            if (expiry != null && System.currentTimeMillis() > expiry) {
                states.put(scenario, false);
                expiryTimes.remove(scenario);
                return false;
            }
        }
        return enabled;
    }

    @Override
    public synchronized void setFault(FaultScenario scenario, boolean enabled) {
        states.put(scenario, enabled);
        if (enabled && isProbeScenario(scenario)) {
            expiryTimes.put(scenario, System.currentTimeMillis() + PROBE_FAULT_EXPIRY_MS);
        } else if (!enabled) {
            expiryTimes.remove(scenario);
        }

        if (scenario == FaultScenario.DB_POOL_EXHAUSTION && !enabled) {
            dbPoolExhaustionService.disable();
        }
    }

    private static boolean isProbeScenario(FaultScenario scenario) {
        return scenario == FaultScenario.READINESS_FAILURE || scenario == FaultScenario.LIVENESS_FAILURE;
    }

    @Override
    public synchronized Map<String, Boolean> getAllStates() {
        Map<String, Boolean> result = new LinkedHashMap<>();
        for (FaultScenario scenario : FaultScenario.values()) {
            result.put(scenario.name(), states.getOrDefault(scenario, false));
        }
        return result;
    }

    @Override
    public synchronized void resetAll() {
        for (FaultScenario scenario : FaultScenario.values()) {
            states.put(scenario, false);
        }
        dbPoolExhaustionService.disable();
    }
}
