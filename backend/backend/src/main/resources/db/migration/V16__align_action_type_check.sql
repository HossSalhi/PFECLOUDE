-- The incident_action.type CHECK constraint was out of sync with the ActionType enum
-- (only 6 of 13 values were allowed), so incidents whose remediation mapped to e.g.
-- ROLLBACK_DEPLOYMENT / DELETE_CRASHLOOPING_PODS failed to persist and the whole
-- incident-creation transaction rolled back. Align the constraint with the full enum.
ALTER TABLE incident_action DROP CONSTRAINT IF EXISTS incident_action_type_check;
ALTER TABLE incident_action ADD CONSTRAINT incident_action_type_check CHECK (
    type IN (
        'NOTIFICATION', 'REMEDIATION', 'INVESTIGATION', 'RESTART_POD',
        'SCALE_DEPLOYMENT', 'SCALE_DOWN', 'ROLLBACK_DEPLOYMENT',
        'DELETE_CRASHLOOPING_PODS', 'CORDON_NODE', 'DRAIN_NODE',
        'FORCE_RECREATE_DEPLOYMENT', 'RESTART_VM', 'ESCALATION'
    )
);
