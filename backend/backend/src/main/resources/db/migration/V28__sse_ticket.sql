-- V28 : tickets d'accès au flux SSE, PARTAGÉS entre les réplicas du Backend (additive : l'ancien Backend l'ignore).
-- On ne stocke que le condensat SHA-256 du ticket (jamais sa valeur), le nom d'utilisateur (pour appliquer ses droits
-- d'espace de noms au flux) et l'expiration. La consommation est un DELETE ... RETURNING atomique : un ticket ne peut être
-- accepté qu'une seule fois, même présenté simultanément à deux pods.
CREATE TABLE sse_ticket (
    token_hash VARCHAR(64)  PRIMARY KEY,
    username   VARCHAR(150) NOT NULL,
    expires_at TIMESTAMPTZ  NOT NULL
);
CREATE INDEX idx_sse_ticket_expires_at ON sse_ticket (expires_at);