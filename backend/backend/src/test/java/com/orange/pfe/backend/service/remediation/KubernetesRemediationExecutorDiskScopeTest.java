package com.orange.pfe.backend.service.remediation;

import com.orange.pfe.backend.config.RemediationProperties;
import com.orange.pfe.backend.model.action.ActionType;
import com.orange.pfe.backend.model.action.AutomationMode;
import com.orange.pfe.backend.model.action.RemediationTargetKind;
import com.orange.pfe.backend.service.remediation.BridgeClient.BridgeResponse;
import org.junit.jupiter.api.Test;

import java.util.ArrayList;
import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyInt;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.times;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.verifyNoInteractions;
import static org.mockito.Mockito.when;

/**
 * DISK-E2E-158-02 : l'EXECUTEUR n'ouvre le Bridge que pour le scenario ; le pod hote privilegie historique n'est JAMAIS
 * utilisable pour le scenario ni pour NODE_DISK_FULL sur k8s-worker2 (.155) ; NODE_DISK_PRESSURE / NODE_INODES_FULL gardent
 * EXACTEMENT leur chemin d'origine. Le chemin historique est remplace par une ESPIONNE : aucun Kubernetes.
 */
class KubernetesRemediationExecutorDiskScopeTest {

    private static final List<String> TYPES = List.of("NODE_DISK_FULL", "NODE_DISK_PRESSURE", "NODE_INODES_FULL");

    /** Remplace le chemin historique (pod hote privilegie) : enregistre l'appel, n'ouvre AUCUN client Kubernetes. */
    private static final class ExecuteurEspion extends KubernetesRemediationExecutor {
        final List<String> appelsPodHote = new ArrayList<>();

        ExecuteurEspion(RemediationProperties properties, BridgeClient bridge) {
            super(properties, bridge);
        }

        @Override
        RemediationExecutionResult executeLegacyHostReclaim(String canonicalTarget, boolean dryRun) {
            appelsPodHote.add(canonicalTarget + (dryRun ? ":dry-run" : ""));
            return new RemediationExecutionResult(true, canonicalTarget, "POD-HOTE-HISTORIQUE-APPELE");
        }
    }

    private static RemediationPlan plan(String type, String target, Long incidentId) {
        return new RemediationPlan(ActionType.HOST_RECLAIM_DISK, AutomationMode.AUTO, false, true, false, false, false,
                "k8s-nodes", RemediationTargetKind.NODE, target, "DISK remediation test", type, incidentId);
    }

    private static RemediationProperties properties(boolean ancien, boolean scenario) {
        RemediationProperties properties = new RemediationProperties();
        properties.setBridgeEnabled(true);
        properties.setHostDiskRemediationEnabled(ancien);
        properties.setHostDiskE2e158Enabled(scenario);
        return properties;
    }

    private static BridgeClient bridgeOk() {
        BridgeClient bridge = mock(BridgeClient.class);
        when(bridge.sendDiskReclaimAction(any(), any(), any(), any(), any(), any(), any()))
                .thenReturn(new BridgeResponse(true, true, 0, "ok"));
        return bridge;
    }

