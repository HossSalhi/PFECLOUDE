# Final Goal Runbook: Complete the 5-VM Local AIOps Platform

This runbook assumes the repo and Kubernetes side are already aligned to the 5-VM target:

- backend inside Kubernetes
- PostgreSQL/CNPG inside Kubernetes
- legacy in-cluster `ai-service` removed
- backend configured to call `192.168.148.161` for AI and Camunda

What is still required is the VM-side cutover on:

- `monitoring` `192.168.148.160`
- `ai-camunda` `192.168.148.161`

## 1. Monitoring VM (`192.168.148.160`)

Run these commands on the monitoring VM as user `kube`.

### 1.1 Prepare env vars

```bash
export MINIO_ROOT_USER=minioadmin
export MINIO_ROOT_PASSWORD='change-me-please'
export MINIO_BACKUP_ACCESS_KEY=cnpgbackup
export MINIO_BACKUP_SECRET_KEY='change-me-too'
export MINIO_BACKUP_BUCKET=cnpg-backups
export GRAFANA_ADMIN_PASSWORD='admin-change-me'
export WORKER1_IP=192.168.148.158
export WORKER2_IP=192.168.148.155
export CLUSTER_PROMETHEUS_HOST=192.168.148.158
export CLUSTER_PROMETHEUS_PORT=30090
export CLUSTER_LOKI_HOST=192.168.148.158
export CLUSTER_LOKI_PORT=31080
```

### 1.2 Run the installer

Copy the repo to the VM first if needed, then run:

```bash
chmod +x infra/vms/scripts/install-monitoring-vm.sh
./infra/vms/scripts/install-monitoring-vm.sh
```

### 1.3 Verify services

```bash
systemctl status prometheus --no-pager
systemctl status grafana-server --no-pager
systemctl status nginx --no-pager
systemctl status minio --no-pager
systemctl status alloy --no-pager
```

### 1.4 Verify telemetry from the monitoring VM itself

```bash
curl -s http://127.0.0.1:9090/api/v1/query?query=up
curl -s 'http://127.0.0.1:9090/api/v1/query?query=up{job="backend"}'
curl -s 'http://127.0.0.1:3100/loki/api/v1/labels'
```

Expected:

- Prometheus returns data
- `up{job="backend"}` is not empty
- Loki labels endpoint returns success

## 2. AI + Camunda VM (`192.168.148.161`)

Run these commands on the AI/Camunda VM as user `kube`.

### 2.1 Re-point AI telemetry to the monitoring VM

```bash
chmod +x infra/vms/scripts/reconfigure-ai-telemetry.sh
sudo MONITORING_VM_IP=192.168.148.160 ./infra/vms/scripts/reconfigure-ai-telemetry.sh
```

### 2.2 Verify AI and Camunda services

```bash
systemctl status pfe-ai-service --no-pager
systemctl status camunda8 --no-pager
systemctl status camunda8-rest-proxy --no-pager
```

### 2.3 Verify local health on the AI/Camunda VM

```bash
curl -s http://127.0.0.1:8000/health
curl -s http://127.0.0.1:8088/actuator/health || true
ss -ltnp | egrep '(:8000|:8088|:26500|:9600)'
```

### 2.4 Verify AI can read telemetry through the monitoring VM

```bash
curl -s http://192.168.148.160:9090/api/v1/query?query=up
curl -s 'http://192.168.148.160:9090/api/v1/query?query=up{job="backend"}'
curl -s http://192.168.148.160:3100/loki/api/v1/labels
```

Expected:

- AI VM can reach monitoring VM Prometheus and Loki
- backend job appears in monitoring VM Prometheus

## 3. Kubernetes-Side Validation From the Operator Machine

Run from the repo root on Windows:

```powershell
powershell -ExecutionPolicy Bypass -File .\infra\scripts\pretest-readiness.ps1 -Kubeconfig (Join-Path $env:TEMP 'pfe-kubeconfig-master')
```

Target result:

- `MonitoringVmBackendMetrics = PASS`
- `PodToExternalServices = PASS`
- `LegacyClusterAiService = PASS`
- `CNPGCluster = PASS`
- `CNPGBackup = PASS`

## 4. Final Integrated Validation

After the pre-test readiness passes:

```powershell
powershell -ExecutionPolicy Bypass -File .\infra\scripts\run-auto-remediation-final-test.ps1 -KubeconfigPath (Join-Path $env:TEMP 'pfe-kubeconfig-master')
```

If the workflow lands in approval mode:

```powershell
powershell -ExecutionPolicy Bypass -File .\infra\scripts\approve-remediation-task.ps1 -TaskId <TASK_ID> -WorkflowId <WORKFLOW_ID> -IncidentId <INCIDENT_ID>
powershell -ExecutionPolicy Bypass -File .\infra\scripts\check-remediation-status.ps1 -WorkflowId <WORKFLOW_ID> -IncidentId <INCIDENT_ID>
```

## 5. What “Done” Looks Like

The final goal is reached when all of these are true at the same time:

- monitoring VM Prometheus exposes backend metrics
- monitoring VM Loki endpoint is healthy
- AI VM reads telemetry from the monitoring VM, not directly from in-cluster NodePorts
- cluster pods can reach `192.168.148.160` and `192.168.148.161`
- backend can deploy BPMN to Camunda without timeout
- AI detects incidents from local telemetry
- Camunda executes remediation
- the incident is resolved without Internet, Docker Desktop, or external services
