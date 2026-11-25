package com.orange.pfe.backend.service.remediation;

import com.orange.pfe.backend.config.RemediationProperties;
import com.orange.pfe.backend.model.action.ActionType;
import com.orange.pfe.backend.model.action.AutomationMode;
import com.orange.pfe.backend.model.action.RemediationTargetKind;
import com.orange.pfe.backend.service.remediation.BridgeClient.BridgeResponse;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.Mockito.*;

class KubernetesRemediationExecutorBridgeTest {

    private RemediationProperties properties;
    private BridgeClient bridge;
    private KubernetesRemediationExecutor executor;

    @BeforeEach
    void setUp() {
        properties = new RemediationProperties();
        properties.setBridgeEnabled(true);
        bridge = mock(BridgeClient.class);
        executor = new KubernetesRemediationExecutor(properties, bridge);
    }

    private RemediationPlan plan(String type, String target) {
        return new RemediationPlan(
                ActionType.HOST_SERVICE_CPU_QUOTA,
                AutomationMode.AUTO,
                false,
                true,
                false,
                false,
                false,
                "k8s-nodes",
                RemediationTargetKind.NODE,
                target,
                "CPU quota remediation test",
                type
        );
    }

    private RemediationPlan memoryPlan(String type, String target) {
        return new RemediationPlan(
                ActionType.HOST_SERVICE_MEMORY_LIMIT,
                AutomationMode.AUTO,
                false, true, false, false, false,
                "k8s-nodes",
                RemediationTargetKind.NODE,
                target,
                "RAM remediation test",
                type
        );
    }

    @Test
    void sendsOnlyTheExpectedCpuActionForAnApprovedClusterNode() {
        when(bridge.sendAction(
                "HOST_SERVICE_CPU_QUOTA", "NODE_HIGH_CPU",
                "k8s-nodes", "k8s-worker1", "k8s-worker1",
                "helios-cpuload.service", 25, 240))
                .thenReturn(new BridgeResponse(true, true, 0, "CPU quota applied"));

        RemediationExecutionResult result =
                executor.execute(plan("NODE_HIGH_CPU", "k8s-worker1"));

        assertTrue(result.success());
        assertEquals("k8s-worker1", result.targetName());
        verify(bridge).sendAction(
                "HOST_SERVICE_CPU_QUOTA", "NODE_HIGH_CPU",
                "k8s-nodes", "k8s-worker1", "k8s-worker1",
                "helios-cpuload.service", 25, 240);
        verifyNoMoreInteractions(bridge);
    }

    @Test
    void dryRunNeverSendsTheBridgeRequest() {
        RemediationExecutionResult result =
                executor.execute(plan("NODE_HIGH_CPU", "k8s-worker1"), true);

        assertTrue(result.success());
        assertTrue(result.message().contains("[DRY-RUN]"));
        verifyNoInteractions(bridge);
    }

    @Test
    void disabledBridgeNeverSendsTheRequest() {
        properties.setBridgeEnabled(false);

        RemediationExecutionResult result =
                executor.execute(plan("NODE_HIGH_CPU", "k8s-worker1"));

        assertFalse(result.success());
        assertTrue(result.message().contains("Bridge desactive"));
        verifyNoInteractions(bridge);
    }

    @Test
    void unapprovedTargetIsRejectedBeforeCallingBridge() {
        RemediationExecutionResult result =
                executor.execute(plan("NODE_HIGH_CPU", "k8s-lb2"));

        assertFalse(result.success());
        assertTrue(result.message().contains("workers CPU de laboratoire"));
        verifyNoInteractions(bridge);
    }

    @Test
    void nonAllowlistedNodeIsRejectedForCpuQuota() {
        // CPU-MASTER-20261029 : k8s-master est désormais autorisé (quota sur helios-cpuload.service
        // uniquement) ; on prouve ici le refus d'un nœud RÉELLEMENT hors allowlist.
        RemediationExecutionResult result =
                executor.execute(plan("NODE_HIGH_CPU", "k8s-worker999"));

        assertFalse(result.success());
        assertTrue(result.message().contains("workers CPU de laboratoire"));
        verifyNoInteractions(bridge);
    }
    @Test
    void unsupportedIncidentTypeIsRejectedBeforeCallingBridge() {
        RemediationExecutionResult result =
                executor.execute(plan("NODE_HIGH_MEMORY", "k8s-worker1"));

        assertFalse(result.success());
        assertTrue(result.message().contains("type d'incident"));
        verifyNoInteractions(bridge);
    }