    @Test
    void matriceCompleteDesDrapeauxCiblesEtTypes() {
        for (boolean ancien : new boolean[] {false, true}) {
            for (boolean scenario : new boolean[] {false, true}) {
                for (String target : List.of("k8s-worker1", "k8s-worker2")) {
                    for (String type : TYPES) {
                        String cas = "ancien=" + ancien + " scenario=" + scenario + " " + target + " / " + type;
                        BridgeClient bridge = bridgeOk();
                        ExecuteurEspion executor = new ExecuteurEspion(properties(ancien, scenario), bridge);

                        RemediationExecutionResult result = executor.execute(plan(type, target, 4242L));

                        boolean leScenario = "k8s-worker1".equals(target) && "NODE_DISK_FULL".equals(type);
                        boolean legacy = !"NODE_DISK_FULL".equals(type);          // PRESSURE / INODES : les DEUX workers, comme avant
                        if (leScenario && scenario) {
                            assertTrue(result.success(), cas);
                            verify(bridge, times(1)).sendDiskReclaimAction("HOST_RECLAIM_DISK", "NODE_DISK_FULL", "k8s-nodes",
                                    "k8s-worker1", "k8s-worker1", "DISK_E2E_158", 4242L);
                            assertEquals(List.of(), executor.appelsPodHote, "le pod hote n'est JAMAIS utilise pour le scenario : " + cas);
                        } else if (legacy && ancien) {
                            assertTrue(result.success(), cas);
                            assertEquals(List.of(target), executor.appelsPodHote, "chemin d'origine, inchange : " + cas);
                            verify(bridge, never()).sendDiskReclaimAction(any(), any(), any(), any(), any(), any(), any());
                        } else {
                            assertFalse(result.success(), "refus attendu : " + cas);
                            assertEquals(List.of(), executor.appelsPodHote, "aucun pod hote : " + cas);
                            verifyNoInteractions(bridge);
                        }
                    }
                }
            }
        }
    }

    @Test
    void worker2EstToujoursRefusePourNodeDiskFullQuelsQueSoientLesDrapeaux() {
        for (boolean ancien : new boolean[] {false, true}) {
            for (boolean scenario : new boolean[] {false, true}) {
                for (boolean dryRun : new boolean[] {false, true}) {
                    BridgeClient bridge = bridgeOk();
                    ExecuteurEspion executor = new ExecuteurEspion(properties(ancien, scenario), bridge);

                    RemediationExecutionResult result = executor.execute(plan("NODE_DISK_FULL", "k8s-worker2", 4242L), dryRun);

                    String cas = "ancien=" + ancien + " scenario=" + scenario + " dryRun=" + dryRun;
                    assertFalse(result.success(), cas);
                    if (ancien) {
                        assertTrue(result.message().contains("k8s-worker2/.155 jamais"), cas + " : " + result.message());
                    }
                    assertEquals(List.of(), executor.appelsPodHote, "ni pod hote ni Bridge : " + cas);
                    verifyNoInteractions(bridge);
                }
            }
        }
    }

    @Test
    void pressureEtInodesSurWorker2GardentLeCheminDOrigineSousLAncienDrapeau() {
        // Consigne : NODE_DISK_PRESSURE / NODE_INODES_FULL ne sont PAS modifies.
        for (String type : List.of("NODE_DISK_PRESSURE", "NODE_INODES_FULL")) {
            BridgeClient bridge = bridgeOk();
            ExecuteurEspion actif = new ExecuteurEspion(properties(true, false), bridge);
            assertTrue(actif.execute(plan(type, "k8s-worker2", 4242L)).success(), type);
            assertEquals(List.of("k8s-worker2"), actif.appelsPodHote, type);

            ExecuteurEspion inactif = new ExecuteurEspion(properties(false, true), bridge);
            assertFalse(inactif.execute(plan(type, "k8s-worker2", 4242L)).success(), "le drapeau du scenario n'ouvre rien : " + type);
            assertEquals(List.of(), inactif.appelsPodHote, type);
            verifyNoInteractions(bridge);
        }
    }

    @Test
    void aucunRepliVersLePodHotePrivilegiePourLeScenarioApresUnEchecDuBridge() {
        // Les DEUX drapeaux actifs : le pire cas pour un repli accidentel.
        BridgeResponse[] echecs = {
                new BridgeResponse(false, true, -1, "NOT_RECOVERED"), new BridgeResponse(true, false, 0, "validation seule"),
                new BridgeResponse(true, true, 3, "code"), null};
        for (BridgeResponse echec : echecs) {
            BridgeClient bridge = mock(BridgeClient.class);
            when(bridge.sendDiskReclaimAction(any(), any(), any(), any(), any(), any(), any())).thenReturn(echec);
            ExecuteurEspion executor = new ExecuteurEspion(properties(true, true), bridge);

            RemediationExecutionResult result = executor.execute(plan("NODE_DISK_FULL", "k8s-worker1", 4242L));

            assertFalse(result.success(), String.valueOf(echec));
            assertEquals(List.of(), executor.appelsPodHote, "repli interdit apres : " + echec);
        }

        BridgeClient lanceur = mock(BridgeClient.class);
        when(lanceur.sendDiskReclaimAction(any(), any(), any(), any(), any(), any(), any())).thenThrow(new IllegalStateException("transport"));
        ExecuteurEspion executor = new ExecuteurEspion(properties(true, true), lanceur);
        assertFalse(executor.execute(plan("NODE_DISK_FULL", "k8s-worker1", 4242L)).success());
        assertEquals(List.of(), executor.appelsPodHote, "repli interdit apres une exception du Bridge");
    }

