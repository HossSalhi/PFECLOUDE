-- V23 — Autorise le type d'action HOST_SERVICE_MEMORY_LIMIT (remediation RAM deterministe, modele CPU).
-- Additive et idempotente : recree la contrainte CHECK de incident_action.type avec la liste complete
-- existante (V20) + HOST_SERVICE_MEMORY_LIMIT. Aucune donnee supprimee, aucune colonne modifiee.
-- Compatible rollback : une image anterieure (sans ce type) ne produit jamais cette valeur ; la contrainte
-- elargie reste satisfaite par les lignes existantes (ignoreFutureMigrations cote Flyway).
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
        'HOST_SERVICE_MEMORY_LIMIT',
        'ESCALATION'
    )
);
