# HELIOS AIOps — Testing Guide (self-service)

Copy-paste commands to test the system end-to-end yourself.

> **Tip:** the easiest way to run these is to SSH into the master node first and run
> everything from there (pure Linux bash, no Windows-quoting headaches):
> ```bash
> ssh kube@192.168.148.153      # password: kube
> ```
> Commands below assume you are **on the master** unless they say "(jump via .161)".

---

## 0. Access & URLs

| What | Where |
|---|---|
| Master (kubectl) | `ssh kube@192.168.148.153` (kube/kube) |
| Web UI | http://192.168.148.153:30880 |
| Backend API | http://192.168.148.153:30808 |
| AI service | http://192.168.148.161:8000 |
| Workers | .158 (worker1), .155 (worker2) |
| Monitoring (Prometheus/Loki) | .160 — **no direct SSH**, jump via .161 |
| AI + Camunda + mail relay | .161 |

Logins: `admin / admin-change-me` (ADMIN role — **use this for scripted API tests**, no 2FA),
`superadmin / superadmin-change-me` (**2FA enabled** — login returns `requires2fa`, enroll via UI;
14-day bypass after one verification), `operator / operator-change-me` (USER role).

Handy alias for the Postgres pod (CNPG primary):
```bash
PG=$(kubectl -n pfe get pod -l cnpg.io/instanceRole=primary -o jsonpath='{.items[0].metadata.name}')
echo "postgres pod = $PG"
```

---

## 1. Health check — is everything up?

```bash
# Cluster nodes + app pods
kubectl get nodes
kubectl -n pfe get pods -o wide

# AI service (should print {"status":"ok",...})
curl -s http://192.168.148.161:8000/health; echo

# VM161 services (run on .161)
ssh kube@192.168.148.161 "echo kube | sudo -S systemctl is-active pfe-ai-service ollama helios-smtp-relay"

# Web UI reachable (expect 200)
curl -s -o /dev/null -w '%{http_code}\n' http://192.168.148.153:30880/
```

If pods are all `Running` and AI service says `ok`, you're ready. If the AI later
reports **all metrics = 0 / no incidents**, the monitoring VM broke on reboot —
see **Section 6 (post-reboot recovery)**.

---

## 2. The real-app test — podinfo (detect → remediate → resolve)

podinfo is a real GitHub app (github.com/stefanprodan/podinfo) deployed in `pfe`.

### 2a. Confirm podinfo is running
```bash
kubectl -n pfe get pods -l app=podinfo -o wide
# If missing, redeploy:
kubectl apply -f /tmp/podinfo-demo.yaml      # (repo copy: scripts/podinfo-demo.yaml)
kubectl -n pfe rollout status deployment/podinfo --timeout=90s
```

### 2b. Trigger a real CPU incident (burn CPU inside the pod)
```bash
POD=$(kubectl -n pfe get pod -l app=podinfo -o jsonpath='{.items[0].metadata.name}')
for i in 1 2 3 4; do
  kubectl -n pfe exec $POD -- sh -c 'nohup sh -c "while :; do :; done" >/dev/null 2>&1 &'
done
echo "stress started on $POD"

# Confirm CPU climbed (should approach its 500m limit)
kubectl -n pfe top pod $POD
```

### 2c. Ask HELIOS to analyze it NOW (instant, instead of waiting ~2 min for polling)
```bash
TOKEN=$(curl -s -X POST http://localhost:30808/api/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"admin-change-me"}' \
  | grep -o '"accessToken":"[^"]*"' | cut -d'"' -f4)
# (use 'admin' — ADMIN role, no 2FA. 'superadmin' now requires 2FA.)

curl -s -X POST http://localhost:30808/api/ai/predict \
  -H 'Content-Type: application/json' -H "Authorization: Bearer $TOKEN" \
  -d '{"namespace":"pfe","app":"podinfo","windowMinutes":5,"createIncident":true}'
echo
```
This call takes **~3 min** (the LLM runs). It returns JSON with `incidentCreated`,
`incidentId`, `incidentStatus`. `IN_PROGRESS` = auto-remediation path.

