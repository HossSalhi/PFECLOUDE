
package com.orange.pfe.backend.service;

import com.orange.pfe.backend.dto.ExecutionEventRequest;
import com.orange.pfe.backend.dto.IncidentCardResponse;
import com.orange.pfe.backend.dto.IncidentVerificationRequest;
import com.orange.pfe.backend.dto.IncidentVerificationResponse;
import com.orange.pfe.backend.dto.TerminalEvidenceRequest;
import com.orange.pfe.backend.dto.TerminalEvidenceResponse;
import com.orange.pfe.backend.model.core.Incident;
import com.orange.pfe.backend.model.core.IncidentHistory;
import com.orange.pfe.backend.model.core.IncidentTerminalEvidence;
import com.orange.pfe.backend.model.core.IncidentVerification;
import com.orange.pfe.backend.model.enums.IncidentSeverity;
import com.orange.pfe.backend.model.enums.IncidentSource;
import com.orange.pfe.backend.model.enums.ExecutionStage;
import com.orange.pfe.backend.model.enums.IncidentStatus;
import com.orange.pfe.backend.model.enums.TerminalEvidencePhase;
import com.orange.pfe.backend.repository.IncidentRepository;
import com.orange.pfe.backend.repository.IncidentTerminalEvidenceRepository;
import com.orange.pfe.backend.repository.IncidentVerificationRepository;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.web.server.ResponseStatusException;

import java.util.stream.StreamSupport;
import java.util.List;
import java.util.Optional;

import static org.mockito.ArgumentMatchers.anyList;
import static org.mockito.ArgumentMatchers.argThat;
import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyLong;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.contains;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

/**
 * LOT HELIOS-INCIDENT-IDENTITY-CARD-01 — Phase F (backend).
 *
 * Tests unitaires avec repositories mockés, sans base de données.
 *
 * Couvre :
 * - assemblage de la carte incident ;
 * - états NON COLLECTÉ ;
 * - enregistrement des preuves terminales ;
 * - protection anti-doublon ;
 * - enregistrement des verdicts génériques ;
 * - interdiction du PASS générique pour VM_DOWN ;
 * - propagation des erreurs de persistance.
 */
class IncidentCardServiceTest {

    private IncidentRepository incidentRepo;
    private IncidentTerminalEvidenceRepository evidenceRepo;
    private IncidentVerificationRepository verifRepo;
    private IncidentHistoryService historyService;
    private IncidentCardService service;

    @BeforeEach
    void setUp() {
        incidentRepo = mock(IncidentRepository.class);
        evidenceRepo = mock(IncidentTerminalEvidenceRepository.class);
        verifRepo = mock(IncidentVerificationRepository.class);
        historyService = mock(IncidentHistoryService.class);

        service = new IncidentCardService(
                incidentRepo,
                evidenceRepo,
                verifRepo,
                historyService
        );
    }

    /**
     * Incident Kubernetes de récupération de nœud.
     *
     * Ce type d'incident est soumis aux protections VM_DOWN.
     */
    private Incident sampleIncident() {

        Incident inc = new Incident();

        inc.setTitle("Infrastructure DOWN: k8s-nodes/k8s-worker1");
        inc.setDescription("Kubernetes node NotReady");
        inc.setStatus(IncidentStatus.OPEN);
        inc.setSeverity(IncidentSeverity.CRITICAL);
        inc.setSource(IncidentSource.AI);
        inc.setIncidentType("NODE_NOT_READY");
        inc.setNamespace("k8s-nodes");
        inc.setApp("k8s-worker1");

        IncidentHistory history = new IncidentHistory();
        history.setStatus(IncidentStatus.OPEN);
        history.setComment("detected");

        inc.getHistory().add(history);

        return inc;
    }

