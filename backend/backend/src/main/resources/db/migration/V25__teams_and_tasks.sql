-- V25 : équipes, membres et tâches organisationnelles (module « Gestion des équipes »).
-- Les références vers app_user réutilisent le modèle utilisateur existant (aucune table utilisateur parallèle).
-- ON DELETE SET NULL / CASCADE : la suppression physique d'un utilisateur (UserService.delete) reste possible.
-- Les tâches ci-dessous sont des travaux organisationnels ; elles ne remplacent PAS les tâches d'approbation
-- d'incidents (table human_task / API /api/tasks).

CREATE TABLE team (
    id                   BIGSERIAL     PRIMARY KEY,
    name                 VARCHAR(100)  NOT NULL,
    name_key             VARCHAR(100)  NOT NULL,
    description          VARCHAR(1000),
    lead_id              BIGINT        REFERENCES app_user(id) ON DELETE SET NULL,
    active               BOOLEAN       NOT NULL DEFAULT TRUE,
    created_by_id        BIGINT        REFERENCES app_user(id) ON DELETE SET NULL,
    created_by_username  VARCHAR(50),
    created_at           TIMESTAMP     NOT NULL DEFAULT NOW(),
    updated_at           TIMESTAMP     NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_team_name_key UNIQUE (name_key)
);

CREATE INDEX idx_team_lead ON team(lead_id);

CREATE TABLE team_member (
    id                   BIGSERIAL     PRIMARY KEY,
    team_id              BIGINT        NOT NULL REFERENCES team(id) ON DELETE CASCADE,
    user_id              BIGINT        NOT NULL REFERENCES app_user(id) ON DELETE CASCADE,
    added_by_username    VARCHAR(50),
    added_at             TIMESTAMP     NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_team_member UNIQUE (team_id, user_id)
);

CREATE INDEX idx_team_member_user ON team_member(user_id);

CREATE TABLE team_task (
    id                   BIGSERIAL     PRIMARY KEY,
    title                VARCHAR(200)  NOT NULL,
    description          TEXT,
    team_id              BIGINT        REFERENCES team(id) ON DELETE SET NULL,
    created_by_id        BIGINT        REFERENCES app_user(id) ON DELETE SET NULL,
    created_by_username  VARCHAR(50),
    assignee_id          BIGINT        REFERENCES app_user(id) ON DELETE SET NULL,
    priority             VARCHAR(12)   NOT NULL DEFAULT 'MEDIUM',
    status               VARCHAR(16)   NOT NULL DEFAULT 'TODO',
    due_date             DATE,
    completed_at         TIMESTAMP,
    created_at           TIMESTAMP     NOT NULL DEFAULT NOW(),
    updated_at           TIMESTAMP     NOT NULL DEFAULT NOW(),
    CONSTRAINT ck_team_task_priority CHECK (priority IN ('LOW', 'MEDIUM', 'HIGH', 'CRITICAL')),
    CONSTRAINT ck_team_task_status   CHECK (status IN ('TODO', 'IN_PROGRESS', 'ON_HOLD', 'DONE', 'CANCELLED'))
);

CREATE INDEX idx_team_task_team     ON team_task(team_id);
CREATE INDEX idx_team_task_assignee ON team_task(assignee_id);
CREATE INDEX idx_team_task_status   ON team_task(status);
CREATE INDEX idx_team_task_due      ON team_task(due_date);
