package com.orange.pfe.backend.service;

import com.orange.pfe.backend.dto.IncidentVerificationResponse;
import com.orange.pfe.backend.dto.TerminalEvidenceResponse;
import com.orange.pfe.backend.model.core.Incident;
import com.orange.pfe.backend.model.core.IncidentTerminalEvidence;
import com.orange.pfe.backend.model.core.IncidentVerification;
import com.orange.pfe.backend.model.enums.TerminalEvidencePhase;
import com.orange.pfe.backend.repository.IncidentRepository;
import com.orange.pfe.backend.repository.IncidentTerminalEvidenceRepository;
import com.orange.pfe.backend.repository.IncidentVerificationRepository;
import com.orange.pfe.backend.service.remediation.NodeReadinessObservation;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.web.server.ResponseStatusException;

import java.time.LocalDateTime;
import java.util.List;
import java.util.Optional;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.argThat;
import static org.mockito.Mockito.*;

class IncidentCardServiceVmDownEvidenceTest {

    private IncidentRepository incidentRepository;
    private IncidentTerminalEvidenceRepository evidenceRepository;
    private IncidentVerificationRepository verificationRepository;
    private IncidentHistoryService historyService;
    private IncidentCardService service;

    private static final Long INCIDENT_ID = 1L;
    private static final Long WORKFLOW_ID = 10L;
    private static final String NODE = "k8s-worker1";

    private static final LocalDateTime BASE_TIME =
            LocalDateTime.of(2026, 11, 2, 10, 0);

    @BeforeEach
    void setUp() {
        incidentRepository = mock(IncidentRepository.class);
        evidenceRepository = mock(IncidentTerminalEvidenceRepository.class);
        verificationRepository = mock(IncidentVerificationRepository.class);
        historyService = mock(IncidentHistoryService.class);

        service = new IncidentCardService(
                incidentRepository,
                evidenceRepository,
                verificationRepository,
                historyService
        );

        when(incidentRepository.findById(INCIDENT_ID))
                .thenReturn(Optional.of(nodeIncident()));
    }

    private Incident nodeIncident() {
        Incident incident = new Incident();
        incident.setIncidentType("VM_DOWN");
        incident.setNamespace("k8s-nodes");
        incident.setApp(NODE);
        return incident;
    }

    private IncidentTerminalEvidence readyEvidence(
            int sample,
            LocalDateTime capturedAt,
            boolean ready) {

        IncidentTerminalEvidence evidence =
                new IncidentTerminalEvidence();

        String condition = ready ? "True" : "False";

        evidence.setIncidentId(INCIDENT_ID);
        evidence.setVm(NODE);
        evidence.setPhase(TerminalEvidencePhase.AFTER);
        evidence.setCapturedAt(capturedAt);

        evidence.setCommand(
                "GET /api/v1/nodes/" + NODE
                        + " [sample=" + sample + "]"
        );

        evidence.setExitCode(200);
        evidence.setStdout(condition);
        evidence.setStderr(null);
        evidence.setAvailable(true);
        evidence.setSource("K8S_API_WF_" + WORKFLOW_ID);

        evidence.setMeasuresJson(
                "{\"workflowId\":" + WORKFLOW_ID
                        + ",\"sample\":" + sample
                        + ",\"apiAvailable\":true"
                        + ",\"ready\":" + ready
                        + ",\"conditionStatus\":\"" + condition + "\"}"
        );

        return evidence;
    }

    private void givenEvidence(List<IncidentTerminalEvidence> evidence) {
        when(evidenceRepository
                .findByIncidentIdAndPhaseOrderByCapturedAtAsc(
                        INCIDENT_ID,
                        TerminalEvidencePhase.AFTER
                ))
                .thenReturn(evidence);
    }

