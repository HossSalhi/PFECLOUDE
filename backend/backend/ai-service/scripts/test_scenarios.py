"""
Run all AIOps scenario tests against the live AI service.
Usage: python scripts/test_scenarios.py [--url http://192.168.148.161:8000]
"""
import argparse
import json
import urllib.request

MB = 1_000_000

SCENARIOS = [
    ("NORMAL", {
        "cpu_usage_cores": 0.05, "memory_working_set_bytes": 120 * MB,
        "pod_restarts_total": 0.0, "unavailable_replicas": 0.0, "pod_count": 2.0,
        "node_not_ready_count": 0.0, "monitoring_vm_up": 1.0, "ai_vm_up": 1.0,
        "http_latency_p95_seconds": 0.05, "http_5xx_ratio": 0.0,
        "log_line_count": 20.0, "error_line_count": 0.0, "warning_line_count": 1.0,
        "restart_keyword_hits": 0.0, "db_keyword_hits": 0.0, "error_ratio": 0.0,
    }, False, "NO_INCIDENT", "LOW"),
    ("CPU_STRESS", {
        "cpu_usage_cores": 1.2, "memory_working_set_bytes": 300 * MB,
        "pod_restarts_total": 0.0, "unavailable_replicas": 0.0, "pod_count": 2.0,
        "node_not_ready_count": 0.0, "monitoring_vm_up": 1.0, "ai_vm_up": 1.0,
        "http_latency_p95_seconds": 1.5, "http_5xx_ratio": 0.03,
        "log_line_count": 100.0, "error_line_count": 5.0, "warning_line_count": 10.0,
        "restart_keyword_hits": 0.0, "db_keyword_hits": 0.0, "error_ratio": 0.05,
    }, True, "CPU_STRESS", "HIGH"),
    ("MEMORY_STRESS", {
        "cpu_usage_cores": 0.2, "memory_working_set_bytes": 1300 * MB,
        "pod_restarts_total": 0.0, "unavailable_replicas": 0.0, "pod_count": 2.0,
        "node_not_ready_count": 0.0, "monitoring_vm_up": 1.0, "ai_vm_up": 1.0,
        "http_latency_p95_seconds": 0.5, "http_5xx_ratio": 0.02,
        "log_line_count": 80.0, "error_line_count": 8.0, "warning_line_count": 5.0,
        "restart_keyword_hits": 0.0, "db_keyword_hits": 0.0, "error_ratio": 0.10,
    }, True, "MEMORY_STRESS", "HIGH"),
    ("RESTART_LOOP", {
        "cpu_usage_cores": 0.3, "memory_working_set_bytes": 250 * MB,
        "pod_restarts_total": 6.0, "unavailable_replicas": 1.0, "pod_count": 2.0,
        "node_not_ready_count": 0.0, "monitoring_vm_up": 1.0, "ai_vm_up": 1.0,
        "http_latency_p95_seconds": 0.8, "http_5xx_ratio": 0.05,
        "log_line_count": 150.0, "error_line_count": 40.0, "warning_line_count": 15.0,
        "restart_keyword_hits": 5.0, "db_keyword_hits": 0.0, "error_ratio": 0.27,
    }, True, "RESTART_LOOP", "HIGH"),
    ("AVAILABILITY_DROP", {
        "cpu_usage_cores": 0.4, "memory_working_set_bytes": 400 * MB,
        "pod_restarts_total": 0.0, "unavailable_replicas": 2.0, "pod_count": 1.0,
        "node_not_ready_count": 0.0, "monitoring_vm_up": 1.0, "ai_vm_up": 1.0,
        "http_latency_p95_seconds": 2.0, "http_5xx_ratio": 0.08,
        "log_line_count": 60.0, "error_line_count": 10.0, "warning_line_count": 6.0,
        "restart_keyword_hits": 0.0, "db_keyword_hits": 0.0, "error_ratio": 0.17,
    }, True, "AVAILABILITY_DROP", "CRITICAL"),
    ("APP_ERROR_STORM", {
        "cpu_usage_cores": 0.4, "memory_working_set_bytes": 350 * MB,
        "pod_restarts_total": 1.0, "unavailable_replicas": 0.0, "pod_count": 2.0,
        "node_not_ready_count": 0.0, "monitoring_vm_up": 1.0, "ai_vm_up": 1.0,
        "http_latency_p95_seconds": 2.0, "http_5xx_ratio": 0.35,
        "log_line_count": 200.0, "error_line_count": 80.0, "warning_line_count": 20.0,
        "restart_keyword_hits": 2.0, "db_keyword_hits": 0.0, "error_ratio": 0.40,
    }, True, "APPLICATION_ERROR_STORM", "CRITICAL"),
    ("DB_CONNECTIVITY", {
        "cpu_usage_cores": 0.1, "memory_working_set_bytes": 280 * MB,
        "pod_restarts_total": 1.0, "unavailable_replicas": 0.0, "pod_count": 2.0,
        "node_not_ready_count": 0.0, "monitoring_vm_up": 1.0, "ai_vm_up": 1.0,
        "http_latency_p95_seconds": 1.2, "http_5xx_ratio": 0.12,
        "log_line_count": 80.0, "error_line_count": 25.0, "warning_line_count": 8.0,
        "restart_keyword_hits": 1.0, "db_keyword_hits": 8.0, "error_ratio": 0.31,
    }, True, "DATABASE_CONNECTIVITY", "HIGH"),
    ("NODE_NOT_READY", {
        "cpu_usage_cores": 0.2, "memory_working_set_bytes": 300 * MB,
        "pod_restarts_total": 3.0, "unavailable_replicas": 2.0, "pod_count": 1.0,
        "node_not_ready_count": 2.0, "monitoring_vm_up": 1.0, "ai_vm_up": 1.0,
        "http_latency_p95_seconds": 3.0, "http_5xx_ratio": 0.4,
        "log_line_count": 100.0, "error_line_count": 30.0, "warning_line_count": 10.0,
        "restart_keyword_hits": 4.0, "db_keyword_hits": 0.0, "error_ratio": 0.30,
    }, True, "VM_DOWN", "CRITICAL"),
    ("MONITORING_DOWN", {
        "cpu_usage_cores": 0.1, "memory_working_set_bytes": 200 * MB,
        "pod_restarts_total": 0.0, "unavailable_replicas": 0.0, "pod_count": 2.0,
        "node_not_ready_count": 0.0, "monitoring_vm_up": 0.0, "ai_vm_up": 1.0,
        "http_latency_p95_seconds": 0.3, "http_5xx_ratio": 0.02,
        "log_line_count": 30.0, "error_line_count": 5.0, "warning_line_count": 4.0,
        "restart_keyword_hits": 0.0, "db_keyword_hits": 0.0, "error_ratio": 0.17,
    }, True, "VM_DOWN", "HIGH"),
    ("HIGH_LATENCY", {
        "cpu_usage_cores": 0.3, "memory_working_set_bytes": 300 * MB,
        "pod_restarts_total": 0.0, "unavailable_replicas": 0.0, "pod_count": 2.0,
        "node_not_ready_count": 0.0, "monitoring_vm_up": 1.0, "ai_vm_up": 1.0,
        "http_latency_p95_seconds": 5.5, "http_5xx_ratio": 0.06,
        "log_line_count": 60.0, "error_line_count": 8.0, "warning_line_count": 5.0,
        "restart_keyword_hits": 0.0, "db_keyword_hits": 0.0, "error_ratio": 0.13,
    }, True, "AVAILABILITY_DROP", "MEDIUM"),
    ("GENERAL_DEGRADATION", {
        "cpu_usage_cores": 0.3, "memory_working_set_bytes": 350 * MB,
        "pod_restarts_total": 0.0, "unavailable_replicas": 0.0, "pod_count": 2.0,
        "node_not_ready_count": 0.0, "monitoring_vm_up": 1.0, "ai_vm_up": 1.0,
        "http_latency_p95_seconds": 1.0, "http_5xx_ratio": 0.03,
        "log_line_count": 60.0, "error_line_count": 10.0, "warning_line_count": 8.0,
        "restart_keyword_hits": 0.0, "db_keyword_hits": 0.0, "error_ratio": 0.17,
    }, True, "GENERAL_DEGRADATION", "MEDIUM"),
]


