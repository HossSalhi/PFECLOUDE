# Kubernetes Manifests

This directory contains the deployment manifests for the `pfe` namespace.

## Included Components
- `backend` deployment, service, config, secret
- backend RBAC and PodDisruptionBudget
- CNPG PostgreSQL cluster and scheduled backups

The target runtime is now the offline 5-VM lab:

- Kubernetes cluster on `k8s-master`, `k8s-worker1`, and `k8s-worker2`
- Monitoring stack on a dedicated monitoring VM
- Camunda on a dedicated `ai-camunda` VM (the VM name is historical)

The AI service, Ollama and ChromaDB were removed from the repository on 2026-11-11 (safety archive: `helios-state\snapshots\ai-removal-20261106`).

## Image Tag Policy
- Do not use mutable `latest` tags in production-like environments.

## Apply (manual)
```bash
kubectl apply -k infra/k8s
```

## Basic Verification
```bash
kubectl -n pfe get deploy,pods,svc
kubectl -n pfe rollout status deploy/backend
kubectl -n pfe get cluster postgres
kubectl -n pfe get backup,scheduledbackup
```
