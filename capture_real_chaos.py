"""Capture real Prometheus features during real chaos injections.
Each scenario: inject chaos → wait stabilize → snapshot features every 30s for 5min → label
Output: /opt/pfe-ai-service/datasets/real_chaos_capture.csv
"""
import csv
import json
import subprocess
import time
from datetime import datetime, timezone

import httpx

PROM = "http://192.168.148.160:9090"
OUTPUT = "/opt/pfe-ai-service/datasets/real_chaos_capture.csv"

# The 416-feature schema's KEY metrics that XGBoost uses (subset for capture)
PROM_QUERIES = {
    "cpu_usage_cores": 'sum(rate(container_cpu_usage_seconds_total{{namespace="pfe",pod=~"{pod_pattern}",container=~"{cont}"}}[2m]))',
    "memory_working_set_bytes": 'sum(container_memory_working_set_bytes{{namespace="pfe",pod=~"{pod_pattern}",container=~"{cont}"}})',
    "pod_restarts_total": 'sum(increase(kube_pod_container_status_restarts_total{{namespace="pfe",pod=~"{pod_pattern}"}}[5m]))',
    "http_5xx_ratio": '''sum(rate(http_server_requests_seconds_count{{application="{app}",status=~"5.."}}[2m])) / clamp_min(sum(rate(http_server_requests_seconds_count{{application="{app}"}}[2m])), 0.001)''',
    "http_latency_p95_seconds": '''histogram_quantile(0.95, sum(rate(http_server_requests_seconds_bucket{{application="{app}"}}[2m])) by (le))''',
    "unavailable_replicas": 'sum(kube_deployment_status_replicas_unavailable{{namespace="pfe",deployment="{app}"}})',
    "up": 'min(up{{namespace="pfe",pod=~"{pod_pattern}"}})',
    "db_pool_pending": 'sum(hikaricp_connections_pending{{application="backend"}})',
}

SCENARIOS = [
    # (name, app, container, pod_pattern, inject_fn, cleanup_fn)
    ("NO_INCIDENT", "demo-app", "demo", "demo-app.*", None, None),
    ("CPU_SATURATION", "demo-app", "demo", "demo-app.*",
     "kubectl -n pfe exec $(kubectl -n pfe get pod -l app=demo-app -o jsonpath='{.items[0].metadata.name}') -- sh -c 'nohup yes > /dev/null & nohup yes > /dev/null &'",
     "kubectl -n pfe exec $(kubectl -n pfe get pod -l app=demo-app -o jsonpath='{.items[0].metadata.name}') -- pkill yes"),
    ("POD_CRASH_LOOP", "demo-app", "demo", "demo-app.*",
     "for i in 1 2 3 4 5 6; do P=$(kubectl -n pfe get pod -l app=demo-app -o jsonpath='{.items[0].metadata.name}'); kubectl -n pfe exec $P -- kill 1 2>/dev/null; sleep 25; done",
     "true"),
    ("NETWORK_DNS_FAILURE", "demo-app", "demo", "demo-app.*",
     "kubectl -n kube-system scale deployment/coredns --replicas=0",
     "kubectl -n kube-system scale deployment/coredns --replicas=2"),
    ("INGRESS_BACKEND_DOWN", "backend", "backend", "backend.*",
     "kubectl -n pfe scale deployment/backend --replicas=0",
     "kubectl -n pfe scale deployment/backend --replicas=1"),
]


def prom_query(client: httpx.Client, expr: str) -> float | None:
    try:
        r = client.get(f"{PROM}/api/v1/query", params={"query": expr}, timeout=10.0)
        data = r.json()
        if data.get("status") == "success" and data["data"]["result"]:
            return float(data["data"]["result"][0]["value"][1])
    except Exception:
        pass
    return None


def snapshot(client: httpx.Client, scenario: dict) -> dict:
    features = {}
    for name, tpl in PROM_QUERIES.items():
        expr = tpl.format(
            pod_pattern=scenario["pod_pattern"],
            cont=scenario["container"],
            app=scenario["app"],
        )
        v = prom_query(client, expr)
        features[name] = v if v is not None else 0.0
    return features


def run_via_ssh(cmd: str) -> None:
    if cmd in (None, "true"):
        return
    full = f'ssh -o StrictHostKeyChecking=no kube@192.168.148.153 "{cmd}"'
    print(f"  RUN: {cmd[:100]}")
    subprocess.run(full, shell=True, timeout=300, check=False)


def main():
    rows = []
    with httpx.Client() as client:
        for label, app, cont, pp, inject, cleanup in SCENARIOS:
            scenario = {"app": app, "container": cont, "pod_pattern": pp}
            print(f"\n=== {label} ===")
            if inject:
                print(f"  Injecting chaos...")
                run_via_ssh(inject)
                print(f"  Wait 90s for stabilization...")
                time.sleep(90)

            for i in range(10):
                print(f"  Snapshot {i+1}/10...")
                feats = snapshot(client, scenario)
                feats["scenario"] = label
                feats["timestamp"] = datetime.now(timezone.utc).isoformat()
                rows.append(feats)
                time.sleep(30)

            if cleanup and cleanup != "true":
                print(f"  Cleanup...")
                run_via_ssh(cleanup)
                time.sleep(60)

    if rows:
        with open(OUTPUT, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
        print(f"\nSaved {len(rows)} samples to {OUTPUT}")
        # Per-scenario summary
        from collections import Counter
        c = Counter(r["scenario"] for r in rows)
        for k, v in c.items():
            print(f"  {k}: {v} samples")


if __name__ == "__main__":
    main()
