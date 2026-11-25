package com.orange.pfe.backend.service.remediation;

import com.orange.pfe.backend.config.RemediationProperties;
import com.orange.pfe.backend.model.action.ActionType;
import com.orange.pfe.backend.model.action.AutomationMode;
import com.orange.pfe.backend.model.action.RemediationTargetKind;
import com.orange.pfe.backend.service.remediation.BridgeClient.BridgeResponse;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.verifyNoInteractions;
import static org.mockito.Mockito.verifyNoMoreInteractions;
import static org.mockito.Mockito.when;

/**
 * DISK-E2E-158-01 : le Backend confie le nettoyage du scenario DISK_E2E_158 (worker1 + NODE_DISK_FULL) au Bridge, et a
 * LUI SEUL. Un succes de l'executeur signifie « le Bridge a supprime les fichiers exacts ET mesure &lt;= 77 % » ; il ne vaut
 * jamais resolution (la verification independante reste a faire). Aucun Kubernetes, aucun reseau : le Bridge est un mock.
 */
class KubernetesRemediationExecutorDiskBridgeTest {

    private static final Long INCIDENT_ID = 4242L;

    private RemediationProperties properties;
    private BridgeClient bridge;
    private KubernetesRemediationExecutor executor;

    @BeforeEach
    void setUp() {
        properties = new RemediationProperties();
        properties.setBridgeEnabled(true);
        properties.setHostDiskE2e158Enabled(true);          // DISK-E2E-158-02 : drapeau DEDIE du scenario
        bridge = mock(BridgeClient.class);
        executor = new KubernetesRemediationExecutor(properties, bridge);
    }

    private static RemediationPlan diskPlan(String namespace, String type, String target, Long incidentId) {
        return new RemediationPlan(
                ActionType.HOST_RECLAIM_DISK,
                AutomationMode.AUTO,
                false, true, false, false, false,
                namespace,
                RemediationTargetKind.NODE,
                target,
                "DISK remediation test",
                type,
                incidentId);
    }

    private static RemediationPlan scenarioPlan() {
        return diskPlan("k8s-nodes", "NODE_DISK_FULL", "k8s-worker1", INCIDENT_ID);
    }

    @Test
    void sendsExactlyTheScenarioRequestForWorker1() {
        when(bridge.sendDiskReclaimAction(
                "HOST_RECLAIM_DISK", "NODE_DISK_FULL", "k8s-nodes",
                "k8s-worker1", "k8s-worker1", "DISK_E2E_158", INCIDENT_ID))
                .thenReturn(new BridgeResponse(true, true, 0,
                        "3 fichier(s) supprime(s) ; occupation 81.25 % -> 70.00 %"));

        RemediationExecutionResult result = executor.execute(scenarioPlan());

        assertTrue(result.success());
        assertEquals("k8s-worker1", result.targetName());
        assertTrue(result.message().contains("occupation"));
        verify(bridge).sendDiskReclaimAction(
                "HOST_RECLAIM_DISK", "NODE_DISK_FULL", "k8s-nodes",
                "k8s-worker1", "k8s-worker1", "DISK_E2E_158", INCIDENT_ID);
        verifyNoMoreInteractions(bridge);
    }

    @Test
    void sendsCanonicalValuesEvenWhenThePlanUsesAnotherCase() {
        when(bridge.sendDiskReclaimAction(
                "HOST_RECLAIM_DISK", "NODE_DISK_FULL", "k8s-nodes",
                "k8s-worker1", "k8s-worker1", "DISK_E2E_158", INCIDENT_ID))
                .thenReturn(new BridgeResponse(true, true, 0, "ok"));

        RemediationExecutionResult result = executor.execute(
                diskPlan("k8s-nodes", "node_disk_full", " K8S-Worker1 ", INCIDENT_ID));

        assertTrue(result.success());
        verify(bridge).sendDiskReclaimAction(
                "HOST_RECLAIM_DISK", "NODE_DISK_FULL", "k8s-nodes",
                "k8s-worker1", "k8s-worker1", "DISK_E2E_158", INCIDENT_ID);
        verifyNoMoreInteractions(bridge);
    }

    @Test
    void dryRunNeverSendsTheBridgeRequest() {
        RemediationExecutionResult result = executor.execute(scenarioPlan(), true);

        assertTrue(result.success());
        assertTrue(result.message().contains("[DRY-RUN]"));
        verifyNoInteractions(bridge);
    }

    @Test
    void disabledScenarioFlagNeverSendsTheRequestEvenWithTheLegacyGlobalFlag() {
        properties.setHostDiskE2e158Enabled(false);
        properties.setHostDiskRemediationEnabled(true);     // l'ancien drapeau global n'active PAS le scenario

        RemediationExecutionResult result = executor.execute(scenarioPlan());

        assertFalse(result.success());
        assertTrue(result.message().contains("HOST_RECLAIM_DISK"));
        assertTrue(result.message().contains("HOST_DISK_E2E_158_ENABLED"));
        verifyNoInteractions(bridge);
    }

    @Test
    void disabledBridgeNeverSendsTheRequest() {
        properties.setBridgeEnabled(false);

        RemediationExecutionResult result = executor.execute(scenarioPlan());

        assertFalse(result.success());
        assertTrue(result.message().contains("Bridge desactive"));
        verifyNoInteractions(bridge);
    }

