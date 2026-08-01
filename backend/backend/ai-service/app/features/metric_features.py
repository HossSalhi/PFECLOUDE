# Correspondance nom-de-machine -> IP, pour cibler les metriques HOTE (node-exporter, etcd)
# de la bonne VM. Couvre les deux conventions de nommage deja presentes dans le projet :
# "k8s-master" (nom reel de l'objet Node K8s) et "master" (raccourci utilise par le bridge SSH).
# Miroir de IP2NOM dans camunda-direct/detecteur/detecter.py.
NODE_IP = {
    "k8s-master": "192.168.148.153", "master": "192.168.148.153",
    "k8s-worker1": "192.168.148.158", "worker1": "192.168.148.158",
    "k8s-worker2": "192.168.148.155", "worker2": "192.168.148.155",
    "monitoring": "192.168.148.160",
    "iacamunda": "192.168.148.161",
    "lb2": "192.168.148.162",
}


def default_metric_features() -> dict[str, float]:
    return {
        "cpu_usage_cores": 0.0,
        "memory_working_set_bytes": 0.0,
        "memory_trend_bytes_per_sec": 0.0,
        "pod_restarts_total": 0.0,
        "unavailable_replicas": 0.0,
        "pod_count": 0.0,
        "node_not_ready_count": 0.0,
        "monitoring_vm_up": 1.0,
        "ai_vm_up": 1.0,
        "http_latency_p95_seconds": 0.0,
        "http_5xx_ratio": 0.0,
        "db_pool_pending": 0.0,
        "db_pool_usage_ratio": 0.0,
        # --- HOTE BRUT (ajoute 01/08) : donnees node-exporter/etcd, absentes tant que
        # `app` ne correspond a aucune entree de NODE_IP (incidents applicatifs classiques,
        # ces cles restent a leur valeur neutre ci-dessous). ---
        "host_cpu_pct": 0.0,
        "host_mem_pct": 0.0,
        "host_disk_pct": 0.0,
        "host_inodes_pct": 0.0,
        "host_fd_pct": 0.0,
        "host_disk_io_pct": 0.0,
        "host_cpu_steal_pct": 0.0,
        "host_oom_kills_5m": 0.0,
        "host_network_errors_ps": 0.0,
        "host_time_drift_seconds": 0.0,
        "etcd_fsync_p99_seconds": 0.0,
        "etcd_has_leader": 1.0,          # neutre = sain (meme convention que monitoring_vm_up)
        "pvc_not_bound_count": 0.0,
    }


def _host_metric_queries(node_ip: str) -> dict[str, str]:
    """Requetes HOTE BRUT pour une VM donnee : memes formules, memes seuils implicites
    que camunda-direct/detecteur/detecter.py (calibrees le 01/08 sur ce cluster charge),
    pour que les features de l'IA et les regles du detecteur mesurent EXACTEMENT la meme
    chose. instance=IP:9100 (node-exporter) ; IP:2381 (metriques etcd, HTTP sans TLS)."""
    ne = f'instance="{node_ip}:9100"'
    etcd = f'instance="{node_ip}:2381"'
    return {
        "host_cpu_pct": (
            f"100 - (avg by(instance)(rate(node_cpu_seconds_total{{{ne},mode=\"idle\"}}[3m])) * 100)"
        ),
        "host_mem_pct": (
            f"(1 - (node_memory_MemAvailable_bytes{{{ne}}} / node_memory_MemTotal_bytes{{{ne}}})) * 100"
        ),
        "host_disk_pct": (
            f'(1 - (node_filesystem_avail_bytes{{{ne},mountpoint="/",fstype!~"tmpfs"}} / '
            f'node_filesystem_size_bytes{{{ne},mountpoint="/",fstype!~"tmpfs"}})) * 100'
        ),
        "host_inodes_pct": (
            f'(1 - (node_filesystem_files_free{{{ne},mountpoint="/"}} / '
            f'node_filesystem_files{{{ne},mountpoint="/"}})) * 100'
        ),
        "host_fd_pct": (
            f"(node_filefd_allocated{{{ne}}} / node_filefd_maximum{{{ne}}}) * 100"
        ),
        "host_disk_io_pct": f"max(rate(node_disk_io_time_seconds_total{{{ne}}}[5m])) * 100",
        "host_cpu_steal_pct": f'avg(rate(node_cpu_seconds_total{{{ne},mode="steal"}}[5m])) * 100',
        "host_oom_kills_5m": f"increase(node_vmstat_oom_kill{{{ne}}}[5m])",
        "host_network_errors_ps": f"max(rate(node_network_receive_errs_total{{{ne}}}[5m]))",
        "host_time_drift_seconds": (
            f"abs(node_time_seconds{{{ne}}} - timestamp(node_time_seconds{{{ne}}}))"
        ),
        # etcd : seule une VM qui porte un membre (les 3 noeuds + .160/.162) repond sur :2381 ;
        # pour les autres, batch_query rend simplement 0/valeur par defaut (sans erreur).
        "etcd_fsync_p99_seconds": (
            f"histogram_quantile(0.99, sum by(le)(rate(etcd_disk_wal_fsync_duration_seconds_bucket{{{etcd}}}[5m])))"
        ),
        "etcd_has_leader": f"etcd_server_has_leader{{{etcd}}}",
    }


