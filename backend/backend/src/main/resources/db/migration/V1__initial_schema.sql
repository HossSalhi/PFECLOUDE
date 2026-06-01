-- ============================================================
-- V1 : schéma initial complet
-- Toutes les tables de base. Les migrations V2/V3/V4 utilisent
-- ADD COLUMN IF NOT EXISTS et DROP CONSTRAINT IF EXISTS, donc
-- elles restent idempotentes même si les colonnes/contraintes
-- sont déjà présentes ici.
-- ============================================================

CREATE TABLE IF NOT EXISTS incident (
    id          BIGSERIAL PRIMARY KEY,
    title       VARCHAR(255)  NOT NULL,
    description VARCHAR(1000) NOT NULL,
    severity    VARCHAR(50),
    status      VARCHAR(50),
    source      VARCHAR(50),
    actor_name  VARCHAR(255),
    actor_type  VARCHAR(255),
    created_at  TIMESTAMP NOT NULL,
    updated_at  TIMESTAMP NOT NULL
);

CREATE TABLE IF NOT EXISTS alert (
    id          BIGSERIAL PRIMARY KEY,
    message     VARCHAR(255) NOT NULL,
    source      VARCHAR(50),
    incident_id BIGINT NOT NULL,
    actor_name  VARCHAR(255),
    actor_type  VARCHAR(255),
    created_at  TIMESTAMP NOT NULL,
    updated_at  TIMESTAMP NOT NULL,
    CONSTRAINT fk_alert_incident
        FOREIGN KEY (incident_id) REFERENCES incident (id)
);

CREATE TABLE IF NOT EXISTS incident_history (
    id          BIGSERIAL PRIMARY KEY,
    incident_id BIGINT,
    status      VARCHAR(50),
    comment     VARCHAR(1000),
    actor_name  VARCHAR(255),
    actor_type  VARCHAR(255),
    created_at  TIMESTAMP NOT NULL,
    updated_at  TIMESTAMP NOT NULL,
    CONSTRAINT fk_history_incident
        FOREIGN KEY (incident_id) REFERENCES incident (id)
);

-- Inclut déjà les colonnes ajoutées par V2 (ADD COLUMN IF NOT EXISTS sera no-op)
CREATE TABLE IF NOT EXISTS incident_action (
    id               BIGSERIAL PRIMARY KEY,
    incident_id      BIGINT        NOT NULL,
    type             VARCHAR(50),
    status           VARCHAR(50),
    description      VARCHAR(1000),
    target_namespace VARCHAR(255),
    target_kind      VARCHAR(64),
    target_name      VARCHAR(255),
    automation_mode  VARCHAR(64),
    attempt_count    INTEGER       NOT NULL DEFAULT 0,
    executor_result  VARCHAR(1000),
    executed_by      VARCHAR(255),
    executed_at      TIMESTAMP,
    failed_reason    VARCHAR(1000),
    actor_name       VARCHAR(255),
    actor_type       VARCHAR(255),
    created_at       TIMESTAMP NOT NULL,
    updated_at       TIMESTAMP NOT NULL,
    CONSTRAINT fk_action_incident
        FOREIGN KEY (incident_id) REFERENCES incident (id)
);

-- Inclut déjà les colonnes ajoutées par V2 (ADD COLUMN IF NOT EXISTS sera no-op)
CREATE TABLE IF NOT EXISTS workflow_instance (
    id                              BIGSERIAL PRIMARY KEY,
    incident_id                     BIGINT    NOT NULL,
    status                          VARCHAR(50),
    workflow_name                   VARCHAR(255),
    camunda_process_definition_id   VARCHAR(255),
    camunda_process_instance_key    VARCHAR(255),
    correlation_key                 VARCHAR(255),
    orchestration_state             VARCHAR(64),
    target_namespace                VARCHAR(255),
    target_app                      VARCHAR(255),
    started_at                      TIMESTAMP,
    ended_at                        TIMESTAMP,
    current_step                    INTEGER,
    total_steps                     INTEGER,
    actor_name                      VARCHAR(255),
    actor_type                      VARCHAR(255),
    created_at                      TIMESTAMP NOT NULL,
    updated_at                      TIMESTAMP NOT NULL,
    CONSTRAINT fk_workflow_incident
        FOREIGN KEY (incident_id) REFERENCES incident (id)
);

CREATE TABLE IF NOT EXISTS workflow_logs (
    id          BIGSERIAL PRIMARY KEY,
    message     VARCHAR(255),
    created_at  TIMESTAMP,
    workflow_id BIGINT,
    CONSTRAINT fk_log_workflow
        FOREIGN KEY (workflow_id) REFERENCES workflow_instance (id)
);

CREATE TABLE IF NOT EXISTS incident_decision (
    id              BIGSERIAL PRIMARY KEY,
    incident_id     BIGINT NOT NULL,
    decision_type   VARCHAR(50),
    decision_source VARCHAR(50),
    comment         VARCHAR(1000),
    actor_name      VARCHAR(255),
    actor_type      VARCHAR(255),
    created_at      TIMESTAMP NOT NULL,
    updated_at      TIMESTAMP NOT NULL,
    CONSTRAINT fk_decision_incident
        FOREIGN KEY (incident_id) REFERENCES incident (id)
);

CREATE TABLE IF NOT EXISTS human_task (
    id                   BIGSERIAL PRIMARY KEY,
    workflow_instance_id BIGINT NOT NULL,
    task_name            VARCHAR(255),
    assigned_to          VARCHAR(255),
    decision             VARCHAR(50) DEFAULT 'PENDING',
    completed_at         TIMESTAMP,
    actor_name           VARCHAR(255),
    actor_type           VARCHAR(255),
    created_at           TIMESTAMP NOT NULL,
    updated_at           TIMESTAMP NOT NULL,
    CONSTRAINT fk_task_workflow
        FOREIGN KEY (workflow_instance_id) REFERENCES workflow_instance (id)
);
