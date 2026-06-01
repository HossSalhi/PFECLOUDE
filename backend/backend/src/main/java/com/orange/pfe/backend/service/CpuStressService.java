package com.orange.pfe.backend.service;

import org.springframework.stereotype.Service;

import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.atomic.AtomicBoolean;

@Service
public class CpuStressService {

    private final AtomicBoolean running = new AtomicBoolean(false);
    private final List<Thread> workers = new ArrayList<>();

    private volatile long startedAtMillis = 0;
    private volatile long endTimeMillis = 0;
    private volatile int currentThreads = 0;

    public synchronized Map<String, Object> stress(int seconds) {
        if (seconds <= 0) {
            throw new IllegalArgumentException("seconds must be > 0");
        }

        if (running.get()) {
            return statusWithMessage("CPU stress already running");
        }

        int threads = Math.max(2, Runtime.getRuntime().availableProcessors() * 2);

        running.set(true);
        startedAtMillis = System.currentTimeMillis();
        endTimeMillis = startedAtMillis + (seconds * 1000L);
        currentThreads = threads;
        workers.clear();

        for (int i = 0; i < threads; i++) {
            Thread worker = new Thread(() -> {
                long x = 0;
                try {
                    while (running.get() && System.currentTimeMillis() < endTimeMillis) {
                        for (int j = 0; j < 1_000_000; j++) {
                            x += (j * 31L) % 7L;
                        }
                    }
                } finally {
                    // nothing
                }
            }, "cpu-stress-" + i);

            worker.setDaemon(true);
            worker.start();
            workers.add(worker);
        }

        Thread supervisor = new Thread(() -> {
            try {
                while (running.get() && System.currentTimeMillis() < endTimeMillis) {
                    Thread.sleep(200);
                }
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
            } finally {
                stopInternal();
            }
        }, "cpu-stress-supervisor");

        supervisor.setDaemon(true);
        supervisor.start();

        Map<String, Object> response = new HashMap<>();
        response.put("cpuStress", true);
        response.put("message", "CPU stress started");
        response.put("threads", currentThreads);
        response.put("startedAtMillis", startedAtMillis);
        response.put("endTimeMillis", endTimeMillis);
        response.put("remainingMillis", Math.max(0, endTimeMillis - System.currentTimeMillis()));
        response.put("availableProcessors", Runtime.getRuntime().availableProcessors());
        return response;
    }

    public synchronized Map<String, Object> status() {
        boolean active = running.get() && System.currentTimeMillis() < endTimeMillis;

        if (!active && running.get()) {
            stopInternal();
        }

        Map<String, Object> response = new HashMap<>();
        response.put("cpuStress", active);
        response.put("message", "CPU stress status");
        response.put("threads", active ? currentThreads : 0);
        response.put("startedAtMillis", active ? startedAtMillis : 0);
        response.put("endTimeMillis", active ? endTimeMillis : 0);
        response.put("remainingMillis", active ? Math.max(0, endTimeMillis - System.currentTimeMillis()) : 0);
        response.put("availableProcessors", Runtime.getRuntime().availableProcessors());
        return response;
    }

    public synchronized Map<String, Object> stop() {
        if (!running.get()) {
            Map<String, Object> response = new HashMap<>();
            response.put("cpuStress", false);
            response.put("message", "CPU stress already stopped");
            response.put("threads", 0);
            response.put("startedAtMillis", 0);
            response.put("endTimeMillis", 0);
            response.put("remainingMillis", 0);
            response.put("availableProcessors", Runtime.getRuntime().availableProcessors());
            return response;
        }

        stopInternal();

        Map<String, Object> response = new HashMap<>();
        response.put("cpuStress", false);
        response.put("message", "CPU stress stopped");
        response.put("threads", 0);
        response.put("startedAtMillis", 0);
        response.put("endTimeMillis", 0);
        response.put("remainingMillis", 0);
        response.put("availableProcessors", Runtime.getRuntime().availableProcessors());
        return response;
    }

    private synchronized void stopInternal() {
        running.set(false);

        for (Thread worker : workers) {
            if (worker != null && worker.isAlive()) {
                worker.interrupt();
            }
        }

        workers.clear();
        currentThreads = 0;
        startedAtMillis = 0;
        endTimeMillis = 0;
    }

    private Map<String, Object> statusWithMessage(String message) {
        Map<String, Object> response = status();
        response.put("message", message);
        return response;
    }
}