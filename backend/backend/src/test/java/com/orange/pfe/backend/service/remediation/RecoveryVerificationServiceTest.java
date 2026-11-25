package com.orange.pfe.backend.service.remediation;

import com.orange.pfe.backend.config.RemediationProperties;
import com.orange.pfe.backend.service.KubernetesSignalEnrichmentService;
import org.junit.jupiter.api.Test;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.times;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

/**
 * Vérification de la récupération — critère DÉTERMINISTE (retrait de l'IA, 11 nov. 2026) :
 * workload sain ET aucun marqueur d'état Kubernetes de panne (hors marqueurs nœud).
 */
class RecoveryVerificationServiceTest {

    private RemediationExecutor executor;
    private KubernetesSignalEnrichmentService enrichment;

    private RecoveryVerificationService service(boolean workloadHealthy, Map<String, Double> markers, int attempts) {
        executor = mock(RemediationExecutor.class);
        when(executor.isWorkloadHealthy("pfe", "backend")).thenReturn(workloadHealthy);
        enrichment = mock(KubernetesSignalEnrichmentService.class);
        when(enrichment.collectFeatureOverrides("pfe", "backend", false)).thenReturn(markers);
        RemediationProperties properties = new RemediationProperties();
        properties.setVerificationRetryAttempts(attempts);
        properties.setVerificationRetryDelaySeconds(0);
        return new RecoveryVerificationService(executor, enrichment, properties);
    }

    @Test
    void shouldResolveWhenWorkloadIsHealthyAndNoFaultMarkerRemains() throws InterruptedException {
        RecoveryVerificationResult result = service(true, Map.of(), 1).verify("pfe", "backend");

        assertTrue(result.workloadHealthy());
        assertFalse(result.faultStillPresent());
        assertTrue(result.resolved());
    }

    @Test
    void shouldKeepIncidentOpenWhenAFaultMarkerIsStillPresent() throws InterruptedException {
        RecoveryVerificationResult result =
                service(true, Map.of("crash_loop_backoff_detected", 1.0), 1).verify("pfe", "backend");

        assertTrue(result.workloadHealthy());
        assertTrue(result.faultStillPresent());
        assertFalse(result.resolved());
    }

    @Test
    void stillUnreachableCountsAsAPersistingFault() throws InterruptedException {
        RecoveryVerificationResult result =
                service(true, Map.of("service_unreachable_detected", 1.0), 1).verify("pfe", "backend");

        assertTrue(result.faultStillPresent());
        assertFalse(result.resolved());
    }

    @Test
    void blockingNetworkMarkersAreFaultsToo() throws InterruptedException {
        for (String marker : List.of("dns_blocked", "db_blocked", "network_partition", "oom_killed_detected")) {
            RecoveryVerificationResult result = service(true, Map.of(marker, 1.0), 1).verify("pfe", "backend");
            assertTrue(result.faultStillPresent(), marker);
        }
    }

    @Test
    void nodeLevelMarkersAreIgnoredByThePerAppVerification() throws InterruptedException {
        RecoveryVerificationResult result = service(true,
                Map.of("node_outage_detected", 1.0, "node_not_ready_count", 1.0), 1).verify("pfe", "backend");

        assertFalse(result.faultStillPresent());
        assertTrue(result.resolved());
    }

    @Test
    void neverResolvedWhileTheWorkloadIsNotHealthy() throws InterruptedException {
        RecoveryVerificationResult result = service(false, Map.of(), 1).verify("pfe", "backend");

        assertFalse(result.workloadHealthy());
        assertFalse(result.resolved());
    }

    @Test
    void scaleModeIgnoresMarkersButStillRequiresAHealthyWorkload() throws InterruptedException {
        Map<String, Double> sustainedLoad = Map.of("http_latency_detected", 1.0);

        RecoveryVerificationResult healthy = service(true, sustainedLoad, 1)
                .verify("pfe", "backend", msg -> { }, true);
        assertTrue(healthy.resolved());

        RecoveryVerificationResult unhealthy = service(false, sustainedLoad, 1)
                .verify("pfe", "backend", msg -> { }, true);
        assertFalse(unhealthy.resolved());
    }

    @Test
    void retriesUntilAttemptsAreExhaustedAndLogsEachAttempt() throws InterruptedException {
        RecoveryVerificationService service = service(false, Map.of(), 3);
        List<String> trace = new ArrayList<>();

        RecoveryVerificationResult result = service.verify("pfe", "backend", trace::add);

        assertFalse(result.resolved());
        verify(executor, times(3)).isWorkloadHealthy("pfe", "backend");
        assertEquals(3, trace.size());
        assertTrue(trace.get(0).contains("pods prêts=NON"), trace.get(0));
        assertTrue(trace.get(2).contains("Vérification 3/3"), trace.get(2));
    }

    @Test
    void stopsRetryingAsSoonAsTheIncidentIsResolved() throws InterruptedException {
        RecoveryVerificationService service = service(true, Map.of(), 5);
        List<String> trace = new ArrayList<>();

        RecoveryVerificationResult result = service.verify("pfe", "backend", trace::add);

        assertTrue(result.resolved());
        verify(executor, times(1)).isWorkloadHealthy("pfe", "backend");
        assertEquals(1, trace.size());
        assertTrue(trace.get(0).contains("plus aucun marqueur de panne Kubernetes"), trace.get(0));
    }

    @Test
    void theVerificationMessageCarriesNoAiVocabulary() throws InterruptedException {
        RecoveryVerificationResult result = service(true, Map.of(), 1).verify("pfe", "backend");

        String message = result.message().toLowerCase();
        assertFalse(message.contains("ai"), message);          // ni « aiConfidence » ni « rawAiIncident »
        assertTrue(message.contains("faultstillpresent=false"), message);
    }
}
