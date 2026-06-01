package com.orange.pfe.backend.service;

import com.orange.pfe.backend.model.fault.FaultScenario;
import jakarta.annotation.PostConstruct;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;

@Service
public class StartupCrashService {

    private final FaultInjectionService faultInjectionService;

    @Value("${fault.crash-on-startup:false}")
    private boolean crashOnStartup;

    public StartupCrashService(FaultInjectionService faultInjectionService) {
        this.faultInjectionService = faultInjectionService;
    }

    @PostConstruct
    public void checkStartupCrash() {
        if (crashOnStartup) {
            faultInjectionService.setFault(FaultScenario.CRASH_ON_STARTUP, true);
            throw new RuntimeException("Injected startup crash: CRASH_ON_STARTUP=true");
        }
    }
}