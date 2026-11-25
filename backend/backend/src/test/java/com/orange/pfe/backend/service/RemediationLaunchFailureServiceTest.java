package com.orange.pfe.backend.service;

import com.orange.pfe.backend.model.core.Incident;
import com.orange.pfe.backend.model.enums.IncidentStatus;
import com.orange.pfe.backend.repository.IncidentRepository;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.test.util.ReflectionTestUtils;

import java.util.Optional;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

class RemediationLaunchFailureServiceTest {

    private IncidentRepository incidentRepository;
    private ActionService actionService;
    private IncidentHistoryService historyService;
    private RemediationLaunchFailureService service;

    @BeforeEach
    void setUp() {
        incidentRepository = mock(IncidentRepository.class);
        actionService = mock(ActionService.class);
        historyService = mock(IncidentHistoryService.class);
        service = new RemediationLaunchFailureService(
                incidentRepository, actionService, historyService);
    }

    @Test
    void recordsFailedActionIncidentAndHistory() {
        Incident incident = new Incident();
        ReflectionTestUtils.setField(incident, "id", 77L);
        incident.setStatus(IncidentStatus.IN_PROGRESS);

        when(incidentRepository.findById(77L)).thenReturn(Optional.of(incident));

        RuntimeException failure = new IllegalStateException("Simulated outage");
        service.recordCamundaLaunchFailure("CPU", 77L, 88L, failure);

        assertEquals(IncidentStatus.RESOLUTION_FAILED, incident.getStatus());
        verify(actionService).markActionFailed(
                eq(88L),
                contains("Camunda workflow launch failed"),
                eq("HELIOS-DETERMINISTIC"),
                contains("Simulated outage"));
        verify(incidentRepository).save(incident);
        verify(historyService).record(
                eq(incident),
                eq(IncidentStatus.RESOLUTION_FAILED),
                contains("Simulated outage"));
    }

    @Test
    void recordsIncidentFailureEvenWhenActionIdIsMissing() {
        Incident incident = new Incident();
        ReflectionTestUtils.setField(incident, "id", 77L);
        incident.setStatus(IncidentStatus.IN_PROGRESS);

        when(incidentRepository.findById(77L)).thenReturn(Optional.of(incident));

        service.recordCamundaLaunchFailure(
                "CPU", 77L, null, new IllegalStateException("Simulated outage"));

        assertEquals(IncidentStatus.RESOLUTION_FAILED, incident.getStatus());
        verifyNoInteractions(actionService);
        verify(incidentRepository).save(incident);
        verify(historyService).record(
                eq(incident),
                eq(IncidentStatus.RESOLUTION_FAILED),
                contains("action id is missing"));
    }
}