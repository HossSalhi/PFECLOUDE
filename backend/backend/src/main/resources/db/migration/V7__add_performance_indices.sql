-- V7: performance indices on high-frequency query columns
-- incident: filtered/sorted by status, severity, created_at
CREATE INDEX IF NOT EXISTS idx_incident_status       ON incident (status);
CREATE INDEX IF NOT EXISTS idx_incident_severity     ON incident (severity);
CREATE INDEX IF NOT EXISTS idx_incident_created_at   ON incident (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_incident_source       ON incident (source);
CREATE INDEX IF NOT EXISTS idx_incident_status_sev   ON incident (status, severity);

-- alert: always queried by incident_id
CREATE INDEX IF NOT EXISTS idx_alert_incident_id     ON alert (incident_id);

-- incident_history: queried by incident_id
CREATE INDEX IF NOT EXISTS idx_history_incident_id   ON incident_history (incident_id);

-- incident_action: queried by incident_id and status
CREATE INDEX IF NOT EXISTS idx_action_incident_id    ON incident_action (incident_id);
CREATE INDEX IF NOT EXISTS idx_action_status         ON incident_action (status);
CREATE INDEX IF NOT EXISTS idx_action_executed_at    ON incident_action (executed_at DESC);

-- workflow_instance: queried by incident_id and status
CREATE INDEX IF NOT EXISTS idx_workflow_incident_id  ON workflow_instance (incident_id);
CREATE INDEX IF NOT EXISTS idx_workflow_status       ON workflow_instance (status);

-- workflow_logs: queried by workflow_id
CREATE INDEX IF NOT EXISTS idx_wflog_workflow_id     ON workflow_logs (workflow_id);

-- incident_decision: queried by incident_id
CREATE INDEX IF NOT EXISTS idx_decision_incident_id  ON incident_decision (incident_id);

-- human_task: queried by workflow_instance_id and decision
CREATE INDEX IF NOT EXISTS idx_task_workflow_id      ON human_task (workflow_instance_id);
CREATE INDEX IF NOT EXISTS idx_task_decision         ON human_task (decision);
