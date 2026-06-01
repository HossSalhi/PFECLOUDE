-- V13 : Add first_name and last_name to app_user

ALTER TABLE app_user
    ADD COLUMN first_name VARCHAR(100),
    ADD COLUMN last_name  VARCHAR(100);
