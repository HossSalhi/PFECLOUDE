#!/usr/bin/env bash
set -euo pipefail

WORKER1_IP="${WORKER1_IP:-192.168.148.158}"
WORKER2_IP="${WORKER2_IP:-192.168.148.155}"
MONITORING_VM_IP="${MONITORING_VM_IP:-192.168.148.160}"

sudo tee /usr/local/bin/pfe-configure-pod-routes.sh >/dev/null <<EOF
#!/usr/bin/env bash
set -euo pipefail

ip route replace 192.168.126.0/24 via ${WORKER2_IP} dev ens33
ip route replace 192.168.194.0/24 via ${WORKER1_IP} dev ens33
EOF
sudo chmod 0755 /usr/local/bin/pfe-configure-pod-routes.sh

sudo tee /etc/systemd/system/pfe-pod-routes.service >/dev/null <<'EOF'
[Unit]
Description=Static routes for Kubernetes pod CIDRs
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
ExecStart=/usr/local/bin/pfe-configure-pod-routes.sh

[Install]
WantedBy=multi-user.target
EOF

sudo tee /etc/systemd/system/pfe-pod-routes.timer >/dev/null <<'EOF'
[Unit]
Description=Periodically reconcile Kubernetes pod CIDR routes

[Timer]
OnBootSec=20s
OnUnitActiveSec=15s
Persistent=true
Unit=pfe-pod-routes.service

[Install]
WantedBy=timers.target
EOF

sudo systemctl daemon-reload
sudo systemctl disable pfe-pod-routes.service >/dev/null 2>&1 || true
sudo systemctl enable --now pfe-pod-routes.timer
sudo systemctl restart pfe-pod-routes.timer
sudo systemctl start pfe-pod-routes.service
sudo /usr/local/bin/pfe-configure-pod-routes.sh

for port in 8000 8088 26500 9600; do
  sudo ufw allow "${port}/tcp" || true
done

for cidr in 192.168.126.0/24 192.168.194.0/24; do
  for port in 8000 8088 26500 9600; do
    sudo ufw allow from "${cidr}" to any port "${port}" proto tcp || true
  done
done

sudo systemctl enable --now camunda8.service
sudo systemctl enable --now camunda8-rest-proxy.service
sudo systemctl enable --now pfe-ai-service.service

echo
echo "=== Route table ==="
ip route | egrep '192.168.126.0|192.168.194.0' || true
echo
echo "=== Listening ports ==="
ss -ltnp | egrep '(:8000|:8088|:26500|:9600)' || true
echo
echo "=== Service status ==="
systemctl --no-pager --full status pfe-ai-service camunda8 camunda8-rest-proxy || true
echo
echo "=== Monitoring reachability ==="
curl -s "http://${MONITORING_VM_IP}:9090/api/v1/query?query=up" | head -c 200 || true
echo
