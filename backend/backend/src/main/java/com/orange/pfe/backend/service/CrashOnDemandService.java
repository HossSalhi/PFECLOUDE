package com.orange.pfe.backend.service;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;

import java.util.concurrent.atomic.AtomicBoolean;

@Service
public class CrashOnDemandService {

    private static final Logger log = LoggerFactory.getLogger(CrashOnDemandService.class);

    private final AtomicBoolean crashScheduled = new AtomicBoolean(false);

    public boolean crashAsync(int delayMs) {
        if (!crashScheduled.compareAndSet(false, true)) {
            log.warn("CRASH_ON_DEMAND already scheduled, ignoring duplicate trigger");
            return false;
        }

        Thread crashThread = new Thread(() -> {
            try {
                log.error("CRASH_ON_DEMAND triggered. JVM will halt in {} ms", delayMs);
                Thread.sleep(Math.max(delayMs, 0));
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
                log.error("Crash thread interrupted, forcing JVM halt immediately");
            }

            log.error("CRASH_ON_DEMAND halting JVM NOW with code 137");
            Runtime.getRuntime().halt(137);
        }, "crash-on-demand-thread");

        crashThread.setDaemon(false);
        crashThread.start();
        return true;
    }

    public boolean isCrashScheduled() {
        return crashScheduled.get();
    }
}