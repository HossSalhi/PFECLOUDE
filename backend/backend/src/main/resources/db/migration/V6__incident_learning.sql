ALTER TABLE IF EXISTS incident
    ADD COLUMN IF NOT EXISTS incident_type VARCHAR(100);

CREATE TABLE IF NOT EXISTS incident_learning (
    id                      BIGSERIAL PRIMARY KEY,
    incident_id             BIGINT,
    namespace               VARCHAR(255),
    app                     VARCHAR(255),
    incident_type           VARCHAR(100),
    action_taken            VARCHAR(255),
    severity                VARCHAR(50),
    success                 BOOLEAN NOT NULL DEFAULT FALSE,
    resolution_time_minutes INTEGER,
    created_at              TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_learning_namespace_app
    ON incident_learning (namespace, app, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_learning_incident_type
    ON incident_learning (incident_type, success, created_at DESC);