    @Test
    void recordNodeReadinessEvidence_persisteObservationReelle() {

        when(evidenceRepository.saveAndFlush(
                any(IncidentTerminalEvidence.class)
        )).thenAnswer(invocation -> invocation.getArgument(0));

        TerminalEvidenceResponse response =
                service.recordNodeReadinessEvidence(
                        INCIDENT_ID,
                        WORKFLOW_ID,
                        NODE,
                        1,
                        BASE_TIME,
                        new NodeReadinessObservation(
                                true, true, "True", "Node Ready"
                        )
                );

        assertTrue(response.available());
        assertEquals(NODE, response.vm());
        assertEquals("K8S_API_WF_10", response.source());

        verify(evidenceRepository).saveAndFlush(argThat(evidence ->
                evidence.getIncidentId().equals(INCIDENT_ID)
                        && evidence.getPhase() == TerminalEvidencePhase.AFTER
                        && evidence.getCapturedAt().equals(BASE_TIME)
                        && evidence.isAvailable()
                        && Integer.valueOf(200).equals(evidence.getExitCode())
                        && "True".equals(evidence.getStdout())
                        && evidence.getCommand().contains("[sample=1]")
        ));
    }

    @Test
    void recordNodeRecoveryVerification_refuseUneSeulePreuve() {

        givenEvidence(List.of(
                readyEvidence(1, BASE_TIME, true)
        ));

        assertFalse(service.hasTwoConsecutiveNodeReadyEvidence(
                INCIDENT_ID, WORKFLOW_ID, NODE
        ));

        assertThrows(
                ResponseStatusException.class,
                () -> service.recordNodeRecoveryVerification(
                        INCIDENT_ID, WORKFLOW_ID, NODE, "Recovery"
                )
        );

        verify(verificationRepository, never())
                .saveAndFlush(any(IncidentVerification.class));
    }

    @Test
    void recordNodeRecoveryVerification_refuseIntervalleInferieurA15Secondes() {

        givenEvidence(List.of(
                readyEvidence(1, BASE_TIME, true),
                readyEvidence(2, BASE_TIME.plusSeconds(14), true)
        ));

        assertFalse(service.hasTwoConsecutiveNodeReadyEvidence(
                INCIDENT_ID, WORKFLOW_ID, NODE
        ));

        assertThrows(
                ResponseStatusException.class,
                () -> service.recordNodeRecoveryVerification(
                        INCIDENT_ID, WORKFLOW_ID, NODE, "Recovery"
                )
        );

        verify(verificationRepository, never())
                .saveAndFlush(any(IncidentVerification.class));
    }

    @Test
    void recordNodeRecoveryVerification_accepteDeuxReadyEspacesDe15Secondes() {

        givenEvidence(List.of(
                readyEvidence(1, BASE_TIME, true),
                readyEvidence(2, BASE_TIME.plusSeconds(15), true)
        ));

        when(verificationRepository.saveAndFlush(
                any(IncidentVerification.class)
        )).thenAnswer(invocation -> invocation.getArgument(0));

        assertTrue(service.hasTwoConsecutiveNodeReadyEvidence(
                INCIDENT_ID, WORKFLOW_ID, NODE
        ));

        IncidentVerificationResponse response =
                service.recordNodeRecoveryVerification(
                        INCIDENT_ID,
                        WORKFLOW_ID,
                        NODE,
                        "Two consecutive Kubernetes Ready observations"
                );

        assertEquals("PASS", response.result());
        assertTrue(response.available());

        verify(verificationRepository).saveAndFlush(argThat(verification ->
                "PASS".equals(verification.getResult())
                        && verification.isAvailable()
                        && verification.getMeasuresJson() != null
                        && verification.getMeasuresJson().contains("\"sample\":1")
                        && verification.getMeasuresJson().contains("\"sample\":2")
                        && verification.getCriteria().contains("15 seconds")
        ));
    }

    @Test
    void recordNodeRecoveryVerification_notReadyInterromptLaSequence() {

        givenEvidence(List.of(
                readyEvidence(1, BASE_TIME, true),
                readyEvidence(2, BASE_TIME.plusSeconds(15), false),
                readyEvidence(3, BASE_TIME.plusSeconds(30), true)
        ));

        assertFalse(service.hasTwoConsecutiveNodeReadyEvidence(
                INCIDENT_ID, WORKFLOW_ID, NODE
        ));

        assertThrows(
                ResponseStatusException.class,
                () -> service.recordNodeRecoveryVerification(
                        INCIDENT_ID, WORKFLOW_ID, NODE, "Recovery"
                )
        );

        verify(verificationRepository, never())
                .saveAndFlush(any(IncidentVerification.class));
    }
}