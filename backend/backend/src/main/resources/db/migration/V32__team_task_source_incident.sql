-- V32 : origine d'une tâche d'équipe créée automatiquement par le routage incident → équipe.
-- Additive : deux colonnes nullables et un index unique ; les tâches existantes gardent NULL (tâches manuelles).
-- source_incident_id est une simple référence (pas de clé étrangère : un incident peut être purgé sans toucher à la tâche).
-- Unicité (incident, étape) : un incident ne produit jamais deux fois la même tâche automatique (NULL distincts :
-- les tâches manuelles ne sont pas concernées).

ALTER TABLE team_task ADD COLUMN source_incident_id BIGINT;
ALTER TABLE team_task ADD COLUMN source_event VARCHAR(16);

ALTER TABLE team_task ADD CONSTRAINT ck_team_task_source
    CHECK ((source_incident_id IS NULL AND source_event IS NULL)
           OR (source_incident_id IS NOT NULL AND source_event IS NOT NULL AND source_event IN ('DECISION', 'FOLLOW_UP', 'RESUME')));

CREATE UNIQUE INDEX uq_team_task_source ON team_task(source_incident_id, source_event);
