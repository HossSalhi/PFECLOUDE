package com.orange.pfe.backend.service;

import org.springframework.stereotype.Service;

import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

@Service
public class MemoryStressService {

    private final Object lock = new Object();
    private final List<byte[]> allocatedBlocks = new ArrayList<>();

    private volatile boolean stressing = false;
    private volatile long startedAtMillis = 0L;
    private volatile long endTimeMillis = 0L;
    private volatile int allocatedMb = 0;

    public Map<String, Object> stress(int mb, int holdSeconds) {
        if (mb <= 0) {
            throw new IllegalArgumentException("mb must be > 0");
        }
        if (holdSeconds <= 0) {
            throw new IllegalArgumentException("holdSeconds must be > 0");
        }

        synchronized (lock) {
            clearInternal();

            stressing = true;
            startedAtMillis = System.currentTimeMillis();
            endTimeMillis = startedAtMillis + (holdSeconds * 1000L);

            boolean partialAllocation = false;
            try {
                for (int i = 0; i < mb; i++) {
                    allocatedBlocks.add(new byte[1024 * 1024]);
                }
            } catch (OutOfMemoryError e) {
                partialAllocation = !allocatedBlocks.isEmpty();
                if (!partialAllocation) {
                    stressing = false;
                    Map<String, Object> response = new HashMap<>();
                    response.put("memoryStress", false);
                    response.put("message", "OutOfMemoryError during allocation");
                    response.put("allocatedMb", 0);
                    response.put("requestedMb", mb);
                    response.put("holdSeconds", holdSeconds);
                    return response;
                }
            }

            allocatedMb = allocatedBlocks.size();

            if (allocatedMb <= 0) {
                stressing = false;
                Map<String, Object> response = new HashMap<>();
                response.put("memoryStress", false);
                response.put("message", "Memory stress could not allocate any memory");
                response.put("allocatedMb", 0);
                response.put("requestedMb", mb);
                response.put("holdSeconds", holdSeconds);
                return response;
            }

            Map<String, Object> response = new HashMap<>();
            response.put("memoryStress", true);
            response.put(
                    "message",
                    partialAllocation ? "Memory stress started with partial allocation" : "Memory stress started"
            );
            response.put("allocatedMb", allocatedMb);
            response.put("requestedMb", mb);
            response.put("holdSeconds", holdSeconds);
            response.put("startedAtMillis", startedAtMillis);
            response.put("endTimeMillis", endTimeMillis);
            response.put("remainingMillis", Math.max(0, endTimeMillis - System.currentTimeMillis()));

            startHolderThread();
            return response;
        }
    }

    public Map<String, Object> status() {
        Map<String, Object> response = new HashMap<>();
        response.put("memoryStress", stressing);
        response.put("message", "Memory stress status");
        response.put("allocatedMb", allocatedMb);
        response.put("startedAtMillis", startedAtMillis);
        response.put("endTimeMillis", endTimeMillis);
        response.put("remainingMillis", stressing ? Math.max(0, endTimeMillis - System.currentTimeMillis()) : 0);
        return response;
    }

    public Map<String, Object> stop() {
        synchronized (lock) {
            boolean wasRunning = stressing;
            clearInternal();

            Map<String, Object> response = new HashMap<>();
            response.put("memoryStress", false);
            response.put("message", wasRunning ? "Memory stress stopped" : "Memory stress already stopped");
            response.put("allocatedMb", 0);
            response.put("startedAtMillis", 0);
            response.put("endTimeMillis", 0);
            response.put("remainingMillis", 0);
            return response;
        }
    }

    private void clearInternal() {
        allocatedBlocks.clear();
        allocatedMb = 0;
        stressing = false;
        startedAtMillis = 0L;
        endTimeMillis = 0L;
        System.gc();
    }

    private void startHolderThread() {
        Thread holder = new Thread(() -> {
            try {
                while (System.currentTimeMillis() < endTimeMillis && stressing) {
                    Thread.sleep(500);
                }
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
            } finally {
                synchronized (lock) {
                    clearInternal();
                }
            }
        }, "memory-stress-holder");

        holder.setDaemon(true);
        holder.start();
    }
}
