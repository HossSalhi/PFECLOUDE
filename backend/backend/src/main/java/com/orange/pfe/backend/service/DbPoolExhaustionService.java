package com.orange.pfe.backend.service;

import jakarta.annotation.PreDestroy;
import org.springframework.stereotype.Service;

import com.zaxxer.hikari.HikariDataSource;

import javax.sql.DataSource;
import java.sql.Connection;
import java.sql.SQLException;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

@Service
public class DbPoolExhaustionService {

    private final DataSource dataSource;
    private final List<Connection> heldConnections = new ArrayList<>();
    private boolean active = false;

    public DbPoolExhaustionService(DataSource dataSource) {
        this.dataSource = dataSource;
    }

    public synchronized Map<String, Object> enable(int connections) {
        if (active) {
            return status();
        }

        try {
            int targetConnections = computeTargetConnections(connections);
            for (int i = 0; i < targetConnections; i++) {
                heldConnections.add(dataSource.getConnection());
            }

            active = true;
            return status();

        } catch (SQLException e) {
            releaseSilently();
            throw new RuntimeException("Failed to exhaust DB pool", e);
        }
    }

    public synchronized Map<String, Object> disable() {
        releaseSilently();
        active = false;
        return status();
    }

    public synchronized Map<String, Object> status() {
        Map<String, Object> response = new HashMap<>();
        response.put("dbPoolExhaustion", active);
        response.put("heldConnections", heldConnections.size());
        response.put("requestedConnections", heldConnections.size());
        return response;
    }

    public synchronized boolean isActive() {
        return active;
    }

    private int computeTargetConnections(int requestedConnections) {
        int safeRequested = Math.max(1, requestedConnections);
        if (dataSource instanceof HikariDataSource hikariDataSource) {
            int maxPoolSize = Math.max(1, hikariDataSource.getMaximumPoolSize());
            // Keep one connection available for health probes and monitoring.
            // Holding all-but-one creates real connection contention (errors, latency)
            // that is visible to Prometheus and Loki — necessary for AI detection.
            int reservedConnections = Math.min(1, Math.max(0, maxPoolSize - 1));
            int availableForExhaustion = Math.max(1, maxPoolSize - reservedConnections);
            return Math.max(1, Math.min(safeRequested, availableForExhaustion));
        }
        return safeRequested;
    }

    private void releaseSilently() {
        for (Connection connection : heldConnections) {
            try {
                connection.close();
            } catch (SQLException ignored) {
            }
        }
        heldConnections.clear();
    }

    @PreDestroy
    public void cleanup() {
        synchronized (this) {
            releaseSilently();
            active = false;
        }
    }
}
