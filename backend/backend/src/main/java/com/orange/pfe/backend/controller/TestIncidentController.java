package com.orange.pfe.backend.controller;

import com.orange.pfe.backend.model.fault.FaultScenario;
import com.orange.pfe.backend.service.CpuStressService;
import com.orange.pfe.backend.service.CrashOnDemandService;
import com.orange.pfe.backend.service.DbPoolExhaustionService;
import com.orange.pfe.backend.service.DbProbeService;
import com.orange.pfe.backend.service.FaultInjectionService;
import com.orange.pfe.backend.service.MemoryStressService;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

import java.util.Map;

@RestController
@RequestMapping("/api/test")
public class TestIncidentController {

    private static final Logger log = LoggerFactory.getLogger(TestIncidentController.class);

    private final FaultInjectionService faultInjectionService;
    private final DbProbeService dbProbeService;
    private final CpuStressService cpuStressService;
    private final MemoryStressService memoryStressService;
    private final DbPoolExhaustionService dbPoolExhaustionService;
    private final CrashOnDemandService crashOnDemandService;

    public TestIncidentController(FaultInjectionService faultInjectionService,
                                  DbProbeService dbProbeService,
                                  CpuStressService cpuStressService,
                                  MemoryStressService memoryStressService,
                                  DbPoolExhaustionService dbPoolExhaustionService,
                                  CrashOnDemandService crashOnDemandService) {
        this.faultInjectionService = faultInjectionService;
        this.dbProbeService = dbProbeService;
        this.cpuStressService = cpuStressService;
        this.memoryStressService = memoryStressService;
        this.dbPoolExhaustionService = dbPoolExhaustionService;
        this.crashOnDemandService = crashOnDemandService;
    }

    @GetMapping("/health-readiness")
    public ResponseEntity<String> readiness() {
        if (faultInjectionService.isEnabled(FaultScenario.READINESS_FAILURE)) {
            return ResponseEntity.status(HttpStatus.INTERNAL_SERVER_ERROR).body("not-ready");
        }
        return ResponseEntity.ok("ready");
    }

    @GetMapping("/health-liveness")
    public ResponseEntity<String> liveness() {
        if (faultInjectionService.isEnabled(FaultScenario.LIVENESS_FAILURE)) {
            return ResponseEntity.status(HttpStatus.INTERNAL_SERVER_ERROR).body("not-alive");
        }
        return ResponseEntity.ok("alive");
    }

    @GetMapping("/db")
    public ResponseEntity<String> db() {
        if (faultInjectionService.isEnabled(FaultScenario.HIGH_LATENCY)) {
            try {
                Thread.sleep(5000);
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
            }
        }
        return ResponseEntity.ok(dbProbeService.probe());
    }

    @GetMapping("/degrade")
    public ResponseEntity<Map<String, Object>> degrade() {
        log.warn("GENERAL_DEGRADATION probe executed");
        return ResponseEntity.ok(Map.of(
                "status", "degraded-path-ok",
                "latencyInjectionEnabled", faultInjectionService.isEnabled(FaultScenario.HIGH_LATENCY)
        ));
    }

    @GetMapping("/error-storm")
    public ResponseEntity<Map<String, Object>> errorStorm(
            @RequestParam(defaultValue = "simulated application error storm") String message) {
        log.error("APPLICATION_ERROR_STORM simulated failure: {}", message);
        return ResponseEntity.status(HttpStatus.INTERNAL_SERVER_ERROR).body(Map.of(
                "error", "APPLICATION_ERROR_STORM",
                "message", message,
                "status", 500
        ));
    }

    @GetMapping("/crash")
    public ResponseEntity<String> crash() {
        if (!faultInjectionService.isEnabled(FaultScenario.CRASH_ON_DEMAND)) {
            return ResponseEntity.ok("crash-disabled");
        }

        boolean scheduled = crashOnDemandService.crashAsync(1000);

        if (!scheduled) {
            return ResponseEntity.status(HttpStatus.CONFLICT).body("crash-already-scheduled");
        }

        return ResponseEntity.status(HttpStatus.ACCEPTED).body("crash-scheduled");
    }

    @GetMapping("/cpu")
    public ResponseEntity<Map<String, Object>> cpu(@RequestParam(defaultValue = "15") int seconds) {
        if (!faultInjectionService.isEnabled(FaultScenario.CPU_STRESS)) {
            return ResponseEntity.ok(Map.of(
                    "cpuStress", false,
                    "message", "CPU_STRESS disabled"
            ));
        }
        return ResponseEntity.ok(cpuStressService.stress(seconds));
    }

    @GetMapping("/cpu/status")
    public ResponseEntity<Map<String, Object>> cpuStatus() {
        return ResponseEntity.ok(cpuStressService.status());
    }

    @PostMapping("/cpu/stop")
    public ResponseEntity<Map<String, Object>> stopCpu() {
        return ResponseEntity.ok(cpuStressService.stop());
    }

    @GetMapping("/memory")
    public ResponseEntity<Map<String, Object>> memory(
            @RequestParam(defaultValue = "100") int mb,
            @RequestParam(defaultValue = "20") int holdSeconds) {

        if (!faultInjectionService.isEnabled(FaultScenario.MEMORY_STRESS)) {
            return ResponseEntity.ok(Map.of(
                    "memoryStress", false,
                    "message", "MEMORY_STRESS disabled"
            ));
        }

        return ResponseEntity.ok(memoryStressService.stress(mb, holdSeconds));
    }

    @GetMapping("/memory/status")
    public ResponseEntity<Map<String, Object>> memoryStatus() {
        return ResponseEntity.ok(memoryStressService.status());
    }

    @PostMapping("/memory/stop")
    public ResponseEntity<Map<String, Object>> stopMemory() {
        return ResponseEntity.ok(memoryStressService.stop());
    }

    @GetMapping("/db-pool-exhaust")
    public ResponseEntity<Map<String, Object>> dbPoolExhaustStatus() {
        return ResponseEntity.ok(dbPoolExhaustionService.status());
    }

    @PostMapping("/db-pool-exhaust/start")
    public ResponseEntity<Map<String, Object>> startDbPoolExhaust(
            @RequestParam(defaultValue = "5") int connections) {
        return ResponseEntity.ok(dbPoolExhaustionService.enable(connections));
    }

    @PostMapping("/db-pool-exhaust/stop")
    public ResponseEntity<Map<String, Object>> stopDbPoolExhaust() {
        return ResponseEntity.ok(dbPoolExhaustionService.disable());
    }
}
