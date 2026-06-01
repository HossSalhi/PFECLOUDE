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

    public InfrastructureController(InfrastructureHealthService healthService,
                                    KubernetesDiscoveryService discoveryService,
                                    AiopsPollingProperties pollingProperties) {
        this.healthService = healthService;
        this.discoveryService = discoveryService;
        this.pollingProperties = pollingProperties;
    }

    @GetMapping("/health")
    public ResponseEntity<Map<String, Object>> health() {
        List<InfrastructureHealthService.ComponentStatus> statuses = healthService.checkAll();

        List<Map<String, Object>> components = statuses.stream().map(s -> Map.<String, Object>of(
                "namespace", s.namespace(),
                "app",       s.app(),
                "up",        s.up(),
                "status",    s.up() ? "UP" : "DOWN",
                "detail",    s.detail()
        )).collect(Collectors.toList());

        long upCount   = statuses.stream().filter(InfrastructureHealthService.ComponentStatus::up).count();
        long downCount = statuses.size() - upCount;

        return ResponseEntity.ok(Map.of(
                "checkedAt",  LocalDateTime.now().toString(),
                "total",      statuses.size(),
                "up",         upCount,
                "down",       downCount,
                "components", components
        ));
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
                {"vm-ai-camunda", "ai-service"},
                {"vm-ai-camunda", "camunda"},
                {"vm-ai-camunda", "ollama"},
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
