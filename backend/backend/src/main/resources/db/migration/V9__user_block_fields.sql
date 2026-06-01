-- Adds block tracking fields to app_user

ALTER TABLE app_user
    ADD COLUMN IF NOT EXISTS blocked_at   TIMESTAMP,
    ADD COLUMN IF NOT EXISTS blocked_by   VARCHAR(50),
    ADD COLUMN IF NOT EXISTS block_reason VARCHAR(255);
