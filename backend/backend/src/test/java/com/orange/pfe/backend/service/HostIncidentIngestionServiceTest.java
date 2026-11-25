package com.orange.pfe.backend.service;

import com.orange.pfe.backend.dto.HostIncidentIngestionRequest;
import com.orange.pfe.backend.dto.HostIncidentIngestionResponse;
import com.orange.pfe.backend.model.core.Incident;
import com.orange.pfe.backend.model.enums.IncidentStatus;
import com.orange.pfe.backend.repository.IncidentRepository;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.web.server.ResponseStatusException;

import java.time.LocalDateTime;
import java.util.List;
import java.util.Optional;

import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyLong;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

/**
 * LOT HELIOS-INCIDENT-CARD-EVENT-INTEGRATION-01 — Phase G. Ingestion host-mesurée : création d'un épisode,
 * corrélation (épisode actif + anti-flapping), distinction d'épisodes, rejet d'un événement incomplet.
 * Repos/historique mockés (sans base de données).
 */
class HostIncidentIngestionServiceTest {

    private IncidentRepository repo;
    private IncidentHistoryService history;
    private HostIncidentIngestionService service;

    @BeforeEach
    void setUp() {
        repo = mock(IncidentRepository.class);
        history = mock(IncidentHistoryService.class);
        service = new HostIncidentIngestionService(repo, history);
    }

    private HostIncidentIngestionRequest req(String type, String target) {
        return new HostIncidentIngestionRequest(type, "k8s-nodes", target, "HIGH",
                LocalDateTime.now().minusSeconds(30), LocalDateTime.now(), "host_cpu 96%", null);
    }

    @Test
    void ingest_creeUnNouvelEpisode_quandAucunExistant() {
        when(repo.findFirstByStatusInAndSourceAndNamespaceAndAppOrderByCreatedAtDesc(any(), any(), any(), any()))
                .thenReturn(Optional.empty());
        when(repo.findBySource(any())).thenReturn(List.of());
        when(repo.save(any(Incident.class))).thenAnswer(a -> a.getArgument(0));

        HostIncidentIngestionResponse r = service.ingest(req("NODE_HIGH_CPU", "k8s-worker1"));

        assertTrue(r.created());
        assertFalse(r.correlated());
        verify(repo).save(any(Incident.class));
    }

    @Test
    void ingest_correleEpisodeActif_sansDoublon() {
        Incident active = mock(Incident.class);
        when(active.getId()).thenReturn(5L);
        when(active.getIncidentType()).thenReturn("NODE_HIGH_CPU");
        when(active.getStatus()).thenReturn(IncidentStatus.OPEN);
        when(repo.findFirstByStatusInAndSourceAndNamespaceAndAppOrderByCreatedAtDesc(
                any(), eq(HostIncidentIngestionService.SOURCE), eq("k8s-nodes"), eq("k8s-worker1")))
                .thenReturn(Optional.of(active));

        HostIncidentIngestionResponse r = service.ingest(req("NODE_HIGH_CPU", "k8s-worker1"));

        assertTrue(r.correlated());
        assertFalse(r.created());
        verify(repo, never()).save(any());
    }

    @Test
    void ingest_antiFlapping_correleIncidentRecentResolu() {
        when(repo.findFirstByStatusInAndSourceAndNamespaceAndAppOrderByCreatedAtDesc(any(), any(), any(), any()))
                .thenReturn(Optional.empty());
        Incident recent = mock(Incident.class);
        when(recent.getId()).thenReturn(7L);
        when(recent.getNamespace()).thenReturn("k8s-nodes");
        when(recent.getApp()).thenReturn("k8s-worker1");
        when(recent.getIncidentType()).thenReturn("NODE_HIGH_CPU");
        when(recent.getStatus()).thenReturn(IncidentStatus.RESOLVED);
        when(recent.getCreatedAt()).thenReturn(LocalDateTime.now().minusMinutes(1));
        when(repo.findBySource(any())).thenReturn(List.of(recent));

        HostIncidentIngestionResponse r = service.ingest(req("NODE_HIGH_CPU", "k8s-worker1"));

        assertTrue(r.correlated());
        assertFalse(r.created());
        verify(repo, never()).save(any());
    }