> If you get **403 Forbidden**, you used a non-admin account. The `/api/ai/**`
> endpoint requires `admin` or `superadmin`. Use `admin` for scripts (`superadmin` has 2FA).
> If you'd rather not call the API, just **wait** — the 2-min polling cycle will
> detect it on its own (it may take a full cycle since each app analysis is slow).

### 2d. Watch HELIOS handle it
```bash
PG=$(kubectl -n pfe get pod -l cnpg.io/instanceRole=primary -o jsonpath='{.items[0].metadata.name}')

# Latest podinfo incident
kubectl -n pfe exec $PG -- psql -U postgres pfe_db -c \
 "SELECT id,status,incident_type,severity FROM incident WHERE app='podinfo' ORDER BY id DESC LIMIT 3;"

# Full timeline of a given incident (replace 1133)
kubectl -n pfe exec $PG -- psql -U postgres pfe_db -c \
 "SELECT status,comment,created_at FROM incident_history WHERE incident_id=1133 ORDER BY id;"

# What action was actually executed
kubectl -n pfe exec $PG -- psql -U postgres pfe_db -c \
 "SELECT type,status,automation_mode,target_name FROM incident_action WHERE incident_id=1133;"
```
Expected timeline:
```
NEW         Incident created from AI prediction
IN_PROGRESS AI remediation path initiated
IN_PROGRESS Automated remediation executed: Deleted pod ... to trigger restart
RESOLVED    Auto-resolved: AI monitoring detects condition has cleared
```
The pod restart also kills the CPU loops, so it self-heals. Check the UI too:
**http://192.168.148.153:30880** → Incidents / Décisions IA / Remédiation.

### 2e. Cleanup
The auto-remediation restarts the pod and wipes the CPU loops, so usually nothing
to do. If an incident stayed `RECOMMENDATION_ONLY` (no restart), kill manually:
```bash
POD=$(kubectl -n pfe get pod -l app=podinfo -o jsonpath='{.items[0].metadata.name}')
kubectl -n pfe exec $POD -- pkill -9 sh 2>/dev/null || true
```

---

## 3. Other incident types you can trigger

### Memory pressure (model classifies MEMORY_LEAK)
```bash
# Quick check the model reacts to high memory (prediction only, no incident):
curl -s -X POST http://192.168.148.161:8000/predict -H 'Content-Type: application/json' \
  -d '{"namespace":"pfe","app":"podinfo","window_minutes":5,
       "feature_overrides":{"memory_working_set_bytes":1900000000,"metric_risk_score":0.88,"overall_risk_score":0.88}}' \
  | grep -oE '"incident":[a-z]+|"incident_type":"[^"]+"|"severity":"[^"]+"'
```

### Crash loop (real CrashLoopBackOff via a bad image)
```bash
kubectl -n pfe set image deployment/podinfo podinfo=stefanprodan/podinfo:does-not-exist
# watch it fail:
kubectl -n pfe get pods -l app=podinfo -w     # Ctrl-C to stop
# restore:
kubectl -n pfe set image deployment/podinfo podinfo=stefanprodan/podinfo:6.7.0
```

### Service outage (scale to zero)
```bash
kubectl -n pfe scale deployment/podinfo --replicas=0   # outage
kubectl -n pfe scale deployment/podinfo --replicas=1   # restore
```

### ⭐ Worker node down (HA — NODE_NOT_READY)  [validated 2026-06-04, incident #1252]
Stop a whole worker VM in VMware. HELIOS detects it automatically (kube-state-metrics
on master + the in-cluster Prometheus replica that survives), raises a **NODE_NOT_READY**
incident (HIGH, action `ESCALATE_NODE_OUTAGE_AND_REBALANCE` — it does NOT try to restart
pods on a dead node), and **auto-resolves it when the worker comes back** (~5 min window).

**Before you start — pick the CLEAN demo path (zero manual steps):** stop the worker that
does NOT host the Postgres **primary**, so the database keeps working on its own.
```bash
kubectl -n pfe get pods -l cnpg.io/cluster=postgres -L cnpg.io/instanceRole -o wide
# note which NODE holds the 'primary' → in VMware, stop the OTHER worker.
```
Then, in VMware Workstation, **power off** that worker (hard stop is fine).

