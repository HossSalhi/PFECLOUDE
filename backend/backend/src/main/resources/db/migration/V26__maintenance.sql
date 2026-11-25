-- V26 : planification et suivi des opérations de maintenance (module « Gestion de la maintenance »).
-- Ce module est DÉCLARATIF : il ne pilote ni le drapeau de maintenance de l'hôte (watchdog), ni la supervision,
-- ni la remédiation. Une maintenance « en cours » est une information de gestion, rien de plus.

CREATE TABLE maintenance (
    id                   BIGSERIAL     PRIMARY KEY,
    title                VARCHAR(200)  NOT NULL,
    description          TEXT,
    reason               VARCHAR(500),
    resource_type        VARCHAR(20)   NOT NULL,
    resource_name        VARCHAR(200)  NOT NULL,
    start_at             TIMESTAMP     NOT NULL,
    end_at               TIMESTAMP     NOT NULL,
    status               VARCHAR(16)   NOT NULL DEFAULT 'PLANNED',
    created_by_id        BIGINT        REFERENCES app_user(id) ON DELETE SET NULL,
    created_by_username  VARCHAR(50),
    responsible_id       BIGINT        REFERENCES app_user(id) ON DELETE SET NULL,
    actual_start_at      TIMESTAMP,
    actual_end_at        TIMESTAMP,
    cancel_reason        VARCHAR(500),
    created_at           TIMESTAMP     NOT NULL DEFAULT NOW(),
    updated_at           TIMESTAMP     NOT NULL DEFAULT NOW(),
    CONSTRAINT ck_maintenance_period        CHECK (end_at > start_at),
    CONSTRAINT ck_maintenance_status        CHECK (status IN ('PLANNED', 'IN_PROGRESS', 'COMPLETED', 'CANCELLED')),
    CONSTRAINT ck_maintenance_resource_type CHECK (resource_type IN ('APPLICATION', 'NODE', 'VM', 'SERVICE'))
);

CREATE INDEX idx_maintenance_resource ON maintenance(resource_type, resource_name);
CREATE INDEX idx_maintenance_period   ON maintenance(start_at, end_at);
CREATE INDEX idx_maintenance_status   ON maintenance(status);
CREATE INDEX idx_maintenance_resp     ON maintenance(responsible_id);

-- Historique des changements d'une maintenance (création, modification, changement de statut, annulation).
CREATE TABLE maintenance_history (
    id                   BIGSERIAL     PRIMARY KEY,
    maintenance_id       BIGINT        NOT NULL REFERENCES maintenance(id) ON DELETE CASCADE,
    changed_at           TIMESTAMP     NOT NULL DEFAULT NOW(),
    changed_by_id        BIGINT,
    changed_by_username  VARCHAR(50),
    change_type          VARCHAR(24)   NOT NULL,
    details              TEXT
);

CREATE INDEX idx_maintenance_history_m ON maintenance_history(maintenance_id, changed_at);
