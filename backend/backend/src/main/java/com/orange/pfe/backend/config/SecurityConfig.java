package com.orange.pfe.backend.config;

import com.orange.pfe.backend.repository.AppUserRepository;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.http.HttpMethod;
import org.springframework.security.authentication.AuthenticationManager;
import org.springframework.security.authentication.AuthenticationProvider;
import org.springframework.security.authentication.dao.DaoAuthenticationProvider;
import org.springframework.security.config.annotation.authentication.configuration.AuthenticationConfiguration;
import org.springframework.security.config.annotation.method.configuration.EnableMethodSecurity;
import org.springframework.security.config.annotation.web.builders.HttpSecurity;
import org.springframework.security.config.annotation.web.configurers.AbstractHttpConfigurer;
import org.springframework.security.config.http.SessionCreationPolicy;
import org.springframework.security.core.userdetails.UserDetailsService;
import org.springframework.security.core.userdetails.UsernameNotFoundException;
import org.springframework.security.crypto.factory.PasswordEncoderFactories;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.security.web.SecurityFilterChain;
import org.springframework.security.web.authentication.UsernamePasswordAuthenticationFilter;
import org.springframework.web.cors.CorsConfiguration;
import org.springframework.web.cors.CorsConfigurationSource;
import org.springframework.web.cors.UrlBasedCorsConfigurationSource;

import java.util.Arrays;
import java.util.List;

/**
 * Rôles :
 *   SUPER_ADMIN — accès total (lecture + écriture + admin + gestion users)
 *   ADMIN       — gestion incidents/actions/workflows/tâches + fine-tuning
 *   USER        — lecture seule + approbation des tâches humaines
 */
@Configuration
@EnableMethodSecurity
public class SecurityConfig {

    @Bean
    public PasswordEncoder passwordEncoder() {
        return PasswordEncoderFactories.createDelegatingPasswordEncoder();
    }

    @Bean
    public UserDetailsService userDetailsService(AppUserRepository userRepository) {
        return username -> userRepository.findByUsername(username)
                .orElseThrow(() -> new UsernameNotFoundException("User not found: " + username));
    }

    @Bean
    public AuthenticationProvider authenticationProvider(UserDetailsService userDetailsService,
                                                         PasswordEncoder passwordEncoder) {
        DaoAuthenticationProvider provider = new DaoAuthenticationProvider(userDetailsService);
        provider.setPasswordEncoder(passwordEncoder);
        return provider;
    }

    @Bean
    public AuthenticationManager authenticationManager(AuthenticationConfiguration config) throws Exception {
        return config.getAuthenticationManager();
    }

    @Bean
    public CorsConfigurationSource corsConfigurationSource(AppSecurityProperties props) {
        CorsConfiguration config = new CorsConfiguration();
        List<String> origins = Arrays.stream(props.getCorsAllowedOrigins().split(","))
                .map(String::trim).filter(s -> !s.isBlank()).toList();
        config.setAllowedOrigins(origins);
        config.setAllowedMethods(List.of("GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"));
        config.setAllowedHeaders(List.of("Authorization", "Content-Type", "X-Requested-With"));
        config.setExposedHeaders(List.of("Authorization", "Content-Type", "X-Total-Count"));
        config.setAllowCredentials(true);
        config.setMaxAge(3600L);
        UrlBasedCorsConfigurationSource source = new UrlBasedCorsConfigurationSource();
        source.registerCorsConfiguration("/**", config);
        return source;
    }

