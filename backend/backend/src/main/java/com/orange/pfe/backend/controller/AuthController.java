package com.orange.pfe.backend.controller;

import com.orange.pfe.backend.config.AppSecurityProperties;
import com.orange.pfe.backend.dto.auth.ChangeEmailRequest;
import com.orange.pfe.backend.dto.auth.ChangePasswordRequest;
import com.orange.pfe.backend.dto.auth.ForgotPasswordRequest;
import com.orange.pfe.backend.dto.auth.LoginRequest;
import com.orange.pfe.backend.dto.auth.LoginResponse;
import com.orange.pfe.backend.dto.auth.RegisterRequest;
import com.orange.pfe.backend.dto.auth.ResetPasswordRequest;
import com.orange.pfe.backend.dto.auth.TotpVerifyRequest;
import com.orange.pfe.backend.dto.user.UserResponse;
import com.orange.pfe.backend.model.user.AppUser;
import com.orange.pfe.backend.model.user.UserRole;
import com.orange.pfe.backend.repository.AppUserRepository;
import com.orange.pfe.backend.service.JwtService;
import com.orange.pfe.backend.service.TotpService;
import com.orange.pfe.backend.service.UserAuthEmailService;
import com.orange.pfe.backend.service.UserService;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.validation.Valid;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.security.authentication.AuthenticationManager;
import org.springframework.security.authentication.BadCredentialsException;
import org.springframework.security.authentication.DisabledException;
import org.springframework.security.authentication.LockedException;
import org.springframework.security.authentication.UsernamePasswordAuthenticationToken;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.web.bind.annotation.*;

import java.time.LocalDateTime;
import java.util.UUID;

import java.time.LocalDateTime;
import java.util.Map;

@RestController
@RequestMapping("/api/auth")
public class AuthController {

    private static final Logger log = LoggerFactory.getLogger(AuthController.class);

    private final AuthenticationManager authManager;
    private final JwtService jwtService;
    private final TotpService totpService;
    private final UserService userService;
    private final AppUserRepository userRepository;
    private final AppSecurityProperties securityProps;
    private final PasswordEncoder passwordEncoder;
    private final UserAuthEmailService userAuthEmailService;

    public AuthController(AuthenticationManager authManager,
                          JwtService jwtService,
                          TotpService totpService,
                          UserService userService,
                          AppUserRepository userRepository,
                          AppSecurityProperties securityProps,
                          PasswordEncoder passwordEncoder,
                          UserAuthEmailService userAuthEmailService) {
        this.authManager = authManager;
        this.jwtService = jwtService;
        this.totpService = totpService;
        this.userService = userService;
        this.userRepository = userRepository;
        this.securityProps = securityProps;
        this.passwordEncoder = passwordEncoder;
        this.userAuthEmailService = userAuthEmailService;
    }

    /**
     * Step 1 — Username + password.
     * If 2FA is enabled: returns {requires2fa: true, tempToken} and does NOT issue real tokens.
     * If 2FA is disabled: returns access + refresh tokens immediately.
     */
    @PostMapping("/login")
    public ResponseEntity<?> login(@Valid @RequestBody LoginRequest request,
                                   HttpServletRequest httpRequest) {
        try {
            authManager.authenticate(
                    new UsernamePasswordAuthenticationToken(request.username(), request.password()));
        } catch (DisabledException e) {
            return ResponseEntity.status(HttpStatus.UNAUTHORIZED)
                    .body(Map.of("error", "Account is disabled"));
        } catch (LockedException e) {
            return ResponseEntity.status(HttpStatus.UNAUTHORIZED)
                    .body(Map.of("error", "Account is locked"));
        } catch (BadCredentialsException e) {
            // Increment failed attempts and lock if threshold reached
            userRepository.findByUsername(request.username()).ifPresent(u -> {
                userRepository.incrementFailedAttempts(u.getId());
                int attempts = u.getFailedAttempts() + 1;
                if (attempts >= securityProps.getMaxFailedAttempts()) {
                    u.setLockedUntil(LocalDateTime.now()
                            .plusMinutes(securityProps.getLockDurationMinutes()));
                    userRepository.save(u);
                    log.warn("User {} locked after {} failed attempts", u.getUsername(), attempts);
                }
            });
            return ResponseEntity.status(HttpStatus.UNAUTHORIZED)
                    .body(Map.of("error", "Invalid username or password"));
        }

        AppUser user = userRepository.findByUsername(request.username()).orElseThrow();

        // Record successful login metadata
        user.setLastLoginAt(LocalDateTime.now());
        user.setLastLoginIp(extractClientIp(httpRequest));
        user.setFailedAttempts(0);
        user.setLockedUntil(null);
        userRepository.save(user);

        if (user.isTwoFactorEnabled()) {
            boolean forced  = user.isForce2faOnNextLogin();
            boolean expired = user.getLast2faVerifiedAt() == null ||
                              user.getLast2faVerifiedAt().isBefore(LocalDateTime.now().minusDays(14));
            if (forced || expired) {
                String tempToken = jwtService.generateTempToken(user);
                String qrCode = totpService.generateQrCodeDataUri(user.getUsername(), user.getTwoFactorSecret());
                log.debug("2FA required for user {} (forced={}, expired={})", user.getUsername(), forced, expired);
                return ResponseEntity.ok(LoginResponse.pending2fa(tempToken, user.getUsername(), qrCode));
            }
            log.debug("2FA skipped for user {} — last verified within 14 days", user.getUsername());
        }

        return ResponseEntity.ok(LoginResponse.authenticated(
                jwtService.generateAccessToken(user),
                jwtService.generateRefreshToken(user),
                user.getUsername(),
                user.getRole().name(),
                user.getFirstName(),
                user.getLastName()
        ));
    }

