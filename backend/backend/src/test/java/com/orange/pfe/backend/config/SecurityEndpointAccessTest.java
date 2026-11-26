package com.orange.pfe.backend.config;

import com.orange.pfe.backend.controller.FalcoWebhookController;
import com.orange.pfe.backend.controller.SseController;
import com.orange.pfe.backend.repository.AppUserRepository;
import com.orange.pfe.backend.repository.IncidentRepository;
import com.orange.pfe.backend.service.AIOpsOrchestrationService;
import com.orange.pfe.backend.service.FaultInjectionService;
import com.orange.pfe.backend.service.IncidentService;
import com.orange.pfe.backend.service.JwtService;
import com.orange.pfe.backend.service.SseEventService;
import com.orange.pfe.backend.service.SseTicketService;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.context.properties.EnableConfigurationProperties;
import org.springframework.boot.webmvc.test.autoconfigure.WebMvcTest;
import org.springframework.context.annotation.Import;
import org.springframework.http.MediaType;
import org.springframework.test.context.TestPropertySource;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.request.MockHttpServletRequestBuilder;
import org.springframework.web.servlet.mvc.method.annotation.SseEmitter;

import java.util.Optional;
import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.when;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;

/**
 * Accès ANONYMES à travers la VRAIE chaîne de filtres (SecurityConfig + JwtAuthFilter, filtres actifs), jetons factices.
 * Les services sont mockés ; aucune infrastructure réelle. Les actuateurs sont absents de cette tranche : un accès AUTORISÉ y
 * donne 404 (aucun handler), un accès REFUSÉ donne 401/403 ; les réponses réelles de /actuator sont couvertes par l'essai HTTP isolé.
 */
@WebMvcTest(controllers = {FalcoWebhookController.class, SseController.class, ActuatorStubController.class})
@Import({SecurityConfig.class, JwtAuthFilter.class, WebSecurityEnablerForTest.class})
@EnableConfigurationProperties(AppSecurityProperties.class)
@TestPropertySource(properties = {
        "helios.falco.webhook-token=jeton-falco-de-test",
        "helios.metrics.scrape-token=jeton-scrape-de-test",
        "app.security.cors-allowed-origins=http://localhost:4200"
})
class SecurityEndpointAccessTest {

    @Autowired private MockMvc mvc;
    @MockitoBean private SseTicketService tickets;
    @MockitoBean private JwtService jwtService;
    @MockitoBean private AppUserRepository appUserRepository;
    @MockitoBean private FaultInjectionService faultInjectionService;
    @MockitoBean private IncidentService incidentService;
    @MockitoBean private AIOpsOrchestrationService orchestrationService;
    @MockitoBean private IncidentRepository incidentRepository;
    @MockitoBean private SseEventService sseEventService;

    private static boolean denied(int s) { return s == 401 || s == 403; }
    private int status(MockHttpServletRequestBuilder b) throws Exception { return mvc.perform(b).andReturn().getResponse().getStatus(); }
    private MockHttpServletRequestBuilder falco(String token) {
        var b = post("/api/webhooks/falco").contentType(MediaType.APPLICATION_JSON).content("{}");
        return token == null ? b : b.header("X-Helios-Falco-Token", token);
    }

    // ── Falco ───────────────────────────────────────────────────────────────────────────────────────────────
    @Test void falco_withoutToken_401() throws Exception { assertThat(status(falco(null))).isEqualTo(401); }
    @Test void falco_wrongToken_401() throws Exception { assertThat(status(falco("mauvais"))).isEqualTo(401); }
    @Test void falco_rightToken_200() throws Exception { assertThat(status(falco("jeton-falco-de-test"))).isEqualTo(200); }

    // ── SSE : cookie-ticket obligatoire ─────────────────────────────────────────────────────────────────────
    private MockHttpServletRequestBuilder stream(String cookie) {
        var b = get("/api/events/stream");
        return cookie == null ? b : b.cookie(new jakarta.servlet.http.Cookie("helios_sse", cookie));
    }
    private com.orange.pfe.backend.model.user.AppUser user(com.orange.pfe.backend.model.user.UserRole role, java.util.Set<String> ns) {
        var u = org.mockito.Mockito.mock(com.orange.pfe.backend.model.user.AppUser.class);
        when(u.isEnabled()).thenReturn(true); when(u.getRole()).thenReturn(role); when(u.getAllowedNamespaces()).thenReturn(ns);
        return u;
    }

