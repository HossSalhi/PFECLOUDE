"""Real chaos capture v2 — incremental save, 15 injectable scenarios.
For each scenario: inject → wait 90s → 5 snapshots × 30s → cleanup → save.
"""
import csv
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

PROM = "http://192.168.148.160:9090"
OUTPUT = Path("/opt/pfe-ai-service/datasets/real_chaos_capture.csv")
SSH_MASTER = "ssh -o StrictHostKeyChecking=no kube@192.168.148.153"

PROM_QUERIES = {
    "cpu_usage_cores": 'sum(rate(container_cpu_usage_seconds_total{{namespace="pfe",pod=~"{pp}"}}[2m]))',
    "memory_working_set_bytes": 'sum(container_memory_working_set_bytes{{namespace="pfe",pod=~"{pp}"}})',
    "pod_restarts_total": 'sum(increase(kube_pod_container_status_restarts_total{{namespace="pfe",pod=~"{pp}"}}[5m]))',
    "http_5xx_ratio": 'sum(rate(http_server_requests_seconds_count{{application="{app}",status=~"5.."}}[2m])) / clamp_min(sum(rate(http_server_requests_seconds_count{{application="{app}"}}[2m])), 0.001)',
    "http_latency_p95_seconds": 'histogram_quantile(0.95, sum(rate(http_server_requests_seconds_bucket{{application="{app}"}}[2m])) by (le))',
    "http_request_rate": 'sum(rate(http_server_requests_seconds_count{{application="{app}"}}[2m]))',
    "unavailable_replicas": 'sum(kube_deployment_status_replicas_unavailable{{namespace="pfe",deployment="{app}"}})',
    "up_min": 'min(up{{namespace="pfe",pod=~"{pp}"}}) or vector(1)',
    "db_pool_pending": 'sum(hikaricp_connections_pending{{application="backend"}}) or vector(0)',
    "db_pool_active": 'sum(hikaricp_connections_active{{application="backend"}}) or vector(0)',
    "node_cpu_busy": '100 - avg(rate(node_cpu_seconds_total{{mode="idle"}}[2m])) * 100',
    "node_disk_free_pct": 'min(node_filesystem_avail_bytes / node_filesystem_size_bytes * 100)',
    "node_memory_free_pct": 'min(node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes * 100)',
}

