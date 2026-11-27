package com.orange.pfe.backend.service.routing;

import com.orange.pfe.backend.service.LeaderElectionService;
import org.junit.jupiter.api.Test;

import java.util.List;

import static org.mockito.ArgumentMatchers.anyLong;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

class IncidentRoutingSchedulerTest {

    private final IncidentTeamRoutingService routing = mock(IncidentTeamRoutingService.class);
    private final LeaderElectionService leader = mock(LeaderElectionService.class);
    private final IncidentRoutingScheduler scheduler = new IncidentRoutingScheduler(routing, leader);

    @Test
    void aReplicaThatIsNotTheLeaderDoesNothing() {
        when(leader.isLeader()).thenReturn(false);

        scheduler.run();

        verify(routing, never()).newIncidentIds();
        verify(routing, never()).resolvedIncidentIdsAwaitingResume();
    }

    @Test
    void theLeaderRoutesNewIncidentsAndNotifiesResolutions() {
        when(leader.isLeader()).thenReturn(true);
        when(routing.newIncidentIds()).thenReturn(List.of(1L, 2L));
        when(routing.resolvedIncidentIdsAwaitingResume()).thenReturn(List.of(3L));

        scheduler.run();

        verify(routing).routeIncident(1L);
        verify(routing).routeIncident(2L);
        verify(routing).notifyResolution(3L);
    }

    @Test
    void anErrorOnOneIncidentDoesNotStopTheOthers() {
        when(leader.isLeader()).thenReturn(true);
        when(routing.newIncidentIds()).thenReturn(List.of(1L, 2L));
        when(routing.resolvedIncidentIdsAwaitingResume()).thenReturn(List.of(3L, 4L));
        when(routing.routeIncident(1L)).thenThrow(new IllegalStateException("boom"));
        when(routing.notifyResolution(3L)).thenThrow(new IllegalStateException("boom"));

        scheduler.run();

        verify(routing).routeIncident(2L);
        verify(routing).notifyResolution(4L);
    }

    @Test
    void nothingIsDoneForAnIncidentWhenThereAreNoCandidates() {
        when(leader.isLeader()).thenReturn(true);
        when(routing.newIncidentIds()).thenReturn(List.of());
        when(routing.resolvedIncidentIdsAwaitingResume()).thenReturn(List.of());

        scheduler.run();

        verify(routing, never()).routeIncident(anyLong());
        verify(routing, never()).notifyResolution(anyLong());
    }
}
