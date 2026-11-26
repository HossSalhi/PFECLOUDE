package com.orange.pfe.backend.config;

import org.junit.jupiter.api.Test;
import org.springframework.test.util.ReflectionTestUtils;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatCode;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

/** Mode strict : refus des valeurs par défaut, des clés Bridge/Falco manquantes ; démarrage normal sinon. */
class SecurityStartupValidatorStrictModeTest {

    private static String def(String name) { return (String) ReflectionTestUtils.getField(SecurityStartupValidator.class, name); }

    private SecurityStartupValidator v(boolean strict, String jwt, String admin, String operator) {
        SecurityStartupValidator v = new SecurityStartupValidator();
        ReflectionTestUtils.setField(v, "strictMode", strict);
        ReflectionTestUtils.setField(v, "jwtSecret", jwt);
        ReflectionTestUtils.setField(v, "superAdminPassword", admin);
        ReflectionTestUtils.setField(v, "operatorPassword", operator);
        ReflectionTestUtils.setField(v, "bridgeApiKey", "");
        ReflectionTestUtils.setField(v, "bridgeAuthRequired", false);
        ReflectionTestUtils.setField(v, "falcoWebhookToken", "");
        ReflectionTestUtils.setField(v, "metricsScrapeToken", "");
        return v;
    }

    private SecurityStartupValidator good(boolean strict) { return v(strict, "autre-secret-jwt-de-test", "autre-mdp-admin-de-test", "autre-mdp-operateur-de-test"); }

    @Test void strict_refusesTheDefaultJwtSecret() {
        assertThatThrownBy(() -> v(true, def("DEFAULT_JWT_SECRET"), "x1", "x2").validateSecurityConfiguration()).isInstanceOf(IllegalStateException.class);
    }
    @Test void strict_refusesTheDefaultAdminPassword() {
        assertThatThrownBy(() -> v(true, "j", def("DEFAULT_SUPER_ADMIN_PASSWORD"), "x2").validateSecurityConfiguration()).isInstanceOf(IllegalStateException.class);
    }
    @Test void strict_refusesTheDefaultOperatorPassword() {
        assertThatThrownBy(() -> v(true, "j", "x1", def("DEFAULT_OPERATOR_PASSWORD")).validateSecurityConfiguration()).isInstanceOf(IllegalStateException.class);
    }
    @Test void strict_acceptsExternalSecrets() {
        assertThatCode(() -> good(true).validateSecurityConfiguration()).doesNotThrowAnyException();
    }
    @Test void nonStrict_onlyWarns_withDefaults() {
        assertThatCode(() -> v(false, def("DEFAULT_JWT_SECRET"), def("DEFAULT_SUPER_ADMIN_PASSWORD"), def("DEFAULT_OPERATOR_PASSWORD")).validateSecurityConfiguration()).doesNotThrowAnyException();
    }
    @Test void strict_refusesBridgeAuthRequiredWithoutKey() {
        SecurityStartupValidator s = good(true); ReflectionTestUtils.setField(s, "bridgeAuthRequired", true);
        assertThatThrownBy(s::validateSecurityConfiguration).isInstanceOf(IllegalStateException.class);
    }    @Test void missingFalcoAndScrapeTokens_doNotBlockStartup_failClosedInstead() {
        assertThatCode(() -> good(true).validateSecurityConfiguration()).doesNotThrowAnyException();
    }
    @Test void validatorMessagesNeverContainTheSecretValues() {
        try { v(true, def("DEFAULT_JWT_SECRET"), def("DEFAULT_SUPER_ADMIN_PASSWORD"), "x").validateSecurityConfiguration(); }
        catch (IllegalStateException e) {
            assertThat(e.getMessage()).doesNotContain(def("DEFAULT_JWT_SECRET")).doesNotContain(def("DEFAULT_SUPER_ADMIN_PASSWORD"));
        }
    }
}