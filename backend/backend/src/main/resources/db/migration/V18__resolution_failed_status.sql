-- Add RESOLUTION_FAILED to both incident and incident_history status enums
ALTER TABLE incident
    DROP CONSTRAINT incident_status_check,
    ADD CONSTRAINT incident_status_check CHECK (status IN (
        'NEW','OPEN','IN_PROGRESS','RESOLVED','CLOSED','ARCHIVED',
        'WAITING_FOR_APPROVAL','RESOLUTION_FAILED'
    ));

ALTER TABLE incident_history
    DROP CONSTRAINT incident_history_status_check,
    ADD CONSTRAINT incident_history_status_check CHECK (status IN (
        'NEW','OPEN','IN_PROGRESS','RESOLVED','CLOSED','ARCHIVED',
        'WAITING_FOR_APPROVAL','RESOLUTION_FAILED'
    ));
