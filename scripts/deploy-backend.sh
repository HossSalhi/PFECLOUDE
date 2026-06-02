#!/usr/bin/env bash
# Deploy the backend JAR to the cluster (hostPath method, PERSISTENT path).
# The JAR lives at /opt/pfe-backend/app.jar on both workers (survives reboots,
# unlike the old /tmp/app.jar which was wiped on every reboot).
#
# Usage: ./scripts/deploy-backend.sh [path-to-jar]
#   default jar: backend/backend/target/backend-0.0.1-SNAPSHOT.jar
set -euo pipefail

JAR="${1:-backend/backend/target/backend-0.0.1-SNAPSHOT.jar}"
WORKERS=(192.168.148.158 192.168.148.155)
MASTER=192.168.148.153
DEST=/opt/pfe-backend/app.jar

[ -f "$JAR" ] || { echo "JAR not found: $JAR (build with: mvn -f backend/backend/pom.xml package -DskipTests)"; exit 1; }
echo "Deploying $JAR -> $DEST on workers"

for w in "${WORKERS[@]}"; do
  # ensure persistent dir exists (kube-owned so no sudo needed for the copy)
  ssh -o StrictHostKeyChecking=no "kube@$w" "echo kube | sudo -S mkdir -p /opt/pfe-backend && echo kube | sudo -S chown kube:kube /opt/pfe-backend"
  scp -o StrictHostKeyChecking=no "$JAR" "kube@$w:$DEST"
  echo "  -> $w OK"
done

echo "Rolling out backend..."
ssh -o StrictHostKeyChecking=no "kube@$MASTER" "kubectl -n pfe rollout restart deployment/backend && kubectl -n pfe rollout status deployment/backend --timeout=180s"
echo "Done."
