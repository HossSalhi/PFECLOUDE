package com.orange.pfe.backend.config;

import com.orange.pfe.backend.controller.SupervisionController;
import com.orange.pfe.backend.dto.supervision.SupervisionDtos.HostsOverview;
import com.orange.pfe.backend.model.user.AppUser;
import com.orange.pfe.backend.repository.AppUserRepository;
import com.orange.pfe.backend.service.JwtService;
import com.orange.pfe.backend.service.supervision.SupervisionService;
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
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.delete;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;

/** Supervision : lecture ouverte à tout utilisateur connecté (USER, ADMIN, SUPER_ADMIN), jamais aux anonymes, jamais en écriture. */
@WebMvcTest(controllers = SupervisionController.class)
@Import({SecurityConfig.class, JwtAuthFilter.class, WebSecurityEnablerForTest.class})
@EnableConfigurationProperties(AppSecurityProperties.class)
@TestPropertySource(properties = "app.security.cors-allowed-origins=http://localhost:4200")
class SupervisionAccessTest {

    @Autowired private MockMvc mvc;
    @MockitoBean private JwtService jwtService;
    @MockitoBean private AppUserRepository users;
    @MockitoBean private com.orange.pfe.backend.service.FaultInjectionService faultInjectionService;
    @MockitoBean private SupervisionService supervision;
    @MockitoBean private com.orange.pfe.backend.service.supervision.CamundaSupervisionService camunda;
    @MockitoBean private com.orange.pfe.backend.service.supervision.GrafanaConfigService grafana;

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
    void everyConnectedRoleCanReadTheSupervision() throws Exception {
        when(supervision.hosts()).thenReturn(new HostsOverview(true, null, LocalDateTime.now(), List.of(), List.of()));
        for (String role : new String[]{"USER", "ADMIN", "SUPER_ADMIN"}) {
            assertThat(status(as(role, get("/api/supervision/hosts")))).as(role).isEqualTo(200);
            assertThat(status(as(role, get("/api/supervision/logs/meanings")))).as(role).isEqualTo(200);
        }
    }

    @Test
    void anAnonymousCallerIsRefused() throws Exception {
        int s = status(get("/api/supervision/hosts"));

        assertThat(s == 401 || s == 403).isTrue();
    }

    @Test
    void thereIsNoWriteAccess() throws Exception {
        assertThat(status(as("ADMIN", post("/api/supervision/hosts")))).isGreaterThanOrEqualTo(400);
        assertThat(status(as("SUPER_ADMIN", delete("/api/supervision/hosts")))).isGreaterThanOrEqualTo(400);
    }

    @Test
    void invalidParametersAreRefusedNotPassedOn() throws Exception {
        assertThat(status(as("USER", get("/api/supervision/series?metric=EVIL")))).isEqualTo(400);
    }
}
