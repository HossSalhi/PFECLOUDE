-- Leader election lease for HA: when the backend runs multiple replicas, only the
-- lease holder runs the automatic schedulers (AIOps polling, deployment watcher,
-- Camunda workflow recovery) so incidents are not created/processed in duplicate.
CREATE TABLE IF NOT EXISTS leader_lease (
    lease_name  VARCHAR(64)  PRIMARY KEY,
    holder      VARCHAR(160) NOT NULL,
    renewed_at  TIMESTAMP    NOT NULL DEFAULT now()
);