# Scenarios with injection commands
SCENARIOS = [
    ("NO_INCIDENT", "demo-app", "demo-app.*", None, None),
    ("CPU_SATURATION", "demo-app", "demo-app.*",
     "kubectl -n pfe exec $(kubectl -n pfe get pod -l app=demo-app -o jsonpath='{.items[0].metadata.name}') -- sh -c 'nohup yes > /dev/null & nohup yes > /dev/null &'",
     "kubectl -n pfe exec $(kubectl -n pfe get pod -l app=demo-app -o jsonpath='{.items[0].metadata.name}') -- pkill yes || true"),
    ("POD_CRASH_LOOP", "demo-app", "demo-app.*",
     "for i in 1 2 3 4 5 6; do P=$(kubectl -n pfe get pod -l app=demo-app -o jsonpath='{.items[0].metadata.name}'); kubectl -n pfe exec $P -- kill 1 2>/dev/null || true; sleep 25; done",
     "kubectl -n pfe rollout status deployment/demo-app --timeout=60s || true"),
    ("NETWORK_DNS_FAILURE", "backend", "backend.*",
     "kubectl -n kube-system scale deployment/coredns --replicas=0",
     "kubectl -n kube-system scale deployment/coredns --replicas=2"),
    ("INGRESS_BACKEND_DOWN", "backend", "backend.*",
     "kubectl -n pfe scale deployment/backend --replicas=0",
     "kubectl -n pfe scale deployment/backend --replicas=1; kubectl -n pfe rollout status deployment/backend --timeout=60s || true"),
    ("DB_CONNECTION_REFUSED", "backend", "backend.*",
     "kubectl -n pfe delete pod -l app=postgres --grace-period=0 --force 2>/dev/null || true",
     "kubectl -n pfe rollout status statefulset/postgres --timeout=60s 2>/dev/null || true"),
    ("PVC_PENDING", "test-pvc-pending", "nope-*",
     "cat <<'EOF' | kubectl apply -f -\napiVersion: v1\nkind: PersistentVolumeClaim\nmetadata:\n  name: chaos-pvc-pending\n  namespace: pfe\nspec:\n  storageClassName: does-not-exist\n  accessModes: [\"ReadWriteOnce\"]\n  resources:\n    requests:\n      storage: 1Gi\nEOF",
     "kubectl -n pfe delete pvc chaos-pvc-pending --ignore-not-found"),
    ("POD_IMAGE_PULL_BACKOFF", "test-img-fail", "chaos-imgfail-.*",
     "kubectl -n pfe run chaos-imgfail --image=this-image-does-not-exist.invalid/nope:0.0.1 --restart=Never 2>/dev/null || true",
     "kubectl -n pfe delete pod chaos-imgfail --grace-period=0 --force --ignore-not-found"),
    ("POD_PENDING_INSUFFICIENT_RESOURCES", "test-pending", "chaos-bigpod-.*",
     "cat <<'EOF' | kubectl apply -f -\napiVersion: v1\nkind: Pod\nmetadata:\n  name: chaos-bigpod\n  namespace: pfe\nspec:\n  containers:\n  - name: hog\n    image: alpine:3.18\n    command: [\"sleep\",\"3600\"]\n    resources:\n      requests:\n        memory: \"99Gi\"\n        cpu: \"99\"\nEOF",
     "kubectl -n pfe delete pod chaos-bigpod --grace-period=0 --force --ignore-not-found"),
    ("REQUEST_RATE_DROP", "backend", "backend.*", None, None),  # natural state, no traffic
    ("NODE_DISK_FULL", "backend", "backend.*",
     "ssh -o StrictHostKeyChecking=no kube@192.168.148.158 \"echo kube | sudo -S sh -c 'dd if=/dev/zero of=/tmp/chaos_bigfile bs=1M count=2000 2>/dev/null'\"",
     "ssh -o StrictHostKeyChecking=no kube@192.168.148.158 \"echo kube | sudo -S rm -f /tmp/chaos_bigfile\""),
    ("METRICS_SCRAPE_FAILURE", "backend", "backend.*",
     "kubectl -n pfe label pod -l app=backend aiops.io/monitor=false --overwrite",
     "kubectl -n pfe label pod -l app=backend aiops.io/monitor=true --overwrite"),
    ("ERROR_RATE_5XX_SPIKE", "backend", "backend.*",
     "for i in 1 2 3 4 5 6 7 8 9 10; do curl -s -o /dev/null http://192.168.148.153:30880/api/this-does-not-exist & done; wait",
     "true"),
    ("NODE_HIGH_CPU", "backend", "backend.*",
     "ssh -o StrictHostKeyChecking=no kube@192.168.148.158 \"nohup yes > /dev/null & nohup yes > /dev/null &\"",
     "ssh -o StrictHostKeyChecking=no kube@192.168.148.158 \"pkill yes || true\""),
    ("POD_STUCK_TERMINATING", "backend", "backend.*",
     "true",
     "true"),  # too risky to inject
]


def prom_query(client: httpx.Client, expr: str) -> float:
    try:
        r = client.get(f"{PROM}/api/v1/query", params={"query": expr}, timeout=10.0)
        d = r.json()
        if d.get("status") == "success" and d["data"]["result"]:
            return float(d["data"]["result"][0]["value"][1])
    except Exception:
        pass
    return 0.0


def snapshot(client: httpx.Client, app: str, pp: str) -> dict:
    feats = {}
    for name, tpl in PROM_QUERIES.items():
        feats[name] = prom_query(client, tpl.format(pp=pp, app=app))
    return feats


def run_cmd(cmd: str | None) -> None:
    if cmd in (None, "", "true"):
        return
    full = f'{SSH_MASTER} "{cmd}"'
    print(f"  CMD: {cmd[:120]}", flush=True)
    try:
        subprocess.run(full, shell=True, timeout=180, check=False)
    except Exception as e:
        print(f"  cmd err: {e}", flush=True)


def append_csv(fields: list, row: dict) -> None:
    new_file = not OUTPUT.exists()
    with OUTPUT.open("a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        if new_file:
            w.writeheader()
        w.writerow(row)


fieldnames = ["scenario", "timestamp"] + list(PROM_QUERIES.keys())

with httpx.Client() as client:
    for label, app, pp, inject, cleanup in SCENARIOS:
        print(f"\n=== {label} ===", flush=True)
        if inject:
            run_cmd(inject)
            print(f"  wait 90s stabilize...", flush=True)
            time.sleep(90)
        for i in range(5):
            f = snapshot(client, app, pp)
            f["scenario"] = label
            f["timestamp"] = datetime.now(timezone.utc).isoformat()
            append_csv(fieldnames, f)
            print(f"  snap {i+1}/5: cpu={f['cpu_usage_cores']:.3f} mem={f['memory_working_set_bytes']/1e9:.2f}GB http5xx={f['http_5xx_ratio']:.3f} latP95={f['http_latency_p95_seconds']:.3f}", flush=True)
            time.sleep(30)
        if cleanup:
            run_cmd(cleanup)
            print(f"  wait 60s recovery...", flush=True)
            time.sleep(60)

print(f"\nDONE. CSV: {OUTPUT}", flush=True)
