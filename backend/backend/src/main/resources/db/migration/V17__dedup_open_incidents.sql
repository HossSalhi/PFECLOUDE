-- Prevent duplicate OPEN incidents for the same workload.
-- A race between two near-simultaneous detections could both pass the application-level
-- "is there already an open incident?" check (TOCTOU) and each insert a row -- observed
-- with #1165/#1166 (same namespace/app/incident_type, 113 ms apart). A partial unique index
-- makes the database itself reject the second still-open incident, which is race-proof and
-- durable regardless of which code path (poller / watcher) created it.
--
-- RESOLVED and CLOSED incidents are excluded, so when a condition recurs after being resolved
-- a fresh incident can still be opened. COALESCE keeps it effective for infra incidents whose
-- namespace/app may be NULL (e.g. NODE_NOT_READY).
CREATE UNIQUE INDEX IF NOT EXISTS ux_incident_open_dedup
    ON incident (COALESCE(namespace, ''), COALESCE(app, ''), incident_type)
    WHERE status NOT IN ('RESOLVED', 'CLOSED');
