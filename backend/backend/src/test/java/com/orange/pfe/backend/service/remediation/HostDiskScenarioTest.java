package com.orange.pfe.backend.service.remediation;

import org.junit.jupiter.api.Test;

import java.lang.reflect.Field;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * DISK-E2E-158-02 : le perimetre du scenario est EXACTEMENT (k8s-nodes, k8s-worker1, NODE_DISK_FULL), sans joker, et toutes les
 * copies de ses constantes (politique, executeur, client Bridge) sont identiques.
 */
class HostDiskScenarioTest {

    @Test
    void matchesTheExactTripletOnly() {
        assertTrue(HostDiskScenario.matches("k8s-nodes", "k8s-worker1", "NODE_DISK_FULL"));
        assertTrue(HostDiskScenario.matches(" K8S-NODES ", " K8s-Worker1 ", " node_disk_full "));
        assertTrue(HostDiskScenario.matchesTargetAndType("k8s-worker1", "NODE_DISK_FULL"));
    }

    @Test
    void rejectsEveryOtherNamespaceTargetOrType() {
        String[] namespaces = {"pfe", "k8s-node", "k8s-nodes-x", "", null};
        String[] targets = {"k8s-worker2", "k8s-master", "k8s-worker3", "k8s-worker", "k8s-worker1-DiskPressure", "k8s-worker11",
                "monitoring", "iacamunda", "lb2", "worker1", "", null};
        String[] types = {"NODE_DISK_PRESSURE", "NODE_INODES_FULL", "NODE_HIGH_CPU", "NODE_NOT_READY", "VM_DOWN", "NODE_DISK_FULLY", "", null};

        for (String namespace : namespaces) {
            assertFalse(HostDiskScenario.matches(namespace, "k8s-worker1", "NODE_DISK_FULL"), "ns=" + namespace);
        }
        for (String target : targets) {
            assertFalse(HostDiskScenario.matches("k8s-nodes", target, "NODE_DISK_FULL"), "cible=" + target);
            assertFalse(HostDiskScenario.matchesTargetAndType(target, "NODE_DISK_FULL"), "cible=" + target);
        }
        for (String type : types) {
            assertFalse(HostDiskScenario.matches("k8s-nodes", "k8s-worker1", type), "type=" + type);
            assertFalse(HostDiskScenario.matchesTargetAndType("k8s-worker1", type), "type=" + type);
        }
    }

    @Test
    void worker2IsNeverTheScenarioWhateverTheTypeAndNamespace() {
        for (String type : new String[] {"NODE_DISK_FULL", "NODE_DISK_PRESSURE", "NODE_INODES_FULL"}) {
            assertFalse(HostDiskScenario.matches("k8s-nodes", "k8s-worker2", type), type);
            assertFalse(HostDiskScenario.matches("pfe", "k8s-worker2", type), type);
        }
    }

    @Test
    void theScenarioConstantsAreIdenticalInEveryCopy() throws Exception {
        assertEquals("DISK_E2E_158", HostDiskScenario.NAME);
        assertEquals("k8s-nodes", HostDiskScenario.NAMESPACE);
        assertEquals("k8s-worker1", HostDiskScenario.TARGET);
        assertEquals("NODE_DISK_FULL", HostDiskScenario.INCIDENT_TYPE);
        assertEquals(77.0, HostDiskScenario.RECOVERY_THRESHOLD_PERCENT);

        // Executeur (visibilite paquet).
        assertEquals(HostDiskScenario.NAME, KubernetesRemediationExecutor.DISK_E2E_SCENARIO);
        assertEquals(HostDiskScenario.TARGET, KubernetesRemediationExecutor.DISK_E2E_TARGET);
        assertEquals(HostDiskScenario.INCIDENT_TYPE, KubernetesRemediationExecutor.DISK_E2E_TYPE);

        // Client Bridge (constantes privees : lues par reflexion).
        assertEquals("HOST_RECLAIM_DISK", privateConstant("DISK_RECLAIM_ACTION"));
        assertEquals(HostDiskScenario.INCIDENT_TYPE, privateConstant("DISK_INCIDENT_TYPE"));
        assertEquals(HostDiskScenario.NAMESPACE, privateConstant("DISK_NAMESPACE"));
        assertEquals(HostDiskScenario.TARGET, privateConstant("DISK_TARGET"));
        assertEquals(HostDiskScenario.NAME, privateConstant("DISK_SCENARIO"));
    }

    private static String privateConstant(String name) throws Exception {
        Field field = RestTemplateBridgeClient.class.getDeclaredField(name);
        field.setAccessible(true);
        return (String) field.get(null);
    }
}