    @Test
    void missingOrInvalidIncidentIsRefusedBeforeCallingTheBridge() {
        for (Long incidentId : new Long[] {null, 0L, -5L}) {
            RemediationExecutionResult result = executor.execute(
                    diskPlan("k8s-nodes", "NODE_DISK_FULL", "k8s-worker1", incidentId));

            assertFalse(result.success(), "incidentId=" + incidentId);
            assertTrue(result.message().contains("incident source inconnu"), result.message());
        }
        verifyNoInteractions(bridge);
    }

    @Test
    void acceptedButNotExecutedIsNotReportedAsSuccess() {
        when(bridge.sendDiskReclaimAction(any(), any(), any(), any(), any(), any(), any()))
                .thenReturn(new BridgeResponse(true, false, 0, "validation seule"));

        RemediationExecutionResult result = executor.execute(scenarioPlan());

        assertFalse(result.success());
    }

    @Test
    void nonZeroExitCodeIsNotReportedAsSuccess() {
        when(bridge.sendDiskReclaimAction(any(), any(), any(), any(), any(), any(), any()))
                .thenReturn(new BridgeResponse(true, true, 1, "code retour"));

        assertFalse(executor.execute(scenarioPlan()).success());
    }

    @Test
    void aBridgeRefusalIsAFailureAndItsReasonIsPropagated() {
        when(bridge.sendDiskReclaimAction(any(), any(), any(), any(), any(), any(), any()))
                .thenReturn(new BridgeResponse(false, true, -1,
                        "fichiers supprimes mais occupation 79.00 % > 77 % : recuperation NON confirmee"));

        RemediationExecutionResult result = executor.execute(scenarioPlan());

        assertFalse(result.success());
        assertTrue(result.message().contains("recuperation NON confirmee"));
    }

    @Test
    void aForeignDiskIncidentReportedByTheBridgeIsAFailureNotASuccess() {
        when(bridge.sendDiskReclaimAction(any(), any(), any(), any(), any(), any(), any()))
                .thenReturn(new BridgeResponse(false, false, -1,
                        "aucun fichier du scenario DISK_E2E_158 ; incident etranger, RIEN n a ete touche"));

        RemediationExecutionResult result = executor.execute(scenarioPlan());

        assertFalse(result.success());
        assertTrue(result.message().contains("RIEN n a ete touche"));
    }

    @Test
    void absentResponseOrBridgeExceptionIsNeverASuccess() {
        when(bridge.sendDiskReclaimAction(any(), any(), any(), any(), any(), any(), any()))
                .thenReturn(null)
                .thenThrow(new IllegalStateException("transport"));

        assertFalse(executor.execute(scenarioPlan()).success());

        RemediationExecutionResult afterException = executor.execute(scenarioPlan());
        assertFalse(afterException.success());
        assertFalse(afterException.message().contains("transport"), "le detail technique n'est pas expose");
    }

    @Test
    void blankBridgeMessageGetsADescriptiveFailureText() {
        when(bridge.sendDiskReclaimAction(any(), any(), any(), any(), any(), any(), any()))
                .thenReturn(new BridgeResponse(false, false, -1, ""));

        RemediationExecutionResult result = executor.execute(scenarioPlan());

        assertFalse(result.success());
        assertTrue(result.message().contains("n'a pas confirme"));
    }

    @Test
    void foreignNamespaceTargetOrTypeAreRefusedBeforeCallingTheBridge() {
        // Precedence d'origine : pour tout ce qui n'est pas le scenario, l'ancien drapeau global est controle en premier ;
        // on l'active ici pour atteindre les controles de namespace / cible / type.
        properties.setHostDiskRemediationEnabled(true);
        RemediationExecutionResult namespace = executor.execute(
                diskPlan("pfe", "NODE_DISK_FULL", "k8s-worker1", INCIDENT_ID));
        RemediationExecutionResult master = executor.execute(
                diskPlan("k8s-nodes", "NODE_DISK_FULL", "k8s-master", INCIDENT_ID));
        RemediationExecutionResult type = executor.execute(
                diskPlan("k8s-nodes", "NODE_HIGH_CPU", "k8s-worker1", INCIDENT_ID));

        assertFalse(namespace.success());
        assertFalse(master.success());
        assertFalse(type.success());
        assertTrue(master.message().contains("allowlist"));
        verifyNoInteractions(bridge);
    }

    /**
     * Les autres cibles/types gardent leur chemin d'origine (commande hote bornee, hors Bridge). On ne verifie ici QUE
     * l'absence d'appel au Bridge : en dry-run, ce chemin ne contacte aucun cluster (client hors ligne).
     */
    @Test
    void otherTargetsAndTypesNeverReachTheBridgeDiskAction() {
        properties.setHostDiskRemediationEnabled(true);      // ancien drapeau : les chemins d'origine s'executent (dry-run)
        String[][] cases = {
                {"NODE_DISK_FULL", "k8s-worker2"},
                {"NODE_INODES_FULL", "k8s-worker1"},
                {"NODE_DISK_PRESSURE", "k8s-worker1"},
        };
        for (String[] c : cases) {
            executor.execute(diskPlan("k8s-nodes", c[0], c[1], INCIDENT_ID), true);
        }
        verifyNoInteractions(bridge);
    }
}
