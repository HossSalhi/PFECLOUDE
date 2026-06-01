package com.orange.pfe.backend.dto.user;

import com.orange.pfe.backend.model.user.AppUser;
import com.orange.pfe.backend.model.user.UserRole;

import java.time.LocalDateTime;

public record UserResponse(
        Long id,
        String username,
        String email,
        String firstName,
        String lastName,
        UserRole role,
        boolean enabled,
        boolean twoFactorEnabled,
        LocalDateTime lastLoginAt,
        String lastLoginIp,
        int failedAttempts,
        LocalDateTime lockedUntil,
        LocalDateTime createdAt,
        LocalDateTime blockedAt,
        String blockedBy,
        String blockReason
) {
    public static UserResponse from(AppUser u) {
        return new UserResponse(
                u.getId(),
                u.getUsername(),
                u.getEmail(),
                u.getFirstName(),
                u.getLastName(),
                u.getRole(),
                u.isEnabled(),
                u.isTwoFactorEnabled(),
                u.getLastLoginAt(),
                u.getLastLoginIp(),
                u.getFailedAttempts(),
                u.getLockedUntil(),
                u.getCreatedAt(),
                u.getBlockedAt(),
                u.getBlockedBy(),
                u.getBlockReason()
        );
    }
}
