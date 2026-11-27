package com.orange.pfe.backend.service;

import com.orange.pfe.backend.dto.user.Enable2faResponse;
import com.orange.pfe.backend.dto.user.UserRequest;
import com.orange.pfe.backend.dto.user.UserResponse;
import com.orange.pfe.backend.exception.ResourceNotFoundException;
import com.orange.pfe.backend.model.user.AppUser;
import com.orange.pfe.backend.model.user.UserRole;
import com.orange.pfe.backend.repository.AppUserRepository;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.util.List;

@Service
@Transactional
public class UserServiceImpl implements UserService {

    private static final Logger log = LoggerFactory.getLogger(UserServiceImpl.class);

    private final AppUserRepository userRepository;
    private final PasswordEncoder passwordEncoder;
    private final TotpService totpService;
    private final EmailNotificationService emailNotificationService;
    private final com.orange.pfe.backend.service.team.UserRoleLabelService roleLabelService;

    public UserServiceImpl(AppUserRepository userRepository,
                           PasswordEncoder passwordEncoder,
                           TotpService totpService,
                           EmailNotificationService emailNotificationService,
                           com.orange.pfe.backend.service.team.UserRoleLabelService roleLabelService) {
        this.roleLabelService = roleLabelService;
        this.userRepository = userRepository;
        this.passwordEncoder = passwordEncoder;
        this.totpService = totpService;
        this.emailNotificationService = emailNotificationService;
    }

    @Override
    @Transactional(readOnly = true)
    public List<UserResponse> findAll() {
        List<AppUser> all = userRepository.findAllByOrderByCreatedAtDesc();
        var labels = roleLabelService.labelsFor(all.stream().map(AppUser::getId).toList());
        return all.stream().map(u -> UserResponse.from(u).withRoleLabels(labels.get(u.getId()))).toList();
    }

    @Override
    @Transactional(readOnly = true)
    public UserResponse findById(Long id) {
        return UserResponse.from(getOrThrow(id));
    }

    @Override
    public UserResponse create(UserRequest request, String createdBy) {
        if (userRepository.existsByUsername(request.username())) {
            throw new IllegalArgumentException("Username already taken: " + request.username());
        }
        if (userRepository.existsByEmail(request.email())) {
            throw new IllegalArgumentException("Email already registered: " + request.email());
        }

        AppUser user = new AppUser();
        user.setUsername(request.username());
        user.setEmail(request.email());
        user.setPassword(passwordEncoder.encode(request.password()));
        user.setRole(request.role());
        user.setFirstName(request.firstName());
        user.setLastName(request.lastName());
        user.setCreatedBy(createdBy);
        user.setUpdatedBy(createdBy);
        // Auto-enable 2FA for all new users
        user.setTwoFactorSecret(totpService.generateSecret());
        user.setTwoFactorEnabled(true);
        user.setForce2faOnNextLogin(true);
        log.info("User {} created by {} (2FA auto-enabled)", request.username(), createdBy);
        UserResponse saved = UserResponse.from(userRepository.save(user));
        emailNotificationService.sendAccountCreatedByAdmin(user, request.password(), createdBy);
        return saved;
    }

    @Override
    public UserResponse update(Long id, UserRequest request, String updatedBy) {
        AppUser user = getOrThrow(id);

        if (!user.getUsername().equals(request.username())
                && userRepository.existsByUsername(request.username())) {
            throw new IllegalArgumentException("Username already taken: " + request.username());
        }
        if (!user.getEmail().equals(request.email())
                && userRepository.existsByEmail(request.email())) {
            throw new IllegalArgumentException("Email already registered: " + request.email());
        }

        user.setUsername(request.username());
        user.setEmail(request.email());
        if (request.password() != null && !request.password().isBlank()) {
            user.setPassword(passwordEncoder.encode(request.password()));
        }
        user.setRole(request.role());
        user.setFirstName(request.firstName());
        user.setLastName(request.lastName());
        user.setUpdatedBy(updatedBy);
        return UserResponse.from(userRepository.save(user));
    }

    @Override
    public void delete(Long id) {
        AppUser user = getOrThrow(id);
        userRepository.delete(user);
        log.info("User {} (id={}) deleted", user.getUsername(), id);
    }

    @Override
    public UserResponse setEnabled(Long id, boolean enabled) {
        AppUser user = getOrThrow(id);
        user.setEnabled(enabled);
        return UserResponse.from(userRepository.save(user));
    }

