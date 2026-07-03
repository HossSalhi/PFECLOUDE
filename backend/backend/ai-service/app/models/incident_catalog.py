"""
HELIOS — Catalogue des 120 incidents (taxonomie SRE) + résolution professionnelle.

Deux niveaux par incident :
  - "resolution" : la résolution PRO (recommandée par l'IA, expliquée à l'opérateur).
  - "auto"       : True  = HELIOS peut EXÉCUTER la remédiation automatiquement (reproductible + action câblée),
                   False = l'IA DIAGNOSTIQUE + RECOMMANDE, un opérateur applique (panne non déclenchable/
                           non réparable in-cluster : control-plane, sécurité, matériel, prédictif…).

Utilisé par le moteur d'inférence pour :
  1) élargir le vocabulaire de classification (les 120 types),
  2) fournir la résolution + l'explication professionnelle en sortie,
  3) router vers l'exécution automatique quand auto=True (sinon recommandation).

Chaque entrée : type -> {category, resolution, auto}
"""

INCIDENT_CATALOG: dict[str, dict] = {
    # ── A. Pods & Applications ────────────────────────────────────────────────
    "POD_CRASH_LOOP":                     {"category": "Pods", "resolution": "Analyser les logs, corriger la configuration, redéployer le pod (rollback vers la dernière révision valide).", "auto": True},
    "POD_OOM_KILLED":                     {"category": "Pods", "resolution": "Augmenter les limites mémoire ou optimiser l'application ; rollback de la révision fautive.", "auto": True},
    "POD_CPU_STRESS":                     {"category": "Pods", "resolution": "HPA, scaling horizontal, optimisation du code ; rolling restart pour purger le process fou.", "auto": True},
    "POD_MEMORY_LEAK":                    {"category": "Pods", "resolution": "Redémarrer le pod, corriger la fuite mémoire côté application.", "auto": True},
    "POD_PENDING_INSUFFICIENT_RESOURCES": {"category": "Pods", "resolution": "Ajouter des ressources/nœuds, ou revenir à une demande de ressources viable (rollback).", "auto": True},
    "POD_INIT_CONTAINER_FAILURE":         {"category": "Pods", "resolution": "Corriger l'init container et redéployer (rollback).", "auto": True},
    "POD_CONFIG_ERROR":                   {"category": "Pods", "resolution": "Corriger ConfigMap/variables d'environnement (rollback vers la révision valide).", "auto": True},
    "POD_IMAGE_PULL_BACKOFF":             {"category": "Pods", "resolution": "Corriger le nom de l'image ou les credentials (rollback vers l'image pullable).", "auto": True},
    "POD_ERR_IMAGE_PULL":                 {"category": "Pods", "resolution": "Vérifier le registry et l'image ; corriger la référence puis redéployer.", "auto": True},
    "POD_STUCK_TERMINATING":              {"category": "Pods", "resolution": "Forcer la suppression du pod (retrait des finalizers).", "auto": True},
    "POD_EVICTED":                        {"category": "Pods", "resolution": "Libérer les ressources du nœud ; recréer le pod (delete pod évincé).", "auto": True},
    "POD_FAILED_PROBE":                   {"category": "Pods", "resolution": "Corriger les probes (readiness/liveness) puis redéployer.", "auto": True},
    "POD_READINESS_FAILURE":              {"category": "Pods", "resolution": "Vérifier les dépendances applicatives ; corriger la readiness (rollback).", "auto": True},
    "POD_LIVENESS_FAILURE":               {"category": "Pods", "resolution": "Corriger l'application ou les probes liveness (rollback).", "auto": True},
    "POD_CONTAINER_RUNTIME_ERROR":        {"category": "Pods", "resolution": "Redémarrer containerd/cri-o sur le nœud concerné.", "auto": False},
    "POD_RESTART_STORM":                  {"category": "Pods", "resolution": "Identifier la cause des redémarrages (logs/events) ; rollback si régression.", "auto": True},
    "POD_SECURITY_CONTEXT_ERROR":         {"category": "Pods", "resolution": "Corriger le securityContext du pod (droits) puis redéployer.", "auto": False},
    "POD_SECRET_MISSING":                 {"category": "Pods", "resolution": "Recréer le Secret manquant.", "auto": False},
    "POD_CONFIGMAP_MISSING":              {"category": "Pods", "resolution": "Recréer la ConfigMap manquante.", "auto": False},
    "POD_TOO_MANY_RESTARTS":              {"category": "Pods", "resolution": "Investiguer logs et événements ; rollback vers une révision stable.", "auto": True},

    # ── B. Deployments ────────────────────────────────────────────────────────
    "DEPLOYMENT_UNAVAILABLE":             {"category": "Deployments", "resolution": "Redémarrer/recréer les replicas (rolling restart) ou rollback.", "auto": True},
    "DEPLOYMENT_ROLLOUT_STUCK":           {"category": "Deployments", "resolution": "Corriger l'image ou la configuration ; forcer le rollout.", "auto": True},
    "DEPLOYMENT_ROLLBACK_REQUIRED":       {"category": "Deployments", "resolution": "Rollback vers la version stable.", "auto": True},
    "REPLICA_SATURATION":                 {"category": "Deployments", "resolution": "Limiter le nombre de replicas (SCALE_DOWN vers le nombre planifiable).", "auto": True},
    "HPA_SCALING_FAILURE":                {"category": "Deployments", "resolution": "Vérifier Metrics Server ; rétablir la source de métriques.", "auto": False},
    "HPA_OVER_SCALING":                   {"category": "Deployments", "resolution": "Ajuster les seuils HPA (scale down).", "auto": True},
    "HPA_UNDER_SCALING":                  {"category": "Deployments", "resolution": "Réduire les seuils de déclenchement (scale up).", "auto": True},
    "DEPLOYMENT_VERSION_MISMATCH":        {"category": "Deployments", "resolution": "Uniformiser les versions des replicas.", "auto": False},
    "DEPLOYMENT_PROGRESS_DEADLINE_EXCEEDED": {"category": "Deployments", "resolution": "Corriger le déploiement puis relancer le rollout (rollback si besoin).", "auto": True},

    # ── C. Services & Réseau ─────────────────────────────────────────────────
    "SERVICE_UNAVAILABLE":                {"category": "Réseau", "resolution": "Vérifier les pods backend ; supprimer la NetworkPolicy bloquante.", "auto": True},
    "SERVICE_NO_ENDPOINTS":               {"category": "Réseau", "resolution": "Vérifier les labels/selectors du Service.", "auto": False},
    "NETWORK_PARTITION":                  {"category": "Réseau", "resolution": "Restaurer la connectivité (supprimer la NetworkPolicy ingress+egress).", "auto": True},
    "NETWORK_PACKET_LOSS":                {"category": "Réseau", "resolution": "Corriger le réseau ou le MTU du nœud.", "auto": False},
    "NETWORK_HIGH_LATENCY":               {"category": "Réseau", "resolution": "Analyser le trafic et optimiser le réseau.", "auto": False},
    "NETWORK_POLICY_BLOCKING":            {"category": "Réseau", "resolution": "Corriger/supprimer les NetworkPolicies bloquantes.", "auto": True},
    "DNS_FAILURE":                        {"category": "Réseau", "resolution": "Redémarrer CoreDNS ou supprimer la policy qui bloque le port 53.", "auto": True},
    "DNS_SLOW_RESPONSE":                  {"category": "Réseau", "resolution": "Ajouter des réplicas CoreDNS.", "auto": True},
    "INGRESS_UNAVAILABLE":                {"category": "Réseau", "resolution": "Redémarrer l'Ingress Controller.", "auto": False},
    "INGRESS_5XX_ERRORS":                 {"category": "Réseau", "resolution": "Vérifier backend et Ingress ; rollback du backend fautif.", "auto": True},
    "LOAD_BALANCER_UNAVAILABLE":          {"category": "Réseau", "resolution": "Recréer le LoadBalancer.", "auto": False},
    "LOAD_BALANCER_OVERLOAD":             {"category": "Réseau", "resolution": "Ajouter des instances backend (scale up).", "auto": True},
    "TCP_CONNECTION_FAILURE":             {"category": "Réseau", "resolution": "Vérifier firewall et routage.", "auto": False},

    # ── D. Nœuds ──────────────────────────────────────────────────────────────
    "NODE_NOT_READY":                     {"category": "Nœuds", "resolution": "Redémarrer kubelet / rallumer la VM (watchdog via Camunda).", "auto": True},
    "NODE_HIGH_CPU":                      {"category": "Nœuds", "resolution": "Rééquilibrer la charge (redémarrer/évincer le pod le plus gourmand).", "auto": True},
    "NODE_HIGH_MEMORY":                   {"category": "Nœuds", "resolution": "Libérer la mémoire ou ajouter de la RAM ; évincer le pod gourmand.", "auto": True},
    "NODE_DISK_PRESSURE":                 {"category": "Nœuds", "resolution": "Nettoyer le disque ; supprimer le pod évincé.", "auto": True},
    "NODE_MEMORY_PRESSURE":               {"category": "Nœuds", "resolution": "Réduire les workloads du nœud.", "auto": True},
    "NODE_PID_PRESSURE":                  {"category": "Nœuds", "resolution": "Augmenter les limites PID du nœud.", "auto": False},
    "NODE_DISK_FULL":                     {"category": "Nœuds", "resolution": "Supprimer les images inutiles (image gc).", "auto": False},
    "NODE_NETWORK_DOWN":                  {"category": "Nœuds", "resolution": "Réparer l'interface réseau du nœud.", "auto": False},
    "NODE_REBOOT":                        {"category": "Nœuds", "resolution": "Vérifier les logs système ; rejoindre le nœud.", "auto": False},
    "NODE_KERNEL_PANIC":                  {"category": "Nœuds", "resolution": "Diagnostiquer le noyau ; rallumer/remplacer le nœud.", "auto": False},
    "NODE_TIME_DRIFT":                    {"category": "Nœuds", "resolution": "Synchroniser NTP/Chrony.", "auto": False},
    "NODE_KUBELET_DOWN":                  {"category": "Nœuds", "resolution": "Redémarrer kubelet.", "auto": True},
    "NODE_CONTAINERD_DOWN":               {"category": "Nœuds", "resolution": "Redémarrer containerd.", "auto": False},
    "NODE_FILESYSTEM_READONLY":           {"category": "Nœuds", "resolution": "Réparer le système de fichiers.", "auto": False},
    "NODE_UNREACHABLE":                   {"category": "Nœuds", "resolution": "Restaurer le réseau / rallumer la VM (watchdog).", "auto": True},

    # ── E. Control Plane ──────────────────────────────────────────────────────
    "API_SERVER_DOWN":                    {"category": "ControlPlane", "resolution": "Redémarrer kube-apiserver.", "auto": False},
    "API_SERVER_HIGH_LATENCY":            {"category": "ControlPlane", "resolution": "Augmenter les ressources ou optimiser ETCD.", "auto": False},
    "ETCD_DOWN":                          {"category": "ControlPlane", "resolution": "Redémarrer ETCD.", "auto": False},
    "ETCD_QUORUM_LOST":                   {"category": "ControlPlane", "resolution": "Restaurer au moins 2 masters (quorum).", "auto": False},
    "ETCD_HIGH_LATENCY":                  {"category": "ControlPlane", "resolution": "Optimiser disque et réseau ETCD.", "auto": False},
    "ETCD_DATABASE_CORRUPTION":           {"category": "ControlPlane", "resolution": "Restaurer un snapshot ETCD.", "auto": False},
    "CONTROLLER_MANAGER_DOWN":            {"category": "ControlPlane", "resolution": "Redémarrer kube-controller-manager.", "auto": False},
    "SCHEDULER_DOWN":                     {"category": "ControlPlane", "resolution": "Redémarrer kube-scheduler.", "auto": False},
    "CERTIFICATE_EXPIRED":                {"category": "ControlPlane", "resolution": "Renouveler les certificats (kubeadm certs renew).", "auto": False},
    "CERTIFICATE_NEAR_EXPIRATION":        {"category": "ControlPlane", "resolution": "Renouvellement préventif des certificats.", "auto": False},
    "LEADER_ELECTION_FAILURE":            {"category": "ControlPlane", "resolution": "Vérifier ETCD ; relancer l'élection de leader.", "auto": False},
    "ADMISSION_WEBHOOK_FAILURE":          {"category": "ControlPlane", "resolution": "Réparer/retirer le webhook d'admission fautif.", "auto": False},

    # ── F. Stockage ───────────────────────────────────────────────────────────
    "PVC_PENDING":                        {"category": "Stockage", "resolution": "Vérifier la StorageClass ; re-bind sur la SC par défaut.", "auto": True},
    "PVC_LOST":                           {"category": "Stockage", "resolution": "Recréer le PVC.", "auto": False},
    "PV_LOST":                            {"category": "Stockage", "resolution": "Restaurer le volume.", "auto": False},
    "STORAGE_CLASS_ERROR":                {"category": "Stockage", "resolution": "Corriger la StorageClass.", "auto": False},
    "STORAGE_CAPACITY_EXCEEDED":          {"category": "Stockage", "resolution": "Étendre le stockage.", "auto": False},
    "VOLUME_MOUNT_FAILURE":               {"category": "Stockage", "resolution": "Vérifier permissions et driver CSI.", "auto": False},
    "READ_ONLY_VOLUME":                   {"category": "Stockage", "resolution": "Réparer le stockage (remonter en RW).", "auto": False},
    "CSI_DRIVER_FAILURE":                 {"category": "Stockage", "resolution": "Redémarrer le CSI Driver.", "auto": False},
    "SNAPSHOT_FAILURE":                   {"category": "Stockage", "resolution": "Recréer le snapshot.", "auto": False},

    # ── G. Base de données ────────────────────────────────────────────────────
    "DATABASE_CONNECTIVITY":              {"category": "Database", "resolution": "Vérifier service et réseau ; supprimer la policy bloquant le port DB.", "auto": True},
    "DB_CONNECTION_REFUSED":              {"category": "Database", "resolution": "Redémarrer la base ou basculer sur un replica.", "auto": True},
    "DB_CONNECTION_POOL_EXHAUSTION":      {"category": "Database", "resolution": "Augmenter le pool ou redémarrer l'app (vide le pool).", "auto": True},
    "DB_SLOW_QUERIES":                    {"category": "Database", "resolution": "Optimiser les requêtes / ajouter des index.", "auto": False},
    "DB_HIGH_CPU":                        {"category": "Database", "resolution": "Ajouter des ressources à la base.", "auto": False},
    "DB_HIGH_MEMORY":                     {"category": "Database", "resolution": "Optimiser cache et mémoire.", "auto": False},
    "DB_DISK_FULL":                       {"category": "Database", "resolution": "Libérer ou étendre le disque de la base.", "auto": False},
    "DB_REPLICATION_LAG":                 {"category": "Database", "resolution": "Corriger la réplication (réseau/charge d'écriture).", "auto": False},
    "DB_PRIMARY_DOWN":                    {"category": "Database", "resolution": "Promouvoir un replica (failover CNPG).", "auto": True},
    "DB_DEADLOCK":                        {"category": "Database", "resolution": "Optimiser les transactions.", "auto": False},
    "DB_TRANSACTION_TIMEOUT":             {"category": "Database", "resolution": "Ajuster le timeout des transactions.", "auto": False},
    "DB_TOO_MANY_CONNECTIONS":            {"category": "Database", "resolution": "Limiter ou augmenter le nombre de connexions.", "auto": False},

    # ── H. Performance Applicative ────────────────────────────────────────────
    "LATENCY_DEGRADATION":                {"category": "Performance", "resolution": "Identifier le composant lent ; rollback de la révision fautive.", "auto": True},
    "HIGH_ERROR_RATE_5XX":                {"category": "Performance", "resolution": "Corriger les erreurs serveur ; rollback de la version fautive.", "auto": True},
    "HIGH_ERROR_RATE_4XX":                {"category": "Performance", "resolution": "Vérifier clients/API ; corriger le contrat.", "auto": False},
    "HIGH_RESPONSE_TIME":                 {"category": "Performance", "resolution": "Optimiser le traitement ; scale up.", "auto": True},
    "REQUEST_TIMEOUT":                    {"category": "Performance", "resolution": "Augmenter le timeout ou optimiser le traitement.", "auto": False},
    "THROUGHPUT_DROP":                    {"category": "Performance", "resolution": "Ajouter des replicas (scale up).", "auto": True},
    "TRAFFIC_SPIKE":                      {"category": "Performance", "resolution": "Autoscaling (scale up).", "auto": True},
    "MEMORY_LEAK_APPLICATION":            {"category": "Performance", "resolution": "Corriger la fuite mémoire ; redémarrer le pod.", "auto": True},
    "THREAD_POOL_EXHAUSTION":             {"category": "Performance", "resolution": "Augmenter ou optimiser le pool de threads ; restart.", "auto": True},
    "CACHE_FAILURE":                      {"category": "Performance", "resolution": "Redémarrer ou reconstruire le cache.", "auto": False},

    # ── I. Sécurité ───────────────────────────────────────────────────────────
    "SECRET_EXPOSED":                     {"category": "Sécurité", "resolution": "Rotation immédiate du secret.", "auto": False},
    "UNAUTHORIZED_ACCESS":                {"category": "Sécurité", "resolution": "Révoquer l'accès.", "auto": False},
    "RBAC_MISCONFIGURATION":              {"category": "Sécurité", "resolution": "Corriger le RBAC.", "auto": False},
    "PRIVILEGED_CONTAINER":               {"category": "Sécurité", "resolution": "Réduire les privilèges du conteneur.", "auto": False},
    "MALICIOUS_POD":                      {"category": "Sécurité", "resolution": "Supprimer le pod malveillant.", "auto": False},
    "CRYPTO_MINING_DETECTED":             {"category": "Sécurité", "resolution": "Isoler le nœud (cordon) et investiguer.", "auto": False},
    "DDOS_ATTACK":                        {"category": "Sécurité", "resolution": "Rate limiting et WAF.", "auto": False},
    "IMAGE_VULNERABILITY":                {"category": "Sécurité", "resolution": "Mettre à jour l'image.", "auto": False},
    "TLS_CERTIFICATE_EXPIRED":            {"category": "Sécurité", "resolution": "Renouveler le certificat TLS.", "auto": False},
    "SUSPICIOUS_NETWORK_ACTIVITY":        {"category": "Sécurité", "resolution": "Isoler et analyser.", "auto": False},

    # ── J. AIOps avancé ───────────────────────────────────────────────────────
    "CASCADING_FAILURE":                  {"category": "AIOps", "resolution": "Isoler la cause racine et rétablir en priorité.", "auto": False},
    "RESOURCE_CONTENTION":                {"category": "AIOps", "resolution": "Rééquilibrer les ressources (scale/évincer).", "auto": True},
    "DEPENDENCY_FAILURE":                 {"category": "AIOps", "resolution": "Restaurer le service dépendant.", "auto": False},
    "PARTIAL_CLUSTER_OUTAGE":             {"category": "AIOps", "resolution": "Restaurer les nœuds touchés (watchdog/failover).", "auto": True},
    "COMPLETE_CLUSTER_OUTAGE":            {"category": "AIOps", "resolution": "Restaurer le cluster (procédure de reprise).", "auto": False},
    "MULTI_NODE_FAILURE":                 {"category": "AIOps", "resolution": "Réactiver ou remplacer les nœuds.", "auto": False},
    "MULTI_SERVICE_DEGRADATION":          {"category": "AIOps", "resolution": "Prioriser les services critiques.", "auto": False},
    "ANOMALOUS_TRAFFIC_PATTERN":          {"category": "AIOps", "resolution": "Bloquer ou limiter le trafic anormal.", "auto": False},
    "PREDICTED_NODE_FAILURE":             {"category": "AIOps", "resolution": "Évacuer préventivement les workloads (drain).", "auto": False},
    "PREDICTED_DISK_EXHAUSTION":          {"category": "AIOps", "resolution": "Étendre le stockage avant saturation.", "auto": False},
}