    @Bean
    public SecurityFilterChain securityFilterChain(HttpSecurity http,
                                                   AppSecurityProperties props,
                                                   JwtAuthFilter jwtAuthFilter,
                                                   AuthenticationProvider authProvider) throws Exception {
        http
            .cors(cors -> cors.configurationSource(corsConfigurationSource(props)))
            .csrf(AbstractHttpConfigurer::disable)
            .sessionManagement(session -> session.sessionCreationPolicy(SessionCreationPolicy.STATELESS))
            .headers(headers -> headers
                .contentSecurityPolicy(csp -> csp.policyDirectives(
                    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; " +
                    "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'"))
                .frameOptions(frame -> frame.deny())
                .httpStrictTransportSecurity(hsts -> hsts
                    .includeSubDomains(true).maxAgeInSeconds(31_536_000))
                .contentTypeOptions(ct -> {})
            )
            .authenticationProvider(authProvider)
            .addFilterBefore(jwtAuthFilter, UsernamePasswordAuthenticationFilter.class)
            .authorizeHttpRequests(auth -> {

                // ── Endpoints publics ────────────────────────────────────────
                auth.requestMatchers(
                        "/error",
                        "/api/auth/login",
                        "/api/auth/register",
                        "/api/auth/activate",
                        "/api/auth/forgot-password",
                        "/api/auth/reset-password",
                        "/api/auth/2fa/verify",
                        "/api/auth/refresh",
                        "/api/test/health-readiness",
                        "/api/test/health-liveness",
                        "/actuator/health",
                        "/actuator/health/**",
                        "/actuator/info",
                        "/api/events/stream",   // flux SSE : accès par TICKET à usage unique validé dans le contrôleur
                        "/api/webhooks/falco"   // authentifié par jeton partagé dans le contrôleur (fermé par défaut)
                ).permitAll();

                // Supervision : uniquement le jeton de scrape dédié (ROLE_METRICS, voir JwtAuthFilter) ou un SUPER_ADMIN.
                auth.requestMatchers(HttpMethod.GET, "/actuator/prometheus").hasAnyRole("METRICS", "SUPER_ADMIN");

                // Swagger (si activé)
                if (props.isExposeApiDocs()) {
                    auth.requestMatchers(
                            "/v3/api-docs/**",
                            "/swagger-ui/**",
                            "/swagger-ui.html"
                    ).permitAll();
                }

                // Liens de supervision (Prometheus, Grafana, Loki, Camunda) : ADMIN et SUPER_ADMIN seulement. Cette règle doit
                // rester AVANT la règle « /api/infrastructure/** » ouverte au rôle USER (la première règle qui correspond gagne).
                auth.requestMatchers(HttpMethod.GET, "/api/infrastructure/monitoring-links").hasAnyRole("ADMIN", "SUPER_ADMIN");

                // Lignes brutes de logs : administrateurs seulement (peuvent contenir des informations sensibles).
                auth.requestMatchers(HttpMethod.GET, "/api/supervision/logs/lines", "/api/supervision/logs/sources").hasAnyRole("ADMIN", "SUPER_ADMIN");

                // Supervision (métriques, logs, rapprochement avec les incidents) : lecture seule, tout utilisateur connecté.
                auth.requestMatchers(HttpMethod.GET, "/api/supervision/**").hasAnyRole("USER", "ADMIN", "SUPER_ADMIN");

                // ── USER : lecture seule ─────────────────────────────────────
                auth.requestMatchers(HttpMethod.GET,
                        "/api/incidents",
                        "/api/incidents/**",
                        "/api/actions",
                        "/api/actions/**",
                        "/api/workflows",
                        "/api/workflows/**",
                        "/api/tasks",
                        "/api/tasks/pending",
                        "/api/dashboard/**",
                        "/api/infrastructure/**",
                        "/api/remediation/**",
                        "/api/feedback/**"
                ).hasAnyRole("USER", "ADMIN", "SUPER_ADMIN");

                // ── ADMIN : décisions tâches humaines ────────────────────────
                auth.requestMatchers(HttpMethod.POST,
                        "/api/tasks/*/approve",
                        "/api/tasks/*/reject",
                        "/api/tasks/*/escalate"
                ).hasAnyRole("ADMIN", "SUPER_ADMIN");

                // Profil + déconnexion : tout utilisateur authentifié
                auth.requestMatchers(
                        "/api/auth/me",
                        "/api/auth/logout",
                        "/api/auth/change-password"
                ).authenticated();

                // ── ADMIN : gestion incidents/actions/workflows ───────────────
                auth.requestMatchers(HttpMethod.POST,
                        "/api/feedback/**"
                ).hasAnyRole("USER", "ADMIN", "SUPER_ADMIN");

                // Routes d'?criture limit?es au service Bridge HELIOS.
                auth.requestMatchers(HttpMethod.POST,
                        "/api/incidents/ingest/host",
                        "/api/incidents/*/execution-event",
                        "/api/incidents/*/evidence/collect",
                        "/api/incidents/*/verification"
                ).hasAnyRole("ADMIN", "SUPER_ADMIN", "HELIOS_BRIDGE");

                auth.requestMatchers(HttpMethod.POST,
                        "/api/incidents",
                        "/api/actions",
                        "/api/workflows/**"
                ).hasAnyRole("ADMIN", "SUPER_ADMIN");

                auth.requestMatchers(HttpMethod.PUT,
                        "/api/incidents/**",
                        "/api/actions/**"
                ).hasAnyRole("ADMIN", "SUPER_ADMIN");

                auth.requestMatchers(HttpMethod.PATCH,
                        "/api/incidents/**"
                ).hasAnyRole("ADMIN", "SUPER_ADMIN");

                auth.requestMatchers(HttpMethod.DELETE,
                        "/api/incidents/**"
                ).hasAnyRole("ADMIN", "SUPER_ADMIN");

                auth.requestMatchers(
                        "/api/audit/**"
                ).hasAnyRole("ADMIN", "SUPER_ADMIN");

                // ── SUPER_ADMIN uniquement ────────────────────────────────────
                auth.requestMatchers(
                        "/actuator/**",
                        "/api/test/**"
                ).hasRole("SUPER_ADMIN");

                // User management: ADMIN can read + block/unblock USERs; SUPER_ADMIN has full access
                // Fine-grained checks are enforced at controller level with @PreAuthorize
                auth.requestMatchers("/api/users/**").hasAnyRole("ADMIN", "SUPER_ADMIN");

                // Tout le reste : authentifié
                auth.anyRequest().authenticated();
            });

        return http.build();
    }
}