```bash
# Watch the node go NotReady (~40s) then the incident appear (~1-3 min, leader poll):
watch -n5 'kubectl get nodes;
  PG=$(kubectl -n pfe get pod -l cnpg.io/instanceRole=primary -o jsonpath="{.items[0].metadata.name}");
  kubectl -n pfe exec $PG -- psql -U postgres pfe_db -t -c \
   "SELECT id,status,app,incident_type FROM incident WHERE incident_type='\''NODE_NOT_READY'\'' AND status NOT IN ('\''RESOLVED'\'','\''CLOSED'\'') ORDER BY id DESC;"'
```
Also visible in the UI: **http://192.168.148.153:30880** → Incidents.

**Timing:** if the down worker also hosted the postgres primary, CNPG **auto-fails-over in
~1 min** (the operator runs on master and survives — durable fix applied). The dominant
delay is the **scheduler leader handover (~4-5 min, lease TTL 300s)** when the dead worker
hosted the leader; otherwise the incident shows in ~1-2 min. Confirmed worker1 → #1285,
worker2 → #1252.

**Recovery:** power the worker back on. Postgres re-syncs to 2/2, and the NODE_NOT_READY
incident **auto-resolves** within ~5 min (the AI re-checks each polled app and sees the
node is Ready again). Confirmed: #1252/#1253 (worker2 test) auto-resolved; #1285 (worker1) too.

> ⚠️ **HOST RAM — read before doing this test (`MEMORY` host-ram note).** The VMware host
> (23.8 GB) can barely run all 5 VMs (aiCamunda alone = 12 GB). **Stopping** a worker is
> safe (frees RAM), but **restarting** it while everything else runs + postgres re-syncs
> can spike memory and *freeze the other worker* (a cascade we hit on 2026-06-04). For a
> live demo, either don't restart the worker live, or `vmrun stop` aiCamunda first for 12 GB
> of headroom and restart it after.

#### Fallback — if postgres failover ever stalls (e.g. operator was unavailable)
On a non-graceful node shutdown the old primary pod can stay `Terminating`. Normally the
operator-on-master promotes the standby automatically; if it ever stalls, fence the node:
```bash
kubectl -n pfe delete pod <PRIMARY_POD> --grace-period=0 --force
# or the K8s-native non-graceful-shutdown taint:
kubectl taint node <DEAD_WORKER> node.kubernetes.io/out-of-service=nodeshutdown:NoExecute
kubectl taint node <DEAD_WORKER> node.kubernetes.io/out-of-service-   # remove after recovery
```
> Durable fix in place so the **CNPG operator survives** a worker loss (runs on master):
> see `infra/k8s/worker-ha-config.yaml` → ConfigMap `node-failure-runbook`.

---

## 4. Checking incidents & email

### All active incidents
```bash
PG=$(kubectl -n pfe get pod -l cnpg.io/instanceRole=primary -o jsonpath='{.items[0].metadata.name}')
kubectl -n pfe exec $PG -- psql -U postgres pfe_db -c \
 "SELECT id,status,app,incident_type,severity,created_at FROM incident \
  WHERE status IN ('NEW','OPEN','IN_PROGRESS','WAITING_FOR_APPROVAL') ORDER BY id DESC;"
```

### Email
Emails go to **real Gmail** (`houssamsalhi273@gmail.com` + `houssamsalhi972@gmail.com`)
via the relay on .161. **Note:** emails fire only on the **human-approval path**
(incidents that go `WAITING_FOR_APPROVAL`), not on fully-automatic ones. A CPU stress
at confidence ~0.50 auto-executes → **no email**. To see an email, trigger an
incident that needs approval (lower-confidence or a high-impact action).

Check the relay delivered:
```bash
ssh kube@192.168.148.161 "echo kube | sudo -S journalctl -u helios-smtp-relay --since '10 min ago' --no-pager | grep -i 'Email sent'"
```

---