    /**
     * Incident applicatif ordinaire.
     *
     * Utilisé pour tester les méthodes génériques sans contourner
     * les protections spécifiques aux récupérations de nœuds.
     */
    private Incident sampleApplicationIncident() {

        Incident inc = sampleIncident();

        inc.setTitle("Application incident: pfe/backend");
        inc.setDescription("Application CPU anomaly");
        inc.setIncidentType("HIGH_CPU");
        inc.setNamespace("pfe");
        inc.setApp("backend");

        return inc;
    }

    @Test
    void buildCard_sansPreuveNiVerification_marqueNonCollecte() {

        when(incidentRepo.findById(1L))
                .thenReturn(Optional.of(sampleIncident()));

        when(evidenceRepo.findByIncidentIdAndPhaseOrderByCapturedAtAsc(
                eq(1L),
                any()
        )).thenReturn(List.of());

        when(verifRepo.findTopByIncidentIdOrderByVerifiedAtDesc(1L))
                .thenReturn(Optional.empty());

        IncidentCardResponse card = service.buildCard(1L);

        assertEquals("NODE_NOT_READY", card.identity().incidentType());
        assertEquals("OPEN", card.identity().status());
        assertEquals("k8s-worker1", card.identity().app());

        assertEquals(1, card.timeline().size());

        assertTrue(card.evidenceBefore().isEmpty());
        assertTrue(card.evidenceAfter().isEmpty());
        assertNull(card.verification());

        assertTrue(card.completeness().hasTimeline());
        assertFalse(card.completeness().hasEvidenceBefore());
        assertFalse(card.completeness().hasEvidenceAfter());
        assertFalse(card.completeness().hasVerification());
    }

    @Test
    void buildCard_incidentInconnu_renvoie404() {

        when(incidentRepo.findById(99L))
                .thenReturn(Optional.empty());

        assertThrows(
                ResponseStatusException.class,
                () -> service.buildCard(99L)
        );
    }

    @Test
    void recordEvidence_persisteEtMappe() {

        when(incidentRepo.findById(1L))
                .thenReturn(Optional.of(sampleIncident()));

        when(evidenceRepo.existsByIncidentIdAndPhaseAndCommand(
                anyLong(),
                any(),
                anyString()
        )).thenReturn(false);

        when(evidenceRepo.save(any(IncidentTerminalEvidence.class)))
                .thenAnswer(a -> a.getArgument(0));

        TerminalEvidenceRequest req = new TerminalEvidenceRequest(
                "k8s-worker1",
                TerminalEvidencePhase.BEFORE,
                null,
                "kubectl get node k8s-worker1",
                0,
                "NotReady",
                "",
                "{\"ready\":false}",
                true,
                "BRIDGE"
        );

        TerminalEvidenceResponse response =
                service.recordEvidence(1L, req);

        assertEquals(TerminalEvidencePhase.BEFORE, response.phase());
        assertTrue(response.available());
        assertEquals("k8s-worker1", response.vm());
        assertEquals(Integer.valueOf(0), response.exitCode());

        verify(evidenceRepo).save(any(IncidentTerminalEvidence.class));
    }

    @Test
    void recordEvidence_doublon_nePersistePas() {

        when(incidentRepo.findById(1L))
                .thenReturn(Optional.of(sampleIncident()));

        when(evidenceRepo.existsByIncidentIdAndPhaseAndCommand(
                eq(1L),
                eq(TerminalEvidencePhase.BEFORE),
                eq("cmd")
        )).thenReturn(true);

        IncidentTerminalEvidence existing = new IncidentTerminalEvidence();

        existing.setIncidentId(1L);
        existing.setPhase(TerminalEvidencePhase.BEFORE);
        existing.setCommand("cmd");
        existing.setAvailable(true);

        when(evidenceRepo.findByIncidentIdAndPhaseOrderByCapturedAtAsc(
                1L,
                TerminalEvidencePhase.BEFORE
        )).thenReturn(List.of(existing));

        TerminalEvidenceRequest req = new TerminalEvidenceRequest(
                "k8s-worker1",
                TerminalEvidencePhase.BEFORE,
                null,
                "cmd",
                0,
                "x",
                "",
                null,
                true,
                "BRIDGE"
        );

        service.recordEvidence(1L, req);

        verify(evidenceRepo, never()).save(any());
    }

