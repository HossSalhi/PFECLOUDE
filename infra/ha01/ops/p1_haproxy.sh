#!/usr/bin/env bash
# HELIOS HA-01 — C1 : applique la config HAProxy canonique (infra/ha01/haproxy/haproxy.cfg) sur UN LB.
#   sauvegarde .bak-ha01-<ts> -> validation `haproxy -c` -> installation -> reload GRACIEUX (aucune connexion coupée) -> vérifications
# Usage : ./p1_haproxy.sh apply 162 | rollback 162 | status 162        (toujours .162 d'abord, puis .160)
set -euo pipefail
ACTION="${1:?usage: $0 apply|rollback|status <160|162>}"
LB="${2:?usage: $0 apply|rollback|status <160|162>}"
HERE="$(cd "$(dirname "$0")" && pwd)"
S=(ssh -o BatchMode=yes -o ConnectTimeout=8 "kube@192.168.148.$LB")
TS=$(date -u +%Y%m%dT%H%M%SZ)
verify() {
  echo "-- service:"; "${S[@]}" 'systemctl is-active haproxy; ps -o pid,lstart,cmd -C haproxy | cut -c1-110 | tail -3'
  echo "-- serveurs (stats CSV):"
  curl -s -m 6 "http://192.168.148.$LB:8404/;csv" | python -I -c '
import sys,csv
rd=csv.reader(sys.stdin); hdr=next(rd); hdr[0]=hdr[0].lstrip("# "); ix={h:i for i,h in enumerate(hdr)}
for r in rd:
    if len(r)>=len(hdr) and r[ix["svname"]] not in ("FRONTEND","BACKEND"):
        print("   %-14s %-9s %-4s check=%s"%(r[ix["pxname"]],r[ix["svname"]],r[ix["status"]],r[ix["check_status"]]))'
  echo "-- trafic (depuis le poste):"
  for t in "80 http://192.168.148.$LB/ -H Host:helios.orange.tn" "443 -k --resolve helios.orange.tn:443:192.168.148.$LB https://helios.orange.tn/" "6443 -k https://192.168.148.$LB:6443/readyz"; do
    set -- $t; P=$1; shift
    printf "   :%s -> " "$P"; curl.exe -s -m 8 -o NUL -w '%{http_code} %{time_total}s\n' "$@" 2>/dev/null || curl -s -m 8 -o /dev/null -w '%{http_code} %{time_total}s\n' "$@"
  done
}
case "$ACTION" in
  status) verify ;;
  apply)
    cat "$HERE/../haproxy/haproxy.cfg" | "${S[@]}" "cat > /tmp/haproxy-ha01.cfg && sudo -n haproxy -c -f /tmp/haproxy-ha01.cfg"
    "${S[@]}" "sudo -n cp -p /etc/haproxy/haproxy.cfg /etc/haproxy/haproxy.cfg.bak-ha01-$TS && sudo -n install -m 644 -o root -g root /tmp/haproxy-ha01.cfg /etc/haproxy/haproxy.cfg && sudo -n systemctl reload haproxy && rm -f /tmp/haproxy-ha01.cfg"
    echo "reload demandé (sauvegarde: haproxy.cfg.bak-ha01-$TS)"; sleep 8; verify ;;
  rollback)
    "${S[@]}" 'B=$(ls -1t /etc/haproxy/haproxy.cfg.bak-ha01-* | tail -1); echo "restauration de $B (la plus ANCIENNE sauvegarde = config d origine)"; sudo -n haproxy -c -f "$B" && sudo -n cp -p "$B" /etc/haproxy/haproxy.cfg && sudo -n systemctl reload haproxy'
    sleep 8; verify ;;
  *) echo "usage"; exit 1 ;;
esac
