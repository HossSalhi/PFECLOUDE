#!/usr/bin/env bash
# B3 — Load a `docker save`d image tar into containerd (k8s.io namespace) on every worker.
# The cluster has NO internet, so images can't be pulled from registries. Build or pull
# an image on a machine that HAS internet, `docker save` it to a tar, then run this to
# make it available to Kubernetes (imagePullPolicy: IfNotPresent) on all workers.
#
# Usage: ./scripts/load-image-to-cluster.sh <image.tar>
set -euo pipefail
TAR="${1:?usage: load-image-to-cluster.sh <image.tar>}"
WORKERS=(192.168.148.158 192.168.148.155)
[ -f "$TAR" ] || { echo "tar not found: $TAR"; exit 1; }

for w in "${WORKERS[@]}"; do
  echo "== $w =="
  scp -o StrictHostKeyChecking=no "$TAR" "kube@$w:/tmp/_img.tar"
  ssh -o StrictHostKeyChecking=no "kube@$w" "echo kube | sudo -S ctr -n k8s.io images import /tmp/_img.tar && rm -f /tmp/_img.tar"
  echo "  -> loaded on $w"
done
echo "Done. Image available on all workers (use imagePullPolicy: IfNotPresent)."
