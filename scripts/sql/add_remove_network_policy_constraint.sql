-- 0.2.68+ : nouvelle ActionType REMOVE_NETWORK_POLICY (HELIOS supprime la NetworkPolicy bloquante).
-- La contrainte CHECK de incident_action.type doit l'autoriser, sinon l'INSERT echoue
-- ("violates check constraint incident_action_type_check") et la remediation reseau ne s'enregistre pas.
-- A rejouer sur toute base existante : kubectl exec -n pfe postgres-2 -c postgres -- psql <url> -f ce_fichier
ALTER TABLE incident_action DROP CONSTRAINT IF EXISTS incident_action_type_check;
ALTER TABLE incident_action ADD CONSTRAINT incident_action_type_check CHECK (type IN (
    'NOTIFICATION','REMEDIATION','INVESTIGATION','RESTART_POD','SCALE_DEPLOYMENT','SCALE_DOWN',
    'ROLLBACK_DEPLOYMENT','DELETE_CRASHLOOPING_PODS','REMOVE_NETWORK_POLICY','CORDON_NODE','DRAIN_NODE',
    'FORCE_RECREATE_DEPLOYMENT','RESTART_VM','ESCALATION'
));
