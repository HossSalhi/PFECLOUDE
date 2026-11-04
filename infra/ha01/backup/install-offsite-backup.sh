#!/usr/bin/env bash
# HELIOS HA-01 — installe la sauvegarde hors cluster sur .161 (script + known_hosts dédié + timer). Idempotent.
# Usage (depuis le poste d'administration) : ./install-offsite-backup.sh [--run-now]
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
S=(ssh -o BatchMode=yes -o ConnectTimeout=8 kube@192.168.148.161)
"${S[@]}" 'sudo -n mkdir -p /opt/helios-backups/ha01/offsite && sudo -n chown -R kube:kube /opt/helios-backups/ha01 && chmod 700 /opt/helios-backups/ha01
ssh-keyscan -t ed25519,rsa 192.168.148.153 192.168.148.155 192.168.148.158 2>/dev/null > /opt/helios-backups/ha01/known_hosts.new
test -s /opt/helios-backups/ha01/known_hosts.new && mv /opt/helios-backups/ha01/known_hosts.new /opt/helios-backups/ha01/known_hosts
wc -l /opt/helios-backups/ha01/known_hosts | awk "{print \"cles hote enregistrees:\", \$1}"'
cat "$HERE/helios-offsite-backup.sh"      | "${S[@]}" 'sudo -n tee /usr/local/bin/helios-offsite-backup.sh >/dev/null && sudo -n chmod 755 /usr/local/bin/helios-offsite-backup.sh'
cat "$HERE/helios-offsite-backup.service" | "${S[@]}" 'sudo -n tee /etc/systemd/system/helios-offsite-backup.service >/dev/null'
cat "$HERE/helios-offsite-backup.timer"   | "${S[@]}" 'sudo -n tee /etc/systemd/system/helios-offsite-backup.timer >/dev/null'
"${S[@]}" 'sudo -n systemctl daemon-reload && sudo -n systemctl enable --now helios-offsite-backup.timer && systemctl list-timers helios-offsite-backup.timer --no-pager | head -3'
if [ "${1:-}" = "--run-now" ]; then
  "${S[@]}" 'sudo -n systemctl start helios-offsite-backup.service; sleep 1; journalctl -u helios-offsite-backup.service --no-pager -n 25 --output=cat; ls -la /opt/helios-backups/ha01/offsite/pg /opt/helios-backups/ha01/offsite/etcd | tail -n +1'
fi
