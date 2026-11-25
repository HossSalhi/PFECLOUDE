package com.orange.pfe.backend.service;

import com.orange.pfe.backend.dto.HostIncidentIngestionRequest;
import com.orange.pfe.backend.model.core.Incident;
import com.orange.pfe.backend.model.enums.IncidentSource;
import com.orange.pfe.backend.repository.IncidentRepository;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.transaction.support.TransactionSynchronization;
import org.springframework.transaction.support.TransactionSynchronizationManager;

import java.time.LocalDateTime;
import java.util.List;
import java.util.Optional;

import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyList;
import static org.mockito.Mockito.*;

class HostIncidentDispatchRoutingTest {

    private IncidentRepository repository;
    private IncidentHistoryService history;
    private HostCpuIncidentRemediationOrchestrator cpu;
    private HostDiskIncidentRemediationOrchestrator disk;
    private HostIncidentIngestionService ingestion;

    @BeforeEach
    void setUp() {
        repository = mock(IncidentRepository.class);
        history = mock(IncidentHistoryService.class);
        cpu = mock(HostCpuIncidentRemediationOrchestrator.class);
        disk = mock(HostDiskIncidentRemediationOrchestrator.class);

        when(repository.findFirstByStatusInAndSourceAndNamespaceAndAppOrderByCreatedAtDesc(
                anyList(), any(), any(), any()))
                .thenReturn(Optional.empty());
        when(repository.findBySource(any(IncidentSource.class)))
                .thenReturn(List.of());
        when(repository.save(any(Incident.class)))
                .thenAnswer(invocation -> invocation.getArgument(0));

        ingestion = new HostIncidentIngestionService(
                repository, history, cpu, disk);
    }

    @AfterEach
    void cleanSynchronization() {
        if (TransactionSynchronizationManager.isSynchronizationActive()) {
            TransactionSynchronizationManager.clearSynchronization();
        }
    }

    @Test
    void cpuIncidentDispatchesOnlyToCpuOrchestratorAfterCommit() {
        HostIncidentIngestionRequest request = request("NODE_HIGH_CPU");

        TransactionSynchronizationManager.initSynchronization();
        try {
            assertTrue(ingestion.ingest(request).created());

            verifyNoInteractions(cpu, disk);

            List<TransactionSynchronization> synchronizations =
                    TransactionSynchronizationManager.getSynchronizations();

            synchronizations.forEach(TransactionSynchronization::afterCommit);

            verify(cpu, times(1)).startIfEligible(any(Incident.class));
            verifyNoInteractions(disk);
        } finally {
            if (TransactionSynchronizationManager.isSynchronizationActive()) {
                TransactionSynchronizationManager.clearSynchronization();
            }
        }
    }

    @Test
    void diskIncidentDispatchesOnlyToDiskOrchestratorAfterCommit() {
        HostIncidentIngestionRequest request = request("NODE_DISK_FULL");

        TransactionSynchronizationManager.initSynchronization();
        try {
            assertTrue(ingestion.ingest(request).created());

            verifyNoInteractions(cpu, disk);

            List<TransactionSynchronization> synchronizations =
                    TransactionSynchronizationManager.getSynchronizations();

            synchronizations.forEach(TransactionSynchronization::afterCommit);

            verify(disk, times(1)).startIfEligible(any(Incident.class));
            verifyNoInteractions(cpu);
        } finally {
            if (TransactionSynchronizationManager.isSynchronizationActive()) {
                TransactionSynchronizationManager.clearSynchronization();
            }
        }
    }

    private static HostIncidentIngestionRequest request(String type) {
        return new HostIncidentIngestionRequest(
                type,
                "k8s-nodes",
                "k8s-worker1",
                "HIGH",
                LocalDateTime.now().minusSeconds(10),
                LocalDateTime.now(),
                "synthetic unit-test event",
                null);
    }
}