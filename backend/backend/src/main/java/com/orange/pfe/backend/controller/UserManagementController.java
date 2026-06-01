package com.orange.pfe.backend.controller;

import com.orange.pfe.backend.dto.user.Enable2faResponse;
import com.orange.pfe.backend.dto.user.UserRequest;
import com.orange.pfe.backend.dto.user.UserResponse;
import com.orange.pfe.backend.model.user.AppUser;
import com.orange.pfe.backend.model.user.UserRole;
import com.orange.pfe.backend.repository.AppUserRepository;
import com.orange.pfe.backend.service.UserService;
import jakarta.validation.Valid;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.security.access.AccessDeniedException;
import org.springframework.security.access.prepost.PreAuthorize;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.web.bind.annotation.*;

import java.util.List;
import java.util.Map;

@RestController
@RequestMapping("/api/users")
@PreAuthorize("hasAnyRole('ADMIN','SUPER_ADMIN')")
public class UserManagementController {

    private final UserService userService;
    private final AppUserRepository userRepository;

    public UserManagementController(UserService userService, AppUserRepository userRepository) {
        this.userService = userService;
        this.userRepository = userRepository;
    }

    // ── Read (ADMIN + SUPER_ADMIN) ────────────────────────────────────────────

    @GetMapping
    public List<UserResponse> listUsers() {
        return userService.findAll();
    }

    @GetMapping("/{id}")
    public UserResponse getUser(@PathVariable Long id) {
        return userService.findById(id);
    }

    // ── Write (SUPER_ADMIN only) ──────────────────────────────────────────────

    @PostMapping
    @ResponseStatus(HttpStatus.CREATED)
    @PreAuthorize("hasRole('SUPER_ADMIN')")
    public UserResponse createUser(@Valid @RequestBody UserRequest request,
                                   @AuthenticationPrincipal AppUser actor) {
        return userService.create(request, actor.getUsername());
    }

    @PutMapping("/{id}")
    @PreAuthorize("hasRole('SUPER_ADMIN')")
    public UserResponse updateUser(@PathVariable Long id,
                                   @Valid @RequestBody UserRequest request,
                                   @AuthenticationPrincipal AppUser actor) {
        return userService.update(id, request, actor.getUsername());
    }

    @DeleteMapping("/{id}")
    @ResponseStatus(HttpStatus.NO_CONTENT)
    @PreAuthorize("hasRole('SUPER_ADMIN')")
    public void deleteUser(@PathVariable Long id) {
        userService.delete(id);
    }

    @PatchMapping("/{id}/enable")
    @PreAuthorize("hasRole('SUPER_ADMIN')")
    public UserResponse enableUser(@PathVariable Long id) {
        return userService.setEnabled(id, true);
    }

    @PatchMapping("/{id}/disable")
    @PreAuthorize("hasRole('SUPER_ADMIN')")
    public UserResponse disableUser(@PathVariable Long id) {
        return userService.setEnabled(id, false);
    }

    @PatchMapping("/{id}/role")
    @PreAuthorize("hasRole('SUPER_ADMIN')")
    public UserResponse changeRole(@PathVariable Long id,
                                   @RequestParam UserRole role) {
        return userService.setRole(id, role);
    }

    @PostMapping("/{id}/reset-password")
    @PreAuthorize("hasRole('SUPER_ADMIN')")
    public ResponseEntity<?> resetPassword(@PathVariable Long id,
                                           @RequestBody Map<String, String> body,
                                           @AuthenticationPrincipal AppUser actor) {
        String newPassword = body.get("newPassword");
        if (newPassword == null || newPassword.length() < 8) {
            return ResponseEntity.badRequest()
                    .body(Map.of("error", "newPassword must be at least 8 characters"));
        }
        userService.adminResetPassword(id, newPassword, actor.getUsername());
        return ResponseEntity.ok(Map.of("message", "Password reset successfully"));
    }

    // ── Block / Unblock (ADMIN + SUPER_ADMIN, with hierarchy check) ──────────

    /**
     * Block a user.
     * SUPER_ADMIN: can block anyone.
     * ADMIN: can only block USER-role accounts.
     */
    @PostMapping("/{id}/block")
    public ResponseEntity<?> blockUser(@PathVariable Long id,
                                       @RequestBody Map<String, String> body,
                                       @AuthenticationPrincipal AppUser actor) {
        String reason = body.get("reason");
        if (reason == null || reason.isBlank()) {
            return ResponseEntity.badRequest().body(Map.of("error", "reason is required"));
        }
        if (actor.getRole() == UserRole.ADMIN) {
            enforceAdminCanOnlyTargetUsers(id, actor);
        }
        return ResponseEntity.ok(userService.blockUser(id, reason, actor.getUsername()));
    }

    /**
     * Unblock a user.
     * SUPER_ADMIN: can unblock anyone.
     * ADMIN: can only unblock USER-role accounts.
     */
    @PostMapping("/{id}/unblock")
    public UserResponse unblockUser(@PathVariable Long id,
                                    @AuthenticationPrincipal AppUser actor) {
        if (actor.getRole() == UserRole.ADMIN) {
            enforceAdminCanOnlyTargetUsers(id, actor);
        }
        return userService.unblockUser(id);
    }

    // ── 2FA management (SUPER_ADMIN only) ────────────────────────────────────

    @PostMapping("/{id}/2fa/setup")
    @PreAuthorize("hasRole('SUPER_ADMIN')")
    public Enable2faResponse setup2fa(@PathVariable Long id) {
        return userService.setup2fa(id);
    }

    @PostMapping("/{id}/2fa/confirm")
    @PreAuthorize("hasRole('SUPER_ADMIN')")
    public UserResponse confirm2fa(@PathVariable Long id,
                                   @RequestParam String code) {
        return userService.confirm2fa(id, code);
    }

    @DeleteMapping("/{id}/2fa")
    @PreAuthorize("hasRole('SUPER_ADMIN')")
    public UserResponse disable2fa(@PathVariable Long id) {
        return userService.disable2fa(id);
    }

    // ── Helper ────────────────────────────────────────────────────────────────

    private void enforceAdminCanOnlyTargetUsers(Long targetId, AppUser actor) {
        AppUser target = userRepository.findById(targetId)
                .orElseThrow(() -> new com.orange.pfe.backend.exception.ResourceNotFoundException("User not found: " + targetId));
        if (target.getRole() != UserRole.USER) {
            throw new AccessDeniedException(
                    "ADMIN role can only manage USER-role accounts, not " + target.getRole());
        }
        if (target.getId().equals(actor.getId())) {
            throw new AccessDeniedException("Cannot perform this action on your own account");
        }
    }
}
