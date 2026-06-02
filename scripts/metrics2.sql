\pset border 2
\echo '==================== 9. REMEDIATION SUCCESS — EXECUTABLE actions only ===================='
-- Exclude advisory pseudo-actions (INVESTIGATION/ESCALATION/NOTIFICATION) that never "complete".
SELECT automation_mode,
       count(*) AS executable_actions,
       count(*) FILTER (WHERE status='COMPLETED') AS completed,
       round(100.0*count(*) FILTER (WHERE status='COMPLETED')/NULLIF(count(*),0),1) AS success_pct
FROM incident_action
WHERE type IN ('RESTART_POD','SCALE_DEPLOYMENT','SCALE_DOWN','DELETE_CRASHLOOPING_PODS','FORCE_RECREATE_DEPLOYMENT','ROLLBACK_DEPLOYMENT')
GROUP BY automation_mode ORDER BY executable_actions DESC;

\echo '==================== 10. MTTR for AUTO-remediated incidents (had an AUTO completed action) ===================='
WITH t AS (
  SELECT i.id,
    (SELECT min(h.created_at) FROM incident_history h WHERE h.incident_id=i.id AND h.status='NEW') AS opened,
    (SELECT min(h.created_at) FROM incident_history h WHERE h.incident_id=i.id AND h.status IN ('RESOLVED','CLOSED')) AS resolved
  FROM incident i
  WHERE i.source='AI'
    AND EXISTS (SELECT 1 FROM incident_action a WHERE a.incident_id=i.id AND a.automation_mode='AUTO' AND a.status='COMPLETED')
)
SELECT count(*) AS auto_remediated,
       round(avg(EXTRACT(EPOCH FROM (resolved-opened)))::numeric,0) AS mttr_avg_sec,
       round((percentile_cont(0.5) WITHIN GROUP (ORDER BY EXTRACT(EPOCH FROM (resolved-opened))))::numeric,0) AS mttr_median_sec
FROM t WHERE resolved IS NOT NULL AND opened IS NOT NULL AND resolved>=opened;

\echo '==================== 11. RECENT WINDOW (last 3 days, post-fixes) ===================='
SELECT count(*) AS incidents_3d,
       count(DISTINCT app) AS apps,
       count(*) FILTER (WHERE incident_type IS NOT NULL AND incident_type<>'') AS typed
FROM incident WHERE source='AI' AND created_at > now() - interval '3 days';

\echo '==================== 12. TYPED incidents only — distribution (clean signal) ===================='
SELECT incident_type, count(*) AS n,
       round(100.0*count(*)/sum(count(*)) OVER (),1) AS pct
FROM incident WHERE source='AI' AND incident_type IS NOT NULL AND incident_type<>''
GROUP BY incident_type ORDER BY n DESC LIMIT 10;

\echo '==================== 13. EMAIL NOTIFICATIONS sent (approval path) ===================='
SELECT count(*) AS human_tasks_created FROM human_task;
