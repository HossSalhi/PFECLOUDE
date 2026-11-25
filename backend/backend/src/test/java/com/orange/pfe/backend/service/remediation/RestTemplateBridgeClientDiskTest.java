package com.orange.pfe.backend.service.remediation;

import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.orange.pfe.backend.config.RemediationProperties;
import com.orange.pfe.backend.service.remediation.BridgeClient.BridgeResponse;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.http.HttpMethod;
import org.springframework.http.MediaType;
import org.springframework.mock.http.client.MockClientHttpRequest;
import org.springframework.test.web.client.MockRestServiceServer;
import org.springframework.web.client.RestTemplate;

import java.util.LinkedHashMap;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.springframework.test.web.client.match.MockRestRequestMatchers.header;
import static org.springframework.test.web.client.match.MockRestRequestMatchers.method;
import static org.springframework.test.web.client.match.MockRestRequestMatchers.requestTo;
import static org.springframework.test.web.client.response.MockRestResponseCreators.withSuccess;

/**
 * DISK-E2E-158-01 : le client HTTP du Bridge n'ouvre la voie HOST_RECLAIM_DISK que pour le scenario DISK_E2E_158, n'envoie
 * que des valeurs canoniques, et refuse sans AUCUNE requete tout ce qui sort de ce perimetre. Aucun reseau (serveur simule).
 */
class RestTemplateBridgeClientDiskTest {

    private static final String BRIDGE_URL = "http://bridge.test/action";
    private static final String TOKEN = "unit-test-token";
    private static final Long INCIDENT_ID = 4242L;

    private RemediationProperties properties;
    private MockRestServiceServer server;
    private RestTemplateBridgeClient client;

    @BeforeEach
    void setUp() {
        properties = new RemediationProperties();
        properties.setBridgeEnabled(true);
        properties.setHostDiskE2e158Enabled(true);          // DISK-E2E-158-02 : drapeau DEDIE du scenario
        properties.setBridgeUrl(BRIDGE_URL);
        properties.setBridgeToken(TOKEN);

        RestTemplate restTemplate = new RestTemplate();
        server = MockRestServiceServer.createServer(restTemplate);
        client = new RestTemplateBridgeClient(properties, restTemplate);
    }

    private BridgeResponse reclaim(String action, String type, String ns, String target, String node,
                                   String scenario, Long incidentId) {
        return client.sendDiskReclaimAction(action, type, ns, target, node, scenario, incidentId);
    }

    private BridgeResponse reclaimScenario() {
        return reclaim("HOST_RECLAIM_DISK", "NODE_DISK_FULL", "k8s-nodes",
                "k8s-worker1", "k8s-worker1", "DISK_E2E_158", INCIDENT_ID);
    }

    @Test
    void sendsExactlyTheCanonicalScenarioPayloadWithTheToken() {
        Map<String, Object> expected = new LinkedHashMap<>();
        expected.put("action", "HOST_RECLAIM_DISK");
        expected.put("type", "NODE_DISK_FULL");
        expected.put("ns", "k8s-nodes");
        expected.put("cible", "k8s-worker1");
        expected.put("noeud", "k8s-worker1");
        expected.put("scenario", "DISK_E2E_158");
        expected.put("incident_id", "4242");

        server.expect(requestTo(BRIDGE_URL))
                .andExpect(method(HttpMethod.POST))
                .andExpect(header("X-Helios-Token", TOKEN))
                .andExpect(request -> {
                    String body = ((MockClientHttpRequest) request).getBodyAsString();
                    Map<String, Object> sent = new ObjectMapper().readValue(
                            body, new TypeReference<Map<String, Object>>() { });
                    assertEquals(expected, sent, "le corps est exactement le contrat, ni plus ni moins");
                })
                .andRespond(withSuccess("""
                        {"ok": true, "execute": true, "exitCode": 0,
                         "stdout": "3 fichier(s) supprime(s) ; occupation 81.25 % -> 70.00 % (<= 77 %)", "stderr": ""}
                        """, MediaType.APPLICATION_JSON));

        BridgeResponse result = reclaimScenario();

        assertTrue(result.ok());
        assertTrue(result.execute());
        assertEquals(0, result.exitCode());
        assertTrue(result.message().contains("<= 77 %"));
        server.verify();
    }

    @Test
    void canonicalValuesAreSentEvenIfTheCallerUsesAnotherCase() {
        server.expect(requestTo(BRIDGE_URL))
                .andExpect(request -> {
                    String body = ((MockClientHttpRequest) request).getBodyAsString();
                    Map<String, Object> sent = new ObjectMapper().readValue(
                            body, new TypeReference<Map<String, Object>>() { });
                    assertEquals("k8s-worker1", sent.get("cible"));
                    assertEquals("k8s-worker1", sent.get("noeud"));
                    assertEquals("NODE_DISK_FULL", sent.get("type"));
                    assertEquals("k8s-nodes", sent.get("ns"));
                })
                .andRespond(withSuccess("{\"ok\": true, \"execute\": true, \"exitCode\": 0}", MediaType.APPLICATION_JSON));

        BridgeResponse result = reclaim("HOST_RECLAIM_DISK", "node_disk_full", "K8S-NODES",
                "K8S-WORKER1", "K8s-Worker1", "DISK_E2E_158", INCIDENT_ID);

        assertTrue(result.ok());
        server.verify();
    }

