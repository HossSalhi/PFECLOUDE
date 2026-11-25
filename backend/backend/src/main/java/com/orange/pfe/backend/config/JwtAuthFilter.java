package com.orange.pfe.backend.config;

import com.orange.pfe.backend.repository.AppUserRepository;
import com.orange.pfe.backend.service.JwtService;
import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import org.springframework.security.authentication.UsernamePasswordAuthenticationToken;
import org.springframework.security.core.context.SecurityContextHolder;
import org.springframework.security.core.userdetails.UserDetails;
import org.springframework.security.web.authentication.WebAuthenticationDetailsSource;
import org.springframework.stereotype.Component;
import org.springframework.web.filter.OncePerRequestFilter;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.List;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.security.core.authority.SimpleGrantedAuthority;

@Component
public class JwtAuthFilter extends OncePerRequestFilter {

    private final JwtService jwtService;
    private final AppUserRepository userRepository;

    // Cl? de service facultative : fournie uniquement par l'environnement.
    @Value("${helios.bridge.api-key:}")
    private String bridgeApiKey = "";

    public JwtAuthFilter(JwtService jwtService, AppUserRepository userRepository) {
        this.jwtService = jwtService;
        this.userRepository = userRepository;
    }

    private boolean isBridgeWriteRequest(HttpServletRequest request) {
        if (!"POST".equalsIgnoreCase(request.getMethod())) {
            return false;
        }

        String path = request.getServletPath();

        // Certains environnements de test renseignent requestURI sans servletPath.
        // On utilise l'URI comme fallback, en retirant le contexte applicatif.
        if (path == null || path.isBlank()) {
            path = request.getRequestURI();
            if (path == null || path.isBlank()) {
                return false;
            }

            String contextPath = request.getContextPath();
            if (contextPath != null && !contextPath.isEmpty()) {
                if (!path.equals(contextPath) && !path.startsWith(contextPath + "/")) {
                    return false;
                }
                path = path.substring(contextPath.length());
            }
        }

        return "/api/incidents/ingest/host".equals(path)
                || path.matches("^/api/incidents/[0-9]+/execution-event$")
                || path.matches("^/api/incidents/[0-9]+/evidence/collect$")
                || path.matches("^/api/incidents/[0-9]+/verification$");
    }

    private boolean isValidBridgeKey(String supplied) {
        if (bridgeApiKey == null || bridgeApiKey.isBlank()
                || supplied == null || supplied.isBlank()) {
            return false;
        }
        return MessageDigest.isEqual(
                bridgeApiKey.getBytes(StandardCharsets.UTF_8),
                supplied.getBytes(StandardCharsets.UTF_8));
    }

    @Override
    protected void doFilterInternal(
            HttpServletRequest request,
            HttpServletResponse response,
            FilterChain filterChain
    ) throws ServletException, IOException {

        // Identit? technique limit?e aux routes d'?criture des cartes HELIOS.
        // Une cl? absente ou invalide ne contourne jamais l'authentification JWT.
        if (isBridgeWriteRequest(request)
                && isValidBridgeKey(request.getHeader("X-HELIOS-Bridge-Key"))) {
            UsernamePasswordAuthenticationToken bridgeAuth =
                    new UsernamePasswordAuthenticationToken(
                            "HELIOS_BRIDGE",
                            null,
                            List.of(new SimpleGrantedAuthority("ROLE_HELIOS_BRIDGE")));
            SecurityContextHolder.getContext().setAuthentication(bridgeAuth);
            filterChain.doFilter(request, response);
            return;
        }

        final String authHeader = request.getHeader("Authorization");

        if (authHeader == null || !authHeader.startsWith("Bearer ")) {
            filterChain.doFilter(request, response);
            return;
        }

        final String token = authHeader.substring(7);

        try {
            if (!jwtService.isTokenValid(token)) {
                filterChain.doFilter(request, response);
                return;
            }

            String username = jwtService.extractUsername(token);

            if (username != null && SecurityContextHolder.getContext().getAuthentication() == null) {
                UserDetails user = userRepository.findByUsername(username).orElse(null);
                if (user != null && user.isEnabled()) {
                    UsernamePasswordAuthenticationToken authToken =
                            new UsernamePasswordAuthenticationToken(user, null, user.getAuthorities());
                    authToken.setDetails(new WebAuthenticationDetailsSource().buildDetails(request));
                    SecurityContextHolder.getContext().setAuthentication(authToken);
                }
            }
        } catch (Exception e) {
            // Invalid token — continue without authentication
        }

        filterChain.doFilter(request, response);
    }
}
