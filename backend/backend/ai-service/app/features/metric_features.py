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

    return {
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
    return max(0.0, min(1.0, score))
