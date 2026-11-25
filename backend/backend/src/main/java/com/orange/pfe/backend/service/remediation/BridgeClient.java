package com.orange.pfe.backend.service.remediation;

public interface BridgeClient {

    BridgeResponse sendAction(
            String action,
            String incidentType,
            String namespace,
            String target,
            String node
    );

    /**
     * Explicit parameterized overload for the allowlisted host CPU quota action.
     * Clients that do not implement this contract fail closed by default.
     */
    default BridgeResponse sendAction(
            String action,
            String incidentType,
            String namespace,
            String target,
            String node,
            String service,
            int quotaPct,
            int durationSeconds) {
        return new BridgeResponse(false, false, -1,
                "Bridge client does not support parameterized CPU quota requests.");
    }

    /**
     * Explicit parameterized RAM limit action.
     * Implementations without this capability fail closed by default.
     */
    default BridgeResponse sendMemoryLimitAction(
            String action,
            String incidentType,
            String namespace,
            String target,
            String node,
            String service,
            int memoryLimitMb,
            int durationSeconds) {
        return new BridgeResponse(false, false, -1,
                "Bridge client does not support parameterized memory limit requests.");
    }

    /**
     * DISK-E2E-158-01 : liberation des fichiers EXACTS d'un scenario disque (identifie par son nom et par l'incident
     * qui le justifie), suivie d'une MESURE de l'occupation reelle faite par le Bridge.
     * Les clients qui n'implementent pas ce contrat echouent en mode ferme.
     */
    default BridgeResponse sendDiskReclaimAction(
            String action,
            String incidentType,
            String namespace,
            String target,
            String node,
            String scenario,
            Long incidentId) {
        return new BridgeResponse(false, false, -1,
                "Bridge client does not support disk reclaim requests.");
    }

    record BridgeResponse(
            boolean ok,
            boolean execute,
            int exitCode,
            String message
    ) {
    }
}