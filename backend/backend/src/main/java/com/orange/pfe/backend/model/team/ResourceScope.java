package com.orange.pfe.backend.model.team;

import com.orange.pfe.backend.model.maintenance.MaintenanceResourceType;

import java.util.Locale;
import java.util.Optional;
import java.util.Set;

/**
 * Domaine de compétence ("spécialité") d'une équipe et portée d'une ressource, partagé par les équipes, les
 * maintenances et le routage des incidents.
 *
 * <p>Ce n'est volontairement pas {@link MaintenanceResourceType} : celui-ci décrit le <em>type technique</em> d'une
 * ressource (APPLICATION, NODE, VM, SERVICE) et NODE et VM y désignent la même machine dans cette architecture
 * (chaque nœud Kubernetes est une VM). Une équipe, elle, se définit par un domaine d'expertise. Les deux
 * correspondances ({@link #fromMaintenance(MaintenanceResourceType)} et
 * {@link #fromIncident(String, String, String)}) sont centralisées ici pour qu'aucune autre classe ne les redéfinisse.
 *
 * <ul>
 *   <li>{@link #VM} : machine hôte (CPU, mémoire, disque, VM arrêtée) ;</li>
 *   <li>{@link #CLUSTER} : plan de contrôle et capacité Kubernetes (nœud non prêt, ordonnancement) ;</li>
 *   <li>{@link #APPLICATION} : applications et pods applicatifs ;</li>
 *   <li>{@link #SERVICE} : services de supervision et d'orchestration (Prometheus, Grafana, Loki, Camunda).</li>
 * </ul>
 */
public enum ResourceScope {
    VM,
    CLUSTER,
    APPLICATION,
    SERVICE;

    /** Namespace sous lequel HELIOS enregistre les incidents d'hôte (le champ {@code app} porte alors le nom du nœud). */
    public static final String NODES_NAMESPACE = "k8s-nodes";

    private static final Set<String> SERVICE_APPS = Set.of("prometheus", "grafana", "loki", "camunda");
    private static final Set<String> SERVICE_NAMESPACES = Set.of("infra", "vm-monitoring", "vm-ai-camunda");

    /** Types d'incident toujours liés à la machine hôte. */
    private static final Set<String> VM_TYPES = Set.of(
            "VM_DOWN",
            "NODE_HIGH_CPU", "NODE_CPU_SATURATION",
            "NODE_HIGH_MEMORY", "NODE_MEMORY_PRESSURE",
            "NODE_DISK_FULL", "NODE_DISK_PRESSURE", "NODE_INODES_FULL",
            "NODE_TIME_DRIFT");

    /** Types liés à l'état ou à la capacité du cluster Kubernetes. */
    private static final Set<String> CLUSTER_TYPES = Set.of(
            "NODE_NOT_READY", "POD_PENDING_INSUFFICIENT_RESOURCES", "POD_EVICTED");

    /** Types ambigus : machine hôte si l'incident est enregistré sous {@link #NODES_NAMESPACE}, application sinon. */
    private static final Set<String> HOST_OR_APPLICATION_TYPES = Set.of("CPU_STRESS", "MEMORY_STRESS", "MEMORY_LEAK");

    /**
     * Portée d'une ressource de maintenance : VM → VM ; NODE → CLUSTER (opération sur un nœud Kubernetes : vidage,
     * mise à jour du noyau…) ; APPLICATION → APPLICATION ; SERVICE → SERVICE.
     */
    public static ResourceScope fromMaintenance(MaintenanceResourceType type) {
        if (type == null) {
            throw new IllegalArgumentException("Le type de ressource est obligatoire");
        }
        return switch (type) {
            case VM -> VM;
            case NODE -> CLUSTER;
            case APPLICATION -> APPLICATION;
            case SERVICE -> SERVICE;
        };
    }

    /**
     * Portée d'un incident, ou {@link Optional#empty()} si elle ne peut pas être établie (aucun routage automatique).
     *
     * <p>Les composants de supervision passent en premier : la sonde d'infrastructure crée des incidents typés
     * {@code VM_DOWN} pour des services injoignables (par exemple Loki) ; ils concernent un SERVICE, pas une VM.
     *
     * @param incidentType type d'incident (par exemple {@code NODE_HIGH_CPU}), insensible à la casse
     * @param namespace    namespace de l'incident ({@code k8s-nodes} pour un incident d'hôte)
     * @param app          application ou nom du nœud concerné
     */
    public static Optional<ResourceScope> fromIncident(String incidentType, String namespace, String app) {
        String ns = lower(namespace);
        if (SERVICE_NAMESPACES.contains(ns) || SERVICE_APPS.contains(lower(app))) {
            return Optional.of(SERVICE);
        }
        String type = incidentType == null ? "" : incidentType.trim().toUpperCase(Locale.ROOT);
        if (VM_TYPES.contains(type)) {
            return Optional.of(VM);
        }
        if (CLUSTER_TYPES.contains(type)) {
            return Optional.of(CLUSTER);
        }
        if (HOST_OR_APPLICATION_TYPES.contains(type)) {
            return Optional.of(NODES_NAMESPACE.equals(ns) ? VM : APPLICATION);
        }
        if (type.startsWith("POD_")) {
            return Optional.of(APPLICATION);
        }
        return Optional.empty();
    }

    private static String lower(String s) {
        return s == null ? "" : s.trim().toLowerCase(Locale.ROOT);
    }
}