    @Test
    void aucunRepliPourLeScenarioQuandLeBridgeEstDesactiveOuQueLIncidentEstInconnu() {
        RemediationProperties sansBridge = properties(true, true);
        sansBridge.setBridgeEnabled(false);
        ExecuteurEspion a = new ExecuteurEspion(sansBridge, bridgeOk());
        assertFalse(a.execute(plan("NODE_DISK_FULL", "k8s-worker1", 4242L)).success());
        assertEquals(List.of(), a.appelsPodHote);

        ExecuteurEspion b = new ExecuteurEspion(properties(true, true), bridgeOk());
        assertFalse(b.execute(plan("NODE_DISK_FULL", "k8s-worker1", null)).success());
        assertFalse(b.execute(plan("NODE_DISK_FULL", "k8s-worker1", 0L)).success());
        assertEquals(List.of(), b.appelsPodHote);
    }

    @Test
    void lAncienDrapeauSeulNeDonneAucuneVoieAuScenario() {
        BridgeClient bridge = bridgeOk();
        ExecuteurEspion executor = new ExecuteurEspion(properties(true, false), bridge);

        RemediationExecutionResult result = executor.execute(plan("NODE_DISK_FULL", "k8s-worker1", 4242L));

        assertFalse(result.success());
        assertTrue(result.message().contains("HOST_DISK_E2E_158_ENABLED"));
        assertEquals(List.of(), executor.appelsPodHote);
        verifyNoInteractions(bridge);
    }

    @Test
    void lesAutresActionsNeSontPasAffecteesParLesDrapeauxDisque() {
        for (boolean ancien : new boolean[] {false, true}) {
            for (boolean scenario : new boolean[] {false, true}) {
                BridgeClient bridge = mock(BridgeClient.class);
                when(bridge.sendAction(eq("HOST_SERVICE_CPU_QUOTA"), eq("NODE_HIGH_CPU"), eq("k8s-nodes"), eq("k8s-worker1"),
                        eq("k8s-worker1"), eq("helios-cpuload.service"), anyInt(), anyInt()))
                        .thenReturn(new BridgeResponse(true, true, 0, "CPU quota applied"));
                ExecuteurEspion executor = new ExecuteurEspion(properties(ancien, scenario), bridge);

                RemediationExecutionResult result = executor.execute(new RemediationPlan(
                        ActionType.HOST_SERVICE_CPU_QUOTA, AutomationMode.AUTO, false, true, false, false, false,
                        "k8s-nodes", RemediationTargetKind.NODE, "k8s-worker1", "CPU quota remediation test", "NODE_HIGH_CPU"));

                assertTrue(result.success(), "ancien=" + ancien + " scenario=" + scenario);
                verify(bridge).sendAction(eq("HOST_SERVICE_CPU_QUOTA"), eq("NODE_HIGH_CPU"), eq("k8s-nodes"), eq("k8s-worker1"),
                        eq("k8s-worker1"), eq("helios-cpuload.service"), anyInt(), anyInt());
                verify(bridge, never()).sendDiskReclaimAction(any(), any(), any(), any(), any(), any(), any());
                assertEquals(List.of(), executor.appelsPodHote);
            }
        }
    }
}
