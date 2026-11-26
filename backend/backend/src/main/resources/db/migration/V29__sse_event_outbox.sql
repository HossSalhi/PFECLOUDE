-- V29 : outbox des évènements SSE, PARTAGÉE entre les réplicas du Backend (additive : l'ancien Backend l'ignore).
-- Chaque évènement est écrit une fois ; chaque pod le lit (curseur croissant sur id) et le remet à SES abonnés.
-- Les évènements récents sont rejouables après une déconnexion (Last-Event-ID) ; purge au-delà d'une heure.
CREATE TABLE sse_event (
    id         BIGSERIAL    PRIMARY KEY,
    event_name VARCHAR(60)  NOT NULL,
    namespace  VARCHAR(253),
    payload    TEXT         NOT NULL,
    created_at TIMESTAMPTZ  NOT NULL DEFAULT now()
);
CREATE INDEX idx_sse_event_created_at ON sse_event (created_at);