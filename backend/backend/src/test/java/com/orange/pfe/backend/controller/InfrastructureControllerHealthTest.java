package com.orange.pfe.backend.controller;

import com.orange.pfe.backend.config.AiopsPollingProperties;
import com.orange.pfe.backend.service.InfrastructureHealthService;
import com.orange.pfe.backend.service.InfrastructureHealthService.ComponentStatus;
import com.orange.pfe.backend.service.InfrastructureHealthService.Snapshot;
import com.orange.pfe.backend.service.KubernetesDiscoveryService;
import com.orange.pfe.backend.service.vmpower.VmPowerWatch;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.ObjectProvider;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.setup.MockMvcBuilders;

import java.util.List;

import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

/**
 * VMDOWN-POLICY-20261105 — contrat JSON de {@code GET /api/infrastructure/health} consommé par l'interface Angular :
 * snapshot (jamais un contrôle bloquant à chaque appel) + {@code vmPolicy} avec les champs EXACTS de l'interface
 * {@code VmPolicyEntry}. Contrôleur isolé (MockMvc autonome) : aucun réseau.
 */
class InfrastructureControllerHealthTest {

    private InfrastructureHealthService health;
    private VmPowerWatch watch;
    private MockMvc mvc;

    @SuppressWarnings("unchecked")
    @BeforeEach
    void setUp() {
        health = mock(InfrastructureHealthService.class);
        watch = mock(VmPowerWatch.class);
        ObjectProvider<VmPowerWatch> provider = mock(ObjectProvider.class);
        when(provider.getIfAvailable()).thenReturn(watch);
        mvc = MockMvcBuilders.standaloneSetup(new InfrastructureController(
                health, mock(KubernetesDiscoveryService.class), mock(AiopsPollingProperties.class), provider)).build();
        when(health.freshSnapshot(45_000L)).thenReturn(new Snapshot(List.of(
                new ComponentStatus("infra", "prometheus", false, "Injoignable"),
                new ComponentStatus("k8s-nodes", "k8s-master", true, "Ready")), 1_800_000_000_000L));
    }

    @Test
    void la_reponse_utilise_le_snapshot_et_ne_relance_jamais_un_controle_complet() throws Exception {
        when(watch.snapshot()).thenReturn(VmPowerWatch.policyOnly());
        mvc.perform(get("/api/infrastructure/health"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.total").value(2))
                .andExpect(jsonPath("$.up").value(1))
                .andExpect(jsonPath("$.down").value(1))
                .andExpect(jsonPath("$.components[0].app").value("prometheus"))
                .andExpect(jsonPath("$.components[0].status").value("DOWN"))
                .andExpect(jsonPath("$.checkedAt").isNotEmpty());
        verify(health).freshSnapshot(45_000L);
        verify(health, never()).checkAll();
    }

    @Test
    void vmPolicy_porte_les_champs_attendus_par_l_interface_pour_les_cinq_vm() throws Exception {
        when(watch.snapshot()).thenReturn(VmPowerWatch.policyOnly());
        mvc.perform(get("/api/infrastructure/health"))
                .andExpect(jsonPath("$.vmPolicy.length()").value(5))
                .andExpect(jsonPath("$.vmPolicy[0].name").value("k8s-worker2"))
                .andExpect(jsonPath("$.vmPolicy[0].ip").value("192.168.148.155"))
                .andExpect(jsonPath("$.vmPolicy[0].mode").value("AUTO"))
                .andExpect(jsonPath("$.vmPolicy[0].vmClass").value("K8S_WORKER"))
                .andExpect(jsonPath("$.vmPolicy[0].restartRoute").value("CAMUNDA"))
                .andExpect(jsonPath("$.vmPolicy[0].notifications").value("EMAIL_AND_UI"))
                .andExpect(jsonPath("$.vmPolicy[3].name").value("k8s-monitoring"))
                .andExpect(jsonPath("$.vmPolicy[3].mode").value("DETECT_ONLY"))
                .andExpect(jsonPath("$.vmPolicy[3].restartRoute").value("NONE"))
                .andExpect(jsonPath("$.vmPolicy[3].notifications").value("UI_ONLY"))
                // .153 : AUTO / route CAMUNDA / e-mail + interface depuis la consigne du 2026-11-17 (était DENY_RESTART / NONE)
                .andExpect(jsonPath("$.vmPolicy[4].name").value("k8s-master"))
                .andExpect(jsonPath("$.vmPolicy[4].ip").value("192.168.148.153"))
                .andExpect(jsonPath("$.vmPolicy[4].mode").value("AUTO"))
                .andExpect(jsonPath("$.vmPolicy[4].vmClass").value("K8S_CONTROL_PLANE"))
                .andExpect(jsonPath("$.vmPolicy[4].restartRoute").value("CAMUNDA"))
                .andExpect(jsonPath("$.vmPolicy[4].notifications").value("EMAIL_AND_UI"));
    }

    @Test
    void sans_surveillance_de_puissance_la_politique_statique_reste_exposee() throws Exception {
        @SuppressWarnings("unchecked")
        ObjectProvider<VmPowerWatch> absent = mock(ObjectProvider.class);
        when(absent.getIfAvailable()).thenReturn(null);
        MockMvc standalone = MockMvcBuilders.standaloneSetup(new InfrastructureController(
                health, mock(KubernetesDiscoveryService.class), mock(AiopsPollingProperties.class), absent)).build();
        standalone.perform(get("/api/infrastructure/health"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.vmPolicy.length()").value(5))
                .andExpect(jsonPath("$.vmPolicy[2].name").value("k8s-lb2"))
                .andExpect(jsonPath("$.vmPolicy[2].mode").value("AUTO"));
    }
}
