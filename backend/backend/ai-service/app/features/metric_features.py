def default_metric_features() -> dict[str, float]:
    return {
        "cpu_usage_cores": 0.0,
        "memory_working_set_bytes": 0.0,
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
        # New metrics — enriched coverage
        "disk_io_bytes": 0.0,
        "network_rx_bytes": 0.0,
        "network_tx_bytes": 0.0,
        "network_errors": 0.0,
        "open_file_descriptors": 0.0,
        "jvm_gc_pause_seconds": 0.0,
        "jvm_threads_live": 0.0,
        "node_memory_pressure": 0.0,
        "node_disk_pressure": 0.0,
        "node_not_ready_total": 0.0,
        "pvc_used_bytes": 0.0,
        "http_request_rate": 0.0,
        # Node-level VM metrics (node_exporter) — infra-wide monitoring
        "node_cpu_usage_ratio": 0.0,
        "node_memory_usage_ratio": 0.0,
        "node_disk_usage_ratio": 0.0,
        "node_load1": 0.0,
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
        # ── Original metrics ──────────────────────────────────────────────────
        "cpu_usage_cores": (
            f'sum(rate(container_cpu_usage_seconds_total{{namespace="{namespace}",'
            f'pod=~"{pod_pattern}",container!="",container!="POD"}}[{window}]))'
        ),
        "memory_working_set_bytes": (
            f'sum(container_memory_working_set_bytes{{namespace="{namespace}",'
            f'pod=~"{pod_pattern}",container!="",container!="POD"}})'
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
        # ── New metrics — disk, network, JVM, node pressure ──────────────────
        "disk_io_bytes": (
            f'sum(rate(container_fs_reads_bytes_total{{namespace="{namespace}",pod=~"{pod_pattern}",container!=""}}[{window}])) + '
            f'sum(rate(container_fs_writes_bytes_total{{namespace="{namespace}",pod=~"{pod_pattern}",container!=""}}[{window}]))'
        ),
        "network_rx_bytes": (
            f'sum(rate(container_network_receive_bytes_total{{namespace="{namespace}",pod=~"{pod_pattern}"}}[{window}]))'
        ),
        "network_tx_bytes": (
            f'sum(rate(container_network_transmit_bytes_total{{namespace="{namespace}",pod=~"{pod_pattern}"}}[{window}]))'
        ),
        "network_errors": (
            f'sum(rate(container_network_receive_errors_total{{namespace="{namespace}",pod=~"{pod_pattern}"}}[{window}])) + '
            f'sum(rate(container_network_transmit_errors_total{{namespace="{namespace}",pod=~"{pod_pattern}"}}[{window}]))'
        ),
        "open_file_descriptors": (
            f'sum(process_open_fds{{namespace="{namespace}",pod=~"{pod_pattern}"}})'
        ),
        "jvm_gc_pause_seconds": (
            f'sum(rate(jvm_gc_pause_seconds_sum{{namespace="{namespace}",pod=~"{pod_pattern}"}}[{window}]))'
        ),
        "jvm_threads_live": (
            f'sum(jvm_threads_live_threads{{namespace="{namespace}",pod=~"{pod_pattern}"}})'
        ),
        "node_memory_pressure": (
            'sum(kube_node_status_condition{condition="MemoryPressure",status="true"}) or vector(0)'
        ),
        "node_disk_pressure": (
            'sum(kube_node_status_condition{condition="DiskPressure",status="true"}) or vector(0)'
        ),
        "node_not_ready_total": (
            'sum(kube_node_status_condition{condition="Ready",status="false"}) or vector(0)'
        ),
        "pvc_used_bytes": (
            f'sum(kubelet_volume_stats_used_bytes{{namespace="{namespace}"}})'
        ),
        "http_request_rate": (
            f'sum(rate(http_server_requests_seconds_count{{application="{app}",uri!~"/actuator.*|/api/test/health.*"}}[{window}]))'
        ),
        # ── Node-level VM metrics (node_exporter) — cluster-wide ────────────────
        # CPU usage ratio across all nodes (1 = 100% used)
        "node_cpu_usage_ratio": (
            '1 - avg(rate(node_cpu_seconds_total{mode="idle"}[' + window + ']))'
        ),
        # Memory usage ratio across all nodes
        "node_memory_usage_ratio": (
            '1 - avg(node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes)'
        ),
        # Disk usage ratio across all nodes (root filesystem)
        "node_disk_usage_ratio": (
            '1 - avg(node_filesystem_avail_bytes{mountpoint="/"} / node_filesystem_size_bytes{mountpoint="/"})'
        ),
        # 1-minute load average across all nodes
        "node_load1": (
            'avg(node_load1)'
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
    # New signals
    disk_io = min(features.get("disk_io_bytes", 0.0) / 100_000_000.0, 1.0)
    net_err = min(features.get("network_errors", 0.0) / 10.0, 1.0)
    fds = min(features.get("open_file_descriptors", 0.0) / 1000.0, 1.0)
    gc = min(features.get("jvm_gc_pause_seconds", 0.0) / 1.0, 1.0)
    node_mem_pressure = min(features.get("node_memory_pressure", 0.0), 1.0)
    node_disk_pressure = min(features.get("node_disk_pressure", 0.0), 1.0)
    node_not_ready_total = min(features.get("node_not_ready_total", 0.0), 1.0)

    score = (
        (0.14 * cpu)
        + (0.16 * memory)
        + (0.10 * restarts)
        + (0.10 * unavailable)
        + (0.08 * node_not_ready)
        + (0.07 * latency)
        + (0.08 * error_ratio)
        + (0.03 * monitoring_vm_down)
        + (0.03 * ai_vm_down)
        + (0.04 * disk_io)
        + (0.04 * net_err)
        + (0.03 * fds)
        + (0.03 * gc)
        + (0.05 * node_mem_pressure)
        + (0.05 * node_disk_pressure)
        + (0.05 * node_not_ready_total)
    )
    return max(0.0, min(1.0, score))
