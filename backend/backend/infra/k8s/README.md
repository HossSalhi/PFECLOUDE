# Kubernetes Manifests

This directory contains the deployment manifests for the `pfe` namespace.

## Included Components
- `backend` deployment, service, config, secret
- backend RBAC and PodDisruptionBudget
- CNPG PostgreSQL cluster and scheduled backups

The target runtime is now the offline 5-VM lab:

- Kubernetes cluster on `k8s-master`, `k8s-worker1`, and `k8s-worker2`
- Monitoring stack on a dedicated monitoring VM
- AI service and Camunda on a dedicated `ai-camunda` VM

Because of that, the default `kustomization.yaml` no longer deploys the AI service inside Kubernetes.
The `ai-service` manifests are kept in the repo only as a legacy fallback, and the monitoring kustomization no longer scrapes that legacy deployment by default.

## Image Tag Policy
- Do not use mutable `latest` tags in production-like environments.

## Apply (manual)
```bash
kubectl apply -k infra/k8s
```

## Optional Legacy Pieces
If you still want to run the AI service inside Kubernetes for a temporary lab run:
```bash
kubectl -n pfe set image deployment/ai-service ai-service=pfe-ai-service:<tag>
kubectl apply -f infra/k8s/ai-service-configmap.yaml
kubectl apply -f infra/k8s/ai-service-service.yaml
kubectl apply -f infra/k8s/ai-service-deployment.yaml
```

## Basic Verification
```bash
kubectl -n pfe get deploy,pods,svc
kubectl -n pfe rollout status deploy/backend
kubectl -n pfe get cluster postgres
kubectl -n pfe get backup,scheduledbackup
```
