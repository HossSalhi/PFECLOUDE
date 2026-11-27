package com.orange.pfe.backend.model.team;

import com.orange.pfe.backend.model.maintenance.MaintenanceResourceType;
import org.junit.jupiter.api.Test;

import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

/** Correspondances centralisées de {@link ResourceScope} (maintenance → portée, incident → portée). */
class ResourceScopeTest {

    @Test
    void enumHasExactlyTheFourAgreedScopes() {
        assertThat(ResourceScope.values())
                .containsExactly(ResourceScope.VM, ResourceScope.CLUSTER, ResourceScope.APPLICATION, ResourceScope.SERVICE);
    }

    @Test
    void everyMaintenanceResourceTypeHasAScope() {
        for (MaintenanceResourceType type : MaintenanceResourceType.values()) {
            assertThat(ResourceScope.fromMaintenance(type)).as("type %s", type).isNotNull();
        }
    }

    @Test
    void maintenanceMappingFollowsTheAgreedChoice() {
        assertThat(ResourceScope.fromMaintenance(MaintenanceResourceType.VM)).isEqualTo(ResourceScope.VM);
        assertThat(ResourceScope.fromMaintenance(MaintenanceResourceType.NODE)).isEqualTo(ResourceScope.CLUSTER);
        assertThat(ResourceScope.fromMaintenance(MaintenanceResourceType.APPLICATION)).isEqualTo(ResourceScope.APPLICATION);
        assertThat(ResourceScope.fromMaintenance(MaintenanceResourceType.SERVICE)).isEqualTo(ResourceScope.SERVICE);
    }

    @Test
    void maintenanceMappingRejectsNull() {
        assertThatThrownBy(() -> ResourceScope.fromMaintenance(null)).isInstanceOf(IllegalArgumentException.class);
    }

    @Test
    void hostIncidentsMapToVm() {
        for (String type : new String[]{"VM_DOWN", "NODE_HIGH_CPU", "NODE_CPU_SATURATION", "NODE_HIGH_MEMORY",
                "NODE_MEMORY_PRESSURE", "NODE_DISK_FULL", "NODE_DISK_PRESSURE", "NODE_INODES_FULL", "NODE_TIME_DRIFT"}) {
            assertThat(ResourceScope.fromIncident(type, "k8s-nodes", "k8s-worker2"))
                    .as("type %s", type).contains(ResourceScope.VM);
        }
    }

    @Test
    void clusterCapacityAndNodeStateIncidentsMapToCluster() {
        assertThat(ResourceScope.fromIncident("NODE_NOT_READY", "k8s-nodes", "k8s-worker1")).contains(ResourceScope.CLUSTER);
        assertThat(ResourceScope.fromIncident("POD_PENDING_INSUFFICIENT_RESOURCES", "pfe", "backend")).contains(ResourceScope.CLUSTER);
        assertThat(ResourceScope.fromIncident("POD_EVICTED", "pfe", "backend")).contains(ResourceScope.CLUSTER);
    }

    @Test
    void otherPodIncidentsMapToApplication() {
        for (String type : new String[]{"POD_CRASH_LOOP", "POD_IMAGE_PULL_BACKOFF", "POD_OOM_KILLED", "POD_CONFIG_ERROR",
                "POD_INIT_CONTAINER_FAILURE", "POD_STUCK_TERMINATING", "POD_ERR_IMAGE_PULL"}) {
            assertThat(ResourceScope.fromIncident(type, "pfe", "backend")).as("type %s", type).contains(ResourceScope.APPLICATION);
        }
    }

    @Test
    void ambiguousTypesDependOnTheNamespace() {
        for (String type : new String[]{"CPU_STRESS", "MEMORY_STRESS", "MEMORY_LEAK"}) {
            assertThat(ResourceScope.fromIncident(type, "k8s-nodes", "k8s-worker2")).as("%s sur un nœud", type).contains(ResourceScope.VM);
            assertThat(ResourceScope.fromIncident(type, "pfe", "backend")).as("%s sur une application", type).contains(ResourceScope.APPLICATION);
        }
    }

    @Test
    void monitoringComponentsMapToServiceEvenWhenTypedVmDown() {
        // Cas réel du 26 nov. 2026 : « Infrastructure component 'loki' is unreachable » est typé VM_DOWN.
        assertThat(ResourceScope.fromIncident("VM_DOWN", "infra", "loki")).contains(ResourceScope.SERVICE);
        assertThat(ResourceScope.fromIncident("VM_DOWN", "vm-monitoring", "prometheus")).contains(ResourceScope.SERVICE);
        assertThat(ResourceScope.fromIncident("VM_DOWN", "vm-ai-camunda", "camunda")).contains(ResourceScope.SERVICE);
        assertThat(ResourceScope.fromIncident("NODE_HIGH_CPU", "k8s-nodes", "grafana")).contains(ResourceScope.SERVICE);
    }

    @Test
    void realVmDownStaysOnVm() {
        assertThat(ResourceScope.fromIncident("VM_DOWN", "k8s-nodes", "k8s-lb2")).contains(ResourceScope.VM);
    }

    @Test
    void matchingIsCaseInsensitiveAndTrimmed() {
        assertThat(ResourceScope.fromIncident("  node_high_cpu ", " K8S-Nodes ", "k8s-worker2")).contains(ResourceScope.VM);
        assertThat(ResourceScope.fromIncident("VM_DOWN", "INFRA", "Loki")).contains(ResourceScope.SERVICE);
    }

    @Test
    void unknownOrMissingTypesGiveNoScopeSoNoAutomaticRouting() {
        assertThat(ResourceScope.fromIncident("SOMETHING_NEW", "pfe", "backend")).isEqualTo(Optional.empty());
        assertThat(ResourceScope.fromIncident(null, null, null)).isEqualTo(Optional.empty());
        assertThat(ResourceScope.fromIncident("", "", "")).isEqualTo(Optional.empty());
    }
}
