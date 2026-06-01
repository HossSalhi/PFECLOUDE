# pfe-incident-platform

## Target Runtime
The target architecture is now an offline **5-VM lab**, not a workstation Docker setup:

- `k8s-master` orchestrates the cluster
- `k8s-worker1` and `k8s-worker2` run the Kubernetes workloads
- a dedicated `monitoring` VM runs Prometheus, Grafana, Loki, Alloy or Promtail, and MinIO for CNPG backups
- a dedicated `ai-camunda` VM runs the AI inference service and Camunda

The workstation is now just the operator machine. It is no longer the target runtime for MinIO or Camunda.
Legacy Docker files remain in the repo only as reference material.

## Kubernetes Deployment
- Manifests and apply order: [infra/k8s/README.md](/c:/Users/houss/IdeaProjects/PFE-Orange/backend/backend/infra/k8s/README.md)
- Deploy helper script: [infra/scripts/deploy-aiops.ps1](/c:/Users/houss/IdeaProjects/PFE-Orange/backend/backend/infra/scripts/deploy-aiops.ps1)
- Validation snapshot script: [infra/scripts/validate-aiops.ps1](/c:/Users/houss/IdeaProjects/PFE-Orange/backend/backend/infra/scripts/validate-aiops.ps1)
- VM topology and role breakdown: [infra/vms/README.md](/c:/Users/houss/IdeaProjects/PFE-Orange/backend/backend/infra/vms/README.md)

## Manual-Only Steps
- Cluster/VM work that must be executed manually: [MANUAL_NEXT_STEPS.md](/c:/Users/houss/IdeaProjects/PFE-Orange/backend/backend/MANUAL_NEXT_STEPS.md)
