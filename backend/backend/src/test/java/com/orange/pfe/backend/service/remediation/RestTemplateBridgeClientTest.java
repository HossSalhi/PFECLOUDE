package com.orange.pfe.backend.service.remediation;

import com.orange.pfe.backend.config.RemediationProperties;
import com.orange.pfe.backend.service.remediation.BridgeClient.BridgeResponse;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.http.HttpMethod;
import org.springframework.http.MediaType;
import org.springframework.test.web.client.MockRestServiceServer;
import org.springframework.web.client.RestTemplate;

import static org.junit.jupiter.api.Assertions.*;
import static org.springframework.test.web.client.match.MockRestRequestMatchers.content;
import static org.springframework.test.web.client.match.MockRestRequestMatchers.header;
import static org.springframework.test.web.client.match.MockRestRequestMatchers.method;
import static org.springframework.test.web.client.match.MockRestRequestMatchers.requestTo;
import static org.springframework.test.web.client.response.MockRestResponseCreators.withServerError;
import static org.springframework.test.web.client.response.MockRestResponseCreators.withSuccess;

class RestTemplateBridgeClientTest {

    private static final String BRIDGE_URL = "http://bridge.test/action";
    private static final String TOKEN = "unit-test-token";

    private RemediationProperties properties;
    private RestTemplate restTemplate;
    private MockRestServiceServer server;
    private RestTemplateBridgeClient client;

    @BeforeEach
    void setUp() {
        properties = new RemediationProperties();
        properties.setBridgeEnabled(true);
        properties.setBridgeUrl(BRIDGE_URL);
        properties.setBridgeToken(TOKEN);

        restTemplate = new RestTemplate();
        server = MockRestServiceServer.createServer(restTemplate);
        client = new RestTemplateBridgeClient(properties, restTemplate);
    }

    @Test
    void sendsExpectedPayloadAndTokenAndParsesResponse() {
        server.expect(requestTo(BRIDGE_URL))
                .andExpect(method(HttpMethod.POST))
                .andExpect(header("X-Helios-Token", TOKEN))
                .andExpect(content().contentTypeCompatibleWith(MediaType.APPLICATION_JSON))
                .andExpect(content().json("""
                        {
                          "action": "RESTART_POD",
                          "type": "NODE_HIGH_CPU",
                          "ns": "default",
                          "cible": "app-cpu",
                          "noeud": "worker1"
                        }
                        """))
                .andRespond(withSuccess(
                        """
                        {
                          "ok": true,
                          "execute": true,
                          "exitCode": 0,
                          "resume": "Quota applied"
                        }
                        """,
                        MediaType.APPLICATION_JSON));

        BridgeResponse result = client.sendAction(
                "RESTART_POD",
                "NODE_HIGH_CPU",
                "default",
                "app-cpu",
                "worker1");

        assertTrue(result.ok());
        assertTrue(result.execute());
        assertEquals(0, result.exitCode());
        assertEquals("Quota applied", result.message());
        server.verify();
    }

    @Test
    void mapsBridgeStderrAsMessageWhenResumeAbsent() {
        // CPU-BRIDGE-ERRPROP-01 : la reponse d'echec du quota (refus a l'etape
        // systemd_control) ne porte PAS de "resume" mais un "stderr". Le message doit
        // desormais remonter ce stderr, et non rester vide (qui masquait la cause reelle).
        server.expect(requestTo(BRIDGE_URL))
                .andExpect(method(HttpMethod.POST))
                .andExpect(header("X-Helios-Token", TOKEN))
                .andRespond(withSuccess(
                        """
                        {
                          "action": "HOST_SERVICE_CPU_QUOTA",
                          "execute": true,
                          "ok": false,
                          "exitCode": -1,
                          "stdout": "",
                          "stderr": "plafond non applique a l etape systemd_control"
                        }
                        """,
                        MediaType.APPLICATION_JSON));

        BridgeResponse result = client.sendAction(
                "HOST_SERVICE_CPU_QUOTA",
                "NODE_HIGH_CPU",
                "k8s-nodes",
                "k8s-master",
                "k8s-master",
                "helios-cpuload.service",
                25,
                240);

        assertFalse(result.ok());
        assertTrue(result.execute());
        assertEquals(-1, result.exitCode());
        assertEquals("plafond non applique a l etape systemd_control", result.message());
        server.verify();
    }

