package com.orange.pfe.backend.service.remediation;

/**
 * DISK-E2E-158-02 : definition UNIQUE du perimetre du scenario manuel DISK_E2E_158.
 *
 * <p>Le scenario n'existe que pour {@code (k8s-nodes, k8s-worker1, NODE_DISK_FULL)} : action {@code HOST_RECLAIM_DISK}
 * executee PAR LE BRIDGE, jamais par le pod hote privilegie historique. Il est active par son propre drapeau
 * ({@code remediation.host-disk-e2e158-enabled}, variable {@code HOST_DISK_E2E_158_ENABLED}, defaut {@code false}) ;
 * l'ancien drapeau global {@code HOST_DISK_REMEDIATION_ENABLED} n'a AUCUN effet sur ce perimetre.
 *
 * <p>Tout ce qui sort de ce triplet (k8s-worker2 = .155, NODE_DISK_PRESSURE, NODE_INODES_FULL, toute autre cible) n'est pas
 * le scenario : {@code matches} rend {@code false}, sans joker ni normalisation hasardeuse.
 */
public final class HostDiskScenario {

    public static final String NAME = "DISK_E2E_158";
    public static final String NAMESPACE = "k8s-nodes";
    public static final String TARGET = "k8s-worker1";
    public static final String INCIDENT_TYPE = "NODE_DISK_FULL";

    /** Recuperation verifiee du scenario : occupation de / &lt;= 77 % (mesuree par le Bridge puis par Prometheus). */
    public static final double RECOVERY_THRESHOLD_PERCENT = 77.0;

    private HostDiskScenario() {
    }

    /** Vrai uniquement pour EXACTEMENT (k8s-nodes, k8s-worker1, NODE_DISK_FULL) ; casse et espaces de bord ignores. */
    public static boolean matches(String namespace, String target, String incidentType) {
        return NAMESPACE.equalsIgnoreCase(clean(namespace)) && matchesTargetAndType(target, incidentType);
    }

    /** Idem sans namespace (la verification Prometheus ne porte que la cible et le type). */
    public static boolean matchesTargetAndType(String target, String incidentType) {
        return TARGET.equalsIgnoreCase(clean(target)) && INCIDENT_TYPE.equalsIgnoreCase(clean(incidentType));
    }

    private static String clean(String value) {
        return value == null ? "" : value.trim();
    }
}
