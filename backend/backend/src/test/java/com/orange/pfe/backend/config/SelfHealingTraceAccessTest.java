package com.orange.pfe.backend.config;

import com.orange.pfe.backend.controller.AuditTrailController;
import com.orange.pfe.backend.controller.SelfHealingTraceController;
import com.orange.pfe.backend.dto.audit.SelfHealingDtos.ActionLogs;
import com.orange.pfe.backend.dto.audit.SelfHealingDtos.Listing;
import com.orange.pfe.backend.model.user.AppUser;
import com.orange.pfe.backend.repository.AppUserRepository;
import com.orange.pfe.backend.service.JwtService;
import com.orange.pfe.backend.service.audit.AuditTrailQueryService;
import com.orange.pfe.backend.service.audit.SelfHealingTraceService;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.context.properties.EnableConfigurationProperties;
import org.springframework.boot.webmvc.test.autoconfigure.WebMvcTest;
import org.springframework.context.annotation.Import;
import org.springframework.test.context.TestPropertySource;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.request.MockHttpServletRequestBuilder;

import java.time.LocalDateTime;
import java.util.List;
import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyInt;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.delete;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;

/** Routes de la trace d'auto-remédiation : authentification exigée, lecture seule, et pas de conflit avec /api/audit-trail/{id}. */
@WebMvcTest(controllers = {SelfHealingTraceController.class, AuditTrailController.class})
@Import({SecurityConfig.class, JwtAuthFilter.class, WebSecurityEnablerForTest.class})
@EnableConfigurationProperties(AppSecurityProperties.class)
@TestPropertySource(properties = "app.security.cors-allowed-origins=http://localhost:4200")
class SelfHealingTraceAccessTest {

    @Autowired private MockMvc mvc;
    @MockitoBean private JwtService jwtService;
    @MockitoBean private AppUserRepository users;
    @MockitoBean private com.orange.pfe.backend.service.FaultInjectionService faultInjectionService;
    @MockitoBean private SelfHealingTraceService service;
    @MockitoBean private AuditTrailQueryService auditQuery;

    @SuppressWarnings({"unchecked", "rawtypes"})
    private MockHttpServletRequestBuilder as(String role, MockHttpServletRequestBuilder b) {
        AppUser u = mock(AppUser.class);
        when(u.isEnabled()).thenReturn(true);
        when(u.getAuthorities()).thenReturn((java.util.Collection) List.of(
                new org.springframework.security.core.authority.SimpleGrantedAuthority("ROLE_" + role)));
        when(jwtService.isTokenValid("tok-" + role)).thenReturn(true);
        when(jwtService.extractUsername("tok-" + role)).thenReturn("u-" + role);
        when(users.findByUsername("u-" + role)).thenReturn(Optional.of(u));
        return b.header("Authorization", "Bearer tok-" + role);
    }

    private int status(MockHttpServletRequestBuilder b) throws Exception {
        return mvc.perform(b).andReturn().getResponse().getStatus();
    }

    @Test
    void theListRouteIsReachedAndNotMistakenForAnAuditEntryId() throws Exception {
        when(service.list(any(), any(), any(), anyInt())).thenReturn(new Listing(LocalDateTime.now().minusDays(1), LocalDateTime.now(), false, List.of()));

        var res = mvc.perform(as("ADMIN", get("/api/audit-trail/self-healing"))).andReturn().getResponse();

        assertThat(res.getStatus()).isEqualTo(200);
        assertThat(res.getContentAsString()).contains("\"items\":[]");
    }

    @Test
    void theLogsRouteReturnsTheServiceAnswer() throws Exception {
        when(service.actionLogs(any(), any(), any())).thenReturn(new ActionLogs(true, null, "VM worker1", LocalDateTime.now(), LocalDateTime.now(), false, List.of()));

        var res = mvc.perform(as("ADMIN", get("/api/audit-trail/self-healing/7/actions/9/logs"))).andReturn().getResponse();

        assertThat(res.getStatus()).isEqualTo(200);
        assertThat(res.getContentAsString()).contains("VM worker1");
    }

    @Test
    void anAnonymousCallerIsRefused() throws Exception {
        for (String path : new String[]{"/api/audit-trail/self-healing", "/api/audit-trail/self-healing/7", "/api/audit-trail/self-healing/7/actions/9/logs"}) {
            int s = status(get(path));
            assertThat(s == 401 || s == 403).as(path).isTrue();
        }
    }

    @Test
    void thereIsNoWriteRouteAndABadDateIsRefused() throws Exception {
        assertThat(status(as("ADMIN", post("/api/audit-trail/self-healing")))).isGreaterThanOrEqualTo(400);
        assertThat(status(as("ADMIN", delete("/api/audit-trail/self-healing/7")))).isGreaterThanOrEqualTo(400);
        assertThat(status(as("ADMIN", get("/api/audit-trail/self-healing?from=pas-une-date")))).isEqualTo(400);
    }
}
