package com.orange.pfe.backend.service.remediation;

/**
 * Lecture de l'état Ready d'un nœud Kubernetes.
 *
 * @param reason          raison de la condition Ready (« KubeletReady » quand le kubelet publie lui-même Ready ;
 *                        « KubeletNotReady » / « NodeStatusUnknown » sinon) — vide si inconnue
 * @param leaseAgeSeconds âge du dernier renouvellement du Lease {@code kube-node-lease/<nœud>} (le kubelet le
 *                        renouvelle toutes les ~10 s) — {@code null} si non lu
 */
public record NodeReadinessObservation(
        boolean apiAvailable,
        boolean ready,
        String conditionStatus,
        String message,
        String reason,
        Long leaseAgeSeconds
) {
    /** Un Lease plus ancien que cette durée n'est plus le signe d'un kubelet vivant. */
    public static final long KUBELET_LEASE_MAX_AGE_SECONDS = 60L;

    public NodeReadinessObservation {
        conditionStatus = conditionStatus == null
                ? "UNKNOWN"
                : conditionStatus;

        message = message == null ? "" : message;
        reason = reason == null ? "" : reason;

        if (ready && !apiAvailable) {
            throw new IllegalArgumentException(
                    "Un nœud ne peut pas être Ready si l'API est indisponible");
        }
    }

    /** Forme historique (sans raison ni Lease) : le kubelet n'est alors PAS prouvé actif. */
    public NodeReadinessObservation(boolean apiAvailable, boolean ready, String conditionStatus, String message) {
        this(apiAvailable, ready, conditionStatus, message, null, null);
    }

    /**
     * Le kubelet est prouvé actif : il publie lui-même Ready ({@code KubeletReady}) ET le Lease du nœud a été
     * renouvelé il y a moins de {@value #KUBELET_LEASE_MAX_AGE_SECONDS} s. Jamais vrai sans ces deux preuves.
     */
    public boolean kubeletActive() {
        return apiAvailable && ready && "KubeletReady".equals(reason)
                && leaseAgeSeconds != null && leaseAgeSeconds >= 0 && leaseAgeSeconds <= KUBELET_LEASE_MAX_AGE_SECONDS;
    }
}