    @Test
    void sendsExplicitCpuQuotaPayloadAndToken() {
        server.expect(requestTo(BRIDGE_URL))
                .andExpect(method(HttpMethod.POST))
                .andExpect(header("X-Helios-Token", TOKEN))
                .andExpect(content().contentTypeCompatibleWith(MediaType.APPLICATION_JSON))
                .andExpect(content().json("""
                        {
                          "action": "HOST_SERVICE_CPU_QUOTA",
                          "type": "NODE_HIGH_CPU",
                          "ns": "k8s-nodes",
                          "cible": "k8s-worker1",
                          "noeud": "k8s-worker1",
                          "service": "helios-cpuload.service",
                          "quota_pct": 25,
                          "duree_s": 240
                        }
                        """))
                .andRespond(withSuccess(
                        """
                        {
                          "ok": true,
                          "execute": true,
                          "exitCode": 0,
                          "resume": "CPU quota applied"
                        }
                        """,
                        MediaType.APPLICATION_JSON));

        BridgeResponse result = client.sendAction(
                "HOST_SERVICE_CPU_QUOTA", "NODE_HIGH_CPU", "k8s-nodes",
                "k8s-worker1", "k8s-worker1", "helios-cpuload.service", 25, 240);

        assertTrue(result.ok());
        assertTrue(result.execute());
        assertEquals(0, result.exitCode());
        assertEquals("CPU quota applied", result.message());
        server.verify();
    }

    @Test
    void cpuQuotaCannotBeSentWithoutExplicitParameters() {
        BridgeResponse result = client.sendAction(
                "HOST_SERVICE_CPU_QUOTA", "NODE_HIGH_CPU", "k8s-nodes",
                "k8s-worker1", "k8s-worker1");

        assertFalse(result.ok());
        assertFalse(result.execute());
        assertEquals(-1, result.exitCode());
        assertTrue(result.message().contains("service, quota_pct et duree_s"));
        server.verify();
    }

    @Test
    void rejectsCpuQuotaOutsideBridgeBoundsWithoutSendingRequest() {
        BridgeResponse result = client.sendAction(
                "HOST_SERVICE_CPU_QUOTA", "NODE_HIGH_CPU", "k8s-nodes",
                "k8s-worker1", "k8s-worker1", "helios-cpuload.service", 10, 240);

        assertFalse(result.ok());
        assertEquals(-1, result.exitCode());
        assertTrue(result.message().contains("hors limites"));
        server.verify();
    }
    @Test
    void sendsExplicitMemoryLimitPayloadAndToken() {
        properties.setHostMemoryRemediationEnabled(true);
        server.expect(requestTo(BRIDGE_URL))
                .andExpect(method(HttpMethod.POST))
                .andExpect(header("X-Helios-Token", TOKEN))
                .andExpect(content().contentTypeCompatibleWith(MediaType.APPLICATION_JSON))
                .andExpect(content().json("""
                        {
                          "action": "HOST_SERVICE_MEMORY_LIMIT",
                          "type": "NODE_HIGH_MEMORY",
                          "ns": "k8s-nodes",
                          "cible": "k8s-worker1",
                          "noeud": "k8s-worker1",
                          "service": "helios-memload.service",
                          "memory_limit_mb": 256,
                          "duree_s": 240
                        }
                        """))
                .andRespond(withSuccess(
                        """
                        {"ok":true,"execute":true,"exitCode":0,"resume":"RAM limit applied"}
                        """, MediaType.APPLICATION_JSON));

        BridgeResponse result = client.sendMemoryLimitAction(
                "HOST_SERVICE_MEMORY_LIMIT", "NODE_HIGH_MEMORY", "k8s-nodes",
                "k8s-worker1", "k8s-worker1", "helios-memload.service", 256, 240);

        assertTrue(result.ok());
        assertTrue(result.execute());
        assertEquals(0, result.exitCode());
        assertEquals("RAM limit applied", result.message());
        server.verify();
    }

    @Test
    void memoryLimitIsRejectedWhenFeatureFlagIsDisabled() {
        BridgeResponse result = client.sendMemoryLimitAction(
                "HOST_SERVICE_MEMORY_LIMIT", "NODE_HIGH_MEMORY", "k8s-nodes",
                "k8s-worker1", "k8s-worker1", "helios-memload.service", 256, 240);

        assertFalse(result.ok());
        assertFalse(result.execute());
        assertEquals(-1, result.exitCode());
        assertTrue(result.message().contains("desactivee"));
        server.verify();
    }