    /**
     * Un incident applicatif ordinaire peut enregistrer un PASS générique.
     */
    @Test
    void recordVerification_persisteLeVerdict() {

        when(incidentRepo.findById(1L))
                .thenReturn(Optional.of(sampleApplicationIncident()));

        when(verifRepo.save(any(IncidentVerification.class)))
                .thenAnswer(a -> a.getArgument(0));

        IncidentVerificationRequest req = new IncidentVerificationRequest(
                "node Ready",
                "PASS",
                "{\"ready\":true}",
                "node back Ready",
                null,
                true
        );

        IncidentVerificationResponse response =
                service.recordVerification(1L, req);

        assertEquals("PASS", response.result());
        assertTrue(response.available());

        verify(verifRepo).save(any(IncidentVerification.class));
    }

    /**
     * Un PASS générique sans critères ni mesures réelles est refusé
     * pour un incident applicatif ordinaire.
     */
    @Test
    void recordVerification_refusePASS_sansObservations() {

        when(incidentRepo.findById(1L))
                .thenReturn(Optional.of(sampleApplicationIncident()));

        IncidentVerificationRequest req = new IncidentVerificationRequest(
                null,
                "PASS",
                null,
                "ok",
                null,
                false
        );

        assertThrows(
                ResponseStatusException.class,
                () -> service.recordVerification(1L, req)
        );

        verify(verifRepo, never()).save(any());
    }

    /**
     * Même avec des mesures disponibles, un PASS générique
     * est interdit pour un incident de récupération de nœud.
     *
     * Le seul chemin autorisé est recordNodeRecoveryVerification().
     */
    @Test
    void recordVerification_refusePASS_generiquePourIncidentNodeRecovery() {

        when(incidentRepo.findById(1L))
                .thenReturn(Optional.of(sampleIncident()));

        IncidentVerificationRequest req = new IncidentVerificationRequest(
                "node Ready",
                "PASS",
                "{\"ready\":true}",
                "node back Ready",
                java.time.LocalDateTime.now(),
                true
        );

        assertThrows(
                ResponseStatusException.class,
                () -> service.recordVerification(1L, req)
        );

        verify(verifRepo, never()).save(any());
    }

    /**
     * Un verdict FAIL reste autorisé pour un incident de récupération
     * de nœud, même sans observations disponibles.
     */
    @Test
    void recordVerification_accepteFAIL_sansObservations() {

        when(incidentRepo.findById(1L))
                .thenReturn(Optional.of(sampleIncident()));

        when(verifRepo.save(any(IncidentVerification.class)))
                .thenAnswer(a -> a.getArgument(0));

        IncidentVerificationRequest req = new IncidentVerificationRequest(
                "node Ready",
                "FAIL",
                null,
                "node still NotReady",
                null,
                false
        );

        IncidentVerificationResponse response =
                service.recordVerification(1L, req);

        assertEquals("FAIL", response.result());

        verify(verifRepo).save(any(IncidentVerification.class));
    }

