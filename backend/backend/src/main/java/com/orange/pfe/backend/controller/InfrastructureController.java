package com.orange.pfe.backend.controller;

import com.orange.pfe.backend.config.AiopsPollingProperties;
import com.orange.pfe.backend.service.InfrastructureHealthService;
import com.orange.pfe.backend.service.KubernetesDiscoveryService;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import java.time.LocalDateTime;
import java.util.List;
import java.util.Map;
import java.util.stream.Collectors;

@RestController
@RequestMapping("/api/infrastructure")
public class InfrastructureController {

    private final InfrastructureHealthService healthService;
    private final KubernetesDiscoveryService discoveryService;
    private final AiopsPollingProperties pollingProperties;
    private final org.springframework.beans.factory.ObjectProvider<com.orange.pfe.backend.service.vmpower.VmPowerWatch> vmPowerWatch;

    public InfrastructureController(InfrastructureHealthService healthService,
                                    KubernetesDiscoveryService discoveryService,
                                    AiopsPollingProperties pollingProperties,
                                    org.springframework.beans.factory.ObjectProvider<com.orange.pfe.backend.service.vmpower.VmPowerWatch> vmPowerWatch) {
        this.healthService = healthService;
        this.discoveryService = discoveryService;
        this.pollingProperties = pollingProperties;
        this.vmPowerWatch = vmPowerWatch;
    }

    @GetMapping("/health")
    public ResponseEntity<Map<String, Object>> health() {
        // VMDOWN-POLICY-20261105 : snapshot (<= 45 s) au lieu d'un contrôle complet à chaque appel de l'interface.
        InfrastructureHealthService.Snapshot snapshot = healthService.freshSnapshot(45_000L);
        List<InfrastructureHealthService.ComponentStatus> statuses = snapshot.statuses();

        List<Map<String, Object>> components = statuses.stream().map(s -> Map.<String, Object>of(
                "namespace", s.namespace(),
                "app",       s.app(),
                "up",        s.up(),
                "status",    s.up() ? "UP" : "DOWN",
                "detail",    s.detail()
        )).collect(Collectors.toList());

        long upCount   = statuses.stream().filter(InfrastructureHealthService.ComponentStatus::up).count();
        long downCount = statuses.size() - upCount;

        Map<String, Object> body = new java.util.LinkedHashMap<>();
        body.put("checkedAt", java.time.LocalDateTime.ofInstant(
                java.time.Instant.ofEpochMilli(snapshot.generatedAtMs()), java.time.ZoneId.systemDefault()).toString());
        body.put("total", statuses.size());
        body.put("up", upCount);
        body.put("down", downCount);
        body.put("components", components);
        // Politique VM_DOWN : mode de chaque VM (AUTO / DETECT_ONLY / DENY_RESTART) et dernier état d'alimentation.
        body.put("vmPolicy", vmPowerWatch.getIfAvailable() != null
                ? vmPowerWatch.getIfAvailable().snapshot() : com.orange.pfe.backend.service.vmpower.VmPowerWatch.policyOnly());
        return ResponseEntity.ok(body);
    }

    @GetMapping("/apps")
    public ResponseEntity<Map<String, Object>> monitoredApps() {
        List<AiopsPollingProperties.Target> discovered = discoveryService.discoverTargets();

        List<Map<String, Object>> apps = new java.util.ArrayList<>();
        for (AiopsPollingProperties.Target t : discovered) {
            Map<String, Object> entry = new java.util.LinkedHashMap<>();
            entry.put("namespace", t.getNamespace());
            entry.put("app",       t.getApp());
            entry.put("type",      "K8s Deployment");
            entry.put("monitored", true);
            apps.add(entry);
        }

        // Static VM-based services
        for (String[] vm : new String[][]{
                {"vm-ai-camunda", "camunda"},
                {"vm-monitoring",  "prometheus"},
                {"vm-monitoring",  "grafana"},
                {"vm-monitoring",  "loki"}
        }) {
            Map<String, Object> entry = new java.util.LinkedHashMap<>();
            entry.put("namespace", vm[0]);
            entry.put("app",       vm[1]);
            entry.put("type",      "VM Process");
            entry.put("monitored", true);
            apps.add(entry);
        }

        Map<String, Object> result = new java.util.LinkedHashMap<>();
        result.put("refreshedAt", LocalDateTime.now().toString());
        result.put("total",       apps.size());
        result.put("apps",        apps);
        return ResponseEntity.ok(result);
    }
}