def run(base_url: str) -> None:
    endpoint = f"{base_url.rstrip('/')}/predict"
    passed = 0
    failed = 0
    rows = []

    for name, overrides, exp_incident, exp_type, exp_severity in SCENARIOS:
        payload = json.dumps({
            "namespace": "pfe",
            "app": "backend",
            "window_minutes": 5,
            "feature_overrides": overrides,
        }).encode()
        req = urllib.request.Request(
            endpoint, data=payload, headers={"Content-Type": "application/json"}
        )
        try:
            resp = urllib.request.urlopen(req, timeout=15)
            d = json.loads(resp.read())
            got_incident = d["incident"]
            got_type = d["incident_type"]
            got_sev = d["severity"]
            got_conf = round(d["confidence"], 3)
            got_action = d.get("recommended_action", "")[:45]
            got_mode = d.get("execution_mode", "")

            ok_incident = (got_incident == exp_incident)
            if exp_type == "NO_INCIDENT":
                ok_type = not got_incident
            else:
                ok_type = (got_type == exp_type)
            ok_sev = (got_sev == exp_severity)

            if ok_incident and ok_type and ok_sev:
                passed += 1
                status = "PASS"
            else:
                failed += 1
                status = "FAIL"
                if not ok_incident:
                    status += "(incident)"
                elif not ok_type:
                    status += "(type)"
                elif not ok_sev:
                    status += "(sev)"

            rows.append((name, status, got_incident, got_type, got_sev, got_conf, got_mode, got_action))
        except Exception as ex:
            failed += 1
            rows.append((name, "ERROR", None, str(ex)[:40], None, 0.0, "", ""))

    hdr = f"{'Scenario':<22} {'Status':<16} {'Inc':<6} {'Type':<26} {'Sev':<10} {'Conf':<6} {'Mode':<24} Action"
    print(hdr)
    print("-" * 120)
    for row in rows:
        name, status, inc, typ, sev, conf, mode, act = row
        print(f"{name:<22} {status:<16} {str(inc):<6} {str(typ):<26} {str(sev):<10} {str(conf):<6} {str(mode):<24} {act}")
    print()
    print(f"RESULT: {passed} PASS / {failed} FAIL / {passed + failed} total")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://192.168.148.161:8000")
    args = parser.parse_args()
    run(args.url)
