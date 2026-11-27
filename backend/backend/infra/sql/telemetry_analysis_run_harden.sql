-- Durcissement de l'historique des analyses IA (table telemetry_analysis_run, migration V33).
--
-- POURQUOI : V33 installe des déclencheurs qui refusent UPDATE, DELETE et TRUNCATE. Mais le PROPRIÉTAIRE d'une table peut
-- désactiver ou supprimer ses propres déclencheurs, et le rôle applicatif (appuser) est le propriétaire des tables créées par
-- Flyway. Ce script retire donc la propriété à l'application : elle ne garde que SELECT et INSERT, et ne peut plus ni
-- désactiver les déclencheurs, ni les supprimer, ni se redonner des droits.
--
-- QUAND : UNE FOIS, après le déploiement du backend qui applique V33. Exécuté par un SUPERUTILISATEUR (postgres), jamais par
-- l'application. Idempotent. Il ne modifie aucune ligne de données.
--
-- USAGE : psql -U postgres -d pfe_db -v app_role=appuser -f telemetry_analysis_run_harden.sql
--         (variable facultative : -v owner_role=postgres ; par défaut postgres)
--
-- DROP TABLE : transférer la propriété de la table NE SUFFIT PAS. Le propriétaire du schéma « public » peut supprimer toute table de ce
-- schéma, et le rôle applicatif possède la base, donc ce schéma (constaté sur la base HELIOS : propriétaire de la base = appuser).
-- Un déclencheur d'ÉVÉNEMENT (réservé aux superutilisateurs : l'application ne peut ni le désactiver ni le supprimer) annule donc
-- toute suppression de la table, y compris DROP SCHEMA public CASCADE.
--
-- LIMITES ASSUMÉES : un superutilisateur peut toujours tout faire (aucune protection contre postgres) ; le propriétaire de la
-- base peut supprimer la base entière depuis une autre base (DROP DATABASE) : seule une sauvegarde hors cluster y répond.
\set ON_ERROR_STOP on
\if :{?app_role}
\else
\set app_role appuser
\endif
\if :{?owner_role}
\else
\set owner_role postgres
\endif

BEGIN;
ALTER TABLE    telemetry_analysis_run                 OWNER TO :"owner_role";
ALTER SEQUENCE telemetry_analysis_run_id_seq          OWNER TO :"owner_role";
ALTER FUNCTION telemetry_analysis_run_append_only()   OWNER TO :"owner_role";

REVOKE ALL ON TABLE    telemetry_analysis_run        FROM PUBLIC;
REVOKE ALL ON TABLE    telemetry_analysis_run        FROM :"app_role";
GRANT  SELECT, INSERT ON TABLE telemetry_analysis_run TO :"app_role";

REVOKE ALL ON SEQUENCE telemetry_analysis_run_id_seq FROM PUBLIC;
REVOKE ALL ON SEQUENCE telemetry_analysis_run_id_seq FROM :"app_role";
GRANT  USAGE ON SEQUENCE telemetry_analysis_run_id_seq TO :"app_role";

-- La fonction vit dans un schéma dédié que l'application ne possède pas : dans « public », DROP SCHEMA public CASCADE l'emporterait
-- avec le déclencheur d'événement, et la protection disparaîtrait au moment où elle sert.
CREATE SCHEMA IF NOT EXISTS helios_guard AUTHORIZATION :"owner_role";
CREATE OR REPLACE FUNCTION helios_guard.telemetry_analysis_run_no_drop() RETURNS event_trigger
    LANGUAGE plpgsql AS $fn$
DECLARE
    r record;
BEGIN
    FOR r IN SELECT object_type, object_name FROM pg_event_trigger_dropped_objects() LOOP
        IF r.object_type = 'table' AND r.object_name = 'telemetry_analysis_run' THEN
            RAISE EXCEPTION 'telemetry_analysis_run est en ajout seul : DROP interdit' USING ERRCODE = 'restrict_violation';
        END IF;
    END LOOP;
END;
$fn$;
ALTER FUNCTION helios_guard.telemetry_analysis_run_no_drop() OWNER TO :"owner_role";
DROP EVENT TRIGGER IF EXISTS trg_telemetry_run_no_drop;
CREATE EVENT TRIGGER trg_telemetry_run_no_drop ON sql_drop EXECUTE FUNCTION helios_guard.telemetry_analysis_run_no_drop();
ALTER EVENT TRIGGER trg_telemetry_run_no_drop OWNER TO :"owner_role";
COMMIT;
