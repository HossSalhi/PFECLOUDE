ALTER TABLE IF EXISTS workflow_instance
    ADD COLUMN IF NOT EXISTS camunda_process_definition_id VARCHAR(255);

ALTER TABLE IF EXISTS workflow_instance
    ADD COLUMN IF NOT EXISTS camunda_process_instance_key VARCHAR(255);

ALTER TABLE IF EXISTS workflow_instance
    ADD COLUMN IF NOT EXISTS correlation_key VARCHAR(255);

ALTER TABLE IF EXISTS workflow_instance
    ADD COLUMN IF NOT EXISTS orchestration_state VARCHAR(64);

ALTER TABLE IF EXISTS workflow_instance
    ADD COLUMN IF NOT EXISTS target_namespace VARCHAR(255);

ALTER TABLE IF EXISTS workflow_instance
    ADD COLUMN IF NOT EXISTS target_app VARCHAR(255);

ALTER TABLE IF EXISTS incident_action
    ADD COLUMN IF NOT EXISTS target_namespace VARCHAR(255);

ALTER TABLE IF EXISTS incident_action
    ADD COLUMN IF NOT EXISTS target_kind VARCHAR(64);

ALTER TABLE IF EXISTS incident_action
    ADD COLUMN IF NOT EXISTS target_name VARCHAR(255);

ALTER TABLE IF EXISTS incident_action
    ADD COLUMN IF NOT EXISTS automation_mode VARCHAR(64);

ALTER TABLE IF EXISTS incident_action
    ADD COLUMN IF NOT EXISTS attempt_count INTEGER DEFAULT 0 NOT NULL;

ALTER TABLE IF EXISTS incident_action
    ADD COLUMN IF NOT EXISTS executor_result VARCHAR(1000);

CREATE INDEX IF NOT EXISTS idx_incident_action_target
    ON incident_action (target_namespace, target_name, created_at);

CREATE INDEX IF NOT EXISTS idx_workflow_instance_correlation_key
    ON workflow_instance (correlation_key);
