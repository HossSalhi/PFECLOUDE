package com.orange.pfe.backend.repository;

import com.orange.pfe.backend.model.core.IncidentTerminalEvidence;
import com.orange.pfe.backend.model.core.IncidentVerification;
import com.orange.pfe.backend.model.enums.TerminalEvidencePhase;
import jakarta.persistence.EntityManager;
import jakarta.persistence.PersistenceContext;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.transaction.annotation.Transactional;

import java.time.LocalDateTime;
import java.util.List;
import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * HELIOS — Validation locale INTÉGRÉE (persistance) de la carte d'incident.
 *
 * Round-trip RÉEL des entités de carte (preuves terminal BEFORE/AFTER + vérification) via les dépôts JPA réels,
 * contre une base H2 en mémoire ISOLÉE et ÉPHÉMÈRE (src/test/resources/application.properties : jdbc:h2:mem,
 * ddl-auto=create-drop, Flyway désactivé — jamais la base de dev/prod). Données SYNTHÉTIQUES (source=FIXTURE).
 * Aucun réseau, aucune VM, aucune remédiation réelle.
 *
 * Comble le vide des tests de service existants (dépôts MOCKÉS, aucune BD) : prouve que l'événement de carte est
 * réellement PERSISTÉ puis RELU depuis la BD, y compris les verdicts RESOLVED(PASS)/ESCALATED, l'anti-doublon,
 * l'état NON COLLECTÉ (available=false) et l'absence (jamais présentée comme un succès).
 */
@SpringBootTest(properties = "jwt.secret=test-only-secret-0123456789-0123456789-0123456789-0123456789")
@Transactional
class IncidentCardPersistenceTest {

    @PersistenceContext private EntityManager em;
    @Autowired private IncidentTerminalEvidenceRepository evidenceRepo;
    @Autowired private IncidentVerificationRepository verificationRepo;

    private static final long INC = 9001L;
    private static final LocalDateTime T0 = LocalDateTime.of(2026, 10, 14, 12, 0, 0);

    private IncidentTerminalEvidence evidence(TerminalEvidencePhase phase, LocalDateTime capturedAt,
                                              String cmd, boolean available, String measures) {
        IncidentTerminalEvidence e = new IncidentTerminalEvidence();
        e.setIncidentId(INC);
        e.setVm("k8s-worker1");
        e.setPhase(phase);
        e.setCapturedAt(capturedAt);               // explicite -> ordre déterministe (pas de flakiness)
        e.setCommand(cmd);
        e.setExitCode(0);
        e.setStdout("synthetic-" + phase);
        e.setMeasuresJson(measures);
        e.setAvailable(available);
        e.setSource("FIXTURE");
        return e;
    }

    private IncidentVerification verification(String result, String criteria, String measures, boolean available) {
        IncidentVerification v = new IncidentVerification();
        v.setIncidentId(INC);
        v.setResult(result);
        v.setCriteria(criteria);
        v.setMeasuresJson(measures);
        v.setReason("synthetic " + result);
        v.setAvailable(available);
        return v;
    }

    @Test
    void evidence_beforeAndAfter_roundTripInOrder() {
        evidenceRepo.save(evidence(TerminalEvidencePhase.BEFORE, T0, "df -h", true, "{\"disk\":\"95%\"}"));
        evidenceRepo.save(evidence(TerminalEvidencePhase.AFTER, T0.plusMinutes(5), "df -h", true, "{\"disk\":\"40%\"}"));
        em.flush();
        em.clear();                                // force un vrai aller-retour BD (hors cache de persistance)

        List<IncidentTerminalEvidence> all = evidenceRepo.findByIncidentIdOrderByCapturedAtAsc(INC);
        assertThat(all).hasSize(2);
        assertThat(all).extracting(IncidentTerminalEvidence::getPhase)
                .containsExactly(TerminalEvidencePhase.BEFORE, TerminalEvidencePhase.AFTER);
        assertThat(all.get(0).getSource()).isEqualTo("FIXTURE");
        assertThat(all.get(0).getMeasuresJson()).contains("95%");
        assertThat(all.get(0).getCreatedAt()).isNotNull();            // @PrePersist appliqué

        List<IncidentTerminalEvidence> afterOnly = evidenceRepo
                .findByIncidentIdAndPhaseOrderByCapturedAtAsc(INC, TerminalEvidencePhase.AFTER);
        assertThat(afterOnly).hasSize(1);
        assertThat(afterOnly.get(0).getMeasuresJson()).contains("40%");
    }

    @Test
    void evidence_antiDoublon_existsByIncidentIdPhaseCommand() {
        evidenceRepo.save(evidence(TerminalEvidencePhase.BEFORE, T0, "systemctl status kubelet", true, null));
        em.flush();
        assertThat(evidenceRepo.existsByIncidentIdAndPhaseAndCommand(
                INC, TerminalEvidencePhase.BEFORE, "systemctl status kubelet")).isTrue();
        assertThat(evidenceRepo.existsByIncidentIdAndPhaseAndCommand(
                INC, TerminalEvidencePhase.AFTER, "systemctl status kubelet")).isFalse();
    }

    @Test
    void evidence_nonCollecte_persistsAsUnavailable() {
        evidenceRepo.save(evidence(TerminalEvidencePhase.AFTER, T0, "crictl images", false, null));
        em.flush();
        em.clear();
        IncidentTerminalEvidence e = evidenceRepo.findByIncidentIdOrderByCapturedAtAsc(INC).get(0);
        assertThat(e.isAvailable()).isFalse();                        // NON COLLECTÉ, jamais inventé
        assertThat(e.getMeasuresJson()).isNull();
    }

    @Test
    void verification_resolved_pass_roundTrip() {
        verificationRepo.save(verification("PASS", "node Ready; host_cpu<90%", "{\"nodeReady\":true,\"cpu\":12}", true));
        em.flush();
        em.clear();
        Optional<IncidentVerification> top = verificationRepo.findTopByIncidentIdOrderByVerifiedAtDesc(INC);
        assertThat(top).isPresent();
        assertThat(top.get().getResult()).isEqualTo("PASS");
        assertThat(top.get().isAvailable()).isTrue();
        assertThat(top.get().getMeasuresJson()).contains("nodeReady");
        assertThat(top.get().getVerifiedAt()).isNotNull();            // @PrePersist appliqué
    }

    @Test
    void verification_escalated_roundTrip() {
        verificationRepo.save(verification("ESCALATED", "aucune action sûre", null, true));
        em.flush();
        em.clear();
        Optional<IncidentVerification> top = verificationRepo.findTopByIncidentIdOrderByVerifiedAtDesc(INC);
        assertThat(top).isPresent();
        assertThat(top.get().getResult()).isEqualTo("ESCALATED");
    }

    @Test
    void absent_returnsEmpty_notFakeSuccess() {
        long unknown = 42424242L;
        assertThat(verificationRepo.findTopByIncidentIdOrderByVerifiedAtDesc(unknown)).isEmpty();
        assertThat(evidenceRepo.findByIncidentIdOrderByCapturedAtAsc(unknown)).isEmpty();
    }
}
