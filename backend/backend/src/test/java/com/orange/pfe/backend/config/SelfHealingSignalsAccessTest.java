package com.orange.pfe.backend.config;

import com.orange.pfe.backend.controller.SelfHealingSignalsController;
import com.orange.pfe.backend.dto.audit.SelfHealingDtos.CamundaTrace;
import com.orange.pfe.backend.model.user.AppUser;
import com.orange.pfe.backend.repository.AppUserRepository;
import com.orange.pfe.backend.service.JwtService;
import com.orange.pfe.backend.service.audit.SelfHealingAlertsService;
import com.orange.pfe.backend.service.audit.SelfHealingCamundaTraceService;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.context.properties.EnableConfigurationProperties;
import org.springframework.boot.webmvc.test.autoconfigure.WebMvcTest;
import org.springframework.context.annotation.Import;
import org.springframework.test.context.TestPropertySource;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.request.MockHttpServletRequestBuilder;

import java.util.List;
import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.delete;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;

/** Routes alertes, signaux de logs et trace Camunda : authentification exigée, lecture seule. */
@WebMvcTest(controllers = SelfHealingSignalsController.class)
@Import({SecurityConfig.class, JwtAuthFilter.class, WebSecurityEnablerForTest.class})
@EnableConfigurationProperties(AppSecurityProperties.class)
@TestPropertySource(properties = "app.security.cors-allowed-origins=http://localhost:4200")
class SelfHealingSignalsAccessTest {

    @Autowired private MockMvc mvc;
    @MockitoBean private JwtService jwtService;
    @MockitoBean private AppUserRepository users;
    @MockitoBean private com.orange.pfe.backend.service.FaultInjectionService faultInjectionService;
    @MockitoBean private SelfHealingAlertsService alerts;
    @MockitoBean private SelfHealingCamundaTraceService camunda;

    @SuppressWarnings({"unchecked", "rawtypes"})
    private MockHttpServletRequestBuilder asAdmin(MockHttpServletRequestBuilder b) {
        AppUser u = mock(AppUser.class);
        when(u.isEnabled()).thenReturn(true);
        when(u.getAuthorities()).thenReturn((java.util.Collection) List.of(
                new org.springframework.security.core.authority.SimpleGrantedAuthority("ROLE_ADMIN")));
        when(jwtService.isTokenValid("tok")).thenReturn(true);
        when(jwtService.extractUsername("tok")).thenReturn("admin");
        when(users.findByUsername("admin")).thenReturn(Optional.of(u));
        return b.header("Authorization", "Bearer tok");
    }

    private int status(MockHttpServletRequestBuilder b) throws Exception {
        return mvc.perform(b).andReturn().getResponse().getStatus();
    }

    @Test
    void theCamundaRouteReturnsTheServiceAnswer() throws Exception {
        when(camunda.trace(any(), any())).thenReturn(new CamundaTrace(true, null, List.of()));

        var res = mvc.perform(asAdmin(get("/api/audit-trail/self-healing/7/camunda"))).andReturn().getResponse();

        assertThat(res.getStatus()).isEqualTo(200);
        assertThat(res.getContentAsString()).contains("\"runs\":[]");
    }

    @Test
    void anAnonymousCallerIsRefusedOnEveryRoute() throws Exception {
        for (String path : new String[]{"/alerts", "/log-signals", "/camunda"}) {
            int s = status(get("/api/audit-trail/self-healing/7" + path));
            assertThat(s == 401 || s == 403).as(path).isTrue();
        }
    }

    @Test
    void thereIsNoWriteRoute() throws Exception {
        assertThat(status(asAdmin(post("/api/audit-trail/self-healing/7/alerts")))).isGreaterThanOrEqualTo(400);
        assertThat(status(asAdmin(delete("/api/audit-trail/self-healing/7/camunda")))).isGreaterThanOrEqualTo(400);
    }
}
