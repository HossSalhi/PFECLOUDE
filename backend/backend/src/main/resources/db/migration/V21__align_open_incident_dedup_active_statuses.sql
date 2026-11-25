-- Align the database deduplication index with HostIncidentIngestionService.ACTIVE.
-- RESOLVED, CLOSED, ARCHIVED and RESOLUTION_FAILED are not active incidents.
-- Keep historical incident rows; only change which statuses participate in deduplication.

DROP INDEX IF EXISTS ux_incident_open_dedup;

CREATE UNIQUE INDEX ux_incident_open_dedup
    ON incident (COALESCE(namespace, ''), COALESCE(app, ''), incident_type)
    WHERE status IN ('NEW', 'OPEN', 'IN_PROGRESS', 'WAITING_FOR_APPROVAL');