-- Namespace-scoping for multi-tenancy: each user can be restricted to a set of K8s namespaces.
-- Empty set means unrestricted (ADMIN / SUPER_ADMIN).
CREATE TABLE IF NOT EXISTS user_allowed_namespace (
    user_id   BIGINT       NOT NULL REFERENCES app_user (id) ON DELETE CASCADE,
    namespace VARCHAR(63)  NOT NULL,
    PRIMARY KEY (user_id, namespace)
);

CREATE INDEX IF NOT EXISTS idx_user_allowed_ns_user ON user_allowed_namespace (user_id);
