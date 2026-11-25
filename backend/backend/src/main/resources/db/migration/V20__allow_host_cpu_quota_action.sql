-- CPU-01B: keep incident_action.type aligned with the complete Java ActionType enum.
-- Additive migration; the historical V16 migration is intentionally unchanged.
ALTER TABLE incident_action DROP CONSTRAINT IF EXISTS incident_action_type_check;

ALTER TABLE incident_action ADD CONSTRAINT incident_action_type_check CHECK (
    type IN (
        'NOTIFICATION',
        'REMEDIATION',
        'INVESTIGATION',
        'RESTART_POD',
        'SCALE_DEPLOYMENT',
        'SCALE_DOWN',
        'ROLLBACK_DEPLOYMENT',
        'DELETE_CRASHLOOPING_PODS',
        'FORCE_DELETE_POD',
        'REMOVE_NETWORK_POLICY',
        'CORDON_NODE',
        'DRAIN_NODE',
        'FORCE_RECREATE_DEPLOYMENT',
        'RESTART_VM',
        'HOST_SYNC_TIME',
        'HOST_IMAGE_GC',
        'HOST_RECLAIM_DISK',
        'HOST_SERVICE_CPU_QUOTA',
        'ESCALATION'
    )
);