-- LOT HELIOS-INCIDENT-IDENTITY-CARD-01
-- Carte d'identite d'incident : persistance des preuves terminal AVANT/APRES et de la verification finale.
-- Tables autonomes reliees a incident(id) par incident_id (pas de FK pour decoupler ; index de lecture).
-- NB : ce script n'est PAS execute sur la base de production dans ce lot (aucun deploiement). Flyway
-- l'appliquera au demarrage du backend (dev/test) comme migration V19 (suivant V18).

CREATE TABLE IF NOT EXISTS incident_terminal_evidence (
    id            BIGSERIAL PRIMARY KEY,
    incident_id   BIGINT       NOT NULL,
    vm            VARCHAR(255),
    phase         VARCHAR(16)  NOT NULL,
    captured_at   TIMESTAMP,
    command       VARCHAR(1000),
    exit_code     INTEGER,
    stdout        TEXT,
    stderr        TEXT,
    measures_json TEXT,
    available     BOOLEAN      NOT NULL DEFAULT FALSE,
    source        VARCHAR(32),
    created_at    TIMESTAMP    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ite_incident ON incident_terminal_evidence (incident_id);
CREATE INDEX IF NOT EXISTS idx_ite_incident_phase ON incident_terminal_evidence (incident_id, phase);

CREATE TABLE IF NOT EXISTS incident_verification (
    id            BIGSERIAL PRIMARY KEY,
    incident_id   BIGINT       NOT NULL,
    criteria      VARCHAR(1000),
    result        VARCHAR(32),
    measures_json TEXT,
    reason        TEXT,
    verified_at   TIMESTAMP,
    available     BOOLEAN      NOT NULL DEFAULT FALSE,
    created_at    TIMESTAMP    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_iv_incident ON incident_verification (incident_id);
