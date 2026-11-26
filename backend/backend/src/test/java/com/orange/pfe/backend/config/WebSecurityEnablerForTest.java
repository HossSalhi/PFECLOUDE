package com.orange.pfe.backend.config;

import org.springframework.boot.web.servlet.FilterRegistrationBean;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.security.config.annotation.web.configuration.EnableWebSecurity;

/**
 * Active Spring Security dans les tranches @WebMvcTest (top-level : une @Configuration imbriquée désactiverait le scan) et
 * retire l'enregistrement SERVLET de JwtAuthFilter : MockMvc l'exécuterait avant la chaîne de sécurité, qui effacerait son
 * authentification (artefact de test ; en exécution réelle la chaîne de sécurité passe en premier).
 */
@Configuration
@EnableWebSecurity
class WebSecurityEnablerForTest {
    @Bean
    FilterRegistrationBean<JwtAuthFilter> disableJwtServletRegistration(JwtAuthFilter filter) {
        FilterRegistrationBean<JwtAuthFilter> r = new FilterRegistrationBean<>(filter);
        r.setEnabled(false);
        return r;
    }
}