def build_metric_queries(
    namespace: str,
    app: str,
    window_minutes: int,
    monitoring_vm_ip: str,
    ai_camunda_vm_ip: str,
    monitoring_prometheus_port: int,
    ai_service_port: int,
) -> dict[str, str]:
    pod_pattern = f"{app}-.*"
    window = f"{window_minutes}m"

    queries = {
        "cpu_usage_cores": (
            f'sum(rate(container_cpu_usage_seconds_total{{namespace="{namespace}",'
            f'pod=~"{pod_pattern}",container!="",container!="POD"}}[{window}]))'
        ),
        "memory_working_set_bytes": (
            f'sum(container_memory_working_set_bytes{{namespace="{namespace}",'
            f'pod=~"{pod_pattern}",container!="",container!="POD"}})'
        ),
        "memory_trend_bytes_per_sec": (
            f'sum(deriv(container_memory_working_set_bytes{{namespace="{namespace}",'
            f'pod=~"{pod_pattern}",container!="",container!="POD"}}[{window}]))'
        ),
        "pod_restarts_total": (
            f'sum(increase(kube_pod_container_status_restarts_total{{namespace="{namespace}",'
            f'pod=~"{pod_pattern}"}}[{window}]))'
        ),
        "unavailable_replicas": (
            f'max_over_time(kube_deployment_status_replicas_unavailable{{namespace="{namespace}",deployment="{app}"}}[{window}])'
        ),
        "pod_count": f'count(kube_pod_info{{namespace="{namespace}",pod=~"{pod_pattern}"}})',
        "node_not_ready_count": (
            f'sum(max_over_time(kube_node_status_condition{{condition="Ready",status="false"}}[{window}])) + '
            f'sum(max_over_time(kube_node_status_condition{{condition="Ready",status="unknown"}}[{window}]))'
        ),
        "monitoring_vm_up": (
            f'max(up{{instance="{monitoring_vm_ip}:{monitoring_prometheus_port}"}})'
        ),
        "ai_vm_up": (
            f'max(up{{instance="{ai_camunda_vm_ip}:{ai_service_port}"}})'
        ),
        "http_latency_p95_seconds": (
            "histogram_quantile(0.95, "
            f'sum(rate(http_server_requests_seconds_bucket{{application="{app}",uri!~"/actuator.*|/api/test/health.*"}}[{window}])) by (le))'
        ),
        "http_5xx_ratio": (
            "("
            f'sum(rate(http_server_requests_seconds_count{{application="{app}",status=~"5..",uri!~"/actuator.*|/api/test/health.*"}}[{window}])) '
            "/ clamp_min("
            f'sum(rate(http_server_requests_seconds_count{{application="{app}",uri!~"/actuator.*|/api/test/health.*"}}[{window}])), 0.001)'
            ")"
        ),
        "db_pool_pending": (
            f'max(max_over_time(hikaricp_connections_pending{{application="{app}"}}[{window}]))'
        ),
        "db_pool_usage_ratio": (
            f'clamp_max(max(hikaricp_connections_active{{application="{app}"}}) / clamp_min(max(hikaricp_connections_max{{application="{app}"}}), 1), 1)'
        ),
    }

    # Incident HOTE (namespace="k8s-nodes", app=nom de la machine) : on AJOUTE les requetes
    # node-exporter/etcd de cette VM. Les requetes pod-centriques ci-dessus restent presentes
    # mais rendent 0 (aucun pod ne s'appelle "k8s-worker1-*") : inoffensif, deja le comportement
    # avant cette extension.
    node_ip = NODE_IP.get(app)
    if node_ip:
        queries.update(_host_metric_queries(node_ip))
        # PVC non lie : information cluster-wide, pas specifique a une VM, mais pertinente
        # uniquement quand on raisonne deja sur un incident "infrastructure" (mode hote/noeud).
        queries["pvc_not_bound_count"] = (
            'count(kube_persistentvolumeclaim_status_phase{phase=~"Pending|Lost"} == 1) or vector(0)'
        )

    return queries


