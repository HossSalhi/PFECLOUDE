ALTER TABLE app_user
  ADD COLUMN IF NOT EXISTS activation_token  VARCHAR(64),
  ADD COLUMN IF NOT EXISTS reset_token       VARCHAR(64),
  ADD COLUMN IF NOT EXISTS reset_token_expiry TIMESTAMP;
