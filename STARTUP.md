# HELIOS AIOps — Startup Runbook

How to bring the platform up cleanly from a cold host.
**Read the golden rules first or you will lose hours.**

---

## Golden rules

1. **Never run Docker Desktop and VMware VMs at the same time.**
   Docker Desktop's WSL2 backend grabs CPU virtualization extensions; VMware
   loses access and the VMs crash with `Error: The operation was canceled`.
   Always use the order: build (Docker on) → quit Docker → start VMs.

2. **`*.vmx.lck` after a crash blocks startup.** The scripts clean these
   automatically (`start-aiops.ps1`). If you boot VMs manually and see lock
   errors, run:
   ```powershell
   Get-ChildItem "$env:USERPROFILE\Documents\Virtual Machines" -Recurse -Filter "*.lck" -Directory |
       Remove-Item -Recurse -Force
   ```

3. **Don't bump `replicas` above 1 for backend** unless you've verified the
   workers have ≥1.5 GB available — backend pods request 384 MB each and
   spike higher under Spring Boot warmup. With 1024–1280 MB workers, 2 replicas
   makes scheduling fragile. See `infra/k8s/backend-hpa.yaml` (HPA caps at 2).

4. **Frontend `docker build` is now self-contained** (multi-stage). Older
   versions of the Dockerfile only copied `dist/`, leading to silently stale
   builds. Use `scripts/build-images.ps1` so you can't miss this.

5. **SuperAdmin login uses 2FA** after the first activation. The TOTP secret is
   in the user record. If 2FA pesters you on every restart, check
   `app.security.two-fa-auto-setup-enabled` — should be `false` in steady state.

---

## Daily flow (cluster already deployed)

```powershell
# Bring everything up
.\scripts\start-aiops.ps1

# ... do your work ...

# Shut down cleanly when done (especially before launching Docker)
.\scripts\stop-aiops.ps1
```

`start-aiops.ps1` checks Docker is off, cleans stale locks, boots 5 VMs,
waits for 3 nodes Ready (default 5 min timeout), and prints the URLs.

---

## Full build + deploy cycle (after code changes)

```powershell
# 1. Stop VMs (build script does this anyway, but explicit is safer)
.\scripts\stop-aiops.ps1

# 2. Build Docker images (auto-starts Docker, auto-quits at end)
.\scripts\build-images.ps1 `
    -BackendTag pfe-backend:0.2.12 `
    -FrontendTag pfe-frontend:1.0.6

# 3. Bring cluster back up
.\scripts\start-aiops.ps1

# 4. Roll out the new images + apply manifests
.\scripts\deploy-images.ps1 `
    -BackendTag pfe-backend:0.2.12 `
    -FrontendTag pfe-frontend:1.0.6
```

The whole cycle: ~25–30 min on a tight-RAM host. Tags must be unique each
build (don't reuse) so K8s actually pulls the new image.

---

## What lives where

| Component | Location | Why |
|---|---|---|
| Backend (Spring Boot) | K8s cluster (workers 158/155) | Stateless, scales horizontally |
| Frontend (Angular) | K8s cluster | Stateless nginx |
| Postgres (CNPG) | K8s cluster | Has K8s-native operator for HA |
| AI service (XGBoost/LLM) | VM 161 (`k8s-aiCamunda`) | Big model files; outside cluster so AI survives cluster failures |
| Camunda Zeebe | VM 161 | Persistent broker state; needs to survive cluster restarts |
| Ollama (local LLM) | VM 161 | GPU/CPU-heavy, dedicated VM |
| Prometheus (central) | VM 160 (`k8s-monitoring`) | Watches everything, fires alerts even when cluster is down |
| Grafana, Loki, Alertmanager | VM 160 | Same survival reason as Prometheus |
| Prometheus (in-cluster) | K8s cluster (slim) | Provides K8s-native discovery for pod/container metrics; federated by VM 160 |

---

## Access URLs

| Service | URL |
|---|---|
| HELIOS UI (frontend) | http://192.168.148.153:30880 |
| Backend health | http://192.168.148.153:30880/actuator/health |
| Backend API docs | http://192.168.148.153:30880/swagger-ui.html (if enabled) |
| Grafana | http://192.168.148.160:3000 |
| Prometheus (central) | http://192.168.148.160:9090 |
| AI service status | http://192.168.148.161:8000/monitor/status |
| AI service docs | http://192.168.148.161:8000/docs |
| Camunda Zeebe | http://192.168.148.161:8080/v2/topology |

Default credentials: `superadmin` / `superadmin-change-me` (change in prod).

---

## Troubleshooting

### VMs won't boot — `Error: The operation was canceled`
Docker Desktop is holding virtualization. Run:
```powershell
& 'C:\Program Files\Docker\Docker\Docker Desktop.exe' --quit
wsl --shutdown
Start-Sleep -Seconds 5
.\scripts\start-aiops.ps1
```

### Backend pods stuck Pending — `Insufficient memory`
Workers don't have RAM for the pod. Either:
- Confirm worker VMs have ≥1280 MB RAM allocation (`.vmx` `memsize`)
- Or lower memory request in `backend-deployment.yaml` (currently 384Mi)
- Or check HPA didn't scale to >2 (current cap is 2, see `backend-hpa.yaml`)

### Frontend deployed but I see the old UI
1. Hard refresh: `Ctrl+Shift+R` (Chrome/Firefox/Edge)
2. Or open private/incognito tab on http://192.168.148.153:30880
3. Verify the served HTML title: should contain `HELIOS`
   ```powershell
   (Invoke-WebRequest http://192.168.148.153:30880/ -UseBasicParsing).Content | Select-String '<title>'
   ```

### SuperAdmin can't login — `requires2fa: true` but I never set up TOTP
Reset the 2FA bypass directly in Postgres:
```powershell
ssh kube@192.168.148.153 "kubectl exec -n pfe postgres-4 -- psql -U postgres -d pfe_db -c \"UPDATE app_user SET last_2fa_verified_at=NOW(), force_2fa_on_next_login=false WHERE username='superadmin'\""
```
Then login normally. The 14-day grace window will be active.

### AI service says `last_apps_analyzed = 0`
Either:
- In-cluster Prometheus is down (no pod metrics for AI to read)
- Or pod template labels are missing `aiops.io/monitor=true` (fixed in
  `backend-deployment.yaml` and `ai-service-deployment.yaml`)
Check: `kubectl get pods -A -l "aiops.io/monitor=true"` — should be non-empty.

---

## VM RAM allocations (tested working on 24 GB host)

| VM | RAM | vCPU | Role |
|---|---:|---:|---|
| k8s-master | 1792 MB | 2 | control plane |
| k8s-worker1 | 1536 MB | 1 | hosts backend + postgres |
| k8s-worker2 | 1280 MB | 1 | hosts frontend + chromadb |
| k8s-monitoring | 1488 MB | 1 | Prometheus/Grafana/Loki/Alertmanager |
| k8s-aiCamunda | 6136 MB | 3 | AI service + Camunda + Ollama |
| **Total** | **~12.2 GB** | **8 vCPU** | leaves ~12 GB for host + Windows |

Going below worker RAM 1280 MB causes kubelet PLEG timeouts (NotReady).
