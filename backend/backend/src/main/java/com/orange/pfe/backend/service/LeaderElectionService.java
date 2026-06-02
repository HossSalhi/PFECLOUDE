package com.orange.pfe.backend.service;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.util.UUID;

/**
 * Lightweight DB-lease leader election for HA.
 *
 * When the backend runs multiple replicas, every replica would otherwise run the
 * automatic schedulers (AIOps polling, deployment watcher, Camunda recovery) and
 * create/process the SAME incident twice. This service lets only one replica — the
 * lease holder — perform that work.
 *
 * The lease is a single row in {@code leader_lease}. {@link #isLeader()} atomically
 * acquires or renews it; a lease not renewed within {@link #LEASE_TTL_SECONDS} is
 * considered stale and can be taken over (so leadership fails over if the holder dies).
 */
@Service
public class LeaderElectionService {

    private static final Logger log = LoggerFactory.getLogger(LeaderElectionService.class);

    private static final String LEASE_NAME = "aiops-scheduler";
    /** A lease not renewed within this window is taken over by another replica. */
    private static final int LEASE_TTL_SECONDS = 300;

    private final JdbcTemplate jdbc;
    private final String instanceId;

    public LeaderElectionService(JdbcTemplate jdbc) {
        this.jdbc = jdbc;
        String host = System.getenv("HOSTNAME");
        this.instanceId = (host != null && !host.isBlank() ? host : "instance")
                + "-" + UUID.randomUUID().toString().substring(0, 8);
        log.info("Leader election instance id = {}", instanceId);
    }

    /**
     * @return true if THIS replica holds the lease after this call (i.e. it is the leader).
     *         On any DB error this returns false (fail-safe: do not assume leadership).
     */
    @Transactional
    public boolean isLeader() {
        try {
            // Atomic acquire-or-renew: insert the lease, or update it only if WE already
            // hold it or the current lease is stale. A fresh lease held by another replica
            // leaves the row untouched (we simply won't be the holder).
            jdbc.update(
                    "INSERT INTO leader_lease (lease_name, holder, renewed_at) VALUES (?, ?, now()) "
                    + "ON CONFLICT (lease_name) DO UPDATE SET holder = EXCLUDED.holder, renewed_at = now() "
                    + "WHERE leader_lease.holder = ? "
                    + "   OR leader_lease.renewed_at < now() - (? * interval '1 second')",
                    LEASE_NAME, instanceId, instanceId, LEASE_TTL_SECONDS
            );
            String holder = jdbc.queryForObject(
                    "SELECT holder FROM leader_lease WHERE lease_name = ?", String.class, LEASE_NAME);
            boolean leader = instanceId.equals(holder);
            log.debug("Leader check: me={} holder={} leader={}", instanceId, holder, leader);
            return leader;
        } catch (Exception ex) {
            log.warn("Leader election check failed ({}) — assuming NOT leader this cycle", ex.getMessage());
            return false;
        }
    }

    public String getInstanceId() {
        return instanceId;
    }
}
