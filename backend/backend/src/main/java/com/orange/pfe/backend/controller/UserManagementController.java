package com.orange.pfe.backend.controller;

import com.orange.pfe.backend.dto.user.Enable2faResponse;
import com.orange.pfe.backend.dto.user.UserRequest;
import com.orange.pfe.backend.dto.user.UserResponse;
import com.orange.pfe.backend.model.audit.AuditAction;
import com.orange.pfe.backend.model.audit.AuditModule;
import com.orange.pfe.backend.model.user.AppUser;
import com.orange.pfe.backend.model.user.UserRole;
import com.orange.pfe.backend.repository.AppUserRepository;
import com.orange.pfe.backend.service.UserService;
import com.orange.pfe.backend.service.audit.AuditEvent;
import com.orange.pfe.backend.service.audit.AuditTrailService;
import jakarta.validation.Valid;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.security.access.AccessDeniedException;
import org.springframework.security.access.prepost.PreAuthorize;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.web.bind.annotation.*;

import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

@RestController
@RequestMapping("/api/users")
@PreAuthorize("hasAnyRole('ADMIN','SUPER_ADMIN')")
public class UserManagementController {

    private final UserService userService;
    private final AppUserRepository userRepository;
    private final AuditTrailService auditTrailService;

    public UserManagementController(UserService userService, AppUserRepository userRepository,
                                    AuditTrailService auditTrailService) {
        this.userService = userService;
        this.userRepository = userRepository;
        this.auditTrailService = auditTrailService;
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
        UserResponse created = userService.create(request, actor.getUsername());
        audit(AuditAction.CREATE, created.id(), "Création de l'utilisateur " + created.username(),
                null, userValues(created), actor);
        return created;
    }

    @PutMapping("/{id}")
    @PreAuthorize("hasRole('SUPER_ADMIN')")
    public UserResponse updateUser(@PathVariable Long id,
                                   @Valid @RequestBody UserRequest request,
                                   @AuthenticationPrincipal AppUser actor) {
        Map<String, Object> before = snapshot(id);
        UserResponse updated = userService.update(id, request, actor.getUsername());
        audit(AuditAction.UPDATE, id, "Modification de l'utilisateur " + updated.username(),
                before, userValues(updated), actor);
        return updated;
    }

    @DeleteMapping("/{id}")
    @ResponseStatus(HttpStatus.NO_CONTENT)
    @PreAuthorize("hasRole('SUPER_ADMIN')")
    public void deleteUser(@PathVariable Long id) {
        Map<String, Object> before = snapshot(id);
        userService.delete(id);
        audit(AuditAction.DELETE, id, "Suppression de l'utilisateur " + displayName(before, id),
                before, null, null);
    }

    @PatchMapping("/{id}/enable")
    @PreAuthorize("hasRole('SUPER_ADMIN')")
    public UserResponse enableUser(@PathVariable Long id) {
        UserResponse r = userService.setEnabled(id, true);
        audit(AuditAction.ENABLE, id, "Activation du compte " + r.username(),
                singleton("enabled", false), singleton("enabled", true), null);
        return r;
    }

    @PatchMapping("/{id}/disable")
    @PreAuthorize("hasRole('SUPER_ADMIN')")
    public UserResponse disableUser(@PathVariable Long id) {
        UserResponse r = userService.setEnabled(id, false);
        audit(AuditAction.DISABLE, id, "Désactivation du compte " + r.username(),
                singleton("enabled", true), singleton("enabled", false), null);
        return r;
    }

