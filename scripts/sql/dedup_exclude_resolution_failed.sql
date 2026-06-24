-- 0.2.76+ : un incident RESOLUTION_FAILED reste OUVERT (jamais auto-ferme) pour que l'ingenieur
-- intervienne. Pour qu'un RESOLUTION_FAILED ouvert ne bloque pas la creation d'un nouvel incident du
-- meme type (contrainte de dedup), on l'exclut (avec ARCHIVED) de l'index unique partiel.
-- A rejouer sur toute base existante.
DROP INDEX IF EXISTS ux_incident_open_dedup;
CREATE UNIQUE INDEX ux_incident_open_dedup
    ON public.incident (COALESCE(namespace, ''::varchar), COALESCE(app, ''::varchar), incident_type)
    WHERE ((status)::text <> ALL (ARRAY['RESOLVED','CLOSED','RESOLUTION_FAILED','ARCHIVED']));
