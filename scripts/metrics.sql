\pset border 2
\echo '==================== 1. INCIDENTS BY STATUS (AI) ===================='
SELECT status, count(*) AS n,
       round(100.0*count(*)/sum(count(*)) OVER (),1) AS pct
FROM incident WHERE source='AI' GROUP BY status ORDER BY n DESC;

\echo '==================== 2. INCIDENT TYPE DISTRIBUTION (top 12) ===================='
SELECT incident_type, count(*) AS n,
       round(100.0*count(*)/sum(count(*)) OVER (),1) AS pct
FROM incident WHERE source='AI' GROUP BY incident_type ORDER BY n DESC LIMIT 12;

\echo '==================== 3. SEVERITY DISTRIBUTION (AI) ===================='
SELECT severity, count(*) AS n,
       round(100.0*count(*)/sum(count(*)) OVER (),1) AS pct
FROM incident WHERE source='AI' GROUP BY severity ORDER BY n DESC;

\echo '==================== 4. MTTR (time NEW -> first RESOLVED, from history) ===================='
WITH t AS (
  SELECT i.id,
    (SELECT min(h.created_at) FROM incident_history h WHERE h.incident_id=i.id AND h.status='NEW') AS opened,
    (SELECT min(h.created_at) FROM incident_history h WHERE h.incident_id=i.id AND h.status IN ('RESOLVED','CLOSED')) AS resolved
  FROM incident i WHERE i.source='AI'
)
SELECT count(*) FILTER (WHERE resolved IS NOT NULL) AS resolved_incidents,
       round(avg(EXTRACT(EPOCH FROM (resolved-opened)))::numeric,0) AS mttr_avg_sec,
       round((percentile_cont(0.5) WITHIN GROUP (ORDER BY EXTRACT(EPOCH FROM (resolved-opened))))::numeric,0) AS mttr_median_sec,
       round((percentile_cont(0.9) WITHIN GROUP (ORDER BY EXTRACT(EPOCH FROM (resolved-opened))))::numeric,0) AS mttr_p90_sec
FROM t WHERE resolved IS NOT NULL AND opened IS NOT NULL AND resolved >= opened;

\echo '==================== 5. AUTO-RESOLUTION vs HUMAN (of resolved AI incidents) ===================='
SELECT
  count(*) FILTER (WHERE status IN ('RESOLVED','CLOSED')) AS total_resolved,
  count(*) FILTER (WHERE EXISTS (SELECT 1 FROM incident_history h WHERE h.incident_id=i.id
        AND (h.comment ILIKE 'Auto-resolved%' OR h.comment ILIKE '%condition has cleared%'
             OR h.comment ILIKE 'Post-remediation verification succeeded%'))) AS auto_resolved,
  count(*) FILTER (WHERE EXISTS (SELECT 1 FROM incident_history h WHERE h.incident_id=i.id
        AND (h.comment ILIKE '%Human task%' OR h.comment ILIKE '%approved%' OR h.comment ILIKE '%rejected%'
             OR h.actor_type='HUMAN' OR h.actor_type='OPERATOR'))) AS human_involved
FROM incident i WHERE source='AI';

\echo '==================== 6. REMEDIATION ACTIONS: auto vs manual + success ===================='
SELECT automation_mode,
       count(*) AS actions,
       count(*) FILTER (WHERE status='COMPLETED') AS completed,
       count(*) FILTER (WHERE status='FAILED') AS failed,
       round(100.0*count(*) FILTER (WHERE status='COMPLETED')/NULLIF(count(*),0),1) AS success_pct
FROM incident_action GROUP BY automation_mode ORDER BY actions DESC;

\echo '==================== 7. ACTION TYPE BREAKDOWN ===================='
SELECT type, count(*) AS n, count(*) FILTER (WHERE status='COMPLETED') AS completed
FROM incident_action GROUP BY type ORDER BY n DESC LIMIT 12;

\echo '==================== 8. FALSE-POSITIVE PROXY (self-cleared fast, no remediation) ===================='
-- Incidents that resolved < 3 min after creation with NO remediation action executed
-- => the signal was transient / likely a false positive (upper-bound estimate).
WITH t AS (
  SELECT i.id, i.incident_type,
    (SELECT min(h.created_at) FROM incident_history h WHERE h.incident_id=i.id AND h.status='NEW') AS opened,
    (SELECT min(h.created_at) FROM incident_history h WHERE h.incident_id=i.id AND h.status IN ('RESOLVED','CLOSED')) AS resolved,
    EXISTS (SELECT 1 FROM incident_action a WHERE a.incident_id=i.id AND a.status='COMPLETED') AS had_action
  FROM incident i WHERE i.source='AI'
)
SELECT
  count(*) AS total_ai,
  count(*) FILTER (WHERE resolved IS NOT NULL AND NOT had_action
        AND EXTRACT(EPOCH FROM (resolved-opened)) < 180) AS fast_selfclear_noaction,
  round(100.0*count(*) FILTER (WHERE resolved IS NOT NULL AND NOT had_action
        AND EXTRACT(EPOCH FROM (resolved-opened)) < 180)/NULLIF(count(*),0),1) AS fp_proxy_pct
FROM t;
