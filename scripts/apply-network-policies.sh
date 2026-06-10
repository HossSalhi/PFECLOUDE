#!/bin/bash
# apply-network-policies.sh — apply corrected NetworkPolicies on the cluster
# Run from the Windows host: ssh kube@192.168.148.153 "bash -s" < scripts/apply-network-policies.sh
#
# What was broken: backend-egress blocked backend→postgres because CNPG pods
# needed the `cnpg.io/cluster: postgres` label (now verified present).
# This version adds explicit namespace labels and covers all required egress paths.

set -euo pipefail

echo "[1/3] Labelling pfe namespace (needed for cross-namespace NetworkPolicy selectors)"
kubectl label namespace pfe kubernetes.io/metadata.name=pfe --overwrite 2>/dev/null || true
kubectl label namespace cnpg-system kubernetes.io/metadata.name=cnpg-system --overwrite 2>/dev/null || true

echo "[2/3] Applying NetworkPolicies from infra/k8s/network-policy.yaml"
kubectl apply -f - << 'EOF'
---
# Allow external traffic (NodePort) to reach the frontend
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: allow-frontend-ingress
  namespace: pfe
spec:
  podSelector:
    matchLabels:
      app: frontend
  policyTypes:
    - Ingress
  ingress:
    - ports:
        - protocol: TCP
          port: 8080
---
# Allow frontend to reach backend
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: frontend-to-backend
  namespace: pfe
spec:
  podSelector:
    matchLabels:
      app: backend
  policyTypes:
    - Ingress
  ingress:
    - from:
        - podSelector:
            matchLabels:
              app: frontend
      ports:
        - protocol: TCP
          port: 8080
---
# Allow external (NodePort) traffic to reach the backend directly (AI service, monitoring, UI)
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: allow-external-backend
  namespace: pfe
spec:
  podSelector:
    matchLabels:
      app: backend
  policyTypes:
    - Ingress
  ingress:
    - ports:
        - protocol: TCP
          port: 8080
---
# Allow backend to reach PostgreSQL (CNPG pods) — FIX: cnpg.io/cluster label confirmed present
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: backend-to-postgres
  namespace: pfe
spec:
  podSelector:
    matchLabels:
      cnpg.io/cluster: postgres
  policyTypes:
    - Ingress
  ingress:
    - from:
        - podSelector:
            matchLabels:
              app: backend
      ports:
        - protocol: TCP
          port: 5432
    # CNPG replication between pods
    - from:
        - podSelector:
            matchLabels:
              cnpg.io/cluster: postgres
      ports:
        - protocol: TCP
          port: 5432
---
# Allow CNPG operator to manage postgres cluster
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: cnpg-controller-to-postgres
  namespace: pfe
spec:
  podSelector:
    matchLabels:
      cnpg.io/cluster: postgres
  policyTypes:
    - Ingress
  ingress:
    - from:
        - namespaceSelector:
            matchLabels:
              kubernetes.io/metadata.name: cnpg-system
      ports:
        - protocol: TCP
          port: 5432
        - protocol: TCP
          port: 9187
---
# Allow external Prometheus (.160) to scrape postgres exporter
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: allow-prometheus-scrape-postgres
  namespace: pfe
spec:
  podSelector:
    matchLabels:
      cnpg.io/cluster: postgres
  policyTypes:
    - Ingress
  ingress:
    - from:
        - ipBlock:
            cidr: 192.168.148.160/32
      ports:
        - protocol: TCP
          port: 9187
---
# Backend egress: postgres + AI VM + Camunda + monitoring + DNS + K8s API
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: backend-egress
  namespace: pfe
spec:
  podSelector:
    matchLabels:
      app: backend
  policyTypes:
    - Egress
  egress:
    # DNS
    - ports:
        - protocol: UDP
          port: 53
        - protocol: TCP
          port: 53
    # PostgreSQL (same namespace)
    - to:
        - podSelector:
            matchLabels:
              cnpg.io/cluster: postgres
      ports:
        - protocol: TCP
          port: 5432
    # External VMs: AI (8000), Camunda REST (8080), Camunda gRPC (26500),
    # Prometheus (9090), Grafana (3000), Loki (3100/3101), Ollama (11434),
    # K8s API (6443), HTTPS (443), backend NodePort (30808/30880)
    - to:
        - ipBlock:
            cidr: 192.168.148.0/24
      ports:
        - protocol: TCP
          port: 8000
        - protocol: TCP
          port: 8080
        - protocol: TCP
          port: 26500
        - protocol: TCP
          port: 9090
        - protocol: TCP
          port: 3000
        - protocol: TCP
          port: 3100
        - protocol: TCP
          port: 3101
        - protocol: TCP
          port: 11434
        - protocol: TCP
          port: 6443
        - protocol: TCP
          port: 443
        - protocol: TCP
          port: 30808
        - protocol: TCP
          port: 30880
EOF

echo "[3/3] Verifying policies"
kubectl get networkpolicies -n pfe
echo ""
echo "NetworkPolicies applied. Test connectivity:"
echo "  kubectl -n pfe exec deploy/backend -- curl -s http://postgres-rw:5432 2>&1 | head -1"
echo "  kubectl -n pfe exec deploy/backend -- curl -s http://192.168.148.161:8000/health"
