package com.orange.pfe.backend.config;

import com.orange.pfe.backend.controller.InfrastructureController;
import com.orange.pfe.backend.model.user.AppUser;
import com.orange.pfe.backend.model.user.UserRole;
import com.orange.pfe.backend.repository.AppUserRepository;
import com.orange.pfe.backend.service.InfrastructureHealthService;
import com.orange.pfe.backend.service.JwtService;
import com.orange.pfe.backend.service.KubernetesDiscoveryService;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.context.properties.EnableConfigurationProperties;
import org.springframework.boot.webmvc.test.autoconfigure.WebMvcTest;
import org.springframework.context.annotation.Import;
import org.springframework.test.context.TestPropertySource;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.request.MockHttpServletRequestBuilder;

import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;

/**
 * Liens de supervision : réservés à ADMIN et SUPER_ADMIN, à travers la VRAIE chaîne de filtres (SecurityConfig +
 * JwtAuthFilter), le jeton étant simulé par rôle. Un utilisateur ordinaire et un anonyme sont refusés, alors que les
 * autres routes d'infrastructure (lecture) restent ouvertes au rôle USER.
 */
@WebMvcTest(controllers = InfrastructureController.class)
@Import({SecurityConfig.class, JwtAuthFilter.class, WebSecurityEnablerForTest.class})
@EnableConfigurationProperties({AppSecurityProperties.class, InfraHealthProperties.class})
@TestPropertySource(properties = {
        "app.security.cors-allowed-origins=http://localhost:4200",
        "infra.health.prometheus-url=http://prom.test:9090",
        "infra.health.grafana-url=http://graf.test:3000",
        "infra.health.loki-url=http://loki.test:3100",
        "infra.health.camunda-url=http://camunda.test:8080"
})
class MonitoringLinksAccessTest {

    @Autowired private MockMvc mvc;
    @MockitoBean private JwtService jwtService;
    @MockitoBean private AppUserRepository users;
    @MockitoBean private com.orange.pfe.backend.service.FaultInjectionService faultInjectionService;
    @MockitoBean private InfrastructureHealthService healthService;
    @MockitoBean private KubernetesDiscoveryService discoveryService;
    @MockitoBean private AiopsPollingProperties pollingProperties;

    private MockHttpServletRequestBuilder as(String role, MockHttpServletRequestBuilder b) {
        AppUser u = mock(AppUser.class);
        when(u.isEnabled()).thenReturn(true);
        when(u.getAuthorities()).thenReturn((java.util.Collection) java.util.List.of(
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
    void anAdministratorGetsTheFourConfiguredLinks() throws Exception {
        var res = mvc.perform(as("ADMIN", get("/api/infrastructure/monitoring-links"))).andReturn().getResponse();

        assertThat(res.getStatus()).isEqualTo(200);
        assertThat(res.getContentAsString()).contains("\"prometheus\":\"http://prom.test:9090\"")
                .contains("\"grafana\":\"http://graf.test:3000\"").contains("\"loki\":\"http://loki.test:3100\"")
                .contains("\"camunda\":\"http://camunda.test:8080\"");
    }

    @Test
    void aSuperAdministratorGetsTheLinks() throws Exception {
        assertThat(status(as("SUPER_ADMIN", get("/api/infrastructure/monitoring-links")))).isEqualTo(200);
    }

    @Test
    void anOrdinaryUserIsRefused() throws Exception {
        assertThat(status(as("USER", get("/api/infrastructure/monitoring-links")))).isEqualTo(403);
    }

    @Test
    void anAnonymousCallerIsRefused() throws Exception {
        int s = status(get("/api/infrastructure/monitoring-links"));

        assertThat(s == 401 || s == 403).isTrue();
    }

    @Test
    void theLinksCannotBeChangedThroughTheApi() throws Exception {
        // aucune écriture n'existe : jamais de succès (le code exact dépend de la gestion globale des erreurs)
        assertThat(status(as("ADMIN", post("/api/infrastructure/monitoring-links")))).isGreaterThanOrEqualTo(400);
    }

    @Test
    void otherInfrastructureReadsStayOpenToOrdinaryUsers() throws Exception {
        assertThat(status(as("USER", get("/api/infrastructure/health")))).isNotIn(401, 403);
    }

    @Test
    void theRoleEnumStillHasTheSameValues() {
        assertThat(UserRole.values()).extracting(Enum::name).contains("USER", "ADMIN", "SUPER_ADMIN");
    }
}
