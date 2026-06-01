package com.orange.pfe.backend.repository;

import com.orange.pfe.backend.model.user.AppUser;
import com.orange.pfe.backend.model.user.UserRole;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Modifying;
import org.springframework.data.jpa.repository.Query;
import org.springframework.transaction.annotation.Transactional;

import java.util.List;
import java.util.Optional;

public interface AppUserRepository extends JpaRepository<AppUser, Long> {
    Optional<AppUser> findByUsername(String username);
    Optional<AppUser> findByEmail(String email);
    boolean existsByUsername(String username);
    boolean existsByEmail(String email);
    List<AppUser> findAllByOrderByCreatedAtDesc();
    List<AppUser> findByRole(UserRole role);

    Optional<AppUser> findByActivationToken(String token);
    Optional<AppUser> findByResetToken(String token);

    @Transactional
    @Modifying
    @Query("UPDATE AppUser u SET u.failedAttempts = u.failedAttempts + 1 WHERE u.id = :id")
    void incrementFailedAttempts(Long id);

    @Transactional
    @Modifying
    @Query("UPDATE AppUser u SET u.failedAttempts = 0, u.lockedUntil = null WHERE u.id = :id")
    void resetFailedAttempts(Long id);
}
