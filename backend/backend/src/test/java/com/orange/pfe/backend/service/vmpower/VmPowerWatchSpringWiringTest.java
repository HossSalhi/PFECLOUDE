package com.orange.pfe.backend.service.vmpower;

import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.test.context.bean.override.mockito.MockitoBean;

import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.when;

/**
 * VMDOWN-POLICY-20261105 — câblage Spring RÉEL de la surveillance de puissance en mode ACTIVÉ (enabled=true, mode ombre).
 *
 * <p>Le vrai {@link WatchdogClient} vise un port fermé (127.0.0.1:1) : le watchdog est injoignable. Seules les sondes
 * réseau sont simulées (aucune connexion vers les VM du laboratoire). On prouve que, dans ce contexte, un cycle ne lève
 * aucune exception, ne crée ni incident ni action (état d'alimentation non prouvé) et que la vue d'interface reste
 * cohérente. Les tests à mocks ne peuvent pas révéler une erreur de câblage des beans réels.
 */
@SpringBootTest(properties = {
        "jwt.secret=test-only-secret-0123456789-0123456789-0123456789-0123456789",
        "helios.vmpower.enabled=true",
        "helios.vmpower.dry-run=true",
        "remediation.master-watchdog-url=http://127.0.0.1:1",
        "remediation.master-watchdog-token=jeton-de-test-sans-valeur"
})
class VmPowerWatchSpringWiringTest {

    @Autowired private VmPowerWatch watch;
    @Autowired private com.orange.pfe.backend.config.VmPowerProperties props;
    @Autowired private com.orange.pfe.backend.repository.IncidentRepository incidents;
    @Autowired private com.orange.pfe.backend.repository.IncidentActionRepository actions;

    @MockitoBean private VmProbes probes;

    @Test
    void les_reglages_actives_sont_bien_lus_et_le_mode_ombre_est_conserve() {
        assertThat(props.isEnabled()).isTrue();
        assertThat(props.isDryRun()).isTrue();
        assertThat(props.getActiveVms()).isEmpty();
    }

    @Test
    void cycle_reel_avec_watchdog_injoignable_ne_leve_rien_et_ne_cree_ni_incident_ni_action() {
        Map<String, VmProbes.Liveness> down = new LinkedHashMap<>();
        for (VmPowerPolicy.Entry e : VmPowerPolicy.entries()) {
            down.put(e.name(), new VmProbes.Liveness(0, 3, List.of(), List.of("tcp/22", "tcp/80", "udp/53")));
        }
        when(probes.livenessAll(any())).thenReturn(down);   // toutes les VM « muettes » : sans preuve d'alimentation, rien ne se passe

        long incidentsBefore = incidents.count();
        long actionsBefore = actions.count();
        List<String> events = null;
        for (int k = 0; k < 4; k++) {
            events = watch.runCycle(1_800_000_000_000L + k * 15_000L);
        }

        assertThat(events).contains("WATCHDOG_UNREACHABLE");
        assertThat(events).noneMatch(e -> e.startsWith("INCIDENT_CREATED") || e.startsWith("LAUNCHED")
                || e.startsWith("SHADOW") || e.startsWith("ERROR"));
        assertThat(incidents.count()).isEqualTo(incidentsBefore);
        assertThat(actions.count()).isEqualTo(actionsBefore);
    }

    @Test
    void la_vue_d_interface_expose_les_cinq_vm_avec_etat_inconnu_tant_que_le_watchdog_est_muet() {
        Map<String, VmProbes.Liveness> down = new LinkedHashMap<>();
        for (VmPowerPolicy.Entry e : VmPowerPolicy.entries()) {
            down.put(e.name(), new VmProbes.Liveness(0, 3, List.of(), List.of("tcp/22")));
        }
        when(probes.livenessAll(any())).thenReturn(down);
        watch.runCycle(1_800_000_000_000L);

        List<Map<String, Object>> view = watch.snapshot();
        assertThat(view).hasSize(5);
        assertThat(view).extracting(m -> m.get("name"))
                .containsExactly("k8s-worker2", "k8s-worker1", "k8s-lb2", "k8s-monitoring", "k8s-master");
        assertThat(view).allSatisfy(m -> {
            assertThat(m.get("powerState")).isEqualTo("UNKNOWN");          // jamais « allumée » sans preuve
            assertThat(m.get("watchdogReachable")).isEqualTo(false);
            assertThat(m.get("offConfirmed")).isEqualTo(false);
        });
        // .160 : DÉTECTION SEULE (consigne finale du 2026-11-06), aucune route de redémarrage, interface seulement
        Map<String, Object> monitoring = view.stream().filter(m -> "k8s-monitoring".equals(m.get("name")))
                .findFirst().orElseThrow();
        assertThat(monitoring.get("mode")).isEqualTo("DETECT_ONLY");
        assertThat(monitoring.get("restartRoute")).isEqualTo("NONE");
        assertThat(monitoring.get("notifications")).isEqualTo("UI_ONLY");
        // .153 : AUTO depuis la consigne du 2026-11-17 (route Camunda, notifications habituelles)
        Map<String, Object> master = view.stream().filter(m -> "k8s-master".equals(m.get("name"))).findFirst().orElseThrow();
        assertThat(master.get("mode")).isEqualTo("AUTO");
        assertThat(master.get("restartRoute")).isEqualTo("CAMUNDA");
        assertThat(master.get("notifications")).isEqualTo("EMAIL_AND_UI");
        assertThat(master.get("vmClass")).isEqualTo("K8S_CONTROL_PLANE");
    }
}
