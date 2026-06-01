package com.orange.pfe.backend.service;

import com.orange.pfe.backend.model.user.AppUser;
import com.orange.pfe.backend.model.user.UserRole;
import com.orange.pfe.backend.repository.AppUserRepository;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.ApplicationArguments;
import org.springframework.boot.ApplicationRunner;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.stereotype.Component;

/**
 * Crée automatiquement le compte SUPER_ADMIN au premier démarrage
 * si aucun SUPER_ADMIN n'existe en base.
 * Configuré via variables d'environnement (SUPER_ADMIN_EMAIL, etc.)
 * ou via application.properties (app.super-admin.*).
 */
@Component
public class SuperAdminBootstrapService implements ApplicationRunner {

    private static final Logger log = LoggerFactory.getLogger(SuperAdminBootstrapService.class);

    private final AppUserRepository userRepository;
    private final PasswordEncoder passwordEncoder;
    private final TotpService totpService;
    private final UserAuthEmailService userAuthEmailService;

    @Value("${app.super-admin.email:${SUPER_ADMIN_EMAIL:}}")
    private String email;

    @Value("${app.super-admin.username:${SUPER_ADMIN_USERNAME:superadmin}}")
    private String username;

    @Value("${app.super-admin.password:${SUPER_ADMIN_PASSWORD:SuperAdmin@Change2024!}}")
    private String rawPassword;

    @Value("${app.super-admin.first-name:${SUPER_ADMIN_FIRST_NAME:Super}}")
    private String firstName;

    @Value("${app.super-admin.last-name:${SUPER_ADMIN_LAST_NAME:Admin}}")
    private String lastName;

    public SuperAdminBootstrapService(AppUserRepository userRepository,
                                      PasswordEncoder passwordEncoder,
                                      TotpService totpService,
                                      UserAuthEmailService userAuthEmailService) {
        this.userRepository = userRepository;
        this.passwordEncoder = passwordEncoder;
        this.totpService = totpService;
        this.userAuthEmailService = userAuthEmailService;
    }

    @Override
    public void run(ApplicationArguments args) {
        if (!userRepository.findByRole(UserRole.SUPER_ADMIN).isEmpty()) {
            log.debug("SUPER_ADMIN already exists — bootstrap skipped");
            return;
        }

        if (email == null || email.isBlank()) {
            log.warn("No SUPER_ADMIN found and SUPER_ADMIN_EMAIL is not configured — " +
                     "set app.super-admin.email or SUPER_ADMIN_EMAIL env var");
            return;
        }

        if (userRepository.existsByUsername(username)) {
            log.warn("Bootstrap skipped — username '{}' already taken by a non-SUPER_ADMIN account", username);
            return;
        }
        if (userRepository.existsByEmail(email)) {
            log.warn("Bootstrap skipped — email '{}' already registered", email);
            return;
        }

        AppUser superAdmin = new AppUser();
        superAdmin.setUsername(username);
        superAdmin.setEmail(email);
        superAdmin.setPassword(passwordEncoder.encode(rawPassword));
        superAdmin.setFirstName(firstName);
        superAdmin.setLastName(lastName);
        superAdmin.setRole(UserRole.SUPER_ADMIN);
        superAdmin.setEnabled(true);
        superAdmin.setTwoFactorSecret(totpService.generateSecret());
        superAdmin.setTwoFactorEnabled(true);
        superAdmin.setForce2faOnNextLogin(true);
        superAdmin.setCreatedBy("SYSTEM");
        superAdmin.setUpdatedBy("SYSTEM");

        userRepository.save(superAdmin);

        log.warn("=======================================================");
        log.warn("SUPER_ADMIN created → username: {}  email: {}", username, email);
        log.warn("Change the default password immediately after first login.");
        log.warn("=======================================================");

        userAuthEmailService.sendWelcomeEmail(email, username, firstName);
    }
}
