#!/usr/bin/env bash
# B1 — Build a REAL backend image with the JAR baked in (replaces the hostPath /opt/pfe-backend
# /app.jar hack), WITHOUT internet, by reusing the JRE base already present in the cluster.
#
# REQUIRES: Docker Desktop running on this PC (the cluster has no build tooling).
#
# After this, switch the deployment to the baked image (manual, see end) — then the backend
# no longer depends on a hostPath file and is immune to node reschedules / reboots.
#
# Usage: ./scripts/build-backend-image.sh [new-tag]   (default: pfe-backend:0.2.16)
set -euo pipefail
TAG="${1:-pfe-backend:0.2.16}"
BASE="pfe-backend:0.2.14"          # JRE base already in the cluster
WORKER=192.168.148.158
JAR=backend/backend/target/backend-0.0.1-SNAPSHOT.jar

docker version >/dev/null 2>&1 || { echo "Docker not running — start Docker Desktop first."; exit 1; }
[ -f "$JAR" ] || { echo "Build the JAR first: mvn -f backend/backend/pom.xml package -DskipTests"; exit 1; }

# 1) Obtain the JRE base from the cluster (no internet needed)
if ! docker image inspect "$BASE" >/dev/null 2>&1; then
  echo "Exporting base $BASE from $WORKER ..."
  ssh -o StrictHostKeyChecking=no "kube@$WORKER" "echo kube | sudo -S ctr -n k8s.io images export - $BASE" > /tmp/base.tar
  docker load < /tmp/base.tar
fi

# 2) Bake the fresh JAR into a new image
work=$(mktemp -d); cp "$JAR" "$work/app.jar"
printf 'FROM %s\nCOPY app.jar /app/app.jar\n' "$BASE" > "$work/Dockerfile"
docker build -t "$TAG" "$work"
rm -rf "$work"

# 3) Save + load onto the cluster
docker save "$TAG" > /tmp/newimg.tar
./scripts/load-image-to-cluster.sh /tmp/newimg.tar

cat <<NEXT

Image $TAG is now on all workers. To switch the deployment off the hostPath hack:

  ssh kube@192.168.148.153 'kubectl -n pfe set image deployment/backend backend=$TAG'
  # then remove the jar-override volume + its mount (edit once):
  ssh kube@192.168.148.153 'kubectl -n pfe edit deployment/backend'
  #   delete the 'jar-override' entry under volumes: and volumeMounts:
  ssh kube@192.168.148.153 'kubectl -n pfe rollout status deployment/backend'

NEXT
