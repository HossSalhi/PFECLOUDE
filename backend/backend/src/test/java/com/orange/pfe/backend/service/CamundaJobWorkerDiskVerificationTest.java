package com.orange.pfe.backend.service;

import com.orange.pfe.backend.config.RemediationProperties;
import com.orange.pfe.backend.repository.HumanTaskRepository;
import com.orange.pfe.backend.repository.IncidentActionRepository;
import com.orange.pfe.backend.service.remediation.HostCpuVerificationService;
import com.orange.pfe.backend.service.remediation.HostDiskVerificationService;
import com.orange.pfe.backend.service.remediation.HostMemoryVerificationService;
import com.orange.pfe.backend.service.remediation.RecoveryVerificationResult;
import com.orange.pfe.backend.service.remediation.RecoveryVerificationService;
import com.orange.pfe.backend.service.remediation.RemediationExecutor;
import com.orange.pfe.backend.service.remediation.RemediationMetricsService;
import io.camunda.client.CamundaClient;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.ObjectProvider;

import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.Mockito.*;

class CamundaJobWorkerDiskVerificationTest {

    private HostDiskVerificationService diskVerifier;
    private RemediationProperties properties;
    private WorkflowService workflowService;
    private CamundaJobWorkerLifecycle worker;

    @SuppressWarnings("unchecked")
    @BeforeEach
    void setUp() {
        diskVerifier = mock(HostDiskVerificationService.class);
        properties = mock(RemediationProperties.class);
        workflowService = mock(WorkflowService.class);

        when(properties.getVerificationRetryAttempts()).thenReturn(5);
        // Les tests restent courts, tout en respectant le délai minimum du code.
        when(properties.getVerificationRetryDelaySeconds()).thenReturn(1);

        ObjectProvider<AIOpsOrchestrationService> orchestrationProvider =
                mock(ObjectProvider.class);
        ObjectProvider<InfrastructureHealthScheduler> infraProvider =
                mock(ObjectProvider.class);

        worker = new CamundaJobWorkerLifecycle(
                mock(CamundaClient.class),
                mock(CamundaWorkflowService.class),
                workflowService,
                mock(IncidentActionRepository.class),
                mock(ActionService.class),
                mock(RemediationExecutor.class),
                mock(RecoveryVerificationService.class),
                mock(HostCpuVerificationService.class),
                diskVerifier,
                mock(HostMemoryVerificationService.class),
                mock(RemediationMetricsService.class),
                mock(HumanTaskRepository.class),
                mock(HumanTaskService.class),
                properties,
                mock(IncidentHistoryService.class),
                mock(NotificationService.class),
                orchestrationProvider,
                infraProvider,
                mock(IncidentCardService.class));
    }

    private static RecoveryVerificationResult healthy() {
        return new RecoveryVerificationResult(true, false, "measured DISK metrics healthy");
    }

    private static RecoveryVerificationResult unhealthy() {
        return new RecoveryVerificationResult(false, true, "DISK recovery not confirmed");
    }

    @Test
    void twoConsecutiveHealthySamplesResolveDiskIncident() throws InterruptedException {
        when(diskVerifier.verify("k8s-worker1", "NODE_DISK_FULL"))
                .thenReturn(healthy(), healthy());

        RecoveryVerificationResult result =
                worker.verifyDiskRecovery(77L, "k8s-worker1", "NODE_DISK_FULL");

        assertTrue(result.resolved());
        verify(diskVerifier, times(2)).verify("k8s-worker1", "NODE_DISK_FULL");
    }

    @Test
    void unhealthySampleResetsConsecutiveHealthyCounter() throws InterruptedException {
        when(diskVerifier.verify("k8s-worker1", "NODE_INODES_FULL"))
                .thenReturn(healthy(), unhealthy(), healthy(), healthy());

        RecoveryVerificationResult result =
                worker.verifyDiskRecovery(77L, "k8s-worker1", "NODE_INODES_FULL");

        assertTrue(result.resolved());
        verify(diskVerifier, times(4)).verify("k8s-worker1", "NODE_INODES_FULL");
    }

    @Test
    void oneHealthySampleIsNotEnoughToResolveIncident() throws InterruptedException {
        when(properties.getVerificationRetryAttempts()).thenReturn(2);
        when(diskVerifier.verify("k8s-worker1", "NODE_DISK_PRESSURE"))
                .thenReturn(healthy(), unhealthy());

        RecoveryVerificationResult result =
                worker.verifyDiskRecovery(77L, "k8s-worker1", "NODE_DISK_PRESSURE");

        assertFalse(result.resolved());
        verify(diskVerifier, times(2)).verify("k8s-worker1", "NODE_DISK_PRESSURE");
    }

    @Test
    void repeatedUnhealthySamplesKeepIncidentUnresolved() throws InterruptedException {
        when(properties.getVerificationRetryAttempts()).thenReturn(2);
        when(diskVerifier.verify("k8s-worker2", "NODE_DISK_FULL"))
                .thenReturn(unhealthy(), unhealthy());

        RecoveryVerificationResult result =
                worker.verifyDiskRecovery(77L, "k8s-worker2", "NODE_DISK_FULL");

        assertFalse(result.resolved());
        verify(diskVerifier, times(2)).verify("k8s-worker2", "NODE_DISK_FULL");
    }
}