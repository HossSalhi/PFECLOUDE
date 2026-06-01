package com.orange.pfe.backend.repository;

import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Modifying;
import org.springframework.data.jpa.repository.Query;

import jakarta.persistence.*;
import java.time.LocalDateTime;

public interface JwtBlacklistRepository extends JpaRepository<JwtBlacklistRepository.JwtBlacklist, Long> {
    boolean existsByJti(String jti);

    @Modifying
    @Query("DELETE FROM JwtBlacklist b WHERE b.expiresAt < :now")
    void deleteExpired(LocalDateTime now);

    @Entity(name = "JwtBlacklist")
    @Table(name = "jwt_blacklist")
    class JwtBlacklist {
        @Id @GeneratedValue(strategy = GenerationType.IDENTITY)
        private Long id;

        @Column(nullable = false, unique = true, length = 100)
        private String jti;

        @Column(name = "expires_at", nullable = false)
        private LocalDateTime expiresAt;

        @Column(name = "revoked_at", nullable = false)
        private LocalDateTime revokedAt = LocalDateTime.now();

        @Column(name = "revoked_by", length = 50)
        private String revokedBy;

        public JwtBlacklist() {}
        public JwtBlacklist(String jti, LocalDateTime expiresAt, String revokedBy) {
            this.jti = jti;
            this.expiresAt = expiresAt;
            this.revokedBy = revokedBy;
        }

        public Long getId() { return id; }
        public String getJti() { return jti; }
        public LocalDateTime getExpiresAt() { return expiresAt; }
        public LocalDateTime getRevokedAt() { return revokedAt; }
        public String getRevokedBy() { return revokedBy; }
    }
}
