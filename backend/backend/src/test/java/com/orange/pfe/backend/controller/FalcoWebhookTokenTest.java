package com.orange.pfe.backend.controller;

import com.orange.pfe.backend.repository.IncidentRepository;
import com.orange.pfe.backend.service.AIOpsOrchestrationService;
import com.orange.pfe.backend.service.IncidentService;
import org.junit.jupiter.api.Test;
import org.springframework.http.MediaType;
import org.springframework.test.util.ReflectionTestUtils;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.request.MockHttpServletRequestBuilder;
import org.springframework.test.web.servlet.setup.MockMvcBuilders;

import static org.mockito.Mockito.mock;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

/** Webhook Falco FERMÉ PAR DÉFAUT : sans jeton configuré, tout est refusé ; jetons factices propres au test. */
class FalcoWebhookTokenTest {

    private MockMvc mvc(String configured) {
        FalcoWebhookController c = new FalcoWebhookController(mock(IncidentService.class), mock(AIOpsOrchestrationService.class), mock(IncidentRepository.class));
        if (configured != null) ReflectionTestUtils.setField(c, "webhookToken", configured);
        return MockMvcBuilders.standaloneSetup(c).build();
    }
    private MockHttpServletRequestBuilder req(String hdr) {
        var b = post("/api/webhooks/falco").contentType(MediaType.APPLICATION_JSON).content("{}");
        return hdr == null ? b : b.header("X-Helios-Falco-Token", hdr);
    }

    @Test void defaultConfiguration_isClosed() throws Exception { mvc(null).perform(req(null)).andExpect(status().isUnauthorized()); }
    @Test void notConfigured_refusesEvenAHeader() throws Exception { mvc("").perform(req("n-importe-quoi")).andExpect(status().isUnauthorized()); }
    @Test void notConfigured_refusesEmptyHeader() throws Exception { mvc("").perform(req("")).andExpect(status().isUnauthorized()); }
    @Test void configured_missingToken_refused() throws Exception { mvc("jeton-factice").perform(req(null)).andExpect(status().isUnauthorized()); }
    @Test void configured_wrongToken_refused() throws Exception { mvc("jeton-factice").perform(req("autre")).andExpect(status().isUnauthorized()); }
    @Test void configured_prefixOfToken_refused() throws Exception { mvc("jeton-factice").perform(req("jeton-fact")).andExpect(status().isUnauthorized()); }
    @Test void configured_rightToken_accepted() throws Exception { mvc("jeton-factice").perform(req("jeton-factice")).andExpect(status().isOk()); }
}