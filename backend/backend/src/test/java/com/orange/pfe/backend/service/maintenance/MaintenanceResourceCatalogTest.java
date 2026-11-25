package com.orange.pfe.backend.service.maintenance;

import com.orange.pfe.backend.config.AiopsPollingProperties;
import com.orange.pfe.backend.config.VmPowerProperties;
import com.orange.pfe.backend.dto.maintenance.MaintenanceDtos.ResourceResponse;
import com.orange.pfe.backend.model.maintenance.MaintenanceResourceType;
import com.orange.pfe.backend.service.KubernetesDiscoveryService;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.ObjectProvider;

import java.util.List;
import java.util.Set;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

/** Catalogue des ressources de maintenance : sources existantes en lecture seule, validation et repli. */
class MaintenanceResourceCatalogTest {

    @SuppressWarnings("unchecked")
    private static <T> ObjectProvider<T> provider(T value) {
        ObjectProvider<T> p = mock(ObjectProvider.class);
        when(p.getIfAvailable()).thenReturn(value);
        return p;
    }

    private static AiopsPollingProperties.Target target(String ns, String app) {
        AiopsPollingProperties.Target t = new AiopsPollingProperties.Target();
        t.setNamespace(ns);
        t.setApp(app);
        return t;
    }

    private MaintenanceResourceCatalog catalog(List<AiopsPollingProperties.Target> apps, Set<String> vms) {
        KubernetesDiscoveryService discovery = mock(KubernetesDiscoveryService.class);
        when(discovery.discoverTargets()).thenReturn(apps);
        VmPowerProperties props = new VmPowerProperties();
        props.setActiveVms(vms);
        return new MaintenanceResourceCatalog(provider(discovery), provider(props));
    }

    @Test
    void listsApplicationsAndVmsFromExistingSources() {
        MaintenanceResourceCatalog c = catalog(List.of(target("pfe", "backend"), target("pfe", "frontend")),
                Set.of("k8s-worker1"));

        assertThat(c.list()).extracting(ResourceResponse::type, ResourceResponse::name).containsExactlyInAnyOrder(
                org.assertj.core.groups.Tuple.tuple(MaintenanceResourceType.APPLICATION, "pfe/backend"),
                org.assertj.core.groups.Tuple.tuple(MaintenanceResourceType.APPLICATION, "pfe/frontend"),
                org.assertj.core.groups.Tuple.tuple(MaintenanceResourceType.VM, "k8s-worker1"));
    }

    @Test
    void validatesKnownTypesAgainstTheCatalogAndNormalisesTheCase() {
        MaintenanceResourceCatalog c = catalog(List.of(target("pfe", "backend")), Set.of("k8s-worker1"));

        assertThat(c.validateAndNormalize(MaintenanceResourceType.APPLICATION, "  PFE/Backend ")).isEqualTo("pfe/backend");
        assertThat(c.validateAndNormalize(MaintenanceResourceType.VM, "K8S-WORKER1")).isEqualTo("k8s-worker1");
        assertThatThrownBy(() -> c.validateAndNormalize(MaintenanceResourceType.APPLICATION, "pfe/inconnue"))
                .isInstanceOf(IllegalArgumentException.class).hasMessageContaining("Ressource inconnue");
        assertThatThrownBy(() -> c.validateAndNormalize(MaintenanceResourceType.VM, "vm-fantome"))
                .isInstanceOf(IllegalArgumentException.class);
    }

    @Test
    void nodeAndServiceResourcesAreFreeTextWhileBlankIsRefused() {
        MaintenanceResourceCatalog c = catalog(List.of(target("pfe", "backend")), Set.of("k8s-worker1"));
        assertThat(c.validateAndNormalize(MaintenanceResourceType.NODE, " k8s-master ")).isEqualTo("k8s-master");
        assertThat(c.validateAndNormalize(MaintenanceResourceType.SERVICE, "postgres-rw")).isEqualTo("postgres-rw");
        assertThatThrownBy(() -> c.validateAndNormalize(MaintenanceResourceType.NODE, "   "))
                .isInstanceOf(IllegalArgumentException.class);
        assertThatThrownBy(() -> c.validateAndNormalize(MaintenanceResourceType.VM, null))
                .isInstanceOf(IllegalArgumentException.class);
    }

    @Test
    void whenASourceIsUnavailableThePlanningIsStillPossible() {
        KubernetesDiscoveryService broken = mock(KubernetesDiscoveryService.class);
        when(broken.discoverTargets()).thenThrow(new IllegalStateException("API Kubernetes injoignable"));
        VmPowerProperties props = new VmPowerProperties();
        MaintenanceResourceCatalog c = new MaintenanceResourceCatalog(provider(broken), provider(props));

        assertThat(c.list()).isEmpty();
        assertThat(c.validateAndNormalize(MaintenanceResourceType.APPLICATION, "pfe/backend")).isEqualTo("pfe/backend");
        assertThat(c.validateAndNormalize(MaintenanceResourceType.VM, "k8s-worker2")).isEqualTo("k8s-worker2");
    }
}
