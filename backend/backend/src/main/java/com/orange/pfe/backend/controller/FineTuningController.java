package com.orange.pfe.backend.controller;

import com.orange.pfe.backend.service.SseEventService;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.time.LocalDateTime;
import java.util.Map;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicReference;

@RestController
@RequestMapping("/api/finetuning")
public class FineTuningController {

    private static final Logger log = LoggerFactory.getLogger(FineTuningController.class);

    private final SseEventService sseEventService;

    @Value("${aiops.finetuning.script-path:ai-service/scripts/llm_continuous_learning.py}")
    private String scriptPath;

    @Value("${aiops.finetuning.python-path:python}")
    private String pythonPath;

    private final AtomicBoolean running = new AtomicBoolean(false);
    private final AtomicReference<String> lastStatus = new AtomicReference<>("IDLE");
    private final AtomicReference<String> startedAt = new AtomicReference<>(null);
    private final AtomicReference<String> lastLog = new AtomicReference<>("");

    public FineTuningController(SseEventService sseEventService) {
        this.sseEventService = sseEventService;
    }

    @GetMapping("/status")
    public ResponseEntity<Map<String, Object>> status() {
        return ResponseEntity.ok(Map.of(
                "running",    running.get(),
                "status",     lastStatus.get(),
                "startedAt",  startedAt.get() != null ? startedAt.get() : "",
                "lastLog",    lastLog.get()
        ));
    }

    @PostMapping("/trigger")
    public ResponseEntity<Map<String, Object>> trigger(
            @RequestParam(defaultValue = "false") boolean force,
            @RequestParam(defaultValue = "20") int minIncidents) {

        if (running.get()) {
            return ResponseEntity.status(409).body(Map.of(
                    "error", "Fine-tuning already running",
                    "startedAt", startedAt.get()
            ));
        }

        running.set(true);
        lastStatus.set("STARTING");
        startedAt.set(LocalDateTime.now().toString());

        Thread thread = new Thread(() -> runPipeline(force, minIncidents));
        thread.setDaemon(true);
        thread.setName("finetuning-pipeline");
        thread.start();

        return ResponseEntity.accepted().body(Map.of(
                "message",   "Fine-tuning pipeline started",
                "startedAt", startedAt.get(),
                "force",     force
        ));
    }

    private void runPipeline(boolean force, int minIncidents) {
        try {
            Path script = Paths.get(scriptPath).toAbsolutePath();
            ProcessBuilder pb = new ProcessBuilder(
                    pythonPath,
                    script.toString(),
                    "--min-incidents", String.valueOf(minIncidents),
                    force ? "--force" : ""
            );
            pb.redirectErrorStream(true);

            broadcast("finetuning_started", Map.of("startedAt", startedAt.get()));
            lastStatus.set("RUNNING");

            Process process = pb.start();
            try (BufferedReader reader = new BufferedReader(new InputStreamReader(process.getInputStream()))) {
                String line;
                while ((line = reader.readLine()) != null) {
                    lastLog.set(line);
                    log.info("[fine-tuning] {}", line);
                    broadcast("finetuning_log", Map.of("line", line, "ts", LocalDateTime.now().toString()));
                }
            }

            int exitCode = process.waitFor();
            if (exitCode == 0) {
                lastStatus.set("SUCCESS");
                broadcast("finetuning_done", Map.of("status", "SUCCESS", "exitCode", exitCode));
            } else {
                lastStatus.set("FAILED");
                broadcast("finetuning_done", Map.of("status", "FAILED", "exitCode", exitCode));
            }
        } catch (Exception e) {
            log.error("Fine-tuning pipeline error: {}", e.getMessage());
            lastStatus.set("ERROR");
            broadcast("finetuning_done", Map.of("status", "ERROR", "error", e.getMessage()));
        } finally {
            running.set(false);
        }
    }

    private void broadcast(String event, Object payload) {
        try {
            sseEventService.broadcast(event, payload);
        } catch (Exception ignored) {}
    }
}