    @Test
    void ingest_distingueDeuxEpisodes_quandPrecedentClosEtAncien() {
        when(repo.findFirstByStatusInAndSourceAndNamespaceAndAppOrderByCreatedAtDesc(any(), any(), any(), any()))
                .thenReturn(Optional.empty());
        Incident vieux = mock(Incident.class);
        when(vieux.getNamespace()).thenReturn("k8s-nodes");
        when(vieux.getApp()).thenReturn("k8s-worker1");
        when(vieux.getIncidentType()).thenReturn("NODE_HIGH_CPU");
        when(vieux.getCreatedAt()).thenReturn(LocalDateTime.now().minusHours(2)); // hors fenêtre 5 min
        when(repo.findBySource(any())).thenReturn(List.of(vieux));
        when(repo.save(any(Incident.class))).thenAnswer(a -> a.getArgument(0));

        HostIncidentIngestionResponse r = service.ingest(req("NODE_HIGH_CPU", "k8s-worker1"));

        assertTrue(r.created(), "un épisode clos et ancien => nouvel incident (nouvel épisode)");
        verify(repo).save(any(Incident.class));
    }

    @Test
    void ingest_evenementIncomplet_rejette_etNeCreePasDeFauxIncident() {
        HostIncidentIngestionRequest incomplet = new HostIncidentIngestionRequest(
                "NODE_HIGH_CPU", "k8s-nodes", null, null, null, null, null, null); // target manquant
        assertThrows(ResponseStatusException.class, () -> service.ingest(incomplet));
        verify(repo, never()).save(any());
    }

    @Test
    void ingest_diskIncidentTypes_areAlwaysCritical_whenSeverityMissingOrLower() {
        when(repo.findFirstByStatusInAndSourceAndNamespaceAndAppOrderByCreatedAtDesc(
                any(), any(), any(), any())).thenReturn(Optional.empty());
        when(repo.findBySource(any())).thenReturn(List.of());
        when(repo.save(any(Incident.class))).thenAnswer(a -> a.getArgument(0));

        for (String type : List.of(
                "NODE_DISK_FULL", "NODE_DISK_PRESSURE", "NODE_INODES_FULL")) {
            for (String severity : new String[] {null, "LOW"}) {
                HostIncidentIngestionRequest diskRequest = new HostIncidentIngestionRequest(
                        type, "k8s-nodes", "k8s-worker1", severity,
                        LocalDateTime.now().minusSeconds(10),
                        LocalDateTime.now(), "test disk severity", null);
                service.ingest(diskRequest);
            }
        }

        org.mockito.ArgumentCaptor<Incident> captor =
                org.mockito.ArgumentCaptor.forClass(Incident.class);
        verify(repo, org.mockito.Mockito.times(6)).save(captor.capture());

        for (Incident saved : captor.getAllValues()) {
            org.junit.jupiter.api.Assertions.assertEquals(
                    com.orange.pfe.backend.model.enums.IncidentSeverity.CRITICAL,
                    saved.getSeverity(),
                    "La severite DISK doit etre determinee par son type");
        }
    }

    @Test
    void ingest_nonDiskIncident_missingSeverityKeepsMediumFallback() {
        when(repo.findFirstByStatusInAndSourceAndNamespaceAndAppOrderByCreatedAtDesc(
                any(), any(), any(), any())).thenReturn(Optional.empty());
        when(repo.findBySource(any())).thenReturn(List.of());
        when(repo.save(any(Incident.class))).thenAnswer(a -> a.getArgument(0));

        HostIncidentIngestionRequest cpuRequest = new HostIncidentIngestionRequest(
                "NODE_HIGH_CPU", "k8s-nodes", "k8s-worker1", null,
                LocalDateTime.now().minusSeconds(10),
                LocalDateTime.now(), "test cpu severity", null);
        service.ingest(cpuRequest);

        org.mockito.ArgumentCaptor<Incident> captor =
                org.mockito.ArgumentCaptor.forClass(Incident.class);
        verify(repo).save(captor.capture());

        org.junit.jupiter.api.Assertions.assertEquals(
                com.orange.pfe.backend.model.enums.IncidentSeverity.MEDIUM,
                captor.getValue().getSeverity(),
                "Le comportement historique CPU doit rester inchange");
    }