# Action SPÉCIFIQUE recommandée par type (JAMAIS un « investigate » générique). Les libellés sont
# choisis pour que _map_executable_action() les route vers l'exécutable HELIOS correct (ROLLBACK/
# RESTART/SCALE/SCALE_DOWN/DELETE) pour les types auto-exécutables ; les types sans commande in-cluster
# reçoivent une ESCALADE SPÉCIFIQUE (préfixe ESCALATE_, nomme la résolution) — pas un investigate.
INCIDENT_ACTIONS: dict[str, str] = {
    # A. Pods
    "POD_CRASH_LOOP": "ROLLBACK_TO_LAST_HEALTHY_REVISION",
    "POD_OOM_KILLED": "ROLLBACK_RECENT_CHANGE_AND_TUNE_MEMORY_LIMITS",
    "POD_CPU_STRESS": "SCALE_UP_AND_REBALANCE_LOAD",
    "POD_MEMORY_LEAK": "RESTART_POD_TO_RECLAIM_MEMORY",
    "POD_PENDING_INSUFFICIENT_RESOURCES": "ROLLBACK_TO_VIABLE_RESOURCE_REQUEST",
    "POD_INIT_CONTAINER_FAILURE": "ROLLBACK_RECENT_CHANGE_AND_RESTART",
    "POD_CONFIG_ERROR": "ROLLBACK_RECENT_CONFIG_CHANGE_AND_RESTART",
    "POD_IMAGE_PULL_BACKOFF": "ROLLBACK_TO_PULLABLE_IMAGE",
    "POD_ERR_IMAGE_PULL": "ROLLBACK_TO_PULLABLE_IMAGE",
    "POD_STUCK_TERMINATING": "DELETE_CRASHLOOPING_OR_STUCK_POD",
    "POD_EVICTED": "DELETE_CRASHLOOPING_OR_EVICTED_POD",
    "POD_FAILED_PROBE": "ROLLBACK_RECENT_PROBE_CHANGE_AND_RESTART",
    "POD_READINESS_FAILURE": "ROLLBACK_RECENT_CHANGE_AND_RESTART",
    "POD_LIVENESS_FAILURE": "ROLLBACK_RECENT_CHANGE_AND_RESTART",
    "POD_CONTAINER_RUNTIME_ERROR": "ESCALATE_RESTART_CONTAINERD_ON_NODE",
    "POD_RESTART_STORM": "ROLLBACK_TO_LAST_HEALTHY_REVISION",
    "POD_SECURITY_CONTEXT_ERROR": "ROLLBACK_RECENT_SECURITY_CONTEXT_CHANGE",
    "POD_SECRET_MISSING": "ESCALATE_RECREATE_MISSING_SECRET",
    "POD_CONFIGMAP_MISSING": "ESCALATE_RECREATE_MISSING_CONFIGMAP",
    "POD_TOO_MANY_RESTARTS": "ROLLBACK_TO_LAST_HEALTHY_REVISION",
    # B. Deployments
    "DEPLOYMENT_UNAVAILABLE": "ROLLBACK_RECENT_CHANGE_AND_RESTART",
    "DEPLOYMENT_ROLLOUT_STUCK": "ROLLBACK_STUCK_ROLLOUT",
    "DEPLOYMENT_ROLLBACK_REQUIRED": "ROLLBACK_TO_STABLE_VERSION",
    "REPLICA_SATURATION": "SCALE_DOWN_TO_FITTING_REPLICAS",
    "HPA_SCALING_FAILURE": "ESCALATE_RESTORE_METRICS_SERVER",
    "HPA_OVER_SCALING": "SCALE_DOWN_TO_FITTING_REPLICAS",
    "HPA_UNDER_SCALING": "SCALE_UP_TO_MEET_DEMAND",
    "DEPLOYMENT_VERSION_MISMATCH": "ROLLBACK_TO_UNIFORM_VERSION",
    "DEPLOYMENT_PROGRESS_DEADLINE_EXCEEDED": "ROLLBACK_STUCK_ROLLOUT",
    # C. Réseau
    "SERVICE_UNAVAILABLE": "REMOVE_BLOCKING_NETWORK_POLICY",
    "SERVICE_NO_ENDPOINTS": "ESCALATE_FIX_SERVICE_SELECTOR",
    "NETWORK_PARTITION": "REMOVE_BLOCKING_NETWORK_POLICY",
    "NETWORK_PACKET_LOSS": "ESCALATE_FIX_NODE_NETWORK",
    "NETWORK_HIGH_LATENCY": "SCALE_UP_AND_REBALANCE_LOAD",
    "NETWORK_POLICY_BLOCKING": "REMOVE_BLOCKING_NETWORK_POLICY",
    "DNS_FAILURE": "REMOVE_BLOCKING_NETWORK_POLICY",
    "DNS_SLOW_RESPONSE": "SCALE_UP_COREDNS_REPLICAS",
    "INGRESS_UNAVAILABLE": "ESCALATE_RESTART_INGRESS_CONTROLLER",
    "INGRESS_5XX_ERRORS": "ROLLBACK_LAST_RELEASE_AND_SHED_TRAFFIC",
    "LOAD_BALANCER_UNAVAILABLE": "ESCALATE_RECREATE_LOAD_BALANCER",
    "LOAD_BALANCER_OVERLOAD": "SCALE_UP_TO_ABSORB_LOAD",
    "TCP_CONNECTION_FAILURE": "ESCALATE_CHECK_FIREWALL_AND_ROUTING",
    # D. Nœuds
    "NODE_NOT_READY": "ESCALATE_RESTART_KUBELET_OR_REBOOT_VM",
    "NODE_HIGH_CPU": "RESTART_POD_TO_REBALANCE_LOAD",
    "NODE_HIGH_MEMORY": "RESTART_POD_TO_RECLAIM_MEMORY",
    "NODE_DISK_PRESSURE": "DELETE_CRASHLOOPING_OR_EVICTED_POD",
    "NODE_MEMORY_PRESSURE": "DRAIN_NODE_TO_REDUCE_WORKLOADS",
    "NODE_PID_PRESSURE": "ESCALATE_INCREASE_NODE_PID_LIMIT",
    "NODE_DISK_FULL": "HOST_IMAGE_GC",
    "NODE_NETWORK_DOWN": "ESCALATE_REPAIR_NODE_NETWORK_INTERFACE",
    "NODE_REBOOT": "ESCALATE_CHECK_SYSTEM_LOGS_AND_REJOIN",
    "NODE_KERNEL_PANIC": "ESCALATE_REBOOT_OR_REPLACE_NODE",
    "NODE_TIME_DRIFT": "HOST_SYNC_TIME",
    "NODE_KUBELET_DOWN": "ESCALATE_RESTART_KUBELET_ON_NODE",
    "NODE_CONTAINERD_DOWN": "ESCALATE_RESTART_CONTAINERD_ON_NODE",
    "NODE_FILESYSTEM_READONLY": "ESCALATE_REPAIR_NODE_FILESYSTEM",
    "NODE_UNREACHABLE": "ESCALATE_RESTORE_NODE_NETWORK_OR_REBOOT_VM",
    # E. Control Plane
    "API_SERVER_DOWN": "ESCALATE_RESTART_APISERVER_STATIC_POD",
    "API_SERVER_HIGH_LATENCY": "ESCALATE_TUNE_APISERVER_AND_ETCD",
    "ETCD_DOWN": "ESCALATE_RESTART_ETCD_STATIC_POD",
    "ETCD_QUORUM_LOST": "ESCALATE_RESTORE_ETCD_QUORUM",
    "ETCD_HIGH_LATENCY": "ESCALATE_TUNE_ETCD_DISK_AND_NETWORK",
    "ETCD_DATABASE_CORRUPTION": "ESCALATE_RESTORE_ETCD_SNAPSHOT",
    "CONTROLLER_MANAGER_DOWN": "ESCALATE_RESTART_CONTROLLER_MANAGER",
    "SCHEDULER_DOWN": "ESCALATE_RESTART_SCHEDULER",
    "CERTIFICATE_EXPIRED": "ESCALATE_RENEW_CONTROL_PLANE_CERTS",
    "CERTIFICATE_NEAR_EXPIRATION": "ESCALATE_RENEW_CERTS_PREVENTIVELY",
    "LEADER_ELECTION_FAILURE": "ESCALATE_CHECK_ETCD_AND_RELECT_LEADER",
    "ADMISSION_WEBHOOK_FAILURE": "ESCALATE_REPAIR_ADMISSION_WEBHOOK",
    # F. Stockage
    "PVC_PENDING": "ESCALATE_FIX_STORAGECLASS_AND_REBIND",
    "PVC_LOST": "ESCALATE_RECREATE_PVC",
    "PV_LOST": "ESCALATE_RESTORE_VOLUME",
    "STORAGE_CLASS_ERROR": "ESCALATE_FIX_STORAGECLASS",
    "STORAGE_CAPACITY_EXCEEDED": "ESCALATE_EXTEND_STORAGE",
    "VOLUME_MOUNT_FAILURE": "ESCALATE_FIX_VOLUME_MOUNT_AND_CSI",
    "READ_ONLY_VOLUME": "ESCALATE_REMOUNT_VOLUME_RW",
    "CSI_DRIVER_FAILURE": "ESCALATE_RESTART_CSI_DRIVER",
    "SNAPSHOT_FAILURE": "ESCALATE_RECREATE_SNAPSHOT",
    # G. Base de données
    "DATABASE_CONNECTIVITY": "REMOVE_BLOCKING_NETWORK_POLICY",
    "DB_CONNECTION_REFUSED": "RESTART_POD_OR_FAILOVER_DATABASE",
    "DB_CONNECTION_POOL_EXHAUSTION": "RESTART_POD_TO_DRAIN_CONNECTION_POOL",
    "DB_SLOW_QUERIES": "ESCALATE_OPTIMIZE_QUERIES_AND_INDEXES",
    "DB_HIGH_CPU": "ESCALATE_ADD_DATABASE_RESOURCES",
    "DB_HIGH_MEMORY": "ESCALATE_TUNE_DATABASE_MEMORY_AND_CACHE",
    "DB_DISK_FULL": "ESCALATE_EXTEND_DATABASE_DISK",
    "DB_REPLICATION_LAG": "ESCALATE_FIX_DATABASE_REPLICATION",
    "DB_PRIMARY_DOWN": "ESCALATE_PROMOTE_DATABASE_REPLICA_FAILOVER",
    "DB_DEADLOCK": "ESCALATE_OPTIMIZE_DATABASE_TRANSACTIONS",
    "DB_TRANSACTION_TIMEOUT": "ESCALATE_ADJUST_TRANSACTION_TIMEOUT",
    "DB_TOO_MANY_CONNECTIONS": "ESCALATE_LIMIT_DATABASE_CONNECTIONS",
    # H. Performance
    "LATENCY_DEGRADATION": "ROLLBACK_LAST_RELEASE_AND_RESTART",
    "HIGH_ERROR_RATE_5XX": "ROLLBACK_LAST_RELEASE_AND_SHED_TRAFFIC",
    "HIGH_ERROR_RATE_4XX": "ESCALATE_FIX_CLIENT_API_CONTRACT",
    "HIGH_RESPONSE_TIME": "SCALE_UP_TO_ABSORB_LOAD",
    "REQUEST_TIMEOUT": "ESCALATE_ADJUST_TIMEOUT_OR_OPTIMIZE",
    "THROUGHPUT_DROP": "SCALE_UP_TO_RESTORE_THROUGHPUT",
    "TRAFFIC_SPIKE": "SCALE_UP_TO_ABSORB_LOAD",
    "MEMORY_LEAK_APPLICATION": "RESTART_POD_TO_RECLAIM_MEMORY",
    "THREAD_POOL_EXHAUSTION": "RESTART_POD_TO_RESET_THREAD_POOL",
    "CACHE_FAILURE": "RESTART_POD_TO_REBUILD_CACHE",
    # I. Sécurité
    "SECRET_EXPOSED": "ESCALATE_ROTATE_EXPOSED_SECRET",
    "UNAUTHORIZED_ACCESS": "ESCALATE_REVOKE_UNAUTHORIZED_ACCESS",
    "RBAC_MISCONFIGURATION": "ESCALATE_FIX_RBAC",
    "PRIVILEGED_CONTAINER": "ESCALATE_REDUCE_CONTAINER_PRIVILEGES",
    "MALICIOUS_POD": "DELETE_CRASHLOOPING_OR_MALICIOUS_POD",
    "CRYPTO_MINING_DETECTED": "CORDON_NODE_TO_ISOLATE",
    "DDOS_ATTACK": "ESCALATE_ENABLE_RATE_LIMITING_AND_WAF",
    "IMAGE_VULNERABILITY": "ESCALATE_UPDATE_VULNERABLE_IMAGE",
    "TLS_CERTIFICATE_EXPIRED": "ESCALATE_RENEW_TLS_CERTIFICATE",
    "SUSPICIOUS_NETWORK_ACTIVITY": "CORDON_NODE_TO_ISOLATE",
    # J. AIOps avancé
    "CASCADING_FAILURE": "ESCALATE_ISOLATE_ROOT_CAUSE_AND_RESTORE",
    "RESOURCE_CONTENTION": "SCALE_UP_AND_REBALANCE_RESOURCES",
    "DEPENDENCY_FAILURE": "ESCALATE_RESTORE_DEPENDENT_SERVICE",
    "PARTIAL_CLUSTER_OUTAGE": "ESCALATE_RESTORE_AFFECTED_NODES",
    "COMPLETE_CLUSTER_OUTAGE": "ESCALATE_CLUSTER_RECOVERY_PROCEDURE",
    "MULTI_NODE_FAILURE": "ESCALATE_REACTIVATE_OR_REPLACE_NODES",
    "MULTI_SERVICE_DEGRADATION": "ESCALATE_PRIORITIZE_CRITICAL_SERVICES",
    "ANOMALOUS_TRAFFIC_PATTERN": "ESCALATE_BLOCK_OR_LIMIT_ANOMALOUS_TRAFFIC",
    "PREDICTED_NODE_FAILURE": "DRAIN_NODE_TO_EVACUATE_WORKLOADS",
    "PREDICTED_DISK_EXHAUSTION": "ESCALATE_EXTEND_STORAGE_PREEMPTIVELY",
}


