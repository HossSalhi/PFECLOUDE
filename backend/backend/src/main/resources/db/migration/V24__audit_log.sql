-- V24 : journal d'audit des activités des utilisateurs (module « Audits et activités »).
-- Table en AJOUT SEUL côté application : aucune API de modification ni de suppression n'est exposée.
-- actor_user_id n'a volontairement PAS de clé étrangère vers app_user : la trace doit survivre à la
-- suppression d'un utilisateur (actor_username / actor_role sont conservés tels quels).
-- Aucun secret ne doit être écrit dans old_values / new_values (masquage côté application).

CREATE TABLE audit_log (
    id              BIGSERIAL    PRIMARY KEY,
    occurred_at     TIMESTAMP    NOT NULL DEFAULT NOW(),
    actor_user_id   BIGINT,
    actor_username  VARCHAR(50),
    actor_role      VARCHAR(20),
    module          VARCHAR(30)  NOT NULL,
    action          VARCHAR(40)  NOT NULL,
    object_type     VARCHAR(60),
    object_id       VARCHAR(64),
    summary         VARCHAR(500),
    old_values      TEXT,
    new_values      TEXT,
    result          VARCHAR(12)  NOT NULL DEFAULT 'SUCCESS',
    client_ip       VARCHAR(45),
    request_id      VARCHAR(64)
);

CREATE INDEX idx_audit_log_occurred_at   ON audit_log(occurred_at);
CREATE INDEX idx_audit_log_actor         ON audit_log(actor_user_id, occurred_at);
CREATE INDEX idx_audit_log_module_action ON audit_log(module, action);
CREATE INDEX idx_audit_log_object        ON audit_log(object_type, object_id);