    /**
     * Vérifie l'enregistrement générique de deux verdicts PASS
     * pour un incident applicatif ordinaire.
     *
     * Ce test ne simule pas les preuves VM_DOWN.
     */
    @Test
    void recordVerifications_persisteDeuxObservationsPass() {

        when(incidentRepo.findById(1L))
                .thenReturn(Optional.of(sampleApplicationIncident()));

        when(verifRepo.saveAllAndFlush(anyList()))
                .thenAnswer(a -> a.getArgument(0));

        List<IncidentVerificationRequest> requests = List.of(

                new IncidentVerificationRequest(
                        "backend observation 1/2",
                        "PASS",
                        "{\"healthy\":true,\"observation\":1}",
                        "Premiere observation saine",
                        java.time.LocalDateTime.now(),
                        true
                ),

                new IncidentVerificationRequest(
                        "backend observation 2/2",
                        "PASS",
                        "{\"healthy\":true,\"observation\":2}",
                        "Deuxieme observation saine",
                        java.time.LocalDateTime.now().plusSeconds(15),
                        true
                )
        );

        List<IncidentVerificationResponse> result =
                service.recordVerifications(1L, requests);

        assertEquals(2, result.size());

        assertEquals("PASS", result.get(0).result());
        assertEquals("PASS", result.get(1).result());

        verify(verifRepo).saveAllAndFlush(argThat(rows -> {

            List<IncidentVerification> saved = StreamSupport
                    .stream(rows.spliterator(), false)
                    .toList();

            return saved.size() == 2
                    && saved.stream().allMatch(v ->
                    "PASS".equals(v.getResult())
                            && v.isAvailable()
                            && v.getIncidentId().equals(1L)
            );
        }));
    }

    /**
     * Protection importante :
     * recordVerifications() ne doit jamais produire un PASS générique
     * pour un incident VM_DOWN / NODE_NOT_READY.
     */
    @Test
    void recordVerifications_refusePASS_generiquePourIncidentNodeRecovery() {

        when(incidentRepo.findById(1L))
                .thenReturn(Optional.of(sampleIncident()));

        List<IncidentVerificationRequest> requests = List.of(

                new IncidentVerificationRequest(
                        "k8s-worker1 Ready observation 1/2",
                        "PASS",
                        "{\"ready\":true,\"observation\":1}",
                        "Premiere observation Ready",
                        java.time.LocalDateTime.now(),
                        true
                ),

                new IncidentVerificationRequest(
                        "k8s-worker1 Ready observation 2/2",
                        "PASS",
                        "{\"ready\":true,\"observation\":2}",
                        "Deuxieme observation Ready",
                        java.time.LocalDateTime.now().plusSeconds(15),
                        true
                )
        );

        assertThrows(
                ResponseStatusException.class,
                () -> service.recordVerifications(1L, requests)
        );

        verify(verifRepo, never()).saveAllAndFlush(anyList());
    }

    /**
     * Vérifie que l'erreur provenant du repository est propagée
     * pour une demande générique valide.
     *
     * La demande utilise un incident applicatif ordinaire afin
     * de ne pas déclencher la garde spécifique VM_DOWN.
     */
    @Test
    void recordVerifications_propageEchecDePersistance() {

        when(incidentRepo.findById(1L))
                .thenReturn(Optional.of(sampleApplicationIncident()));

        when(verifRepo.saveAllAndFlush(anyList()))
                .thenThrow(new IllegalStateException("Database unavailable"));

        List<IncidentVerificationRequest> requests = List.of(

                new IncidentVerificationRequest(
                        "backend observation 1/2",
                        "PASS",
                        "{\"healthy\":true,\"observation\":1}",
                        "Observation 1",
                        java.time.LocalDateTime.now(),
                        true
                ),

                new IncidentVerificationRequest(
                        "backend observation 2/2",
                        "PASS",
                        "{\"healthy\":true,\"observation\":2}",
                        "Observation 2",
                        java.time.LocalDateTime.now().plusSeconds(15),
                        true
                )
        );

        assertThrows(
                IllegalStateException.class,
                () -> service.recordVerifications(1L, requests)
        );
    }

    /**
     * L'étape d'exécution est tracée dans la chronologie.
     */
    @Test
    void recordExecutionEvent_enregistreEtapeDansLaChronologie() {

        when(incidentRepo.findById(1L))
                .thenReturn(Optional.of(sampleIncident()));

        ExecutionEventRequest req = new ExecutionEventRequest(
                ExecutionStage.COMPLETED,
                "HOST_SERVICE_CPU_QUOTA",
                "quota 25% applique",
                Boolean.TRUE
        );

        service.recordExecutionEvent(1L, req);

        verify(historyService).record(
                any(),
                any(),
                contains("[EXEC:COMPLETED]")
        );
    }
}
