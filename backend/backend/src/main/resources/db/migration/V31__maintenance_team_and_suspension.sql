-- V31 : maintenance liée à une équipe + statut SUSPENDED (pause / reprise).
-- Additive pour les données : quatre colonnes nullables et un index ; les maintenances existantes ne sont ni
-- supprimées ni modifiées (team_id et colonnes de suspension restent NULL). Seule la contrainte de statut est
-- remplacée, pour accepter SUSPENDED en plus des quatre valeurs existantes (toutes les lignes existantes la satisfont).
-- Aucune référence directe à l'incident : suspend_incident_id est une simple référence (l'incident peut être purgé).

ALTER TABLE maintenance ADD COLUMN team_id BIGINT REFERENCES team(id) ON DELETE SET NULL;
ALTER TABLE maintenance ADD COLUMN suspended_at TIMESTAMP;
ALTER TABLE maintenance ADD COLUMN suspend_reason VARCHAR(500);
ALTER TABLE maintenance ADD COLUMN suspend_incident_id BIGINT;

ALTER TABLE maintenance DROP CONSTRAINT ck_maintenance_status;
ALTER TABLE maintenance ADD CONSTRAINT ck_maintenance_status
    CHECK (status IN ('PLANNED', 'IN_PROGRESS', 'SUSPENDED', 'COMPLETED', 'CANCELLED'));

-- Invariant : les informations de suspension n'existent que pour une maintenance suspendue.
ALTER TABLE maintenance ADD CONSTRAINT ck_maintenance_suspension
    CHECK (status = 'SUSPENDED'
           OR (suspended_at IS NULL AND suspend_reason IS NULL AND suspend_incident_id IS NULL));

CREATE INDEX idx_maintenance_team ON maintenance(team_id);
