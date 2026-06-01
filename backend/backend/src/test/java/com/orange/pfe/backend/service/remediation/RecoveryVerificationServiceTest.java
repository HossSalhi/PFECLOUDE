package com.orange.pfe.backend.service.remediation;

import com.orange.pfe.backend.config.RemediationProperties;
import com.orange.pfe.backend.dto.AIPredictResponse;
import com.orange.pfe.backend.service.AIOpsClientService;
import com.orange.pfe.backend.service.KubernetesSignalEnrichmentService;
import com.orange.pfe.backend.service.RemediationHistoryContextService;
import org.junit.jupiter.api.Test;

import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

class RecoveryVerificationServiceTest {

    @Test
    void shouldResolveWhenWorkloadIsHealthyAndOnlyResidualLowConfidenceSignalRemains() throws InterruptedException {
        RemediationExecutor remediationExecutor = mock(RemediationExecutor.class);
        when(remediationExecutor.isWorkloadHealthy("pfe", "backend")).thenReturn(true);

        AIOpsClientService aiOpsClientService = mock(AIOpsClientService.class);
        KubernetesSignalEnrichmentService kubernetesSignalEnrichmentService = mock(KubernetesSignalEnrichmentService.class);
        RemediationHistoryContextService remediationHistoryContextService = mock(RemediationHistoryContextService.class);
        when(kubernetesSignalEnrichmentService.collectFeatureOverrides("pfe", "backend")).thenReturn(Map.of());
        when(remediationHistoryContextService.buildContext("pfe", "backend")).thenReturn(Map.of());
        AIPredictResponse response = new AIPredictResponse();
        response.setIncident(true);
        response.setIncidentType("APPLICATION_ERROR_STORM");
        response.setConfidence(0.55d);
        when(aiOpsClientService.predict(any())).thenReturn(response);

        RemediationProperties properties = new RemediationProperties();
        properties.setVerificationRetryAttempts(1);
        properties.setVerificationRetryDelaySeconds(0);
        properties.setVerificationIncidentConfidenceThreshold(0.70d);

        RecoveryVerificationService service = new RecoveryVerificationService(
                remediationExecutor,
                aiOpsClientService,
                kubernetesSignalEnrichmentService,
                remediationHistoryContextService,
                properties
        );

        RecoveryVerificationResult result = service.verify("pfe", "backend");

        assertTrue(result.workloadHealthy());
        assertFalse(result.aiStillSeesIncident());
        assertTrue(result.resolved());
    }

    @Test
    void shouldKeepIncidentOpenWhenHealthyWorkloadStillHasStrongAiSignal() throws InterruptedException {
        RemediationExecutor remediationExecutor = mock(RemediationExecutor.class);
        when(remediationExecutor.isWorkloadHealthy("pfe", "backend")).thenReturn(true);

        AIOpsClientService aiOpsClientService = mock(AIOpsClientService.class);
        KubernetesSignalEnrichmentService kubernetesSignalEnrichmentService = mock(KubernetesSignalEnrichmentService.class);
        RemediationHistoryContextService remediationHistoryContextService = mock(RemediationHistoryContextService.class);
        when(kubernetesSignalEnrichmentService.collectFeatureOverrides("pfe", "backend")).thenReturn(Map.of());
        when(remediationHistoryContextService.buildContext("pfe", "backend")).thenReturn(Map.of());
        AIPredictResponse response = new AIPredictResponse();
        response.setIncident(true);
        response.setIncidentType("AVAILABILITY_DROP");
        response.setConfidence(0.82d);
        when(aiOpsClientService.predict(any())).thenReturn(response);

        RemediationProperties properties = new RemediationProperties();
        properties.setVerificationRetryAttempts(1);
        properties.setVerificationRetryDelaySeconds(0);
        properties.setVerificationIncidentConfidenceThreshold(0.70d);

        RecoveryVerificationService service = new RecoveryVerificationService(
                remediationExecutor,
                aiOpsClientService,
                kubernetesSignalEnrichmentService,
                remediationHistoryContextService,
                properties
        );

        RecoveryVerificationResult result = service.verify("pfe", "backend");

        assertTrue(result.workloadHealthy());
        assertTrue(result.aiStillSeesIncident());
        assertFalse(result.resolved());
    }
}
