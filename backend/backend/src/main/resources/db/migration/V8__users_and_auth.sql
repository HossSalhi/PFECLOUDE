-- V8 : Gestion des utilisateurs, rôles, 2FA et sessions JWT

CREATE TABLE app_user (
    id                   BIGSERIAL PRIMARY KEY,
    username             VARCHAR(50)  NOT NULL UNIQUE,
    email                VARCHAR(255) NOT NULL UNIQUE,
    password_hash        VARCHAR(255) NOT NULL,
    role                 VARCHAR(20)  NOT NULL DEFAULT 'USER',
    enabled              BOOLEAN      NOT NULL DEFAULT TRUE,
    -- 2FA
    two_factor_enabled   BOOLEAN      NOT NULL DEFAULT FALSE,
    two_factor_secret    VARCHAR(64),
    -- Login tracking
    last_login_at        TIMESTAMP,
    last_login_ip        VARCHAR(45),
    failed_attempts      INT          NOT NULL DEFAULT 0,
    locked_until         TIMESTAMP,
    -- Audit
    created_at           TIMESTAMP    NOT NULL DEFAULT NOW(),
    updated_at           TIMESTAMP    NOT NULL DEFAULT NOW(),
    created_by           VARCHAR(50),
    updated_by           VARCHAR(50)
);

-- Index pour les lookups fréquents
CREATE INDEX idx_app_user_username ON app_user(username);
CREATE INDEX idx_app_user_email    ON app_user(email);
CREATE INDEX idx_app_user_role     ON app_user(role);

-- Table de révocation de tokens JWT (logout / blacklist)
CREATE TABLE jwt_blacklist (
    id          BIGSERIAL PRIMARY KEY,
    jti         VARCHAR(100) NOT NULL UNIQUE,
    expires_at  TIMESTAMP    NOT NULL,
    revoked_at  TIMESTAMP    NOT NULL DEFAULT NOW(),
    revoked_by  VARCHAR(50)
);

CREATE INDEX idx_jwt_blacklist_jti        ON jwt_blacklist(jti);
CREATE INDEX idx_jwt_blacklist_expires_at ON jwt_blacklist(expires_at);

-- Insérer les utilisateurs par défaut (mots de passe à changer en production)
-- Passwords hashés avec BCrypt (cost 12) :
--   superadmin-change-me → $2a$12$hash...
--   admin-change-me      → $2a$12$hash...
--   operator-change-me   → $2a$12$hash...
-- Note : les vrais hashes seront générés au démarrage via InitialDataLoader
INSERT INTO app_user (username, email, password_hash, role, enabled, created_by)
VALUES
    ('superadmin', 'superadmin@aiops.local', '{noop}superadmin-change-me', 'SUPER_ADMIN', true, 'system'),
    ('admin',      'admin@aiops.local',      '{noop}admin-change-me',      'ADMIN',       true, 'system'),
    ('operator',   'operator@aiops.local',   '{noop}operator-change-me',   'USER',        true, 'system');
