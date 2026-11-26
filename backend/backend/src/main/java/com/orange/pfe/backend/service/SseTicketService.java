package com.orange.pfe.backend.service;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.security.SecureRandom;
import java.util.Base64;
import java.util.HexFormat;
import java.util.List;
import java.util.Optional;

/**
 * Tickets d'accès au flux SSE, PARTAGÉS entre réplicas via PostgreSQL (table {@code sse_ticket}, migration V28).
 * Un utilisateur déjà authentifié (JWT) obtient un ticket aléatoire de 256 bits, de courte durée (30 s par défaut). Seul son
 * condensat SHA-256 est stocké. La consommation est un {@code DELETE ... RETURNING} atomique : un ticket n'est accepté
 * qu'UNE fois, quel que soit le pod, y compris en cas de présentations simultanées. L'horloge est celle de PostgreSQL
 * (pas de décalage entre pods). Le JWT permanent n'est jamais utilisé comme ticket.
 */
@Service
public class SseTicketService {

    private static final SecureRandom RANDOM = new SecureRandom();
    private final JdbcTemplate jdbc;
    private final long ttlSeconds;

    public SseTicketService(JdbcTemplate jdbc, @Value("${helios.sse.ticket-ttl-seconds:30}") long ttlSeconds) {
        this.jdbc = jdbc;
        this.ttlSeconds = Math.max(5, Math.min(ttlSeconds, 120));
    }

    public long ttlSeconds() { return ttlSeconds; }

    public String issue(String username) {
        byte[] raw = new byte[32];
        RANDOM.nextBytes(raw);
        String ticket = Base64.getUrlEncoder().withoutPadding().encodeToString(raw);
        jdbc.update("INSERT INTO sse_ticket (token_hash, username, expires_at) VALUES (?, ?, now() + (? * interval '1 second'))",
                sha256(ticket), username, ttlSeconds);
        // Purge opportuniste des tickets expirés (jamais ceux encore valides).
        jdbc.update("DELETE FROM sse_ticket WHERE expires_at < now() - interval '1 minute'");
        return ticket;
    }

    /** Valide et CONSOMME le ticket de façon atomique ; renvoie le nom d'utilisateur, ou vide si absent, expiré, inconnu ou déjà utilisé. */
    public Optional<String> consume(String ticket) {
        if (ticket == null || ticket.isBlank() || ticket.length() > 100) return Optional.empty();
        List<String> rows = jdbc.query(
                "DELETE FROM sse_ticket WHERE token_hash = ? AND expires_at > now() RETURNING username",
                (rs, i) -> rs.getString(1), sha256(ticket));
        return rows.isEmpty() ? Optional.empty() : Optional.of(rows.get(0));
    }

    static String sha256(String s) {
        try {
            return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(s.getBytes(StandardCharsets.UTF_8)));
        } catch (NoSuchAlgorithmException e) {
            throw new IllegalStateException(e);
        }
    }
}