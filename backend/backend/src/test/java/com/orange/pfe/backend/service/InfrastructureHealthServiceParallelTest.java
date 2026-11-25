package com.orange.pfe.backend.service;

import com.orange.pfe.backend.config.InfraHealthProperties;
import com.orange.pfe.backend.service.InfrastructureHealthService.ComponentStatus;
import com.orange.pfe.backend.service.InfrastructureHealthService.Snapshot;
import org.junit.jupiter.api.Test;

import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertSame;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * VMDOWN-POLICY-20261105 — le contrôle d'infrastructure est PARALLÈLE à délai borné et alimente un snapshot :
 * une VM muette (.160) ne bloque plus l'API /api/infrastructure/health pendant ~30 s.
 * Aucun appel à l'API Kubernetes : les contrôles Kubernetes sont neutralisés par une sous-classe de test.
 */
class InfrastructureHealthServiceParallelTest {

    /** Service dont les contrôles Kubernetes/DNS sont inertes (aucun accès au cluster ni au réseau). */
    private static class Offline extends InfrastructureHealthService {
        Offline(InfraHealthProperties p) {
            super(p);
        }

        @Override
        List<ComponentStatus> checkKubernetesNodes() {
            return List.of(new ComponentStatus("k8s-nodes", "k8s-worker1", true, "Ready"));
        }

        @Override
        public List<ComponentStatus> checkNodePressures() {
            return List.of();
        }

        @Override
        public ComponentStatus checkDnsResolution() {
            return new ComponentStatus("cluster", "coredns", true, "DNS resolution OK");
        }
    }

    private static InfraHealthProperties blackhole(int timeoutSeconds) {
        InfraHealthProperties p = new InfraHealthProperties();
        p.setTimeoutSeconds(timeoutSeconds);
        // adresse de « trou noir » : la connexion expire (ou est rejetée immédiatement selon le réseau)
        p.setPrometheusUrl("http://10.255.255.1:9090");
        p.setGrafanaUrl("http://10.255.255.1:3000");
        p.setLokiUrl("http://10.255.255.1:3100");
        p.setCamundaUrl("http://10.255.255.1:8088");
        return p;
    }

    @Test
    void quatre_services_muets_ne_se_cumulent_pas_le_pire_cas_est_borne() {
        InfrastructureHealthService svc = new Offline(blackhole(2));
        long t0 = System.nanoTime();
        List<ComponentStatus> statuses = svc.checkAll();
        long elapsedMs = (System.nanoTime() - t0) / 1_000_000;

        // séquentiel : 4 x 2 s = 8 s minimum ; parallèle : ~2 s
        assertTrue(elapsedMs < 6_000, "contrôles parallèles et bornés : " + elapsedMs + " ms");
        assertEquals(List.of("k8s-worker1", "coredns", "prometheus", "grafana", "loki", "camunda"),
                statuses.stream().map(ComponentStatus::app).toList(), "ordre stable des composants");
        for (String app : List.of("prometheus", "grafana", "loki", "camunda")) {
            ComponentStatus s = statuses.stream().filter(x -> x.app().equals(app)).findFirst().orElseThrow();
            assertEquals("infra", s.namespace());
            assertFalse(s.up(), app);
        }
    }

    @Test
    void le_snapshot_est_alimente_et_reutilise_tant_qu_il_est_frais() {
        InfrastructureHealthService svc = new Offline(blackhole(1));
        svc.checkAll();
        Snapshot first = svc.freshSnapshot(60_000);
        assertNotNull(first);
        assertEquals(6, first.statuses().size());
        // appel suivant : même objet, aucun nouveau contrôle
        assertSame(first, svc.freshSnapshot(60_000));
        // snapshot expiré (âge max 0) : un nouveau contrôle est effectué
        Snapshot refreshed = svc.freshSnapshot(-1);
        assertTrue(refreshed.generatedAtMs() >= first.generatedAtMs());
        assertFalse(refreshed == first);
    }

    @Test
    void un_snapshot_est_calcule_a_la_demande_si_aucun_controle_n_a_eu_lieu() {
        InfrastructureHealthService svc = new Offline(blackhole(1));
        Snapshot s = svc.freshSnapshot(45_000);
        assertNotNull(s);
        assertEquals(6, s.statuses().size());
    }
}
