-- V27 : rapports et plannings de rapports périodiques (module « Gestion des rapports »).
-- Les statistiques sont calculées à la génération à partir des données réelles et conservées dans result_json
-- (consultation ultérieure des rapports déjà générés).
-- uq_report_scheduled_period : un même planning ne peut produire qu'UN rapport par période, même si deux
-- réplicas du Backend s'exécutent en même temps (les rapports manuels, schedule_id NULL, ne sont pas concernés).

CREATE TABLE report_schedule (
    id                   BIGSERIAL     PRIMARY KEY,
    report_type          VARCHAR(30)   NOT NULL,
    frequency            VARCHAR(10)   NOT NULL,
    enabled              BOOLEAN       NOT NULL DEFAULT TRUE,
    created_by_id        BIGINT        REFERENCES app_user(id) ON DELETE SET NULL,
    created_by_username  VARCHAR(50),
    created_at           TIMESTAMP     NOT NULL DEFAULT NOW(),
    updated_at           TIMESTAMP     NOT NULL DEFAULT NOW(),
    CONSTRAINT ck_report_schedule_freq CHECK (frequency IN ('DAILY', 'WEEKLY', 'MONTHLY')),
    CONSTRAINT uq_report_schedule UNIQUE (report_type, frequency)
);

CREATE TABLE report (
    id                     BIGSERIAL     PRIMARY KEY,
    report_type            VARCHAR(30)   NOT NULL,
    title                  VARCHAR(200)  NOT NULL,
    period_start           TIMESTAMP     NOT NULL,
    period_end             TIMESTAMP     NOT NULL,
    compare_previous       BOOLEAN       NOT NULL DEFAULT FALSE,
    trigger_type           VARCHAR(10)   NOT NULL DEFAULT 'MANUAL',
    schedule_id            BIGINT        REFERENCES report_schedule(id) ON DELETE SET NULL,
    status                 VARCHAR(12)   NOT NULL DEFAULT 'COMPLETED',
    error_message          VARCHAR(500),
    summary                VARCHAR(1000),
    result_json            TEXT,
    generated_by_id        BIGINT        REFERENCES app_user(id) ON DELETE SET NULL,
    generated_by_username  VARCHAR(50),
    generated_at           TIMESTAMP     NOT NULL DEFAULT NOW(),
    CONSTRAINT ck_report_status  CHECK (status IN ('COMPLETED', 'FAILED')),
    CONSTRAINT ck_report_trigger CHECK (trigger_type IN ('MANUAL', 'SCHEDULED')),
    CONSTRAINT ck_report_period  CHECK (period_end > period_start)
);

CREATE UNIQUE INDEX uq_report_scheduled_period ON report(schedule_id, period_start, period_end);
CREATE INDEX idx_report_generated_at ON report(generated_at);
CREATE INDEX idx_report_type         ON report(report_type, generated_at);
