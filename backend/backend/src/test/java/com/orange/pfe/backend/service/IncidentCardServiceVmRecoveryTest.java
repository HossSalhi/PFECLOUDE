package com.orange.pfe.backend.service;

import com.orange.pfe.backend.model.core.Incident;
import com.orange.pfe.backend.model.core.IncidentTerminalEvidence;
import com.orange.pfe.backend.model.core.IncidentVerification;
import com.orange.pfe.backend.model.enums.TerminalEvidencePhase;
import com.orange.pfe.backend.repository.IncidentRepository;
import com.orange.pfe.backend.repository.IncidentTerminalEvidenceRepository;
import com.orange.pfe.backend.repository.IncidentVerificationRepository;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.http.HttpStatus;
import org.springframework.test.util.ReflectionTestUtils;
import org.springframework.web.server.ResponseStatusException;

import java.time.LocalDateTime;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.List;
import java.util.Optional;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

/**
 * VMDOWN-POLICY-20261105 — preuves de RÉCUPÉRATION d'une VM (clé = incident, sans workflow) : deux passes
 * consécutives persistées, espacées d'au moins 15 s, avant tout PASS. Les dépôts sont simulés par une liste en
 * mémoire : le service lit ce qu'il a lui-même écrit (aller-retour complet de la logique de séquence).
 */
class IncidentCardServiceVmRecoveryTest {

    private static final Long INC = 7L;
    private static final String VM = "k8s-lb2";
    private static final LocalDateTime T = LocalDateTime.of(2026, 11, 5, 18, 0, 0);

    private IncidentRepository incidents;
    private IncidentTerminalEvidenceRepository evidenceRepo;
    private IncidentVerificationRepository verificationRepo;
    private IncidentCardService service;
    private final List<IncidentTerminalEvidence> store = new ArrayList<>();
    private final List<IncidentVerification> verifications = new ArrayList<>();
    private long ids = 100;

    @BeforeEach
    void setUp() {
        incidents = mock(IncidentRepository.class);
        evidenceRepo = mock(IncidentTerminalEvidenceRepository.class);
        verificationRepo = mock(IncidentVerificationRepository.class);
        service = new IncidentCardService(incidents, evidenceRepo, verificationRepo, mock(IncidentHistoryService.class));
        when(incidents.findById(INC)).thenReturn(Optional.of(incident("infra", VM, "VM_DOWN")));
        when(evidenceRepo.saveAndFlush(any(IncidentTerminalEvidence.class))).thenAnswer(inv -> {
            IncidentTerminalEvidence e = inv.getArgument(0);
            ReflectionTestUtils.setField(e, "id", ++ids);
            store.add(e);
            return e;
        });
        when(evidenceRepo.findByIncidentIdAndPhaseOrderByCapturedAtAsc(INC, TerminalEvidencePhase.AFTER))
                .thenAnswer(inv -> store.stream()
                        .sorted(Comparator.comparing(IncidentTerminalEvidence::getCapturedAt)).toList());
        when(verificationRepo.saveAndFlush(any(IncidentVerification.class))).thenAnswer(inv -> {
            IncidentVerification v = inv.getArgument(0);
            verifications.add(v);
            return v;
        });
        when(verificationRepo.findTopByIncidentIdOrderByVerifiedAtDesc(INC)).thenAnswer(inv ->
                verifications.isEmpty() ? Optional.empty() : Optional.of(verifications.get(verifications.size() - 1)));
    }

    private static Incident incident(String ns, String app, String type) {
        Incident i = new Incident();
        i.setNamespace(ns);
        i.setApp(app);
        i.setIncidentType(type);
        return i;
    }

    private void pass(int sample, int seconds) {
        service.recordVmRecoveryEvidence(INC, VM, sample, T.plusSeconds(seconds), true, "{\"ssh\":true}");
    }

    private void fail(int sample, int seconds) {
        service.recordVmRecoveryEvidence(INC, VM, sample, T.plusSeconds(seconds), false, "{\"ssh\":false}");
    }

    @Test
    void deux_passes_espacees_de_15s_forment_une_paire_et_autorisent_le_PASS() {
        pass(1, 0);
        assertFalse(service.hasTwoConsecutiveVmRecoveryEvidence(INC, VM), "une seule passe");
        pass(2, 15);
        assertTrue(service.hasTwoConsecutiveVmRecoveryEvidence(INC, VM));

        service.recordVmRecoveryVerification(INC, VM, "récupérée");
        assertEquals(1, verifications.size());
        IncidentVerification v = verifications.get(0);
        assertEquals("PASS", v.getResult());
        assertTrue(v.isAvailable());
        assertTrue(v.getMeasuresJson().contains("\"sample\":1") && v.getMeasuresJson().contains("\"sample\":2"));
        assertTrue(service.hasPassVerification(INC));
    }

    @Test
    void ecart_de_14_secondes_insuffisant_et_le_PASS_est_refuse_en_409() {
        pass(1, 0);
        pass(2, 14);
        assertFalse(service.hasTwoConsecutiveVmRecoveryEvidence(INC, VM));
        ResponseStatusException ex = assertThrows(ResponseStatusException.class,
                () -> service.recordVmRecoveryVerification(INC, VM, "x"));
        assertEquals(HttpStatus.CONFLICT, ex.getStatusCode());
        assertTrue(verifications.isEmpty(), "aucun PASS ne doit être écrit sans paire");
        assertFalse(service.hasPassVerification(INC));
    }

