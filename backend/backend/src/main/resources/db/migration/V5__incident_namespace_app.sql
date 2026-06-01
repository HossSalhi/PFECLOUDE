ALTER TABLE IF EXISTS incident
    ADD COLUMN IF NOT EXISTS namespace VARCHAR(255);

ALTER TABLE IF EXISTS incident
    ADD COLUMN IF NOT EXISTS app VARCHAR(255);

CREATE INDEX IF NOT EXISTS idx_incident_source_namespace_app
    ON incident (source, namespace, app);
