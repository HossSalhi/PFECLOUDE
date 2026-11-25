package com.orange.pfe.backend.service;

import com.orange.pfe.backend.exception.ActionException;
import com.orange.pfe.backend.model.action.ActionType;
import com.orange.pfe.backend.model.action.AutomationMode;
import com.orange.pfe.backend.model.action.RemediationTargetKind;
import com.orange.pfe.backend.model.core.Incident;
import com.orange.pfe.backend.model.enums.IncidentSeverity;
import com.orange.pfe.backend.model.enums.IncidentSource;
import com.orange.pfe.backend.model.enums.IncidentStatus;
import com.orange.pfe.backend.repository.IncidentActionRepository;
import com.orange.pfe.backend.repository.IncidentRepository;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.data.domain.Pageable;

import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.CyclicBarrier;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import java.util.concurrent.TimeUnit;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

/**
 * VMDOWN-POLICY-20261105 — UNE SEULE action RESTART_VM par incident, sous verrou de ligne, sans migration.
 *
 * <p>Base H2 en mémoire éphémère (src/test/resources/application.properties), contexte Spring réel : le verrou
 * {@code SELECT ... FOR UPDATE} et la transaction de {@code ActionServiceImpl.createAction} sont ceux de la production.
 * Données synthétiques ; aucun réseau, aucune VM. Le nettoyage est explicite (le test ne tourne pas dans une
 * transaction rollbackée : les threads concurrents doivent pouvoir COMMITTER).
 */
@SpringBootTest(properties = "jwt.secret=test-only-secret-0123456789-0123456789-0123456789-0123456789")
class ActionServiceRestartVmOncePerIncidentTest {

    @Autowired private ActionService actionService;
    @Autowired private IncidentRepository incidentRepository;
    @Autowired private IncidentActionRepository actionRepository;

    private final List<Long> created = new ArrayList<>();

    @AfterEach
    void cleanup() {
        for (Long id : created) {
            actionRepository.deleteAll(actionRepository.findByIncidentId(id, Pageable.unpaged()).getContent());
            incidentRepository.deleteById(id);
        }
        created.clear();
    }

    private Incident incident(String app) {
        Incident i = new Incident();
        i.setTitle("Kubernetes node NotReady: " + app);
        i.setDescription("incident synthetique VMDOWN-POLICY");
        i.setSeverity(IncidentSeverity.CRITICAL);
        i.setStatus(IncidentStatus.OPEN);
        i.setSource(IncidentSource.OTHER);
        i.setIncidentType("NODE_NOT_READY");
        i.setNamespace("k8s-nodes");
        i.setApp(app);
        i = incidentRepository.save(i);
        created.add(i.getId());
        return i;
    }

    private void restartVm(Incident i) {
        actionService.createAction(i, ActionType.RESTART_VM, "rallumage", "AI-SYSTEM", "k8s-nodes",
                RemediationTargetKind.UNKNOWN, i.getApp(), AutomationMode.AUTO);
    }

    private long restartVmCount(Incident i) {
        return actionRepository.countByIncidentIdAndType(i.getId(), ActionType.RESTART_VM);
    }

    @Test
    void la_premiere_action_est_creee_la_seconde_est_refusee_quel_que_soit_le_statut_de_la_premiere() {
        Incident i = incident("k8s-worker1");
        restartVm(i);
        assertThat(restartVmCount(i)).isEqualTo(1);

        // PENDING : refusée
        assertThatThrownBy(() -> restartVm(i)).isInstanceOf(ActionException.class)
                .hasMessageContaining("RESTART_VM").hasMessageContaining("un seul demarrage");

        // FAILED : refusée aussi (un échec ne rouvre PAS le droit de redémarrer pour ce même incident)
        Long actionId = actionRepository.findByIncidentId(i.getId(), Pageable.unpaged()).getContent().get(0).getId();
        actionService.markActionFailed(actionId, "echec de test", "AI-SYSTEM");
        assertThatThrownBy(() -> restartVm(i)).isInstanceOf(ActionException.class);
        assertThat(restartVmCount(i)).isEqualTo(1);
    }

    @Test
    void les_autres_types_d_action_ne_sont_pas_limites() {
        Incident i = incident("k8s-worker2");
        for (int n = 0; n < 2; n++) {
            actionService.createAction(i, ActionType.INVESTIGATION, "enquete", "AI-SYSTEM", "k8s-nodes",
                    RemediationTargetKind.UNKNOWN, i.getApp(), AutomationMode.MANUAL);
            actionService.createAction(i, ActionType.RESTART_POD, "pod", "AI-SYSTEM", "k8s-nodes",
                    RemediationTargetKind.POD, i.getApp(), AutomationMode.MANUAL);
        }
        assertThat(actionRepository.countByIncidentIdAndType(i.getId(), ActionType.INVESTIGATION)).isEqualTo(2);
        assertThat(actionRepository.countByIncidentIdAndType(i.getId(), ActionType.RESTART_POD)).isEqualTo(2);
        // ... et la limite RESTART_VM reste disponible pour cet incident.
        restartVm(i);
        assertThat(restartVmCount(i)).isEqualTo(1);
    }

    @Test
    void chaque_incident_a_droit_a_sa_propre_action_RESTART_VM() {
        Incident a = incident("k8s-worker1");
        Incident b = incident("k8s-worker1");
        restartVm(a);
        restartVm(b);
        assertThat(restartVmCount(a)).isEqualTo(1);
        assertThat(restartVmCount(b)).isEqualTo(1);
    }

    @Test
    void incident_archive_ou_clos_reste_refuse() {
        Incident i = incident("k8s-worker1");
        i.setStatus(IncidentStatus.ARCHIVED);
        incidentRepository.save(i);
        assertThatThrownBy(() -> restartVm(i)).isInstanceOf(ActionException.class);
        assertThat(restartVmCount(i)).isZero();
    }

    @Test
    void creations_concurrentes_une_seule_reussit_et_une_seule_ligne_est_ecrite() throws Exception {
        ExecutorService pool = Executors.newFixedThreadPool(4);
        try {
            for (int round = 0; round < 6; round++) {
                Incident i = incident("k8s-worker1");
                CyclicBarrier barrier = new CyclicBarrier(4);
                List<Future<Boolean>> results = new ArrayList<>();
                for (int t = 0; t < 4; t++) {
                    results.add(pool.submit(() -> {
                        barrier.await(10, TimeUnit.SECONDS);
                        try {
                            restartVm(i);
                            return true;
                        } catch (ActionException refused) {
                            return false;
                        }
                    }));
                }
                int accepted = 0;
                for (Future<Boolean> f : results) {
                    if (f.get(60, TimeUnit.SECONDS)) {
                        accepted++;
                    }
                }
                assertThat(accepted).as("manche %d : un seul créateur doit réussir", round).isEqualTo(1);
                assertThat(restartVmCount(i)).as("manche %d : une seule ligne", round).isEqualTo(1);
            }
        } finally {
            pool.shutdownNow();
        }
    }
}
