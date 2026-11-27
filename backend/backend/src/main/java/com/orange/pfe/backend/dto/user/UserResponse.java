package com.orange.pfe.backend.dto.user;

import com.orange.pfe.backend.model.user.AppUser;
import com.orange.pfe.backend.model.user.UserRole;

import java.time.LocalDateTime;
import java.util.List;

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
        String blockReason,
        /** Badges « Chef / Technicien &lt;spécialité&gt; » calculés depuis les équipes ; vide hors liste des utilisateurs. */
        List<String> roleLabels
) {
    public UserResponse withRoleLabels(List<String> labels) {
        return new UserResponse(id, username, email, firstName, lastName, role, enabled, twoFactorEnabled, lastLoginAt,
                lastLoginIp, failedAttempts, lockedUntil, createdAt, blockedAt, blockedBy, blockReason,
                labels == null ? List.of() : labels);
    }

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
                u.getBlockReason(),
                List.of()
        );
    }
}
