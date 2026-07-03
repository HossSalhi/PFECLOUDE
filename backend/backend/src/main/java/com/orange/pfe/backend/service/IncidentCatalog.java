package com.orange.pfe.backend.service;

import java.util.Map;

/**
 * Catalogue HELIOS (résolutions professionnelles de référence) côté backend, indexé sur le TYPE FINAL
 * de l'incident (après classification IA + éventuel garde-fou force*). On le calcule ici — et non sur le
 * type brut renvoyé par l'IA — pour que la résolution AFFICHÉE corresponde toujours au type affiché.
 *
 * <p>Miroir Java du sous-ensemble de {@code ai-service/app/models/incident_catalog.py} (120 types) que le
 * pipeline HELIOS produit réellement, alias inclus (MEMORY_STRESS→OOM, CPU_STRESS→POD_CPU_STRESS, etc.).
 * Pour les types absents de cette table, on retombe sur la {@code catalog_resolution} fournie par l'IA.
 */
final class IncidentCatalog {

    private IncidentCatalog() {}

    private static final Map<String, String> RESOLUTIONS = Map.ofEntries(
            Map.entry("POD_CRASH_LOOP",
                    "Analyser les logs, corriger la configuration, redéployer le pod (rollback vers la dernière révision valide)."),
            Map.entry("POD_IMAGE_PULL_BACKOFF",
                    "Corriger le nom de l'image ou les credentials (rollback vers l'image pullable)."),
            Map.entry("POD_CONFIG_ERROR",
                    "Corriger ConfigMap/variables d'environnement (rollback vers la révision valide)."),
            Map.entry("POD_INIT_CONTAINER_FAILURE",
                    "Corriger l'init container et redéployer (rollback)."),
            Map.entry("POD_PENDING_INSUFFICIENT_RESOURCES",
                    "Ajouter des ressources/nœuds, ou revenir à une demande de ressources viable (rollback)."),
            Map.entry("POD_STUCK_TERMINATING",
                    "Forcer la suppression du pod (retrait des finalizers)."),
            Map.entry("MEMORY_STRESS",
                    "Augmenter les limites mémoire ou optimiser l'application ; rollback de la révision fautive."),
            Map.entry("MEMORY_LEAK",
                    "Redémarrer le pod, corriger la fuite mémoire côté application."),
            Map.entry("CPU_STRESS",
                    "HPA, scaling horizontal, optimisation du code ; rolling restart pour purger le process fou."),
            Map.entry("NODE_DISK_PRESSURE",
                    "Nettoyer le disque ; supprimer le pod évincé."),
            Map.entry("DEPLOYMENT_UNAVAILABLE",
                    "Redémarrer/recréer les replicas (rolling restart) ou rollback."),
            Map.entry("REPLICA_SATURATION",
                    "Limiter le nombre de replicas (SCALE_DOWN vers le nombre planifiable)."),
            Map.entry("HIGH_ERROR_RATE",
                    "Corriger les erreurs serveur ; rollback de la version fautive."),
            Map.entry("LATENCY_DEGRADATION",
                    "Identifier le composant lent ; rollback de la révision fautive."),
            Map.entry("AUTOSCALING",
                    "Autoscaling (scale up) pour absorber la montée en charge."),
            Map.entry("SERVICE_UNAVAILABLE",
                    "Vérifier les pods backend ; supprimer la NetworkPolicy bloquante."),
            Map.entry("NETWORK_PARTITION",
                    "Restaurer la connectivité (supprimer la NetworkPolicy ingress+egress)."),
            Map.entry("DNS_FAILURE",
                    "Redémarrer CoreDNS ou supprimer la policy qui bloque le port 53."),
            Map.entry("DATABASE_CONNECTIVITY",
                    "Vérifier service et réseau ; supprimer la policy bloquant le port DB."),
            Map.entry("NODE_NOT_READY",
                    "Redémarrer kubelet / rallumer la VM (watchdog via Camunda)."),
            Map.entry("VM_DOWN",
                    "Redémarrer kubelet / rallumer la VM (watchdog via Camunda).")
    );

    /** Résolution catalogue pour le type FINAL, ou {@code null} si absent (l'appelant retombe sur l'IA). */
    static String resolutionFor(String incidentType) {
        return incidentType == null ? null : RESOLUTIONS.get(incidentType);
    }
}
