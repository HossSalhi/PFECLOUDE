-- V33 : historique TRAÇABLE des analyses IA des logs (Loki) et des métriques (Prometheus).
-- NON appliquée à ce jour sur la base HELIOS (dernière migration appliquée : V32) : elle peut encore être ajustée.
-- Table en AJOUT SEUL : aucune API de modification ni de suppression n'est exposée, ET des déclencheurs PostgreSQL refusent
-- UPDATE, DELETE et TRUNCATE (voir plus bas, et infra/sql/telemetry_analysis_run_harden.sql pour retirer la propriété à
-- l'application : le propriétaire d'une table peut désactiver ses propres déclencheurs).
-- Une ligne = une analyse demandée par un utilisateur : qui, quand, quelle période et quel périmètre, le verdict, la qualité des
-- données, les constats chiffrés calculés par des règles DÉTERMINISTES (findings_json), la qualité des sources (quality_json), la
-- TRACE complète (trace_json : périodes analysée et de référence, pas des séries, seuils, requêtes PromQL/LogQL exactes avec leur
-- état et le motif des échecs, couverture par entité) et le texte rédigé par l'IA (facultatif). Aucune ligne de log brute n'est stockée.
-- requested_by_user_id et team_id n'ont volontairement PAS de clé étrangère : la trace doit survivre à la suppression
-- d'un utilisateur ou d'une équipe (leurs noms sont conservés tels quels).

CREATE TABLE telemetry_analysis_run (
    id                    BIGSERIAL    PRIMARY KEY,
    requested_at          TIMESTAMP    NOT NULL DEFAULT NOW(),
    requested_by_user_id  BIGINT,
    requested_by_username VARCHAR(50),
    team_id               BIGINT,
    team_name             VARCHAR(100),
    scope                 VARCHAR(16)  NOT NULL,
    window_minutes        INTEGER      NOT NULL,
    window_start          TIMESTAMP    NOT NULL,
    window_end            TIMESTAMP    NOT NULL,
    verdict               VARCHAR(20)  NOT NULL,
    data_quality          VARCHAR(12)  NOT NULL,
    anomaly_count         INTEGER      NOT NULL,
    high_count            INTEGER      NOT NULL,
    findings_json         TEXT         NOT NULL,
    quality_json          TEXT         NOT NULL,
    trace_json            TEXT         NOT NULL,
    narrative_status      VARCHAR(16)  NOT NULL,
    narrative_text        TEXT,
    narrative_message     VARCHAR(300),
    model                 VARCHAR(80),
    duration_ms           BIGINT       NOT NULL,
    CONSTRAINT ck_telemetry_run_verdict CHECK (verdict IN ('NO_ANOMALY', 'ANOMALIES', 'PARTIAL_COVERAGE', 'INSUFFICIENT_DATA')),
    CONSTRAINT ck_telemetry_run_quality CHECK (data_quality IN ('COMPLETE', 'PARTIAL', 'INSUFFICIENT')),
    CONSTRAINT ck_telemetry_run_scope   CHECK (scope IN ('ALL', 'VM', 'CLUSTER', 'APPLICATION', 'SERVICE'))
);

CREATE INDEX idx_telemetry_run_requested_at ON telemetry_analysis_run(requested_at);
CREATE INDEX idx_telemetry_run_team         ON telemetry_analysis_run(team_id, requested_at);
CREATE INDEX idx_telemetry_run_user         ON telemetry_analysis_run(requested_by_user_id, requested_at);

-- Immutabilité : l'historique est une trace, on ne la réécrit pas. ENABLE ALWAYS : les déclencheurs restent actifs même si une
-- session passe en session_replication_role = replica (réservé aux superutilisateurs).
CREATE FUNCTION telemetry_analysis_run_append_only() RETURNS trigger
    LANGUAGE plpgsql AS $fn$
BEGIN
    RAISE EXCEPTION 'telemetry_analysis_run est en ajout seul : % interdit', TG_OP USING ERRCODE = 'restrict_violation';
END;
$fn$;

CREATE TRIGGER trg_telemetry_run_no_update   BEFORE UPDATE   ON telemetry_analysis_run FOR EACH ROW       EXECUTE FUNCTION telemetry_analysis_run_append_only();
CREATE TRIGGER trg_telemetry_run_no_delete   BEFORE DELETE   ON telemetry_analysis_run FOR EACH ROW       EXECUTE FUNCTION telemetry_analysis_run_append_only();
CREATE TRIGGER trg_telemetry_run_no_truncate BEFORE TRUNCATE ON telemetry_analysis_run FOR EACH STATEMENT EXECUTE FUNCTION telemetry_analysis_run_append_only();

ALTER TABLE telemetry_analysis_run ENABLE ALWAYS TRIGGER trg_telemetry_run_no_update;
ALTER TABLE telemetry_analysis_run ENABLE ALWAYS TRIGGER trg_telemetry_run_no_delete;
ALTER TABLE telemetry_analysis_run ENABLE ALWAYS TRIGGER trg_telemetry_run_no_truncate;

-- Limiteur d'analyses simultanées, PARTAGÉ entre les réplicas du backend (une analyse par utilisateur à la fois, plafond global) :
-- une ligne par analyse en cours, supprimée à la fin ; une ligne plus vieille que le délai de péremption est considérée comme
-- abandonnée (réplica arrêté en cours d'analyse). Aucune donnée métier : table jetable, volontairement non protégée.
CREATE TABLE telemetry_analysis_inflight (
    user_id    BIGINT       PRIMARY KEY,
    token      VARCHAR(36)  NOT NULL,
    started_at TIMESTAMP    NOT NULL DEFAULT NOW(),
    instance   VARCHAR(80)
);
