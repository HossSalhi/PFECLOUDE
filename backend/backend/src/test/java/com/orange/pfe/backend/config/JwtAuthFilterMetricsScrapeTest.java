package com.orange.pfe.backend.config;

import com.orange.pfe.backend.repository.AppUserRepository;
import com.orange.pfe.backend.service.JwtService;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.Test;
import org.springframework.mock.web.MockFilterChain;
import org.springframework.mock.web.MockHttpServletRequest;
import org.springframework.mock.web.MockHttpServletResponse;
import org.springframework.security.core.context.SecurityContextHolder;
import org.springframework.test.util.ReflectionTestUtils;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.mock;

/** Jeton de scrape Prometheus : identité technique ROLE_METRICS, limitée à GET /actuator/prometheus, fermée par défaut. */
class JwtAuthFilterMetricsScrapeTest {

    @AfterEach void clear() { SecurityContextHolder.clearContext(); }

    private JwtAuthFilter filter(String token) {
        JwtAuthFilter f = new JwtAuthFilter(mock(JwtService.class), mock(AppUserRepository.class));
        if (token != null) ReflectionTestUtils.setField(f, "metricsScrapeToken", token);
        return f;
    }
    private boolean authorizedAsMetrics(JwtAuthFilter f, String method, String uri, String bearer) throws Exception {
        MockHttpServletRequest r = new MockHttpServletRequest(method, uri);
        if (bearer != null) r.addHeader("Authorization", "Bearer " + bearer);
        f.doFilter(r, new MockHttpServletResponse(), new MockFilterChain());
        var a = SecurityContextHolder.getContext().getAuthentication();
        return a != null && a.getAuthorities().stream().anyMatch(x -> x.getAuthority().equals("ROLE_METRICS"));
    }

    @Test void validToken_onPrometheus_isMetricsRole() throws Exception { assertThat(authorizedAsMetrics(filter("jeton-scrape"), "GET", "/actuator/prometheus", "jeton-scrape")).isTrue(); }
    @Test void wrongToken_isRefused() throws Exception { assertThat(authorizedAsMetrics(filter("jeton-scrape"), "GET", "/actuator/prometheus", "autre")).isFalse(); }
    @Test void noTokenConfigured_isClosed() throws Exception { assertThat(authorizedAsMetrics(filter(null), "GET", "/actuator/prometheus", "")).isFalse(); }
    @Test void validToken_onOtherPath_isRefused() throws Exception { assertThat(authorizedAsMetrics(filter("jeton-scrape"), "GET", "/actuator/metrics", "jeton-scrape")).isFalse(); }
    @Test void validToken_onApi_isRefused() throws Exception { assertThat(authorizedAsMetrics(filter("jeton-scrape"), "GET", "/api/incidents", "jeton-scrape")).isFalse(); }
    @Test void validToken_withPost_isRefused() throws Exception { assertThat(authorizedAsMetrics(filter("jeton-scrape"), "POST", "/actuator/prometheus", "jeton-scrape")).isFalse(); }
}