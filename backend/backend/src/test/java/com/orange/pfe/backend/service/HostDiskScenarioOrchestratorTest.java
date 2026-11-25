package com.orange.pfe.backend.service;

import com.orange.pfe.backend.config.RemediationProperties;
import com.orange.pfe.backend.dto.AIOpsAnalyzeRequest;
import com.orange.pfe.backend.dto.AIPredictResponse;
import com.orange.pfe.backend.model.action.ActionType;
import com.orange.pfe.backend.model.action.AutomationMode;
import com.orange.pfe.backend.model.action.IncidentAction;
import com.orange.pfe.backend.model.action.RemediationTargetKind;
import com.orange.pfe.backend.model.core.Incident;
import com.orange.pfe.backend.model.enums.IncidentSeverity;
import com.orange.pfe.backend.model.enums.IncidentStatus;
import com.orange.pfe.backend.model.workflow.WorkflowInstance;
import com.orange.pfe.backend.repository.IncidentRepository;
import com.orange.pfe.backend.service.remediation.RemediationPlan;
import com.orange.pfe.backend.service.remediation.RemediationPolicyService;
import org.junit.jupiter.api.Test;
import org.springframework.test.util.ReflectionTestUtils;

import java.util.List;
import java.util.Optional;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyBoolean;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.contains;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.times;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.verifyNoInteractions;
import static org.mockito.Mockito.when;

/**
 * DISK-E2E-158-02 : dispatch de la remediation DISK pour toutes les combinaisons de drapeaux, de cibles et de types.
 *
 * <pre>
 *   k8s-worker1 + NODE_DISK_FULL                    : demarre SSI HOST_DISK_E2E_158_ENABLED (l'ancien drapeau n'y change rien)
 *   k8s-worker2 (.155) + NODE_DISK_FULL             : ne demarre JAMAIS (aucun appel a la politique), quels que soient les drapeaux
 *   k8s-worker1/2 + NODE_DISK_PRESSURE / INODES_FULL: demarre SSI HOST_DISK_REMEDIATION_ENABLED (chemin d'origine STRICTEMENT inchange)
 * </pre>
 */
class HostDiskScenarioOrchestratorTest {

    private static final List<String> TYPES = List.of("NODE_DISK_FULL", "NODE_DISK_PRESSURE", "NODE_INODES_FULL");

    /** Un orchestrateur frais (mocks neufs) par cas : aucune interaction ne fuit d'un cas a l'autre. */
    private static final class Fixture {
        final RemediationProperties properties = new RemediationProperties();
        final RemediationPolicyService policy = mock(RemediationPolicyService.class);
        final ActionService actionService = mock(ActionService.class);
        final WorkflowService workflowService = mock(WorkflowService.class);
        final CamundaWorkflowService camundaWorkflowService = mock(CamundaWorkflowService.class);
        final IncidentHistoryService historyService = mock(IncidentHistoryService.class);
        final RemediationLaunchFailureService launchFailureService = mock(RemediationLaunchFailureService.class);
        final IncidentRepository incidentRepository = mock(IncidentRepository.class);
        final HostDiskIncidentRemediationOrchestrator orchestrator;

        Fixture(boolean ancienDrapeau, boolean drapeauScenario) {
            properties.setHostDiskRemediationEnabled(ancienDrapeau);
            properties.setHostDiskE2e158Enabled(drapeauScenario);
            orchestrator = new HostDiskIncidentRemediationOrchestrator(
                    properties, policy, actionService, workflowService, camundaWorkflowService,
                    historyService, launchFailureService, incidentRepository);
        }

        Incident incident(String type, String target) {
            Incident incident = new Incident();
            ReflectionTestUtils.setField(incident, "id", 77L);
            incident.setNamespace("k8s-nodes");
            incident.setApp(target);
            incident.setIncidentType(type);
            incident.setSeverity(IncidentSeverity.HIGH);
            incident.setStatus(IncidentStatus.OPEN);
            when(incidentRepository.findById(77L)).thenReturn(Optional.of(incident));
            return incident;
        }

        void politiqueAccepte(String type, String target) {
            when(policy.buildPlan(any(AIOpsAnalyzeRequest.class), any(AIPredictResponse.class)))
                    .thenReturn(new RemediationPlan(ActionType.HOST_RECLAIM_DISK, AutomationMode.AUTO, false, true, false, false, false,
                            "k8s-nodes", RemediationTargetKind.NODE, target, "policy accepted", type));
            WorkflowInstance workflow = mock(WorkflowInstance.class);
            IncidentAction action = mock(IncidentAction.class);
            when(workflowService.startWorkflow(any(), anyString(), anyString(), anyString())).thenReturn(workflow);
            when(actionService.createAction(any(), any(), anyString(), anyString(), anyString(), any(), anyString(), any()))
                    .thenReturn(action);
            when(camundaWorkflowService.startWorkflowWithOutcome(any(), any(), any(), any(), anyBoolean()))
                    .thenReturn(CamundaLaunchOutcome.CAMUNDA_STARTED);
        }
    }

    private static boolean demarre(boolean ancien, boolean scenario, String target, String type) {
        if ("NODE_DISK_FULL".equals(type)) {
            return "k8s-worker1".equals(target) && scenario;          // scenario : drapeau dedie seul ; .155 : JAMAIS
        }
        return ("k8s-worker1".equals(target) || "k8s-worker2".equals(target)) && ancien;     // PRESSURE / INODES : comme avant
    }

