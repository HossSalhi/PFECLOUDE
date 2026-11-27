package com.orange.pfe.backend.config;

import com.orange.pfe.backend.controller.SupervisionLogsController;
import com.orange.pfe.backend.dto.supervision.SupervisionDtos.LogLines;
import com.orange.pfe.backend.dto.supervision.SupervisionDtos.LogSources;
import com.orange.pfe.backend.model.user.AppUser;
import com.orange.pfe.backend.repository.AppUserRepository;
import com.orange.pfe.backend.service.JwtService;
import com.orange.pfe.backend.service.supervision.LogViewerService;
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
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;

/** Les lignes brutes de logs sont réservées aux administrateurs ; un utilisateur ordinaire et un anonyme sont refusés. */
@WebMvcTest(controllers = SupervisionLogsController.class)
@Import({SecurityConfig.class, JwtAuthFilter.class, WebSecurityEnablerForTest.class})
@EnableConfigurationProperties(AppSecurityProperties.class)
@TestPropertySource(properties = "app.security.cors-allowed-origins=http://localhost:4200")
class SupervisionLogsAccessTest {

    @Autowired private MockMvc mvc;
    @MockitoBean private JwtService jwtService;
    @MockitoBean private AppUserRepository users;
    @MockitoBean private com.orange.pfe.backend.service.FaultInjectionService faultInjectionService;
    @MockitoBean private LogViewerService viewer;

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
    void administratorsCanReadSourcesAndLines() throws Exception {
        when(viewer.sources()).thenReturn(new LogSources(true, null, List.of()));
        when(viewer.lines(any(), any(), any(), anyInt(), anyInt())).thenReturn(
                new LogLines(true, null, "host:master", "ALL", "", 30, LocalDateTime.now(), LocalDateTime.now(), false, 0, List.of()));

        for (String role : new String[]{"ADMIN", "SUPER_ADMIN"}) {
            assertThat(status(as(role, get("/api/supervision/logs/sources")))).as(role).isEqualTo(200);
            assertThat(status(as(role, get("/api/supervision/logs/lines?source=host:master")))).as(role).isEqualTo(200);
        }
    }

    @Test
    void anOrdinaryUserIsRefusedOnRawLogs() throws Exception {
        assertThat(status(as("USER", get("/api/supervision/logs/sources")))).isEqualTo(403);
        assertThat(status(as("USER", get("/api/supervision/logs/lines?source=host:master")))).isEqualTo(403);
    }

    @Test
    void anAnonymousCallerIsRefused() throws Exception {
        int s = status(get("/api/supervision/logs/lines?source=host:master"));

        assertThat(s == 401 || s == 403).isTrue();
    }

    @Test
    void thereIsNoWriteRouteAndTheSourceIsRequired() throws Exception {
        assertThat(status(as("ADMIN", post("/api/supervision/logs/lines")))).isGreaterThanOrEqualTo(400);
        // sans source, le service refuse (IllegalArgumentException) et l'API répond 400, pas 500
        when(viewer.lines(org.mockito.ArgumentMatchers.isNull(), any(), any(), anyInt(), anyInt()))
                .thenThrow(new IllegalArgumentException("Source de logs invalide"));
        assertThat(status(as("ADMIN", get("/api/supervision/logs/lines")))).isEqualTo(400);
    }
}