    @Test
    void memoryLimitRejectsNonLabWorkerWithoutSendingRequest() {
        properties.setHostMemoryRemediationEnabled(true);

        BridgeResponse result = client.sendMemoryLimitAction(
                "HOST_SERVICE_MEMORY_LIMIT", "NODE_HIGH_MEMORY", "k8s-nodes",
                "k8s-worker2", "k8s-worker2", "helios-memload.service", 256, 240);

        assertFalse(result.ok());
        assertEquals(-1, result.exitCode());
        assertTrue(result.message().contains("k8s-worker1"));
        server.verify();
    }

    @Test
    void memoryLimitRejectsOutOfBoundsParametersWithoutSendingRequest() {
        properties.setHostMemoryRemediationEnabled(true);

        BridgeResponse result = client.sendMemoryLimitAction(
                "HOST_SERVICE_MEMORY_LIMIT", "NODE_HIGH_MEMORY", "k8s-nodes",
                "k8s-worker1", "k8s-worker1", "helios-memload.service", 64, 240);

        assertFalse(result.ok());
        assertEquals(-1, result.exitCode());
        assertTrue(result.message().contains("bornes"));
        server.verify();
    }

    @Test
    void disabledBridgeDoesNotSendRequest() {
        properties.setBridgeEnabled(false);

        BridgeResponse result = client.sendAction(
                "RESTART_POD", "NODE_HIGH_CPU",
                "default", "app-cpu", "worker1");

        assertFalse(result.ok());
        assertEquals("Bridge desactive.", result.message());
    }

    @Test
    void missingConfigurationDoesNotSendRequest() {
        properties.setBridgeToken("");

        BridgeResponse result = client.sendAction(
                "RESTART_POD", "NODE_HIGH_CPU",
                "default", "app-cpu", "worker1");

        assertFalse(result.ok());
        assertEquals("Configuration du Bridge incomplete.", result.message());
    }

    @Test
    void httpErrorReturnsGenericFailureWithoutExceptionDetails() {
        server.expect(requestTo(BRIDGE_URL))
                .andRespond(withServerError());

        BridgeResponse result = client.sendAction(
                "RESTART_POD", "NODE_HIGH_CPU",
                "default", "app-cpu", "worker1");

        assertFalse(result.ok());
        assertEquals(-1, result.exitCode());
        assertEquals("Erreur de communication avec le Bridge.", result.message());
        assertFalse(result.message().contains(TOKEN));
        server.verify();
    }

    @Test
    void malformedJsonReturnsGenericFailure() {
        server.expect(requestTo(BRIDGE_URL))
                .andRespond(withSuccess("{not-json", MediaType.APPLICATION_JSON));

        BridgeResponse result = client.sendAction(
                "RESTART_POD", "NODE_HIGH_CPU",
                "default", "app-cpu", "worker1");

        assertFalse(result.ok());
        assertEquals("Erreur de communication avec le Bridge.", result.message());
        server.verify();
    }

    @Test
    void nonzeroExitCodeIsNotReportedAsSuccess() {
        server.expect(requestTo(BRIDGE_URL))
                .andRespond(withSuccess(
                        """
                        {
                          "ok": true,
                          "execute": true,
                          "exitCode": 23,
                          "resume": "Action failed"
                        }
                        """,
                        MediaType.APPLICATION_JSON));

        BridgeResponse result = client.sendAction(
                "RESTART_POD", "NODE_HIGH_CPU",
                "default", "app-cpu", "worker1");

        assertFalse(result.ok());
        assertTrue(result.execute());
        assertEquals(23, result.exitCode());
        server.verify();
    }

    @Test
    void executeFalseIsPreservedInResponse() {
        server.expect(requestTo(BRIDGE_URL))
                .andRespond(withSuccess(
                        """
                        {
                          "ok": true,
                          "execute": false,
                          "exitCode": 0,
                          "resume": "No execution requested"
                        }
                        """,
                        MediaType.APPLICATION_JSON));

        BridgeResponse result = client.sendAction(
                "RESTART_POD", "NODE_HIGH_CPU",
                "default", "app-cpu", "worker1");

        assertTrue(result.ok());
        assertFalse(result.execute());
        assertEquals(0, result.exitCode());
        server.verify();
    }
}