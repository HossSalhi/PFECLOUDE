# Offline 5-VM Runtime

This project now targets an offline lab with **5 VMs**.

## VM Topology

| VM | IP | Main contents | Main role |
| --- | --- | --- | --- |
| `k8s-master` | `192.168.148.153` | Kubernetes control plane, `etcd`, scheduler, controller-manager, kubelet, container runtime | Orchestrates the cluster and manages worker scheduling |
| `k8s-worker1` | `192.168.148.158` | Kubernetes worker services and application pods | Runs part of the backend and database workloads |
| `k8s-worker2` | `192.168.148.155` | Kubernetes worker services and application pods | Runs the second part of the backend and database workloads |
| `monitoring` | `192.168.148.160` | Prometheus, Grafana, Loki API gateway, Alloy or Promtail, MinIO | Observability hub for all VMs and Kubernetes, plus CNPG backup object storage |
| `ai-camunda` | `192.168.148.161` | AI inference service and Camunda | Incident analysis, classification, recommendation, and workflow orchestration |

## Role of Each VM

- `k8s-master` coordinates the cluster and decides where workloads run.
- `k8s-worker1` and `k8s-worker2` keep the application available by sharing workload execution across two nodes.
- `monitoring` watches the entire platform, stores metrics and logs, and can also host the MinIO endpoint used by CNPG backups.
- `ai-camunda` receives telemetry context from the backend, analyzes incidents, recommends actions, and runs the remediation workflow through Camunda.

## AI Role

The AI service is responsible for:

- analyzing logs and metrics
- detecting incidents
- classifying whether an event is a real incident or just noise
- assigning severity
- recommending a remediation action
- returning that recommendation to the Spring Boot backend so Camunda can orchestrate the next step

## Recommended Sizing

These are practical starting points for a laptop-based offline lab:

| VM | Minimum practical sizing | More comfortable sizing |
| --- | --- | --- |
| `k8s-master` | `2 vCPU / 4 GiB RAM` | `4 vCPU / 6 GiB RAM` |
| `k8s-worker1` | `2 vCPU / 4 GiB RAM` | `3 vCPU / 6 GiB RAM` |
| `k8s-worker2` | `2 vCPU / 4 GiB RAM` | `3 vCPU / 6 GiB RAM` |
| `monitoring` | `2 vCPU / 4 GiB RAM` | `4 vCPU / 8 GiB RAM` |
| `ai-camunda` | `4 vCPU / 8 GiB RAM` | `6-8 vCPU / 12-16 GiB RAM` |

If you want to minimize the Kubernetes and monitoring nodes, keep those near the minimum profile and allocate the largest share of CPU and RAM to `ai-camunda`.

## Repository Assumptions

- Kubernetes manifests under `infra/k8s` now assume the backend and PostgreSQL stay inside the cluster.
- The backend now points to the external AI/Camunda VM by default.
- CNPG backups now target MinIO on the monitoring VM by default.
- The old workstation Docker files are kept only as legacy references and are not the target runtime anymore.

## Important Ports

- Monitoring VM:
  - `3000` Grafana
  - `3100` Loki
  - `9090` Prometheus
  - `9000` MinIO API
- AI/Camunda VM:
  - `8000` AI service
  - `8088` Camunda REST
  - `26500` Camunda gRPC

## Monitoring VM Telemetry Design

- The monitoring VM is the intended telemetry hub for the full offline lab.
- VM metrics:
  - Prometheus on `192.168.148.160:9090` scrapes node exporters on all 5 VMs
  - it also scrapes the dedicated `ai-camunda` VM directly
- Kubernetes metrics:
  - Prometheus on the monitoring VM federates selected metrics from the in-cluster Prometheus NodePort exposed by [cluster-monitoring-nodeports.yaml](/c:/Users/houss/IdeaProjects/PFE-Orange/backend/backend/infra/k8s/cluster-monitoring-nodeports.yaml)
- VM logs:
  - Alloy or Promtail agents on each VM push logs to `http://192.168.148.160:3100`
- Kubernetes logs:
  - the monitoring VM exposes a Loki-compatible gateway on `192.168.148.160:3100` which proxies the in-cluster Loki NodePort so the AI service can use a single local endpoint

## AI Telemetry Wiring

- The AI VM should query the monitoring VM directly:
  - `http://192.168.148.160:9090`
  - `http://192.168.148.160:3100`
- The monitoring VM depends on these repo-managed cluster services:
  - Prometheus NodePort on `30090`
  - Loki gateway NodePort on `31080`
- To switch an existing AI VM to the monitoring VM endpoints, run:
  - [reconfigure-ai-telemetry.sh](/c:/Users/houss/IdeaProjects/PFE-Orange/backend/backend/infra/vms/scripts/reconfigure-ai-telemetry.sh)

## What Still Must Be Done Manually

- Provision the two new VMs: `monitoring` and `ai-camunda`
- Install and start the monitoring stack on `192.168.148.160`
- Install and start the AI service and Camunda on `192.168.148.161`
- Re-run the monitoring VM installer so Prometheus federation and the Loki gateway are configured
- Re-run the AI VM telemetry reconfiguration so it points to `192.168.148.160`
- Remove the legacy in-cluster `ai-service` after the dedicated AI VM is confirmed healthy
- Copy `infra/vm-config/monitoring-minio.env.example` to `infra/vm-config/monitoring-minio.env` and fill the real MinIO credentials before syncing the CNPG backup secret
- Re-apply the updated Kubernetes manifests after the external services are reachable
