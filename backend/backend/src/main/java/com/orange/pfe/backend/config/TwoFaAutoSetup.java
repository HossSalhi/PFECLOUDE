package com.orange.pfe.backend.config;

import com.orange.pfe.backend.model.user.AppUser;
import com.orange.pfe.backend.repository.AppUserRepository;
import com.orange.pfe.backend.service.TotpService;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.boot.context.event.ApplicationReadyEvent;
import org.springframework.context.event.EventListener;
import org.springframework.stereotype.Component;

import java.util.List;

/**
 * One-time auto-enable of 2FA on existing accounts.
 * Disabled by default; opt in with app.security.two-fa-auto-setup-enabled=true.
 * Once everyone has 2FA you should leave this off to avoid re-forcing it on every reboot.
 */
@Component
@ConditionalOnProperty(name = "app.security.two-fa-auto-setup-enabled", havingValue = "true")
public class TwoFaAutoSetup {

    private static final Logger log = LoggerFactory.getLogger(TwoFaAutoSetup.class);

    private final AppUserRepository userRepository;
    private final TotpService totpService;

    public TwoFaAutoSetup(AppUserRepository userRepository, TotpService totpService) {
        this.userRepository = userRepository;
        this.totpService = totpService;
    }

    @EventListener(ApplicationReadyEvent.class)
    public void enableTwoFaForAllUsers() {
        List<AppUser> users = userRepository.findAll();
        int count = 0;
        for (AppUser user : users) {
            if (!user.isTwoFactorEnabled()) {
                user.setTwoFactorSecret(totpService.generateSecret());
                user.setTwoFactorEnabled(true);
                user.setForce2faOnNextLogin(true);
                userRepository.save(user);
                count++;
                log.info("2FA auto-enabled for existing user: {}", user.getUsername());
            }
        }
        if (count > 0) {
            log.info("2FA auto-setup complete: {} user(s) updated", count);
        }
    }
}