    @Test
    void une_passe_en_echec_casse_la_serie_il_faut_deux_nouvelles_passes() {
        pass(1, 0);
        fail(2, 15);
        pass(3, 30);
        assertFalse(service.hasTwoConsecutiveVmRecoveryEvidence(INC, VM));
        pass(4, 45);
        assertTrue(service.hasTwoConsecutiveVmRecoveryEvidence(INC, VM));
    }

    @Test
    void numeros_non_consecutifs_ne_forment_pas_une_paire() {
        pass(1, 0);
        pass(3, 30);
        assertFalse(service.hasTwoConsecutiveVmRecoveryEvidence(INC, VM));
    }

    @Test
    void les_preuves_d_une_autre_source_ou_d_une_autre_vm_sont_ignorees() {
        pass(1, 0);
        pass(2, 15);
        store.forEach(e -> e.setSource("K8S_API_WF_9"));            // autre source (workflow Kubernetes)
        assertFalse(service.hasTwoConsecutiveVmRecoveryEvidence(INC, VM));
        store.forEach(e -> e.setSource("VM_RECOVERY:" + INC));
        store.forEach(e -> e.setVm("k8s-monitoring"));                // autre VM
        assertFalse(service.hasTwoConsecutiveVmRecoveryEvidence(INC, VM));
    }

    @Test
    void une_preuve_alteree_ne_compte_pas() {
        pass(1, 0);
        pass(2, 15);
        store.get(1).setMeasuresJson("{\"incident\":7,\"sample\":2,\"passed\":false,\"criteria\":{}}");
        assertFalse(service.hasTwoConsecutiveVmRecoveryEvidence(INC, VM), "mesures incohérentes avec PASS");
        store.get(1).setMeasuresJson("{\"incident\":7,\"sample\":2,\"passed\":true,\"criteria\":{}}");
        store.get(1).setStdout("FAIL");
        assertFalse(service.hasTwoConsecutiveVmRecoveryEvidence(INC, VM));
        store.get(1).setStdout("PASS");
        store.get(1).setExitCode(1);
        assertFalse(service.hasTwoConsecutiveVmRecoveryEvidence(INC, VM));
        store.get(1).setExitCode(0);
        store.get(1).setCommand("VM_RECOVERY " + VM + " [sample=9]");
        assertFalse(service.hasTwoConsecutiveVmRecoveryEvidence(INC, VM));
    }

    @Test
    void validation_de_la_cible_et_des_parametres() {
        // VM différente de l'application de l'incident
        assertEquals(HttpStatus.BAD_REQUEST, assertThrows(ResponseStatusException.class,
                () -> service.recordVmRecoveryEvidence(INC, "k8s-monitoring", 1, T, true, "{}")).getStatusCode());
        // namespace applicatif : hors famille VM
        when(incidents.findById(INC)).thenReturn(Optional.of(incident("pfe", VM, "VM_DOWN")));
        assertThrows(ResponseStatusException.class, () -> service.recordVmRecoveryEvidence(INC, VM, 1, T, true, "{}"));
        // type hors famille
        when(incidents.findById(INC)).thenReturn(Optional.of(incident("infra", VM, "NODE_HIGH_CPU")));
        assertThrows(ResponseStatusException.class, () -> service.recordVmRecoveryEvidence(INC, VM, 1, T, true, "{}"));
        // paramètres invalides
        when(incidents.findById(INC)).thenReturn(Optional.of(incident("infra", VM, "VM_DOWN")));
        assertThrows(ResponseStatusException.class, () -> service.recordVmRecoveryEvidence(INC, VM, 0, T, true, "{}"));
        assertThrows(ResponseStatusException.class, () -> service.recordVmRecoveryEvidence(INC, VM, 1, null, true, "{}"));
        assertTrue(store.isEmpty(), "rien n'est écrit pour une demande invalide");
    }

    @Test
    void famille_vm_acceptee_noeuds_kubernetes_et_vm_d_infrastructure() {
        when(incidents.findById(INC)).thenReturn(Optional.of(incident("k8s-nodes", "k8s-master", "NODE_NOT_READY")));
        service.recordVmRecoveryEvidence(INC, "k8s-master", 1, T, true, "{\"node_ready\":true}");
        when(incidents.findById(INC)).thenReturn(Optional.of(incident("infra", "k8s-monitoring", "VM_UNREACHABLE")));
        service.recordVmRecoveryEvidence(INC, "k8s-monitoring", 2, T.plusSeconds(15), true, "{}");
        assertEquals(2, store.size());
    }

    @Test
    void hasPassVerification_vrai_seulement_pour_un_dernier_verdict_PASS_disponible() {
        assertFalse(service.hasPassVerification(INC), "aucun verdict");
        IncidentVerification failed = new IncidentVerification();
        failed.setIncidentId(INC);
        failed.setResult("FAIL");
        failed.setAvailable(true);
        verifications.add(failed);
        assertFalse(service.hasPassVerification(INC));
        IncidentVerification unavailable = new IncidentVerification();
        unavailable.setIncidentId(INC);
        unavailable.setResult("PASS");
        unavailable.setAvailable(false);
        verifications.add(unavailable);
        assertFalse(service.hasPassVerification(INC), "PASS non disponible n'est pas une preuve");
        IncidentVerification ok = new IncidentVerification();
        ok.setIncidentId(INC);
        ok.setResult("PASS");
        ok.setAvailable(true);
        verifications.add(ok);
        assertTrue(service.hasPassVerification(INC));
    }
}
