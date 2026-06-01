"""Real chaos capture v3+ — STRONGER injection (longer duration, more impactful)."""
import csv
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

PROM = "http://192.168.148.160:9090"
OUTPUT = Path("/opt/pfe-ai-service/datasets/real_chaos_capture_v2.csv")
MASTER_SSH = "ssh -o StrictHostKeyChecking=no kube@192.168.148.153"

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

SCENARIOS = [
    ("NO_INCIDENT", "demo-app", "demo-app.*", None, None, 30, 5),
    ("CPU_SATURATION", "demo-app", "demo-app.*",
     "P=$(kubectl -n pfe get pod -l app=demo-app -o jsonpath='{.items[0].metadata.name}'); for i in 1 2 3 4; do kubectl -n pfe exec $P -- sh -c 'nohup setsid sh -c \"while :; do :; done\" </dev/null >/dev/null 2>&1 &' & done; sleep 5",
     "P=$(kubectl -n pfe get pod -l app=demo-app -o jsonpath='{.items[0].metadata.name}'); kubectl -n pfe exec $P -- pkill -9 sh || true",
     120, 8),
    ("POD_CRASH_LOOP", "demo-app", "demo-app.*",
     "for i in 1 2 3 4 5 6 7 8; do P=$(kubectl -n pfe get pod -l app=demo-app -o jsonpath='{.items[0].metadata.name}'); kubectl -n pfe exec $P -- kill 1 2>/dev/null || true; sleep 20; done",
     "sleep 60",
     30, 8),
    ("NETWORK_DNS_FAILURE", "backend", "backend.*",
     "kubectl -n kube-system scale deployment/coredns --replicas=0; sleep 10; for i in $(seq 1 50); do curl -s -m 5 -o /dev/null http://192.168.148.153:30880/api/incidents -H 'Authorization: Bearer fake' & done; wait",
     "kubectl -n kube-system scale deployment/coredns --replicas=2; sleep 30",
     60, 8),
    ("INGRESS_BACKEND_DOWN", "backend", "backend.*",
     "kubectl -n pfe scale deployment/backend --replicas=0",
     "kubectl -n pfe scale deployment/backend --replicas=1; sleep 30",
     45, 6),
    ("DB_CONNECTION_REFUSED", "backend", "backend.*",
     "kubectl -n pfe delete pod -l app=postgres --grace-period=0 --force 2>/dev/null; sleep 5; for i in $(seq 1 30); do curl -s -m 3 -o /dev/null http://192.168.148.153:30880/api/incidents -H 'Authorization: Bearer fake' & done; wait",
     "sleep 60",
     60, 6),
    ("PVC_PENDING", "chaos-pvc", "chaos.*",
     "cat <<'EOF' | kubectl apply -f -\napiVersion: v1\nkind: PersistentVolumeClaim\nmetadata:\n  name: chaos-pvc-pending\n  namespace: pfe\nspec:\n  storageClassName: does-not-exist\n  accessModes: [ReadWriteOnce]\n  resources:\n    requests:\n      storage: 1Gi\nEOF",
     "kubectl -n pfe delete pvc chaos-pvc-pending --ignore-not-found",
     30, 5),
    ("POD_IMAGE_PULL_BACKOFF", "chaos-imgfail", "chaos-imgfail.*",
     "kubectl -n pfe delete pod chaos-imgfail --ignore-not-found --grace-period=0 --force 2>/dev/null; kubectl -n pfe run chaos-imgfail --image=bogus.invalid/nope:0.0.1 --restart=Never",
     "kubectl -n pfe delete pod chaos-imgfail --grace-period=0 --force --ignore-not-found",
     60, 5),
    ("POD_PENDING_INSUFFICIENT_RESOURCES", "chaos-bigpod", "chaos-bigpod.*",
     "kubectl -n pfe delete pod chaos-bigpod --ignore-not-found --grace-period=0 --force 2>/dev/null; cat <<'EOF' | kubectl apply -f -\napiVersion: v1\nkind: Pod\nmetadata:\n  name: chaos-bigpod\n  namespace: pfe\nspec:\n  containers:\n  - name: hog\n    image: alpine:3.18\n    command: [sleep, '3600']\n    resources:\n      requests:\n        memory: 99Gi\n        cpu: '99'\nEOF",
     "kubectl -n pfe delete pod chaos-bigpod --grace-period=0 --force --ignore-not-found",
     30, 5),
    ("REQUEST_RATE_DROP", "backend", "backend.*", None, None, 30, 5),
    ("METRICS_SCRAPE_FAILURE", "backend", "backend.*",
     "kubectl -n pfe label pod -l app=backend aiops.io/monitor=false --overwrite",
     "kubectl -n pfe label pod -l app=backend aiops.io/monitor=true --overwrite; sleep 30",
     90, 5),
    ("ERROR_RATE_5XX_SPIKE", "backend", "backend.*",
     "for i in $(seq 1 200); do curl -s -m 3 -o /dev/null http://192.168.148.153:30880/api/nonexistent-endpoint -H 'Authorization: Bearer fake' & done; wait",
     "sleep 30",
     30, 5),
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
    f = {}
    for name, tpl in PROM_QUERIES.items():
        f[name] = prom_query(client, tpl.format(pp=pp, app=app))
    return f


def run_cmd(cmd):
    if not cmd:
        return
    full = f'{MASTER_SSH} "{cmd}"'
    print(f"  CMD: {cmd[:100]}", flush=True)
    try:
        subprocess.run(full, shell=True, timeout=300, capture_output=True, text=True)
    except Exception as e:
        print(f"  err: {e}", flush=True)


def append_csv(fields, row):
    new = not OUTPUT.exists()
    with OUTPUT.open("a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        if new:
            w.writeheader()
        w.writerow(row)


fields = ["scenario", "timestamp"] + list(PROM_QUERIES.keys())

with httpx.Client() as client:
    for label, app, pp, inject, cleanup, stab, count in SCENARIOS:
        print(f"\n=== {label} ===", flush=True)
        if inject:
            run_cmd(inject)
            print(f"  wait {stab}s stabilize...", flush=True)
            time.sleep(stab)
        for i in range(count):
            f = snapshot(client, app, pp)
            f["scenario"] = label
            f["timestamp"] = datetime.now(timezone.utc).isoformat()
            append_csv(fields, f)
            print(f"  snap {i+1}/{count}: cpu={f['cpu_usage_cores']:.4f} mem={f['memory_working_set_bytes']/1e9:.2f}GB http5xx={f['http_5xx_ratio']:.3f} lat={f['http_latency_p95_seconds']:.3f} restarts={f['pod_restarts_total']:.1f}", flush=True)
            time.sleep(20)
        if cleanup:
            run_cmd(cleanup)
            print(f"  recovery 30s...", flush=True)
            time.sleep(30)

print(f"\nDONE. {OUTPUT}", flush=True)