    /**
     * RAM (Étape C) — escalade DÉTERMINISTE, TRAÇABLE et FAIL-CLOSED : un incident mémoire est créé,
     * une escalade est tracée dans l'historique, et l'incident reste OUVERT (jamais RESOLVED par défaut,
     * aucune récupération fabriquée). Aucune action/éviction n'est tentée.
     */
    @Test
    void ingest_memoryIncident_escaladeDeterministe_resteOuvert_sansResolution() {
        when(repo.findFirstByStatusInAndSourceAndNamespaceAndAppOrderByCreatedAtDesc(any(), any(), any(), any()))
                .thenReturn(Optional.empty());
        when(repo.findBySource(any())).thenReturn(List.of());
        when(repo.save(any(Incident.class))).thenAnswer(a -> a.getArgument(0));

        for (String type : List.of("NODE_HIGH_MEMORY", "NODE_MEMORY_PRESSURE")) {
            HostIncidentIngestionRequest memReq = new HostIncidentIngestionRequest(
                    type, "k8s-nodes", "k8s-worker2", "HIGH",
                    LocalDateTime.now().minusSeconds(10), LocalDateTime.now(), "host_mem 95%", null);
            HostIncidentIngestionResponse r = service.ingest(memReq);
            assertTrue(r.created(), type + " doit creer l'incident");
            org.junit.jupiter.api.Assertions.assertEquals(IncidentStatus.OPEN.name(), r.status(),
                    type + " doit rester OUVERT (escalade fail-closed, jamais RESOLVED)");
        }

        org.mockito.ArgumentCaptor<String> msg = org.mockito.ArgumentCaptor.forClass(String.class);
        verify(history, org.mockito.Mockito.atLeastOnce())
                .record(any(Incident.class), eq(IncidentStatus.OPEN), msg.capture());
        long ram = msg.getAllValues().stream()
                .filter(m -> m != null && m.contains("[RAM] Escalade deterministe")).count();
        assertTrue(ram >= 2,
                "chaque incident memoire doit tracer une escalade RAM deterministe; vu=" + ram);
    }

    /**
     * RAM (Étape C) — un incident mémoire n'emprunte AUCUN chemin de remédiation : ni orchestrateur CPU,
     * ni orchestrateur DISK (donc ni Bridge, ni éviction, ni action). Fail-closed par absence d'action.
     */
    @Test
    void ingest_memoryIncident_neDeclencheAucuneRemediation() {
        HostCpuIncidentRemediationOrchestrator cpu = mock(HostCpuIncidentRemediationOrchestrator.class);
        HostDiskIncidentRemediationOrchestrator disk = mock(HostDiskIncidentRemediationOrchestrator.class);
        HostIncidentIngestionService svc = new HostIncidentIngestionService(repo, history, cpu, disk);

        when(repo.findFirstByStatusInAndSourceAndNamespaceAndAppOrderByCreatedAtDesc(any(), any(), any(), any()))
                .thenReturn(Optional.empty());
        when(repo.findBySource(any())).thenReturn(List.of());
        when(repo.save(any(Incident.class))).thenAnswer(a -> a.getArgument(0));

        HostIncidentIngestionResponse r = svc.ingest(new HostIncidentIngestionRequest(
                "NODE_HIGH_MEMORY", "k8s-nodes", "k8s-worker2", "HIGH",
                LocalDateTime.now().minusSeconds(10), LocalDateTime.now(), "host_mem 95%", null));

        assertTrue(r.created());
        verify(cpu, never()).startIfEligible(any());
        verify(disk, never()).startIfEligible(any());
    }
}
