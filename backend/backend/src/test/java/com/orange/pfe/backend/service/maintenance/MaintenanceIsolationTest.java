package com.orange.pfe.backend.service.maintenance;

import org.junit.jupiter.api.Test;

import java.lang.reflect.Constructor;
import java.lang.reflect.Field;
import java.lang.reflect.Method;
import java.util.ArrayList;
import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * GARANTIE D'ISOLEMENT : le module Maintenance est déclaratif. Il ne dépend d'aucun composant de supervision, de
 * remédiation, de pilotage des VM, d'orchestration ou de sécurité : déclarer une ressource « en maintenance » ne peut
 * donc ni arrêter la supervision, ni poser/lever le drapeau de maintenance de l'hôte (watchdog), ni déclencher une
 * action de remédiation. Le test échoue si quelqu'un ajoute une telle dépendance.
 */
class MaintenanceIsolationTest {

    private static final List<String> FORBIDDEN_PACKAGES = List.of(
            "com.orange.pfe.backend.service.vmpower",
            "com.orange.pfe.backend.service.remediation");

    private static final List<String> FORBIDDEN_CLASSES = List.of(
            "ActionService", "ActionServiceImpl", "CamundaWorkflowService", "CamundaJobWorkerLifecycle",
            "OrchestrationService", "AIOpsOrchestrationService", "InfrastructureHealthScheduler",
            "InfrastructureHealthService", "HostIncidentIngestionService", "FaultInjectionService",
            "WorkflowService", "IncidentService", "NotificationService", "EmailNotificationService",
            "RemediationProperties", "SecurityConfig", "JwtAuthFilter");

    private static List<Class<?>> dependenciesOf(Class<?> type) {
        List<Class<?>> deps = new ArrayList<>();
        for (Field f : type.getDeclaredFields()) {
            deps.add(f.getType());
        }
        for (Constructor<?> c : type.getDeclaredConstructors()) {
            deps.addAll(List.of(c.getParameterTypes()));
        }
        for (Method m : type.getDeclaredMethods()) {
            deps.add(m.getReturnType());
            deps.addAll(List.of(m.getParameterTypes()));
        }
        return deps;
    }

    private static void assertIsolated(Class<?> type) {
        for (Class<?> dep : dependenciesOf(type)) {
            String name = dep.getName();
            assertThat(FORBIDDEN_PACKAGES).as(type.getSimpleName() + " dépend de " + name)
                    .noneMatch(name::startsWith);
            assertThat(FORBIDDEN_CLASSES).as(type.getSimpleName() + " dépend de " + name)
                    .noneMatch(c -> dep.getSimpleName().equals(c));
        }
    }

    @Test
    void maintenanceServiceDoesNotDependOnSupervisionRemediationOrVmPowerComponents() {
        assertIsolated(MaintenanceService.class);
    }

    @Test
    void theResourceCatalogOnlyReadsDiscoveryAndConfigurationAndCommandsNothing() {
        assertIsolated(MaintenanceResourceCatalog.class);
        assertThat(dependenciesOf(MaintenanceResourceCatalog.class).stream().map(Class::getSimpleName))
                .doesNotContain("WatchdogClient", "VmPowerWatch", "RemediationExecutor");
    }

    @Test
    void noMethodOfTheMaintenanceModuleIsNamedAfterAnActionOnTheHostWatchdog() {
        for (Class<?> type : List.of(MaintenanceService.class, MaintenanceResourceCatalog.class)) {
            for (Method m : type.getDeclaredMethods()) {
                assertThat(m.getName().toLowerCase()).as(type.getSimpleName() + "#" + m.getName())
                        .doesNotContain("watchdog").doesNotContain("restart").doesNotContain("poweron")
                        .doesNotContain("remediat");
            }
        }
    }
}