def catalog_types() -> list[str]:
    """Les 120 types (vocabulaire de classification)."""
    return list(INCIDENT_CATALOG.keys())


# Alias des types INTERNES du moteur d'inférence (ML/règles, ~52 classes historiques) vers les clés
# du catalogue des 120 — pour rattacher la résolution pro même quand le nom interne diffère du
# catalogue (ex. l'IA sort MEMORY_STRESS pour un OOM = POD_OOM_KILLED du catalogue).
_TYPE_ALIASES: dict[str, str] = {
    "MEMORY_STRESS": "POD_OOM_KILLED",
    "MEMORY_LEAK": "POD_MEMORY_LEAK",
    "CPU_STRESS": "POD_CPU_STRESS",
    "RESTART_LOOP": "POD_CRASH_LOOP",
    "HIGH_ERROR_RATE": "HIGH_ERROR_RATE_5XX",
    "APPLICATION_ERROR_STORM": "HIGH_ERROR_RATE_5XX",
    "AVAILABILITY_DROP": "DEPLOYMENT_UNAVAILABLE",
    "AUTOSCALING": "TRAFFIC_SPIKE",
    "VM_DOWN": "NODE_NOT_READY",
    "MONITORING_DOWN": "NODE_UNREACHABLE",
    "AI_SERVICE_DOWN": "NODE_UNREACHABLE",
    "DATABASE_POOL_SATURATION": "DB_CONNECTION_POOL_EXHAUSTION",
    "GENERAL_DEGRADATION": "LATENCY_DEGRADATION",
}


def canonical_type(incident_type: str) -> str | None:
    """Résout un type interne vers sa clé du catalogue des 120 (exact puis alias)."""
    if not incident_type:
        return None
    if incident_type in INCIDENT_CATALOG:
        return incident_type
    return _TYPE_ALIASES.get(incident_type)


def resolution_for(incident_type: str) -> str | None:
    key = canonical_type(incident_type)
    entry = INCIDENT_CATALOG.get(key) if key else None
    return entry["resolution"] if entry else None


def category_for(incident_type: str) -> str | None:
    key = canonical_type(incident_type)
    entry = INCIDENT_CATALOG.get(key) if key else None
    return entry["category"] if entry else None


def is_auto_executable(incident_type: str) -> bool:
    key = canonical_type(incident_type)
    entry = INCIDENT_CATALOG.get(key) if key else None
    return bool(entry and entry["auto"])


def action_for(incident_type: str) -> str | None:
    """Action SPÉCIFIQUE recommandée pour ce type (jamais un investigate). Alias-résolue."""
    key = canonical_type(incident_type)
    return INCIDENT_ACTIONS.get(key) if key else None
