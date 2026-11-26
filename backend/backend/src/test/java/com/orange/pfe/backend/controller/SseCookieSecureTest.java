package com.orange.pfe.backend.controller;

import com.orange.pfe.backend.repository.AppUserRepository;
import com.orange.pfe.backend.service.SseEventService;
import com.orange.pfe.backend.service.SseTicketService;
import org.junit.jupiter.api.Test;
import org.springframework.mock.web.MockHttpServletRequest;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.mock;

/** Attribut Secure du cookie SSE : X-Forwarded-Proto n'est cru que s'il vient d'un proxy de confiance. */
class SseCookieSecureTest {
    private SseController c(String mode, String cidrs) {
        return new SseController(mock(SseEventService.class), mock(SseTicketService.class), mock(AppUserRepository.class), mode, cidrs);
    }
    private MockHttpServletRequest req(String remote, String fwd, boolean https) {
        MockHttpServletRequest r = new MockHttpServletRequest(); r.setRemoteAddr(remote); r.setSecure(https);
        if (fwd != null) r.addHeader("X-Forwarded-Proto", fwd); return r;
    }
    private static final String TRUSTED = "10.0.0.0/8,172.16.0.0/12,127.0.0.0/8";

    @Test void https_request_isSecure() { assertThat(c("auto", TRUSTED).secure(req("203.0.113.9", null, true))).isTrue(); }
    @Test void forwardedHttps_fromTrustedProxy_isSecure() { assertThat(c("auto", TRUSTED).secure(req("172.16.126.63", "https", false))).isTrue(); }
    @Test void forwardedHttps_listWithFirstHttps_isSecure() { assertThat(c("auto", TRUSTED).secure(req("10.1.2.3", "https, http", false))).isTrue(); }
    @Test void forwardedHttps_fromUntrustedSource_isNotBelieved() { assertThat(c("auto", TRUSTED).secure(req("203.0.113.9", "https", false))).isFalse(); }
    @Test void forwardedHttp_isNotSecure() { assertThat(c("auto", TRUSTED).secure(req("10.1.2.3", "http", false))).isFalse(); }
    @Test void noHeader_plainHttp_isNotSecure() { assertThat(c("auto", TRUSTED).secure(req("10.1.2.3", null, false))).isFalse(); }
    @Test void forcedTrue_alwaysSecure_forcedFalse_followsTheRequestOnly() {
        assertThat(c("true", TRUSTED).secure(req("203.0.113.9", null, false))).isTrue();
        assertThat(c("false", TRUSTED).secure(req("10.1.2.3", "https", false))).isFalse();
    }
    @Test void malformedConfiguration_failsClosed() { assertThat(c("auto", "10.0.0.0/8, ,").secure(req("203.0.113.9", "https", false))).isFalse(); }
}