    @Override
    public UserResponse blockUser(Long id, String reason, String blockedBy) {
        AppUser user = getOrThrow(id);
        user.setEnabled(false);
        user.setBlockedAt(java.time.LocalDateTime.now());
        user.setBlockedBy(blockedBy);
        user.setBlockReason(reason);
        log.info("User {} blocked by {} — reason: {}", user.getUsername(), blockedBy, reason);
        UserResponse result = UserResponse.from(userRepository.save(user));
        emailNotificationService.sendAccountBlocked(user, reason, blockedBy);
        return result;
    }

    @Override
    public UserResponse unblockUser(Long id) {
        AppUser user = getOrThrow(id);
        user.setEnabled(true);
        user.setBlockedAt(null);
        user.setBlockedBy(null);
        user.setBlockReason(null);
        user.setFailedAttempts(0);
        user.setLockedUntil(null);
        log.info("User {} unblocked", user.getUsername());
        UserResponse result = UserResponse.from(userRepository.save(user));
        emailNotificationService.sendAccountUnblocked(user);
        return result;
    }

    @Override
    public UserResponse setRole(Long id, UserRole role) {
        AppUser user = getOrThrow(id);
        user.setRole(role);
        return UserResponse.from(userRepository.save(user));
    }

    @Override
    public Enable2faResponse setup2fa(Long id) {
        AppUser user = getOrThrow(id);
        String secret = totpService.generateSecret();
        user.setTwoFactorSecret(secret);
        userRepository.save(user);
        return new Enable2faResponse(
                secret,
                totpService.generateQrCodeDataUri(user.getUsername(), secret),
                totpService.getOtpAuthUri(user.getUsername(), secret)
        );
    }

    @Override
    public UserResponse confirm2fa(Long id, String code) {
        AppUser user = getOrThrow(id);
        if (user.getTwoFactorSecret() == null) {
            throw new IllegalStateException("2FA not initialized — call setup first");
        }
        if (!totpService.verifyCode(user.getTwoFactorSecret(), code)) {
            throw new IllegalArgumentException("Invalid TOTP code");
        }
        user.setTwoFactorEnabled(true);
        return UserResponse.from(userRepository.save(user));
    }

    @Override
    public UserResponse disable2fa(Long id) {
        AppUser user = getOrThrow(id);
        user.setTwoFactorEnabled(false);
        user.setTwoFactorSecret(null);
        return UserResponse.from(userRepository.save(user));
    }

    @Override
    public void adminResetPassword(Long id, String newPassword, String resetBy) {
        AppUser user = getOrThrow(id);
        user.setPassword(passwordEncoder.encode(newPassword));
        user.setUpdatedBy(resetBy);
        user.setFailedAttempts(0);
        user.setLockedUntil(null);
        userRepository.save(user);
        log.info("Password for user {} reset by {}", user.getUsername(), resetBy);
        emailNotificationService.sendAdminPasswordReset(user, newPassword, resetBy);
    }

    @Override
    public void changePassword(AppUser user, String currentPassword, String newPassword) {
        if (!passwordEncoder.matches(currentPassword, user.getPassword())) {
            throw new IllegalArgumentException("Mot de passe actuel incorrect.");
        }
        if (newPassword.length() < 8) {
            throw new IllegalArgumentException("Le nouveau mot de passe doit contenir au moins 8 caractères.");
        }
        user.setPassword(passwordEncoder.encode(newPassword));
        userRepository.save(user);
        emailNotificationService.sendPasswordChangedConfirmation(user);
    }

    @Override
    public void changeEmail(AppUser user, String currentPassword, String newEmail) {
        if (!passwordEncoder.matches(currentPassword, user.getPassword())) {
            throw new IllegalArgumentException("Mot de passe incorrect — modification annulée.");
        }
        if (userRepository.existsByEmail(newEmail) && !newEmail.equalsIgnoreCase(user.getEmail())) {
            throw new IllegalArgumentException("Cet email est déjà utilisé par un autre compte.");
        }
        String oldEmail = user.getEmail();
        user.setEmail(newEmail);
        userRepository.save(user);
        emailNotificationService.sendEmailChangedConfirmation(user, oldEmail);
    }

    private AppUser getOrThrow(Long id) {
        return userRepository.findById(id)
                .orElseThrow(() -> new ResourceNotFoundException("User not found: " + id));
    }
}