    /**
     * Step 2 — TOTP verification (only when 2FA is enabled).
     * Validates the temp token + TOTP code, then issues real tokens.
     */
    @PostMapping("/2fa/verify")
    public ResponseEntity<?> verify2fa(@Valid @RequestBody TotpVerifyRequest request) {
        if (!jwtService.isTokenValid(request.tempToken())) {
            return ResponseEntity.status(HttpStatus.UNAUTHORIZED)
                    .body(Map.of("error", "Temp token is invalid or expired"));
        }

        String tokenType = jwtService.extractTokenType(request.tempToken());
        if (!"2fa-pending".equals(tokenType)) {
            return ResponseEntity.status(HttpStatus.UNAUTHORIZED)
                    .body(Map.of("error", "Token is not a 2FA pending token"));
        }

        String username = jwtService.extractUsername(request.tempToken());
        AppUser user = userRepository.findByUsername(username).orElse(null);
        if (user == null || !user.isEnabled() || !user.isAccountNonLocked()) {
            return ResponseEntity.status(HttpStatus.UNAUTHORIZED)
                    .body(Map.of("error", "Account unavailable"));
        }

        if (!totpService.verifyCode(user.getTwoFactorSecret(), request.code())) {
            return ResponseEntity.status(HttpStatus.UNAUTHORIZED)
                    .body(Map.of("error", "Invalid TOTP code"));
        }

        // Revoke the consumed temp token
        jwtService.revokeToken(request.tempToken(), username);

        // Mark 2FA as verified now — enables 14-day bypass
        user.setLast2faVerifiedAt(LocalDateTime.now());
        user.setForce2faOnNextLogin(false);
        userRepository.save(user);

        return ResponseEntity.ok(LoginResponse.authenticated(
                jwtService.generateAccessToken(user),
                jwtService.generateRefreshToken(user),
                user.getUsername(),
                user.getRole().name(),
                user.getFirstName(),
                user.getLastName()
        ));
    }

    /**
     * Returns the profile of the currently authenticated user.
     */
    @GetMapping("/me")
    public ResponseEntity<UserResponse> me(@AuthenticationPrincipal AppUser user) {
        if (user == null) {
            return ResponseEntity.status(HttpStatus.UNAUTHORIZED).build();
        }
        return ResponseEntity.ok(UserResponse.from(user));
    }

    /**
     * Revokes the current Bearer token (logout).
     */
    @PostMapping("/logout")
    public ResponseEntity<Void> logout(HttpServletRequest request,
                                       @AuthenticationPrincipal AppUser user) {
        String authHeader = request.getHeader("Authorization");
        if (authHeader != null && authHeader.startsWith("Bearer ")) {
            String token = authHeader.substring(7);
            String who = user != null ? user.getUsername() : "unknown";
            jwtService.revokeToken(token, who);
            log.info("User {} logged out", who);
        }
        return ResponseEntity.noContent().build();
    }

    /**
     * Allows a user to change their own password.
     */
    @PostMapping("/change-password")
    public ResponseEntity<?> changePassword(@Valid @RequestBody ChangePasswordRequest request,
                                            @AuthenticationPrincipal AppUser user) {
        if (user == null) {
            return ResponseEntity.status(HttpStatus.UNAUTHORIZED).build();
        }
        try {
            userService.changePassword(user, request.currentPassword(), request.newPassword());
            return ResponseEntity.ok(Map.of("message", "Mot de passe modifié avec succès."));
        } catch (IllegalArgumentException e) {
            return ResponseEntity.badRequest().body(Map.of("error", e.getMessage()));
        }
    }

    @PostMapping("/change-email")
    public ResponseEntity<?> changeEmail(@Valid @RequestBody ChangeEmailRequest request,
                                         @AuthenticationPrincipal AppUser user) {
        if (user == null) {
            return ResponseEntity.status(HttpStatus.UNAUTHORIZED).build();
        }
        try {
            userService.changeEmail(user, request.currentPassword(), request.newEmail());
            return ResponseEntity.ok(Map.of("message", "Email modifié avec succès.", "newEmail", request.newEmail()));
        } catch (IllegalArgumentException e) {
            return ResponseEntity.badRequest().body(Map.of("error", e.getMessage()));
        }
    }

