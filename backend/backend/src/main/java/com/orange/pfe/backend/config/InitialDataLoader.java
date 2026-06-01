package com.orange.pfe.backend.config;

import com.orange.pfe.backend.repository.AppUserRepository;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.boot.context.event.ApplicationReadyEvent;
import org.springframework.context.event.EventListener;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.stereotype.Component;
import org.springframework.transaction.annotation.Transactional;

/**
 * Re-encodes any {noop} placeholder passwords inserted by Flyway V8 into BCrypt hashes.
 * Runs once after the application context is fully started.
 */
@Component
public class InitialDataLoader {

    private static final Logger log = LoggerFactory.getLogger(InitialDataLoader.class);
    private static final String NOOP_PREFIX = "{noop}";

    private final AppUserRepository userRepository;
    private final PasswordEncoder passwordEncoder;

    public InitialDataLoader(AppUserRepository userRepository, PasswordEncoder passwordEncoder) {
        this.userRepository = userRepository;
        this.passwordEncoder = passwordEncoder;
    }

    @EventListener(ApplicationReadyEvent.class)
    @Transactional
    public void upgradeNoopPasswords() {
        userRepository.findAll().forEach(user -> {
            String raw = user.getPassword();
            if (raw != null && raw.startsWith(NOOP_PREFIX)) {
                String plaintext = raw.substring(NOOP_PREFIX.length());
                user.setPassword(passwordEncoder.encode(plaintext));
                userRepository.save(user);
                log.info("Password for user '{}' upgraded from {{noop}} to BCrypt", user.getUsername());
            }
        });
    }
}