    @Test void sse_anonymousWithoutCookie_401() throws Exception { assertThat(status(stream(null))).isEqualTo(401); }
    @Test void sse_unknownOrForgedTicket_401() throws Exception { assertThat(status(stream("falsifie"))).isEqualTo(401); }
    @Test void sse_ticketRequestWithoutAuthentication_refused() throws Exception { assertThat(denied(status(post("/api/events/ticket")))).isTrue(); }
    @Test void sse_ticketInUrlIsIgnored() throws Exception {
        when(tickets.consume("dans-url")).thenReturn(Optional.of("alice"));
        assertThat(status(get("/api/events/stream").param("ticket", "dans-url"))).isEqualTo(401);
    }
    @Test void sse_validCookie_accepted_thenReuseRefused_andCookieCleared() throws Exception {
        when(tickets.consume("bon")).thenReturn(Optional.of("alice")).thenReturn(Optional.empty());
        var alice = user(com.orange.pfe.backend.model.user.UserRole.ADMIN, java.util.Set.of());
        when(appUserRepository.findByUsername("alice")).thenReturn(Optional.of(alice));
        when(sseEventService.subscribe(org.mockito.ArgumentMatchers.anySet(), org.mockito.ArgumentMatchers.any())).thenReturn(new SseEmitter());
        var r = mvc.perform(stream("bon")).andReturn().getResponse();
        assertThat(r.getStatus()).isEqualTo(200);
        var reuse = mvc.perform(stream("bon")).andReturn().getResponse();
        assertThat(reuse.getStatus()).isEqualTo(401);
        assertThat(reuse.getHeader("Set-Cookie")).contains("helios_sse=").contains("Max-Age=0").contains("HttpOnly");
    }
    @Test void sse_restrictedUser_isSubscribedToItsNamespacesOnly() throws Exception {
        when(tickets.consume("r")).thenReturn(Optional.of("bob"));
        var bob = user(com.orange.pfe.backend.model.user.UserRole.USER, java.util.Set.of("ns-a"));
        when(appUserRepository.findByUsername("bob")).thenReturn(Optional.of(bob));
        when(sseEventService.subscribe(org.mockito.ArgumentMatchers.anySet(), org.mockito.ArgumentMatchers.any())).thenReturn(new SseEmitter());
        assertThat(status(stream("r"))).isEqualTo(200);
        org.mockito.Mockito.verify(sseEventService).subscribe(java.util.Set.of("ns-a"), null);
    }
    @Test void sse_disabledOrMissingUser_refused() throws Exception {
        when(tickets.consume("x")).thenReturn(Optional.of("fantome"));
        when(appUserRepository.findByUsername("fantome")).thenReturn(Optional.empty());
        assertThat(status(stream("x"))).isEqualTo(401);
    }
    @Test void sse_otherEventsRoutes_requireAuthentication() throws Exception { assertThat(denied(status(get("/api/events/autre")))).isTrue(); }
    // ── Prometheus ──────────────────────────────────────────────────────────────────────────────────────────
    @Test void prometheus_anonymous_refused() throws Exception { assertThat(denied(status(get("/actuator/prometheus")))).isTrue(); }
    @Test void prometheus_wrongToken_refused() throws Exception { assertThat(denied(status(get("/actuator/prometheus").header("Authorization", "Bearer mauvais")))).isTrue(); }
    @Test void prometheus_scrapeToken_authorized() throws Exception { assertThat(status(get("/actuator/prometheus").header("Authorization", "Bearer jeton-scrape-de-test"))).isEqualTo(200); }
    @Test void prometheus_scrapeToken_doesNotOpenOtherActuators() throws Exception {
        assertThat(denied(status(get("/actuator/metrics").header("Authorization", "Bearer jeton-scrape-de-test")))).isTrue();
        assertThat(denied(status(get("/api/incidents").header("Authorization", "Bearer jeton-scrape-de-test")))).isTrue();
    }

    // ── Santé : sonde minimale publique, autres actuateurs protégés ─────────────────────────────────────────
    @Test void health_probe_isNotBlocked() throws Exception {
        assertThat(status(get("/actuator/health"))).isEqualTo(200);
        assertThat(status(get("/actuator/health/readiness"))).isEqualTo(200);
    }
    @Test void otherActuatorEndpoints_requireAuthentication() throws Exception {
        for (String p : new String[]{"/actuator/metrics", "/actuator/env", "/actuator/heapdump"}) { assertThat(denied(status(get(p)))).as(p).isTrue(); }
    }

    // ── Routes métier ───────────────────────────────────────────────────────────────────────────────────────
    @Test void businessEndpoints_refuseAnonymousCallers() throws Exception {
        for (String p : new String[]{"/api/incidents", "/api/workflows", "/api/users", "/api/audit/x", "/api/remediation/x"}) { assertThat(denied(status(get(p)))).as(p).isTrue(); }
        assertThat(denied(status(post("/api/incidents").contentType(MediaType.APPLICATION_JSON).content("{}")))).isTrue();
        assertThat(denied(status(post("/api/incidents/ingest/host").contentType(MediaType.APPLICATION_JSON).content("{}")))).isTrue();
    }
    @Test void bridgeWriteRoutes_refuseAnAbsentOrInvalidKey() throws Exception {
        assertThat(denied(status(post("/api/incidents/12/verification").header("X-HELIOS-Bridge-Key", "x").contentType(MediaType.APPLICATION_JSON).content("{}")))).isTrue();
    }
}