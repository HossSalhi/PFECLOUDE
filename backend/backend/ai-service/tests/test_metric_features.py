from app.features.metric_features import build_metric_queries


def test_unavailable_replicas_query_uses_window_signal_and_deployment_fallback() -> None:
    queries = build_metric_queries(
        namespace="pfe",
        app="backend",
        window_minutes=5,
        monitoring_vm_ip="192.168.148.160",
        ai_camunda_vm_ip="192.168.148.161",
        monitoring_prometheus_port=9090,
        ai_service_port=8000,
    )

    unavailable_query = queries["unavailable_replicas"]

    assert unavailable_query == (
        'max_over_time('
        'kube_deployment_status_replicas_unavailable{namespace="pfe",deployment="backend"}[5m]'
        ')'
    )


def test_restart_query_uses_windowed_increase_to_avoid_cross_incident_contamination() -> None:
    queries = build_metric_queries(
        namespace="pfe",
        app="backend",
        window_minutes=5,
        monitoring_vm_ip="192.168.148.160",
        ai_camunda_vm_ip="192.168.148.161",
        monitoring_prometheus_port=9090,
        ai_service_port=8000,
    )

    restart_query = queries["pod_restarts_total"]

    assert restart_query == (
        'sum(increase('
        'kube_pod_container_status_restarts_total{namespace="pfe",pod=~"backend-.*"}[5m]'
        '))'
    )