    @Test
    void matriceCompleteDesDrapeauxCiblesEtTypes() {
        for (boolean ancien : new boolean[] {false, true}) {
            for (boolean scenario : new boolean[] {false, true}) {
                for (String target : List.of("k8s-worker1", "k8s-worker2")) {
                    for (String type : TYPES) {
                        String cas = "ancien=" + ancien + " scenario=" + scenario + " " + target + " / " + type;
                        Fixture f = new Fixture(ancien, scenario);
                        Incident incident = f.incident(type, target);
                        f.politiqueAccepte(type, target);

                        f.orchestrator.startIfEligible(incident);

                        if (demarre(ancien, scenario, target, type)) {
                            verify(f.actionService, times(1)).createAction(
                                    eq(incident), eq(ActionType.HOST_RECLAIM_DISK), anyString(), eq("HELIOS-DETERMINISTIC"),
                                    eq("k8s-nodes"), eq(RemediationTargetKind.NODE), eq(target), eq(AutomationMode.AUTO));
                            assertEquals(IncidentStatus.IN_PROGRESS, incident.getStatus(), cas);
                        } else {
                            verifyNoInteractions(f.workflowService, f.actionService, f.camundaWorkflowService);
                            assertEquals(IncidentStatus.OPEN, incident.getStatus(), cas);
                        }
                    }
                }
            }
        }
    }

    @Test
    void worker2NAppelleJamaisLaPolitiqueNiLeFluxDeTravailPourNodeDiskFull() {
        for (boolean ancien : new boolean[] {false, true}) {
            for (boolean scenario : new boolean[] {false, true}) {
                Fixture f = new Fixture(ancien, scenario);
                Incident incident = f.incident("NODE_DISK_FULL", "k8s-worker2");

                f.orchestrator.startIfEligible(incident);

                verifyNoInteractions(f.policy, f.workflowService, f.actionService, f.camundaWorkflowService);
                verify(f.historyService).record(eq(incident), eq(IncidentStatus.OPEN),
                        contains("NODE_DISK_FULL is eligible only for the DISK_E2E_158 scenario"));
                assertEquals(IncidentStatus.OPEN, incident.getStatus(), "ancien=" + ancien + " scenario=" + scenario);
            }
        }
    }

    @Test
    void pressureEtInodesSurLesDeuxWorkersDemarrentSousLAncienDrapeauComme() {
        // Consigne : NODE_DISK_PRESSURE / NODE_INODES_FULL ne sont PAS modifies (memes cibles qu'avant, ancien drapeau).
        for (String target : List.of("k8s-worker1", "k8s-worker2")) {
            for (String type : List.of("NODE_DISK_PRESSURE", "NODE_INODES_FULL")) {
                Fixture ancienSeul = new Fixture(true, false);
                Incident incident = ancienSeul.incident(type, target);
                ancienSeul.politiqueAccepte(type, target);
                ancienSeul.orchestrator.startIfEligible(incident);
                verify(ancienSeul.actionService, times(1)).createAction(
                        eq(incident), eq(ActionType.HOST_RECLAIM_DISK), anyString(), eq("HELIOS-DETERMINISTIC"),
                        eq("k8s-nodes"), eq(RemediationTargetKind.NODE), eq(target), eq(AutomationMode.AUTO));
                assertEquals(IncidentStatus.IN_PROGRESS, incident.getStatus(), target + " / " + type);

                Fixture scenarioSeul = new Fixture(false, true);
                Incident autre = scenarioSeul.incident(type, target);
                scenarioSeul.orchestrator.startIfEligible(autre);
                verifyNoInteractions(scenarioSeul.policy, scenarioSeul.workflowService, scenarioSeul.actionService,
                        scenarioSeul.camundaWorkflowService);
                verify(scenarioSeul.historyService).record(eq(autre), eq(IncidentStatus.OPEN), contains("HOST_DISK_REMEDIATION_ENABLED=false"));
            }
        }
    }

    @Test
    void leScenarioDesactiveEnregistreLeDrapeauDedieEtJamaisLAncien() {
        for (boolean ancien : new boolean[] {false, true}) {
            Fixture f = new Fixture(ancien, false);
            Incident incident = f.incident("NODE_DISK_FULL", "k8s-worker1");

            f.orchestrator.startIfEligible(incident);

            verifyNoInteractions(f.policy, f.workflowService, f.actionService, f.camundaWorkflowService);
            verify(f.historyService).record(eq(incident), eq(IncidentStatus.OPEN), contains("HOST_DISK_E2E_158_ENABLED=false"));
        }
    }

    @Test
    void pressureEtInodesGardentLeMessageEtLeDrapeauDAvant() {
        for (String type : List.of("NODE_DISK_PRESSURE", "NODE_INODES_FULL")) {
            Fixture f = new Fixture(false, true);          // le drapeau du scenario n'ouvre rien d'autre
            Incident incident = f.incident(type, "k8s-worker1");

            f.orchestrator.startIfEligible(incident);

            verifyNoInteractions(f.policy, f.workflowService, f.actionService, f.camundaWorkflowService);
            verify(f.historyService).record(eq(incident), eq(IncidentStatus.OPEN), contains("HOST_DISK_REMEDIATION_ENABLED=false"));
        }
    }

    @Test
    void unTypeHorsDiskOuUneCibleHorsPerimetreEstIgnore() {
        for (String target : List.of("k8s-master", "monitoring", "iacamunda")) {
            Fixture f = new Fixture(true, true);
            Incident incident = f.incident("NODE_DISK_FULL", target);

            f.orchestrator.startIfEligible(incident);

            verifyNoInteractions(f.policy, f.workflowService, f.actionService, f.camundaWorkflowService);
        }
        Fixture f = new Fixture(true, true);
        Incident cpu = f.incident("NODE_HIGH_CPU", "k8s-worker1");

        f.orchestrator.startIfEligible(cpu);

        verifyNoInteractions(f.policy, f.workflowService, f.actionService, f.camundaWorkflowService);
    }
}
