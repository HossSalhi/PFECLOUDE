#!/usr/bin/env bash
# HELIOS Calico watchdog — reactive companion to helios-calico-refresh.service.
# The boot oneshot only fires on MASTER reboot; this watchdog (run every few minutes
# by a systemd timer) catches the case where a WORKER reboots on its own, or a CNI
# token expires at any time. It restarts calico-node ONLY when both:
#   (1) there are pods not yet Running (stuck), AND
#   (2) the tell-tale Calico CNI auth error is present in recent events.
# That keeps it a no-op when the cluster is healthy.
set -uo pipefail
KC=/etc/kubernetes/admin.conf
KUBECTL=/usr/bin/kubectl

# API reachable?
$KUBECTL --kubeconfig=$KC get nodes >/dev/null 2>&1 || exit 0

pending=$($KUBECTL --kubeconfig=$KC get pods -A --no-headers 2>/dev/null \
  | awk '$4!="Running" && $4!="Completed" && $4!="Succeeded" {c++} END{print c+0}')

if [ "${pending:-0}" -gt 0 ] && \
   $KUBECTL --kubeconfig=$KC get events -A 2>/dev/null | grep -q "ClusterInformation: connection is unauthorized"; then
    echo "[calico-watchdog] CNI auth error detected with $pending non-running pod(s) — restarting calico-node"
    $KUBECTL --kubeconfig=$KC -n kube-system delete pod -l k8s-app=calico-node --grace-period=20
    $KUBECTL --kubeconfig=$KC -n kube-system rollout status ds/calico-node --timeout=120s || true
else
    echo "[calico-watchdog] healthy (pending=$pending) — no action"
fi