def score_metric_risk(features: dict[str, float]) -> float:
    cpu = min(features.get("cpu_usage_cores", 0.0) / 1.0, 1.0)
    memory = min(features.get("memory_working_set_bytes", 0.0) / 1_000_000_000.0, 1.0)
    restarts = min(features.get("pod_restarts_total", 0.0) / 2.0, 1.0)
    unavailable = min(features.get("unavailable_replicas", 0.0), 1.0)
    node_not_ready = min(features.get("node_not_ready_count", 0.0), 1.0)
    monitoring_vm_down = 1.0 - min(max(features.get("monitoring_vm_up", 1.0), 0.0), 1.0)
    ai_vm_down = 1.0 - min(max(features.get("ai_vm_up", 1.0), 0.0), 1.0)
    latency = min(features.get("http_latency_p95_seconds", 0.0) / 3.0, 1.0)
    error_ratio = min(features.get("http_5xx_ratio", 0.0) / 0.35, 1.0)

    # Blend workload stress with infrastructure health so the inference layer
    # can distinguish a stressed pod from a probable VM or node outage.
    score = (
        (0.18 * cpu)
        + (0.22 * memory)
        + (0.12 * restarts)
        + (0.12 * unavailable)
        + (0.09 * node_not_ready)
        + (0.09 * latency)
        + (0.10 * error_ratio)
        + (0.04 * monitoring_vm_down)
        + (0.04 * ai_vm_down)
    )

    # --- termes HOTE BRUT (ajoute 01/08) : ADDITIFS, ne touchent PAS aux poids ci-dessus qui
    # restent calibres/valides pour les incidents applicatifs. Restent a 0 pour ces incidents
    # (les cles host_* valent 0 par defaut) ; ne comptent que quand un vrai incident hote
    # fournit des chiffres. Le clamp final absorbe le cumul si les deux surviennent ensemble. ---
    host_cpu = min(features.get("host_cpu_pct", 0.0) / 100.0, 1.0)
    host_mem = min(features.get("host_mem_pct", 0.0) / 100.0, 1.0)
    host_disk = min(features.get("host_disk_pct", 0.0) / 100.0, 1.0)
    host_inodes = min(features.get("host_inodes_pct", 0.0) / 100.0, 1.0)
    host_fd = min(features.get("host_fd_pct", 0.0) / 100.0, 1.0)
    etcd_no_leader = 1.0 - min(max(features.get("etcd_has_leader", 1.0), 0.0), 1.0)
    etcd_slow = min(features.get("etcd_fsync_p99_seconds", 0.0) / 0.5, 1.0)
    pvc_issue = min(features.get("pvc_not_bound_count", 0.0), 1.0)

    score += (
        (0.06 * host_cpu) + (0.06 * host_mem) + (0.05 * host_disk)
        + (0.03 * host_inodes) + (0.03 * host_fd)
        + (0.05 * etcd_no_leader) + (0.05 * etcd_slow) + (0.03 * pvc_issue)
    )

    return max(0.0, min(1.0, score))
