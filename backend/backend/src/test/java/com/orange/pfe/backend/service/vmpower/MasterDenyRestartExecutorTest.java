package com.orange.pfe.backend.service.vmpower;

import com.orange.pfe.backend.config.RemediationProperties;
import com.orange.pfe.backend.model.action.ActionType;
import com.orange.pfe.backend.model.action.AutomationMode;
import com.orange.pfe.backend.model.action.RemediationTargetKind;
import com.orange.pfe.backend.service.remediation.KubernetesRemediationExecutor;
import com.orange.pfe.backend.service.remediation.RemediationExecutionResult;
import com.orange.pfe.backend.service.remediation.RemediationPlan;
import com.sun.net.httpserver.HttpServer;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.Test;

import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.util.concurrent.atomic.AtomicInteger;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * Consigne du 2026-11-17 : {@code k8s-master} (.153) est une cible AUTO ; le mode {@code DENY_RESTART} est CONSERVÉ et
 * honoré par la politique. Ce test le rejoue SUR .153 par la couture {@link VmPowerPolicy#overrideEntryForTest}
 * (package-private : voilà pourquoi il vit dans le paquet {@code vmpower}) : un mode DENY_RESTART est refusé par
 * l'exécuteur AVANT tout transport — aucun appel au watchdog, donc aucun démarrage.
 */
class MasterDenyRestartExecutorTest {

    @AfterEach
    void restaurerLaTableDeProduction() {
        VmPowerPolicy.resetForTest();
    }

    private static RemediationPlan vmPlan(String target) {
        return new RemediationPlan(ActionType.RESTART_VM, AutomationMode.AUTO,
                false, true, false, false, false,
                "k8s-nodes", RemediationTargetKind.UNKNOWN, target, "test-vmdown", "NODE_NOT_READY", 4242L);
    }

    @Test
    void master_en_deny_restart_force_est_refuse_avant_tout_reseau() throws Exception {
        VmPowerPolicy.overrideEntryForTest(new VmPowerPolicy.Entry("k8s-master", "192.168.148.153",
                VmPowerPolicy.Mode.DENY_RESTART, VmPowerPolicy.VmClass.K8S_CONTROL_PLANE, "k8s-nodes", "k8s-master",
                "NODE_NOT_READY", VmPowerPolicy.Route.NONE, VmPowerPolicy.Notifications.EMAIL_AND_UI));
        AtomicInteger hits = new AtomicInteger();
        HttpServer server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        server.createContext("/", ex -> {
            hits.incrementAndGet();
            byte[] body = "{\"started\":true,\"status\":\"STARTED\"}".getBytes(StandardCharsets.UTF_8);
            ex.sendResponseHeaders(200, body.length);
            ex.getResponseBody().write(body);
            ex.close();
        });
        server.start();
        try {
            RemediationProperties p = new RemediationProperties();
            p.setMasterWatchdogUrl("http://127.0.0.1:" + server.getAddress().getPort());
            p.setMasterWatchdogToken("tok");
            RemediationExecutionResult r = new KubernetesRemediationExecutor(p).execute(vmPlan("k8s-master"));
            assertFalse(r.success(), "un mode DENY_RESTART ne doit JAMAIS déclencher de power-on");
            assertEquals(0, hits.get(), "aucun appel réseau : refus AVANT tout transport");
            assertTrue(r.message().toLowerCase().contains("aucune autorisation"), r.message());
        } finally {
            server.stop(0);
        }
        VmPowerPolicy.resetForTest();
        assertEquals(VmPowerPolicy.Mode.AUTO, VmPowerPolicy.modeOf("k8s-master"), "la table de production est restaurée");
    }

    @Test
    void sans_couture_master_est_une_cible_auto_qui_tente_l_appel_au_watchdog() throws Exception {
        AtomicInteger hits = new AtomicInteger();
        HttpServer server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        server.createContext("/", ex -> {
            hits.incrementAndGet();
            byte[] body = "{\"started\":true,\"status\":\"STARTED\"}".getBytes(StandardCharsets.UTF_8);
            ex.sendResponseHeaders(200, body.length);
            ex.getResponseBody().write(body);
            ex.close();
        });
        server.start();
        try {
            RemediationProperties p = new RemediationProperties();
            p.setMasterWatchdogUrl("http://127.0.0.1:" + server.getAddress().getPort());
            p.setMasterWatchdogToken("tok");
            RemediationExecutionResult r = new KubernetesRemediationExecutor(p).execute(vmPlan("k8s-master"));
            assertTrue(r.success(), r.message());
            assertEquals(1, hits.get(), "UN SEUL POST (jamais rejoué)");
        } finally {
            server.stop(0);
        }
    }
}