    /**
     * Exchanges a valid refresh token for a new access token.
     */
    @PostMapping("/refresh")
    public ResponseEntity<?> refresh(HttpServletRequest request) {
        String authHeader = request.getHeader("Authorization");
        if (authHeader == null || !authHeader.startsWith("Bearer ")) {
            return ResponseEntity.status(HttpStatus.UNAUTHORIZED)
                    .body(Map.of("error", "Missing refresh token"));
        }
        String refreshToken = authHeader.substring(7);

        if (!jwtService.isTokenValid(refreshToken)) {
            return ResponseEntity.status(HttpStatus.UNAUTHORIZED)
                    .body(Map.of("error", "Refresh token invalid or expired"));
        }

        String tokenType = jwtService.extractTokenType(refreshToken);
        if (!"refresh".equals(tokenType)) {
            return ResponseEntity.status(HttpStatus.UNAUTHORIZED)
                    .body(Map.of("error", "Not a refresh token"));
        }

        String username = jwtService.extractUsername(refreshToken);
        AppUser user = userRepository.findByUsername(username).orElse(null);
        if (user == null || !user.isEnabled()) {
            return ResponseEntity.status(HttpStatus.UNAUTHORIZED)
                    .body(Map.of("error", "Account unavailable"));
        }

        return ResponseEntity.ok(Map.of("accessToken", jwtService.generateAccessToken(user)));
    }

    @PostMapping("/register")
    public ResponseEntity<?> register(@Valid @RequestBody RegisterRequest request) {
        if (userRepository.existsByUsername(request.username())) {
            return ResponseEntity.badRequest().body(Map.of("error", "Username already taken"));
        }
        if (userRepository.existsByEmail(request.email())) {
            return ResponseEntity.badRequest().body(Map.of("error", "Email already registered"));
        }
        AppUser user = new AppUser();
        user.setUsername(request.username());
        user.setEmail(request.email());
        user.setPassword(passwordEncoder.encode(request.password()));
        user.setFirstName(request.firstName());
        user.setLastName(request.lastName());
        user.setRole(UserRole.USER);
        user.setEnabled(false);
        String token = UUID.randomUUID().toString();
        user.setActivationToken(token);
        userRepository.save(user);
        userAuthEmailService.sendActivationEmail(request.email(), request.username(), request.firstName(), token);
        log.info("User {} registered — activation email sent to {}", request.username(), request.email());
        return ResponseEntity.status(HttpStatus.CREATED).body(Map.of(
                "message", "Account created. Please check your email to activate your account."
        ));
    }

    @GetMapping("/activate")
    public ResponseEntity<?> activate(@RequestParam String token) {
        AppUser user = userRepository.findByActivationToken(token).orElse(null);
        if (user == null) {
            return ResponseEntity.badRequest().body(Map.of("error", "Invalid or expired activation token"));
        }
        user.setEnabled(true);
        user.setActivationToken(null);
        // Auto-enable 2FA on first activation
        if (!user.isTwoFactorEnabled()) {
            String secret = totpService.generateSecret();
            user.setTwoFactorSecret(secret);
            user.setTwoFactorEnabled(true);
        }
        user.setForce2faOnNextLogin(true);
        userRepository.save(user);
        userAuthEmailService.sendWelcomeEmail(user.getEmail(), user.getUsername(), user.getFirstName());
        log.info("User {} account activated", user.getUsername());
        return ResponseEntity.ok(Map.of("message", "Account activated successfully. You can now log in."));
    }

    @PostMapping("/forgot-password")
    public ResponseEntity<?> forgotPassword(@Valid @RequestBody ForgotPasswordRequest request) {
        AppUser user = userRepository.findByEmail(request.email()).orElse(null);
        if (user != null) {
            String token = UUID.randomUUID().toString();
            user.setResetToken(token);
            user.setResetTokenExpiry(LocalDateTime.now().plusHours(1));
            userRepository.save(user);
            userAuthEmailService.sendPasswordResetEmail(user.getEmail(), user.getUsername(), user.getFirstName(), token);
            log.info("Password reset requested for {} — reset email sent", user.getUsername());
        }
        return ResponseEntity.ok(Map.of("message", "If the email exists, a reset link has been sent."));
    }

    @PostMapping("/reset-password")
    public ResponseEntity<?> resetPassword(@Valid @RequestBody ResetPasswordRequest request) {
        AppUser user = userRepository.findByResetToken(request.token()).orElse(null);
        if (user == null || user.getResetTokenExpiry() == null
                || user.getResetTokenExpiry().isBefore(LocalDateTime.now())) {
            return ResponseEntity.badRequest().body(Map.of("error", "Invalid or expired reset token"));
        }
        user.setPassword(passwordEncoder.encode(request.newPassword()));
        user.setResetToken(null);
        user.setResetTokenExpiry(null);
        if (user.isTwoFactorEnabled()) {
            user.setForce2faOnNextLogin(true);
        }
        userRepository.save(user);
        log.info("Password reset successfully for user {}", user.getUsername());
        return ResponseEntity.ok(Map.of("message", "Password reset successfully. You can now log in."));
    }

    private String extractClientIp(HttpServletRequest request) {
        String forwarded = request.getHeader("X-Forwarded-For");
        if (forwarded != null && !forwarded.isBlank()) {
            return forwarded.split(",")[0].trim();
        }
        return request.getRemoteAddr();
    }
}
