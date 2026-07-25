# Infrastructure réseau / HA / monitoring — HELIOS

Architecture mise en place suite aux remarques de l'encadrant technique (LB, Ingress, VIP, monitoring).
Le cluster est un **HA 3-masters** (kubeadm, stacked etcd) : `k8s-master (.153)`, `k8s-worker1 (.158)`,
`k8s-worker2 (.155)` sont **tous des control-plane nodes** (les noms « worker » sont trompeurs).

## Exposition des applications (LoadBalancer + Ingress)
- **MetalLB** (`metallb-pool.yaml`) : donne un vrai `EXTERNAL-IP` aux services `type=LoadBalancer`
  (pool `192.168.148.200-210`). ⚠️ L2 non joignable de l'extérieur sur ce réseau **VMware NAT** (ARP filtré) —
  d'où le LB nginx ci-dessous pour l'accès réel.
- **ingress-nginx** (installé via le manifeste `cloud` upstream, 2 répliques HA, `externalTrafficPolicy: Cluster`)
  + objet **Ingress** (`ingress-helios.yaml`) : routage `helios.orange.tn` → frontend.
- **nginx sur la VM .160** (`nginx-apps-lb.conf` → `/etc/nginx/conf.d/`) : LB externe, point d'entrée des apps.
  **Accès : http://192.168.148.160/** (frontend + `/api/` → backend, via l'Ingress).

## HA du control-plane (LB externe, sans VIP)
Le VIP keepalived `.150` (ancien `controlPlaneEndpoint`) a été **supprimé** (demande encadrant : un VIP
n'est pas une notion Kubernetes). Remplacé par le pattern kubeadm **HA + LB externe** :
- **HAProxy sur .160** (`haproxy-controlplane.cfg` → append à `/etc/haproxy/haproxy.cfg`) : `:6443` mode tcp
  → équilibre vers les 3 apiservers, health-check `/healthz`, stats `:8404`.
- `.160` ajouté aux **SAN** des 3 certs apiserver (`certSANs` dans `kubeadm-config` = .150/.153/.155/.158/.160,
  puis `kubeadm init phase certs apiserver --config` + restart apiserver, un master à la fois).
- `controlPlaneEndpoint` + `kubelet.conf` des 3 masters → `192.168.148.160:6443`.
- **Bascule prouvée** : arrêt de l'apiserver d'un master → HAProxy le marque `DOWN`, le cluster survit
  (API ok via .160, 3 nodes Ready), quorum etcd 2/3 intact.
- ⚠️ HAProxy `.160` = LB **unique** (SPOF) ; HA totale = 2 LB + keepalived.

## Monitoring 100% hors cluster (demande encadrant)
La stack (Prometheus, Grafana, Loki, node-exporter) tourne en **systemd sur la VM .160**, hors cluster.
- **kube-state-metrics externalisé** sur .160 (`kube-state-metrics.service`, binaire extrait de l'image v2.10.1,
  jeton lecture-seule du SA `kube-system/kube-state-metrics` via `ksm.kubeconfig` pointant sur `.160:6443`).
  Prometheus le scrape en `127.0.0.1:8080`. Le KSM in-cluster a été supprimé.
- **metrics-server RESTE** dans le cluster : c'est l'API cœur K8s `metrics.k8s.io` (APIService agrégée) requise
  par le HPA — **non externalisable**, ce n'est pas du monitoring.
- Résultat : **0 pod de monitoring dans le cluster**.

## Accès
| Service | URL |
|---|---|
| Frontend HELIOS | http://192.168.148.160/ |
| API backend | http://192.168.148.160/api/... |
| Grafana | http://192.168.148.160:3000 |
| Prometheus | http://192.168.148.160:9090 |
| API Kubernetes | https://192.168.148.160:6443 (HAProxy → 3 masters) |
| Stats HAProxy | http://192.168.148.160:8404 |