## 5. Exclude an app from monitoring
(backend is already excluded — example for any other app)
```bash
# 1. label opt-out (stops discovery + watcher)
kubectl -n pfe label deployment <app> helios.io/monitor=false --overwrite
# 2. if it's in the static list, remove it (only 'demo-app' is static now)
kubectl -n pfe set env deployment/backend AIOPS_POLLING_APPS=demo-app
```
Verify it disappears from the next polling cycle:
```bash
kubectl -n pfe logs -l app=backend --tail=200 | grep 'target(s) to analyze' | tail -1
```

---

## 6. Post-reboot recovery (run if detection returns all-zero / no incidents)

After a full VM power-cycle, three things break. Symptom: AI predictions show every
feature = 0 and no incidents are created.

### 6a. Calico CNI (new pods stuck `ContainerCreating`)
```bash
kubectl -n kube-system delete pod -l k8s-app=calico-node      # regenerates CNI token
# then delete any stuck pod so it reschedules
```

### 6b. VM160 clock skew + poisoned Prometheus TSDB  (the big one)
Symptom: AI features all 0; on .160 `count(container_cpu_usage_seconds_total)` is empty
and Prometheus logs show `err="out of bounds"` / "samples too old or too far into the future".
```bash
# jump to .160 via .161
ssh kube@192.168.148.161 "sshpass -p kube ssh -o StrictHostKeyChecking=no kube@192.168.148.160 '\
  echo kube | sudo -S systemctl stop prometheus; \
  echo kube | sudo -S rm -rf /var/lib/prometheus/metrics2; \
  echo kube | sudo -S mkdir -p /var/lib/prometheus/metrics2; \
  echo kube | sudo -S chown prometheus:prometheus /var/lib/prometheus/metrics2; \
  echo kube | sudo -S systemctl start prometheus'"
# verify (after ~30s) — should be a number, and podinfo CPU should be non-zero:
ssh kube@192.168.148.161 "curl -s 'http://192.168.148.160:9090/api/v1/query?query=count(container_cpu_usage_seconds_total)'"
```

### 6c. VM160 out of memory (Prometheus won't even start)
VM160 has only ~900 MB RAM. Free some by stopping Grafana (HELIOS doesn't need it):
```bash
ssh kube@192.168.148.161 "sshpass -p kube ssh -o StrictHostKeyChecking=no kube@192.168.148.160 '\
  echo kube | sudo -S systemctl stop grafana-server; \
  echo kube | sudo -S systemctl disable grafana-server'"
```

---

## 7. Quick troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| AI features all 0, no incidents | VM160 Prometheus broken after reboot | Section 6b |
| New pods stuck `ContainerCreating` | Calico CNI token expired | Section 6a |
| Prometheus won't start on .160 | VM160 out of RAM | Section 6c (stop Grafana) |
| `/api/ai/predict` → 403 | used non-admin account | login as `admin` (ADMIN, no 2FA); `superadmin` has 2FA |
| No email for an auto-resolved incident | by design (email only on approval path) | trigger an approval-path incident |
| Incident type looks wrong (e.g. POD_PENDING for CPU) | model labeling | CPU relabel rule is in inference.py; check `cpu_usage_cores` |
| Duplicate incidents for one app | — | prevented: DB-lease leader election + partial unique index `ux_incident_open_dedup` (Flyway V17) |
| NODE_NOT_READY won't persist after stopping a worker | down node hosted the Postgres primary → DB write-down (CNPG won't auto-promote a dead primary) | fence the node — see §3 "Worker node down" |

---

## 8. One-shot smoke test (paste on master)
```bash
PG=$(kubectl -n pfe get pod -l cnpg.io/instanceRole=primary -o jsonpath='{.items[0].metadata.name}')
echo "== pods =="; kubectl -n pfe get pods --no-headers | awk '{print $1,$3}'
echo "== AI =="; curl -s http://192.168.148.161:8000/health; echo
echo "== active incidents =="; kubectl -n pfe exec $PG -- psql -U postgres pfe_db -t -c \
 "SELECT id,status,app,incident_type FROM incident WHERE status IN ('NEW','OPEN','IN_PROGRESS','WAITING_FOR_APPROVAL') ORDER BY id DESC;"
echo "== polling targets (backend should be absent) =="; kubectl -n pfe logs -l app=backend --tail=300 2>/dev/null | grep 'target(s) to analyze' | tail -1
```