    @Test
    void bridgeFailureKeepsItsReasonAndIsNeverASuccess() {
        server.expect(requestTo(BRIDGE_URL))
                .andRespond(withSuccess("""
                        {"ok": false, "execute": true, "exitCode": -1, "rejet": "NOT_RECOVERED",
                         "stdout": "", "stderr": "fichiers supprimes mais occupation 79.00 % > 77 % : recuperation NON confirmee"}
                        """, MediaType.APPLICATION_JSON));

        BridgeResponse result = reclaimScenario();

        assertFalse(result.ok());
        assertTrue(result.execute());
        assertEquals(-1, result.exitCode());
        assertTrue(result.message().contains("recuperation NON confirmee"));
        server.verify();
    }

    @Test
    void okTrueWithANonZeroExitCodeIsNotOk() {
        server.expect(requestTo(BRIDGE_URL))
                .andRespond(withSuccess("{\"ok\": true, \"execute\": true, \"exitCode\": 3}", MediaType.APPLICATION_JSON));

        assertFalse(reclaimScenario().ok());
        server.verify();
    }

    @Test
    void anythingOutsideTheScenarioIsRefusedWithoutAnyRequest() {
        // Aucune attente declaree : toute requete emise ferait echouer le serveur simule.
        String hr = "HOST_RECLAIM_DISK";
        String full = "NODE_DISK_FULL";
        String ns = "k8s-nodes";
        String w1 = "k8s-worker1";
        String sc = "DISK_E2E_158";

        assertFalse(reclaim("HOST_IMAGE_GC", full, ns, w1, w1, sc, INCIDENT_ID).ok(), "autre action");
        assertFalse(reclaim(hr, "NODE_DISK_PRESSURE", ns, w1, w1, sc, INCIDENT_ID).ok(), "autre type");
        assertFalse(reclaim(hr, "NODE_INODES_FULL", ns, w1, w1, sc, INCIDENT_ID).ok(), "inodes");
        assertFalse(reclaim(hr, full, "pfe", w1, w1, sc, INCIDENT_ID).ok(), "autre namespace");
        assertFalse(reclaim(hr, full, ns, "k8s-worker2", "k8s-worker2", sc, INCIDENT_ID).ok(), "worker2");
        assertFalse(reclaim(hr, full, ns, "k8s-master", "k8s-master", sc, INCIDENT_ID).ok(), "master");
        assertFalse(reclaim(hr, full, ns, w1, "k8s-worker2", sc, INCIDENT_ID).ok(), "noeud different de la cible");
        assertFalse(reclaim(hr, full, ns, w1, w1, "DISK_E2E_155", INCIDENT_ID).ok(), "autre scenario");
        assertFalse(reclaim(hr, full, ns, w1, w1, "", INCIDENT_ID).ok(), "scenario vide");
        assertFalse(reclaim(hr, full, ns, w1, w1, null, INCIDENT_ID).ok(), "scenario absent");
        assertFalse(reclaim(hr, full, ns, w1, w1, sc, null).ok(), "incident absent");
        assertFalse(reclaim(hr, full, ns, w1, w1, sc, 0L).ok(), "incident nul");
        assertFalse(reclaim(hr, full, ns, w1, w1, sc, -1L).ok(), "incident negatif");
        server.verify();
    }

    @Test
    void disabledFeatureFlagBridgeOrIncompleteConfigurationSendNothing() {
        properties.setHostDiskE2e158Enabled(false);
        BridgeResponse flagOff = reclaimScenario();
        assertFalse(flagOff.ok());
        assertTrue(flagOff.message().contains("desactivee"));

        properties.setHostDiskRemediationEnabled(true);     // l'ancien drapeau global ne suffit PAS
        assertFalse(reclaimScenario().ok());
        properties.setHostDiskRemediationEnabled(false);

        properties.setHostDiskE2e158Enabled(true);
        properties.setBridgeEnabled(false);
        assertFalse(reclaimScenario().ok());

        properties.setBridgeEnabled(true);
        properties.setBridgeToken("");
        assertFalse(reclaimScenario().ok());

        properties.setBridgeToken(TOKEN);
        properties.setBridgeUrl(" ");
        assertFalse(reclaimScenario().ok());

        server.verify();
    }

    @Test
    void theFiveArgumentOverloadRefusesTheDiskActionWithoutAnyRequest() {
        BridgeResponse result = client.sendAction(
                "HOST_RECLAIM_DISK", "NODE_DISK_FULL", "k8s-nodes", "k8s-worker1", "k8s-worker1");

        assertFalse(result.ok());
        assertFalse(result.execute());
        assertTrue(result.message().contains("scenario et l'incident"));
        server.verify();
    }

    @Test
    void theDefaultInterfaceImplementationFailsClosed() {
        BridgeClient bare = (action, incidentType, namespace, target, node) ->
                new BridgeResponse(true, true, 0, "ne doit jamais etre utilise pour le disque");

        BridgeResponse result = bare.sendDiskReclaimAction(
                "HOST_RECLAIM_DISK", "NODE_DISK_FULL", "k8s-nodes", "k8s-worker1", "k8s-worker1",
                "DISK_E2E_158", INCIDENT_ID);

        assertFalse(result.ok());
        assertFalse(result.execute());
        assertEquals(-1, result.exitCode());
    }
}
