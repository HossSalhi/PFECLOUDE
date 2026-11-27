package com.orange.pfe.backend.config;

import com.orange.pfe.backend.controller.AiAssistController;
import com.orange.pfe.backend.dto.aiassist.AiAssistDtos.AnomalyResponse;
import com.orange.pfe.backend.dto.aiassist.AiAssistDtos.Capabilities;
import com.orange.pfe.backend.dto.aiassist.AiAssistDtos.FeatureState;
import com.orange.pfe.backend.dto.aiassist.AiAssistDtos.Narrative;
import com.orange.pfe.backend.model.user.AppUser;
import com.orange.pfe.backend.repository.AppUserRepository;
import com.orange.pfe.backend.service.JwtService;
import com.orange.pfe.backend.service.aiassist.AiCapabilitiesService;
import com.orange.pfe.backend.service.aiassist.AiSummaryService;
import com.orange.pfe.backend.service.aiassist.AuditAnomalyService;
import com.orange.pfe.backend.service.aiassist.MaintenanceWindowAdvisor;
import com.orange.pfe.backend.service.aiassist.TaskAssignmentAdvisor;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.context.properties.EnableConfigurationProperties;
import org.springframework.boot.webmvc.test.autoconfigure.WebMvcTest;
import org.springframework.context.annotation.Import;
import org.springframework.security.access.AccessDeniedException;
import org.springframework.test.context.TestPropertySource;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.request.MockHttpServletRequestBuilder;

import java.util.Collection;
import java.util.List;
import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyInt;
import static org.mockito.ArgumentMatchers.anyLong;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;

/**
 * Accès HTTP : un anonyme est refusé ; un utilisateur connecté atteint le service, qui décide (le refus d'un chef d'une
 * autre équipe ou d'un technicien arrive en 403 depuis la règle d'accès).
 */
@WebMvcTest(controllers = AiAssistController.class)
@Import({SecurityConfig.class, JwtAuthFilter.class, WebSecurityEnablerForTest.class})
@EnableConfigurationProperties(AppSecurityProperties.class)
@TestPropertySource(properties = "app.security.cors-allowed-origins=http://localhost:4200")
class AiAssistHttpAccessTest {

    @Autowired private MockMvc mvc;
    @MockitoBean private JwtService jwtService;
    @MockitoBean private AppUserRepository users;
    @MockitoBean private com.orange.pfe.backend.service.FaultInjectionService faultInjectionService;
    @MockitoBean private AiCapabilitiesService capabilities;
    @MockitoBean private AiSummaryService summaries;
    @MockitoBean private AuditAnomalyService anomalies;
    @MockitoBean private TaskAssignmentAdvisor assignment;
    @MockitoBean private MaintenanceWindowAdvisor windows;
    @MockitoBean private com.orange.pfe.backend.service.aiassist.telemetry.TelemetryAnalysisService telemetry;

    @SuppressWarnings({"unchecked", "rawtypes"})
    private MockHttpServletRequestBuilder as(String role, MockHttpServletRequestBuilder b) {
        AppUser u = mock(AppUser.class);
        when(u.isEnabled()).thenReturn(true);
        when(u.getAuthorities()).thenReturn((Collection) List.of(new org.springframework.security.core.authority.SimpleGrantedAuthority("ROLE_" + role)));
        when(jwtService.isTokenValid("tok-" + role)).thenReturn(true);
        when(jwtService.extractUsername("tok-" + role)).thenReturn("u-" + role);
        when(users.findByUsername("u-" + role)).thenReturn(Optional.of(u));
        return b.header("Authorization", "Bearer tok-" + role);
    }

    private int status(MockHttpServletRequestBuilder b) throws Exception {
        return mvc.perform(b).andReturn().getResponse().getStatus();
    }

    @Test
    void anonymousUsersAreRefusedEverywhere() throws Exception {
        assertThat(status(get("/api/ai-assist/capabilities"))).isEqualTo(403);
        assertThat(status(get("/api/ai-assist/audit/anomalies"))).isEqualTo(403);
        assertThat(status(post("/api/ai-assist/reports/1/summary"))).isEqualTo(403);
        assertThat(status(post("/api/ai-assist/teams/1/summary"))).isEqualTo(403);
        assertThat(status(post("/api/ai-assist/maintenance/window").contentType("application/json").content("{}"))).isEqualTo(403);
        assertThat(status(post("/api/ai-assist/telemetry/analyses").contentType("application/json").content("{}"))).isEqualTo(403);
        assertThat(status(get("/api/ai-assist/telemetry/analyses"))).isEqualTo(403);
        assertThat(status(get("/api/ai-assist/telemetry/analyses/1"))).isEqualTo(403);
    }

    @Test
    void aConnectedUserReachesTheCapabilitiesAndTheServiceDecidesTheRest() throws Exception {
        when(capabilities.capabilities(any())).thenReturn(new Capabilities(false, List.of(new com.orange.pfe.backend.dto.aiassist.AiAssistDtos.LedTeam(7L, "Réseau")), true,
                FeatureState.off(), FeatureState.off(), FeatureState.off(), FeatureState.off(), FeatureState.off(), FeatureState.off()));
        when(anomalies.detect(any(), any(), anyInt())).thenReturn(new AnomalyResponse(7L, 7, List.of(), List.of(), Narrative.none()));

        assertThat(status(as("USER", get("/api/ai-assist/capabilities")))).isEqualTo(200);
        assertThat(status(as("USER", get("/api/ai-assist/audit/anomalies?teamId=7")))).isEqualTo(200);
    }

    @Test
    void anAccessDeniedFromTheRuleBecomes403() throws Exception {
        when(summaries.teamSummary(any(), anyLong())).thenThrow(new AccessDeniedException("autre équipe"));
        when(telemetry.analyse(any(), any())).thenThrow(new AccessDeniedException("autre équipe"));
        when(telemetry.get(any(), anyLong())).thenThrow(new AccessDeniedException("autre équipe"));
        when(summaries.reportSummary(any(), anyLong())).thenThrow(new AccessDeniedException("admin seulement"));
        when(anomalies.explain(any(), any(), anyInt())).thenThrow(new AccessDeniedException("autre équipe"));

        assertThat(status(as("USER", post("/api/ai-assist/teams/2/summary")))).isEqualTo(403);
        assertThat(status(as("USER", post("/api/ai-assist/telemetry/analyses").contentType("application/json").content("{\"teamId\":2}")))).isEqualTo(403);
        assertThat(status(as("USER", get("/api/ai-assist/telemetry/analyses/9")))).isEqualTo(403);
        assertThat(status(as("USER", post("/api/ai-assist/reports/1/summary")))).isEqualTo(403);
        assertThat(status(as("USER", post("/api/ai-assist/audit/anomalies/explain?teamId=2")))).isEqualTo(403);
    }

    @Test
    void unknownRoutesOfTheModuleStayClosedToAnonymous() throws Exception {
        assertThat(status(get("/api/ai-assist/anything"))).isEqualTo(403);
    }
}
