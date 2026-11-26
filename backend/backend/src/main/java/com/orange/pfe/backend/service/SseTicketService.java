package com.orange.pfe.backend.service;

import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;

import javax.crypto.Mac;
import javax.crypto.spec.SecretKeySpec;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.SecureRandom;
import java.time.Clock;
import java.util.Base64;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;

/**
 * Tickets d'accès au flux SSE ({@code EventSource} ne peut pas envoyer d'en-tête Authorization).
 * Un utilisateur DÉJÀ authentifié (JWT) obtient un ticket court (30 s par défaut), signé (HMAC-SHA256) et à usage unique ;
 * le JWT permanent n'est JAMAIS placé dans l'URL. Sans état partagé : la signature permet de valider le ticket sur n'importe
 * quel pod ; l'usage unique est garanti PAR POD (limite documentée : un rejeu sur un autre pod dans la fenêtre de validité
 * reste possible, d'où la durée de vie courte).
 */
@Service
public class SseTicketService {

    private static final SecureRandom RANDOM = new SecureRandom();
    private final byte[] key;
    private final Clock clock;
    private final long ttlSeconds;
    private final Map<String, Long> used = new ConcurrentHashMap<>();

    @Autowired
    public SseTicketService(@Value("${jwt.secret:}") String secret,
                            @Value("${helios.sse.ticket-ttl-seconds:30}") long ttlSeconds) {
        this(secret, ttlSeconds, Clock.systemUTC());
    }

    SseTicketService(String secret, long ttlSeconds, Clock clock) {
        byte[] base = (secret == null || secret.isBlank()) ? randomBytes(32) : secret.getBytes(StandardCharsets.UTF_8);
        this.key = hmac(base, "helios-sse-ticket".getBytes(StandardCharsets.UTF_8));
        this.ttlSeconds = Math.max(5, Math.min(ttlSeconds, 120));
        this.clock = clock;
    }

    public long ttlSeconds() { return ttlSeconds; }

    public String issue() {
        long exp = clock.instant().getEpochSecond() + ttlSeconds;
        String nonce = Base64.getUrlEncoder().withoutPadding().encodeToString(randomBytes(16));
        String body = exp + "." + nonce;
        return body + "." + Base64.getUrlEncoder().withoutPadding().encodeToString(hmac(key, body.getBytes(StandardCharsets.UTF_8)));
    }

    /** Valide et CONSOMME le ticket : faux si absent, falsifié, expiré ou déjà utilisé. */
    public boolean consume(String ticket) {
        if (ticket == null || ticket.length() > 200) return false;
        String[] p = ticket.split("\\.");
        if (p.length != 3) return false;
        long now = clock.instant().getEpochSecond();
        long exp;
        try { exp = Long.parseLong(p[0]); } catch (NumberFormatException e) { return false; }
        byte[] expected = hmac(key, (p[0] + "." + p[1]).getBytes(StandardCharsets.UTF_8));
        byte[] given;
        try { given = Base64.getUrlDecoder().decode(p[2]); } catch (IllegalArgumentException e) { return false; }
        if (!MessageDigest.isEqual(expected, given) || exp < now) return false;
        used.entrySet().removeIf(e -> e.getValue() < now);
        return used.putIfAbsent(p[1], exp) == null;
    }

    private static byte[] randomBytes(int n) { byte[] b = new byte[n]; RANDOM.nextBytes(b); return b; }

    private static byte[] hmac(byte[] k, byte[] data) {
        try {
            Mac mac = Mac.getInstance("HmacSHA256");
            mac.init(new SecretKeySpec(k, "HmacSHA256"));
            return mac.doFinal(data);
        } catch (Exception e) {
            throw new IllegalStateException("HMAC indisponible", e);
        }
    }
}