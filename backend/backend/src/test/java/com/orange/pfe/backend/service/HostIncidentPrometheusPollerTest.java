package com.orange.pfe.backend.service;

import com.orange.pfe.backend.dto.HostIncidentIngestionRequest;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;

import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.times;
import static org.mockito.Mockito.verify;

/**
 * LOT HELIOS-INCIDENT-PRODUCER-VERIFICATION-WIRING-01 — Phase G. Producteur host-mesuré : la LOGIQUE de
 * décision (quels signaux déclenchent une ingestion) et le parsing de la série Prometheus sont testés
 * SANS réseau. Le poller est désactivé par défaut ; il n'exécute aucun appel réel dans ce lot.
 */
class HostIncidentPrometheusPollerTest {

    private HostIncidentIngestionService ingestion;
    private HostIncidentPrometheusPoller poller;

    @BeforeEach
    void setUp() {
        ingestion = mock(HostIncidentIngestionService.class);
        poller = new HostIncidentPrometheusPoller(ingestion);
    }

    @Test
    void processSignals_ingereTypesHost_ignoreLesAutres() {
        List<HostIncidentPrometheusPoller.HostSignal> signals = List.of(
                new HostIncidentPrometheusPoller.HostSignal("NODE_HIGH_CPU", "k8s-nodes", "k8s-worker1"),
                new HostIncidentPrometheusPoller.HostSignal("POD_IMAGE_PULL_BACKOFF", "pfe", "frontend"));

        int n = poller.processSignals(signals);

        assertEquals(1, n, "seul le type host-mesuré est ingéré (le pod est ignoré)");
        ArgumentCaptor<HostIncidentIngestionRequest> cap =
                ArgumentCaptor.forClass(HostIncidentIngestionRequest.class);
        verify(ingestion, times(1)).ingest(cap.capture());
        assertEquals("NODE_HIGH_CPU", cap.getValue().incidentType());
        assertEquals("k8s-worker1", cap.getValue().target());
    }

    @Test
    void processSignals_sansCible_neCreeAucunIncident() {
        List<HostIncidentPrometheusPoller.HostSignal> signals = List.of(
                new HostIncidentPrometheusPoller.HostSignal("NODE_HIGH_CPU", "k8s-nodes", null));
        int n = poller.processSignals(signals);
        assertEquals(0, n);
        verify(ingestion, never()).ingest(any());
    }

    @Test
    void parseVector_litTypeNsCibleDepuisLesLabels() throws Exception {
        String body = "{\"status\":\"success\",\"data\":{\"resultType\":\"vector\",\"result\":[" +
                "{\"metric\":{\"__name__\":\"helios_incident\",\"type\":\"NODE_HIGH_CPU\"," +
                "\"ns\":\"k8s-nodes\",\"cible\":\"k8s-worker1\"},\"value\":[1791501570,\"1\"]}]}}";
        List<HostIncidentPrometheusPoller.HostSignal> s = poller.parseVector(body);
        assertEquals(1, s.size());
        assertEquals("NODE_HIGH_CPU", s.get(0).type());
        assertEquals("k8s-worker1", s.get(0).cible());
        assertTrue("k8s-nodes".equals(s.get(0).ns()));
    }

    @Test
    void processSignals_null_renvoieZero() {
        assertEquals(0, poller.processSignals(null));
        verify(ingestion, never()).ingest(any());
    }
}