    @PatchMapping("/{id}/role")
    @PreAuthorize("hasRole('SUPER_ADMIN')")
    public UserResponse changeRole(@PathVariable Long id,
                                   @RequestParam UserRole role) {
        UserRole oldRole = userRepository.findById(id).map(AppUser::getRole).orElse(null);
        UserResponse r = userService.setRole(id, role);
        audit(AuditAction.ROLE_CHANGE, id,
                "Changement de rôle de " + r.username() + " : " + oldRole + " → " + r.role(),
                singleton("role", oldRole), singleton("role", r.role()), null);
        return r;
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
        // Jamais de mot de passe dans la trace : seul l'événement est enregistré.
        audit(AuditAction.PASSWORD_RESET, id, "Réinitialisation du mot de passe de l'utilisateur "
                + displayName(snapshot(id), id), null, null, actor);
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
        UserResponse blocked = userService.blockUser(id, reason, actor.getUsername());
        audit(AuditAction.BLOCK, id, "Blocage de l'utilisateur " + blocked.username(),
                null, singleton("reason", reason), actor);
        return ResponseEntity.ok(blocked);
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
        UserResponse unblocked = userService.unblockUser(id);
        audit(AuditAction.UNBLOCK, id, "Déblocage de l'utilisateur " + unblocked.username(), null, null, actor);
        return unblocked;
    }

    // ── 2FA management (SUPER_ADMIN only) ────────────────────────────────────

    @PostMapping("/{id}/2fa/setup")
    @PreAuthorize("hasRole('SUPER_ADMIN')")
    public Enable2faResponse setup2fa(@PathVariable Long id) {
        Enable2faResponse response = userService.setup2fa(id);
        // Ni secret ni QR code dans la trace.
        audit(AuditAction.TWO_FACTOR_CHANGE, id, "Configuration 2FA initiée pour " + displayName(snapshot(id), id),
                null, null, null);
        return response;
    }

    @PostMapping("/{id}/2fa/confirm")
    @PreAuthorize("hasRole('SUPER_ADMIN')")
    public UserResponse confirm2fa(@PathVariable Long id,
                                   @RequestParam String code) {
        UserResponse r = userService.confirm2fa(id, code);
        audit(AuditAction.TWO_FACTOR_CHANGE, id, "2FA activée pour " + r.username(),
                singleton("twoFactorEnabled", false), singleton("twoFactorEnabled", true), null);
        return r;
    }

    @DeleteMapping("/{id}/2fa")
    @PreAuthorize("hasRole('SUPER_ADMIN')")
    public UserResponse disable2fa(@PathVariable Long id) {
        UserResponse r = userService.disable2fa(id);
        audit(AuditAction.TWO_FACTOR_CHANGE, id, "2FA désactivée pour " + r.username(),
                singleton("twoFactorEnabled", true), singleton("twoFactorEnabled", false), null);
        return r;
    }

    // ── Audit (additif : n'altère jamais le résultat de l'opération) ─────────

    private void audit(AuditAction action, Long targetId, String summary,
                       Map<String, ?> oldValues, Map<String, ?> newValues, AppUser actor) {
        auditTrailService.recordQuietly(AuditEvent.of(AuditModule.USERS, action)
                .actor(actor)
                .object("AppUser", targetId)
                .summary(summary)
                .oldValues(oldValues)
                .newValues(newValues));
    }

    private Map<String, Object> snapshot(Long id) {
        return userRepository.findById(id).map(UserManagementController::userValues).orElse(null);
    }

    private static Map<String, Object> singleton(String key, Object value) {
        Map<String, Object> m = new LinkedHashMap<>();
        m.put(key, value);
        return m;
    }

    private static String displayName(Map<String, Object> snapshot, Long id) {
        Object name = snapshot == null ? null : snapshot.get("username");
        return name != null ? String.valueOf(name) : "#" + id;
    }

    private static Map<String, Object> userValues(UserResponse r) {
        Map<String, Object> m = new LinkedHashMap<>();
        m.put("username", r.username());
        m.put("email", r.email());
        m.put("firstName", r.firstName());
        m.put("lastName", r.lastName());
        m.put("role", r.role());
        m.put("enabled", r.enabled());
        return m;
    }

    private static Map<String, Object> userValues(AppUser u) {
        Map<String, Object> m = new LinkedHashMap<>();
        m.put("username", u.getUsername());
        m.put("email", u.getEmail());
        m.put("firstName", u.getFirstName());
        m.put("lastName", u.getLastName());
        m.put("role", u.getRole());
        m.put("enabled", u.isEnabled());
        return m;
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
