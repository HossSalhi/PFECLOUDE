package com.orange.pfe.backend.service;

import com.orange.pfe.backend.dto.AIPredictRequest;
import com.orange.pfe.backend.exception.AIOpsIntegrationException;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.http.client.SimpleClientHttpRequestFactory;
import org.springframework.test.util.ReflectionTestUtils;
import org.springframework.web.client.RestClient;

import static org.junit.jupiter.api.Assertions.*;

class AIOpsClientServiceTest {

    private AIOpsClientService service;

    @BeforeEach
    void setUp() {
        SimpleClientHttpRequestFactory factory = new SimpleClientHttpRequestFactory();
        factory.setConnectTimeout(1000);
        factory.setReadTimeout(1000);
        RestClient.Builder builder = RestClient.builder().requestFactory(factory);
        service = new AIOpsClientService(builder, "http://localhost:9999", "", false);
        ReflectionTestUtils.setField(service, "circuitBreakerThreshold", 3);
        ReflectionTestUtils.setField(service, "circuitBreakerOpenMs", 60000L);
    }

    @Test
    void predictShouldThrowWhenServiceUnreachable() {
        AIPredictRequest request = new AIPredictRequest();
        request.setNamespace("pfe");
        request.setApp("backend");

        assertThrows(AIOpsIntegrationException.class, () -> service.predict(request));
    }

    @Test
    void circuitShouldOpenAfterThresholdFailures() {
        AIPredictRequest request = new AIPredictRequest();

        // Trigger 3 consecutive failures to open the circuit
        for (int i = 0; i < 3; i++) {
            assertThrows(AIOpsIntegrationException.class, () -> service.predict(request));
        }

        // Circuit is now open — next call should fail with circuit-open message
        AIOpsIntegrationException ex = assertThrows(
                AIOpsIntegrationException.class, () -> service.predict(request));
        assertTrue(ex.getMessage().contains("circuit breaker is open"),
                "Expected circuit-open message but got: " + ex.getMessage());
    }

    @Test
    void isAvailableShouldReturnTrueInitially() {
        assertTrue(service.isAvailable());
    }

    @Test
    void isAvailableShouldReturnFalseAfterCircuitOpens() {
        AIPredictRequest request = new AIPredictRequest();

        for (int i = 0; i < 3; i++) {
            try { service.predict(request); } catch (Exception ignored) {}
        }

        assertFalse(service.isAvailable());
    }

    @Test
    void learnShouldNotThrowWhenServiceUnreachable() {
        com.orange.pfe.backend.dto.AILearnRequest learnRequest =
                new com.orange.pfe.backend.dto.AILearnRequest();
        assertDoesNotThrow(() -> service.learn(learnRequest));
    }

    @Test
    void learnShouldSkipWhenCircuitIsOpen() {
        AIPredictRequest request = new AIPredictRequest();

        // Open the circuit
        for (int i = 0; i < 3; i++) {
            try { service.predict(request); } catch (Exception ignored) {}
        }

        // learn() should silently skip — no exception thrown
        com.orange.pfe.backend.dto.AILearnRequest learnRequest =
                new com.orange.pfe.backend.dto.AILearnRequest();
        assertDoesNotThrow(() -> service.learn(learnRequest));
    }
}