    @Test
    void acceptedButNotExecutedIsNotReportedAsSuccessfulRemediation() {
        when(bridge.sendAction(
                "HOST_SERVICE_CPU_QUOTA", "NODE_CPU_SATURATION",
                "k8s-nodes", "k8s-worker1", "k8s-worker1",
                "helios-cpuload.service", 25, 240))
                .thenReturn(new BridgeResponse(true, false, 0, "Action not executed"));

        RemediationExecutionResult result =
                executor.execute(plan("NODE_CPU_SATURATION", "k8s-worker1"));

        assertFalse(result.success());
        verify(bridge).sendAction(
                "HOST_SERVICE_CPU_QUOTA", "NODE_CPU_SATURATION",
                "k8s-nodes", "k8s-worker1", "k8s-worker1",
                "helios-cpuload.service", 25, 240);
    }

    @Test
    void disabledCpuRemediationFlagNeverSendsTheRequest() {
        // CPU-02 : garde a l EXECUTION couvrant les instances Camunda DEJA soumises.
        // Meme avec le Bridge actif et un plan valide, HOST_CPU_REMEDIATION_ENABLED=false
        // refuse l execution du quota (proprietaire = Camunda v25).
        properties.setHostCpuRemediationEnabled(false);

        RemediationExecutionResult result =
                executor.execute(plan("NODE_HIGH_CPU", "k8s-worker1"));

        assertFalse(result.success());
        assertTrue(result.message().contains("HOST_CPU_REMEDIATION_ENABLED=false"));
        verifyNoInteractions(bridge);
    }

    // ----------------------------------------------------------------------------------------------
    // STOP_CPU_LOAD_SERVICE : arrêt automatique de la charge de LABORATOIRE après résolution CPU réelle.
    // ----------------------------------------------------------------------------------------------

    @Test
    void sendsRamLimitToBridgeForApprovedLabWorker() {
        properties.setHostMemoryRemediationEnabled(true);
        when(bridge.sendMemoryLimitAction(
                "HOST_SERVICE_MEMORY_LIMIT", "NODE_HIGH_MEMORY",
                "k8s-nodes", "k8s-worker1", "k8s-worker1",
                "helios-memload.service", 256, 240))
                .thenReturn(new BridgeResponse(true, true, 0, "RAM action accepted"));

        RemediationExecutionResult result =
                executor.execute(memoryPlan("NODE_HIGH_MEMORY", "k8s-worker1"));

        assertTrue(result.success());
        verify(bridge).sendMemoryLimitAction(
                "HOST_SERVICE_MEMORY_LIMIT", "NODE_HIGH_MEMORY",
                "k8s-nodes", "k8s-worker1", "k8s-worker1",
                "helios-memload.service", 256, 240);
        verifyNoMoreInteractions(bridge);
    }

    @Test
    void disabledRamFlagNeverCallsBridge() {
        properties.setHostMemoryRemediationEnabled(false);

        RemediationExecutionResult result =
                executor.execute(memoryPlan("NODE_HIGH_MEMORY", "k8s-worker1"));

        assertFalse(result.success());
        assertTrue(result.message().contains("flag desactive"));
        verifyNoInteractions(bridge);
    }

    @Test
    void ramRejectsWorkerOutsideLabAllowlist() {
        properties.setHostMemoryRemediationEnabled(true);

        RemediationExecutionResult result =
                executor.execute(memoryPlan("NODE_HIGH_MEMORY", "k8s-worker2"));

        assertFalse(result.success());
        assertTrue(result.message().contains("k8s-worker1"));
        verifyNoInteractions(bridge);
    }

    @Test
    void ramRejectsUnsupportedIncidentType() {
        properties.setHostMemoryRemediationEnabled(true);

        RemediationExecutionResult result =
                executor.execute(memoryPlan("NODE_DISK_FULL", "k8s-worker1"));

        assertFalse(result.success());
        assertTrue(result.message().contains("type incident"));
        verifyNoInteractions(bridge);
    }

    @Test
    void stopsCpuLoadWithTheDedicatedActionForAnApprovedWorkerOnSuccess() {
        // Arrêt APRÈS récupération confirmée : action dédiée, bonne VM, bon service (résolu par le Bridge),
        // un seul appel (pas de boucle).
        when(bridge.sendAction("STOP_CPU_LOAD_SERVICE", "NODE_HIGH_CPU",
                "k8s-nodes", "k8s-worker1", "k8s-worker1"))
                .thenReturn(new BridgeResponse(true, true, 0, "helios-cpuload.service arrete"));

        RemediationExecutionResult r = executor.stopCpuLoadServiceAfterRecovery(
                "k8s-nodes", "k8s-worker1", "NODE_HIGH_CPU");

        assertTrue(r.success());
        assertEquals("k8s-worker1", r.targetName());
        verify(bridge).sendAction("STOP_CPU_LOAD_SERVICE", "NODE_HIGH_CPU",
                "k8s-nodes", "k8s-worker1", "k8s-worker1");
        verifyNoMoreInteractions(bridge);
    }

