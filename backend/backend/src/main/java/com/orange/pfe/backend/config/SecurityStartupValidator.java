package com.orange.pfe.backend.config;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.context.event.ApplicationReadyEvent;
import org.springframework.context.event.EventListener;
import org.springframework.stereotype.Component;

import java.util.ArrayList;

/**
 * Validates security-critical configuration at startup and logs WARN/ERROR
 * messages when insecure defaults are detected. Does NOT crash the application
 * so that CI smoke tests pass, but makes the risk clearly visible in logs.
 */
@Component
public class SecurityStartupValidator {

    private static final Logger log = LoggerFactory.getLogger(SecurityStartupValidator.class);

    // The default value baked into application.properties
    private static final String DEFAULT_JWT_SECRET =
            "bXlTdXBlclNlY3JldEtleUZvckpXVEF1dGhlbnRpY2F0aW9uMjAyNA==";
    private static final String DEFAULT_SUPER_ADMIN_PASSWORD =
            "SuperAdmin@Change2024!";
    private static final String DEFAULT_OPERATOR_PASSWORD =
            "operator-change-me";

    @Value("${jwt.secret:}")
    private String jwtSecret;

    @Value("${app.super-admin.password:}")
    private String superAdminPassword;

    @Value("${app.security.api-password:}")
    private String operatorPassword;

    @Value("${helios.bridge.api-key:}")
    private String bridgeApiKey;

    @Value("${helios.bridge.auth-required:false}")
    private boolean bridgeAuthRequired;

    /**
     * When true, any detected insecure default causes an immediate startup failure.
     * Set SECURITY_STRICT_MODE=true in production K8s secrets.
     * CI smoke tests leave this false so they can boot with default credentials.
     */
    @Value("${security.strict-mode:false}")
    private boolean strictMode;

    @EventListener(ApplicationReadyEvent.class)
    public void validateSecurityConfiguration() {
        var violations = new ArrayList<String>();

        if (DEFAULT_JWT_SECRET.equals(jwtSecret)) {
            violations.add("JWT_SECRET is the default development value — run: openssl rand -base64 32");
        }
        if (DEFAULT_SUPER_ADMIN_PASSWORD.equals(superAdminPassword)) {
            violations.add("SUPER_ADMIN_PASSWORD is the default value — set it in the K8s secret");
        }
        if (DEFAULT_OPERATOR_PASSWORD.equals(operatorPassword)) {
            violations.add("BACKEND_API_PASSWORD is 'operator-change-me' — set it in the K8s secret");
        }

        if (bridgeAuthRequired
                && (bridgeApiKey == null || bridgeApiKey.isBlank())) {
            violations.add(
                    "HELIOS_BRIDGE_API_KEY est requis lorsque HELIOS_BRIDGE_AUTH_REQUIRED=true");
        }

        if (violations.isEmpty()) {
            log.info("Security startup check PASSED — no default credentials detected.");
            return;
        }

        log.error("=======================================================================");
        log.error("SECURITY RISK — {} insecure default(s) detected:", violations.size());
        violations.forEach(v -> log.error("  • {}", v));
        log.error("=======================================================================");

        if (strictMode) {
            throw new IllegalStateException(
                "Startup aborted: " + violations.size() + " insecure default(s) detected. "
                + "Fix them or set SECURITY_STRICT_MODE=false to override (not recommended)."
            );
        }
    }
}
