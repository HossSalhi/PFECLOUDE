package com.orange.pfe.backend.controller;

import com.orange.pfe.backend.model.user.AppUser;
import com.orange.pfe.backend.model.user.UserRole;
import com.orange.pfe.backend.repository.AppUserRepository;
import com.orange.pfe.backend.service.SseEventService;
import com.orange.pfe.backend.service.SseTicketService;
import jakarta.servlet.http.HttpServletRequest;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.security.web.util.matcher.IpAddressMatcher;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseCookie;
import org.springframework.http.ResponseEntity;
import org.springframework.security.core.Authentication;
import org.springframework.web.bind.annotation.CookieValue;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.servlet.mvc.method.annotation.SseEmitter;

import java.time.Duration;
import java.util.Arrays;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.Set;

/**
 * Flux SSE sécurisé. {@code EventSource} ne peut pas envoyer d'en-tête Authorization : l'utilisateur authentifié obtient d'abord
 * un TICKET à usage unique via {@code POST /api/events/ticket}, remis dans un COOKIE HttpOnly, SameSite=Strict, limité au
 * chemin du flux et de 30 s de durée : le ticket n'apparaît donc ni dans l'URL ni dans les journaux (nginx compris) et n'est pas
 * lisible par le JavaScript. Le ticket est partagé entre réplicas et consommé atomiquement (voir {@link SseTicketService}).
 */
@RestController
@RequestMapping("/api/events")
public class SseController {

    static final String COOKIE = "helios_sse";
    static final String COOKIE_PATH = "/api/events/stream";

    private final SseEventService sseEventService;
    private final SseTicketService ticketService;
    private final AppUserRepository userRepository;

    /** auto : Secure si la requête est en HTTPS, ou annoncée HTTPS par un proxy DE CONFIANCE ; true/false : forcé. */
    private final String cookieSecureMode;
    private final List<IpAddressMatcher> trustedProxies;

    public SseController(SseEventService sseEventService, SseTicketService ticketService, AppUserRepository userRepository,
                         @Value("${helios.sse.cookie-secure:auto}") String cookieSecureMode,
                         @Value("${helios.sse.trusted-proxy-cidrs:10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,127.0.0.0/8}") String trustedCidrs) {
        this.sseEventService = sseEventService;
        this.ticketService = ticketService;
        this.userRepository = userRepository;
        this.cookieSecureMode = cookieSecureMode == null ? "auto" : cookieSecureMode.trim().toLowerCase();
        this.trustedProxies = Arrays.stream(trustedCidrs.split(",")).map(String::trim).filter(s -> !s.isEmpty()).map(IpAddressMatcher::new).toList();
    }

    /** Ne fait confiance à X-Forwarded-Proto que s'il provient d'une adresse de proxy de confiance (jamais d'un client quelconque). */
    boolean secure(HttpServletRequest r) {
        if ("true".equals(cookieSecureMode)) return true;
        if ("false".equals(cookieSecureMode)) return r.isSecure();
        if (r.isSecure()) return true;
        String fwd = r.getHeader("X-Forwarded-Proto");
        if (fwd == null || !"https".equalsIgnoreCase(fwd.split(",")[0].trim())) return false;
        String remote = r.getRemoteAddr();
        try {
            return remote != null && trustedProxies.stream().anyMatch(m -> m.matches(remote));
        } catch (Exception e) {
            return false;
        }
    }

    private static ResponseCookie cookie(String value, long maxAgeSeconds, boolean secure) {
        return ResponseCookie.from(COOKIE, value).httpOnly(true).secure(secure).sameSite("Strict")
                .path(COOKIE_PATH).maxAge(Duration.ofSeconds(maxAgeSeconds)).build();
    }

    private static Long parseId(String s) {
        if (s == null || s.length() > 18) return null;
        try { long v = Long.parseLong(s.trim()); return v >= 0 ? v : null; } catch (NumberFormatException e) { return null; }
    }

    /** Réservé aux utilisateurs authentifiés (JWT dans l'en-tête, règle {@code anyRequest().authenticated()}). */
    @PostMapping("/ticket")
    public ResponseEntity<Map<String, Object>> ticket(Authentication authentication, HttpServletRequest request) {
        String ticket = ticketService.issue(authentication.getName());
        return ResponseEntity.ok()
                .header(HttpHeaders.SET_COOKIE, cookie(ticket, ticketService.ttlSeconds(), secure(request)).toString())
                .header(HttpHeaders.CACHE_CONTROL, "no-store")
                .body(Map.of("expiresInSeconds", ticketService.ttlSeconds()));
    }

    /** Accessible au sens de Spring Security, mais 401 sans cookie-ticket valide, non expiré et non encore utilisé. */
    @GetMapping(value = "/stream", produces = MediaType.TEXT_EVENT_STREAM_VALUE)
    public ResponseEntity<SseEmitter> stream(@CookieValue(name = COOKIE, required = false) String ticket, HttpServletRequest request,
                                             @RequestParam(name = "lastEventId", required = false) String lastEventIdParam,
                                             @RequestHeader(name = "Last-Event-ID", required = false) String lastEventIdHeader) {
        Optional<AppUser> user = ticketService.consume(ticket).flatMap(userRepository::findByUsername).filter(AppUser::isEnabled);
        String clear = cookie("", 0, secure(request)).toString();
        if (user.isEmpty()) {
            return ResponseEntity.status(HttpStatus.UNAUTHORIZED).header(HttpHeaders.SET_COOKIE, clear).build();
        }
        // Même règle que TenantContext : un USER avec des espaces autorisés ne reçoit que ceux-ci ; ADMIN/SUPER_ADMIN : tout.
        Set<String> scope = Set.of();
        if (user.get().getRole() == UserRole.USER && user.get().getAllowedNamespaces() != null) {
            scope = user.get().getAllowedNamespaces();
        }
        return ResponseEntity.ok().header(HttpHeaders.SET_COOKIE, clear).body(sseEventService.subscribe(scope, parseId(lastEventIdHeader != null ? lastEventIdHeader : lastEventIdParam)));
    }
}