    @Test
    void stopCpuLoadRejectsNonCpuIncidentWithoutCallingBridge() {
        RemediationExecutionResult r = executor.stopCpuLoadServiceAfterRecovery(
                "k8s-nodes", "k8s-worker1", "NODE_DISK_FULL");

        assertFalse(r.success());
        assertTrue(r.message().contains("non CPU"));
        verifyNoInteractions(bridge);
    }

    @Test
    void stopCpuLoadRejectsNonLabWorkerWithoutCallingBridge() {
        RemediationExecutionResult r = executor.stopCpuLoadServiceAfterRecovery(
                "k8s-nodes", "k8s-worker999", "NODE_HIGH_CPU");

        assertFalse(r.success());
        assertTrue(r.message().contains("workers CPU de laboratoire"));
        verifyNoInteractions(bridge);
    }

    @Test
    void stopCpuLoadRejectsForeignNamespaceWithoutCallingBridge() {
        RemediationExecutionResult r = executor.stopCpuLoadServiceAfterRecovery(
                "pfe", "k8s-worker1", "NODE_HIGH_CPU");

        assertFalse(r.success());
        assertTrue(r.message().contains("namespace"));
        verifyNoInteractions(bridge);
    }

    @Test
    void stopCpuLoadReportsFailureWhenBridgeDoesNotConfirmExecution() {
        // Jamais annoncé réussi si systemctl stop n'est pas confirmé (execute=false).
        when(bridge.sendAction("STOP_CPU_LOAD_SERVICE", "NODE_CPU_SATURATION",
                "k8s-nodes", "k8s-worker2", "k8s-worker2"))
                .thenReturn(new BridgeResponse(true, false, 0, "non execute"));

        RemediationExecutionResult r = executor.stopCpuLoadServiceAfterRecovery(
                "k8s-nodes", "k8s-worker2", "NODE_CPU_SATURATION");

        assertFalse(r.success());
        verify(bridge).sendAction("STOP_CPU_LOAD_SERVICE", "NODE_CPU_SATURATION",
                "k8s-nodes", "k8s-worker2", "k8s-worker2");
    }

    @Test
    void stopCpuLoadReportsFailureOnNonZeroExitCode() {
        when(bridge.sendAction("STOP_CPU_LOAD_SERVICE", "NODE_HIGH_CPU",
                "k8s-nodes", "k8s-worker1", "k8s-worker1"))
                .thenReturn(new BridgeResponse(true, true, 3, "echec stop"));

        RemediationExecutionResult r = executor.stopCpuLoadServiceAfterRecovery(
                "k8s-nodes", "k8s-worker1", "NODE_HIGH_CPU");

        assertFalse(r.success());
    }

    @Test
    void stopCpuLoadReportsFailureWhenBridgeThrows() {
        when(bridge.sendAction("STOP_CPU_LOAD_SERVICE", "NODE_HIGH_CPU",
                "k8s-nodes", "k8s-worker1", "k8s-worker1"))
                .thenThrow(new RuntimeException("io"));

        RemediationExecutionResult r = executor.stopCpuLoadServiceAfterRecovery(
                "k8s-nodes", "k8s-worker1", "NODE_HIGH_CPU");

        assertFalse(r.success());
    }

    @Test
    void stopCpuLoadDisabledByFlagNeverCallsBridge() {
        properties.setAutoStopCpuLoadEnabled(false);

        RemediationExecutionResult r = executor.stopCpuLoadServiceAfterRecovery(
                "k8s-nodes", "k8s-worker1", "NODE_HIGH_CPU");

        assertFalse(r.success());
        assertTrue(r.message().contains("auto-stop-cpu-load-enabled"));
        verifyNoInteractions(bridge);
    }

    @Test
    void stopCpuLoadWithDisabledBridgeNeverCallsBridge() {
        properties.setBridgeEnabled(false);

        RemediationExecutionResult r = executor.stopCpuLoadServiceAfterRecovery(
                "k8s-nodes", "k8s-worker1", "NODE_HIGH_CPU");

        assertFalse(r.success());
        verifyNoInteractions(bridge);
    }
}