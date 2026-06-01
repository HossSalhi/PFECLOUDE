package com.orange.pfe.backend.service;

import com.orange.pfe.backend.model.user.AppUser;
import com.orange.pfe.backend.repository.JwtBlacklistRepository;
import io.jsonwebtoken.*;
import io.jsonwebtoken.io.Decoders;
import io.jsonwebtoken.security.Keys;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import javax.crypto.SecretKey;
import java.time.LocalDateTime;
import java.time.ZoneId;
import java.util.Date;
import java.util.Map;
import java.util.UUID;

@Service
public class JwtService {

    private static final Logger log = LoggerFactory.getLogger(JwtService.class);

    @Value("${jwt.secret}")
    private String secret;

    @Value("${jwt.expiration-ms:86400000}")
    private long expirationMs;

    @Value("${jwt.refresh-expiration-ms:604800000}")
    private long refreshExpirationMs;

    private final JwtBlacklistRepository blacklistRepo;

    public JwtService(JwtBlacklistRepository blacklistRepo) {
        this.blacklistRepo = blacklistRepo;
    }

    public String generateAccessToken(AppUser user) {
        return buildToken(user, expirationMs, "access");
    }

    public String generateRefreshToken(AppUser user) {
        return buildToken(user, refreshExpirationMs, "refresh");
    }

    /** Short-lived token (5 min) issued after password check when 2FA is required */
    public String generateTempToken(AppUser user) {
        return buildToken(user, 5 * 60 * 1000L, "2fa-pending");
    }

    private String buildToken(AppUser user, long expiryMs, String tokenType) {
        Date now    = new Date();
        Date expiry = new Date(now.getTime() + expiryMs);

        return Jwts.builder()
                .id(UUID.randomUUID().toString())
                .subject(user.getUsername())
                .issuedAt(now)
                .expiration(expiry)
                .claims(Map.of(
                        "role",      user.getRole().name(),
                        "userId",    user.getId(),
                        "email",     user.getEmail() != null ? user.getEmail() : "",
                        "tokenType", tokenType
                ))
                .signWith(getSignKey())
                .compact();
    }

    public boolean isTokenValid(String token) {
        try {
            Claims claims = parseClaims(token);
            String jti = claims.getId();
            if (jti != null && blacklistRepo.existsByJti(jti)) {
                log.debug("Token {} is blacklisted", jti);
                return false;
            }
            return true;
        } catch (JwtException | IllegalArgumentException e) {
            log.debug("Invalid JWT: {}", e.getMessage());
            return false;
        }
    }

    public String extractUsername(String token) {
        return parseClaims(token).getSubject();
    }

    public String extractJti(String token) {
        return parseClaims(token).getId();
    }

    public Date extractExpiration(String token) {
        return parseClaims(token).getExpiration();
    }

    public String extractTokenType(String token) {
        return (String) parseClaims(token).get("tokenType");
    }

    @Transactional
    public void revokeToken(String token, String revokedBy) {
        try {
            Claims claims = parseClaims(token);
            String jti = claims.getId();
            Date exp   = claims.getExpiration();
            if (jti != null) {
                LocalDateTime expiresAt = exp.toInstant()
                        .atZone(ZoneId.systemDefault()).toLocalDateTime();
                blacklistRepo.save(new JwtBlacklistRepository.JwtBlacklist(jti, expiresAt, revokedBy));
                log.info("Token {} revoked by {}", jti, revokedBy);
            }
        } catch (JwtException e) {
            log.debug("Could not revoke token: {}", e.getMessage());
        }
    }

    @Scheduled(fixedDelay = 3_600_000)
    @Transactional
    public void cleanupExpiredBlacklist() {
        blacklistRepo.deleteExpired(LocalDateTime.now());
        log.debug("JWT blacklist cleanup done");
    }

    private Claims parseClaims(String token) {
        return Jwts.parser()
                .verifyWith(getSignKey())
                .build()
                .parseSignedClaims(token)
                .getPayload();
    }

    private SecretKey getSignKey() {
        byte[] keyBytes = Decoders.BASE64.decode(secret);
        return Keys.hmacShaKeyFor(keyBytes);
    }
}
