ALTER TABLE app_user
    ADD COLUMN last_2fa_verified_at  TIMESTAMP,
    ADD COLUMN force_2fa_on_next_login BOOLEAN NOT NULL DEFAULT FALSE;
