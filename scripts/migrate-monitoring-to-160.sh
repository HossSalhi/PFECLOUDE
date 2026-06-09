#!/bin/bash
# Runs ON the master (has kubectl + ssh to .160). Migrates .160 Prometheus to direct scraping.
set -e
S160="ssh -o BatchMode=yes -o ConnectTimeout=8 -o StrictHostKeyChecking=no kube@192.168.148.160"

# 1) token for cAdvisor scraping
kubectl -n kube-system get secret prom-external-scraper-token -o jsonpath='{.data.token}' | base64 -d > /tmp/scraper-token
scp -o BatchMode=yes -o StrictHostKeyChecking=no /tmp/scraper-token kube@192.168.148.160:/tmp/scraper-token >/dev/null
rm -f /tmp/scraper-token

# 2) direct scrape jobs file
cat > /tmp/extra.yml <<'YML'

  - job_name: kube-state-metrics
    honor_labels: true
    static_configs:
      - targets: ['192.168.148.153:30809']
  - job_name: kubernetes-cadvisor
    scheme: https
    tls_config:
      insecure_skip_verify: true
    authorization:
      credentials_file: /etc/prometheus/scraper-token
    metrics_path: /metrics/cadvisor
    static_configs:
      - targets: ['192.168.148.153:10250','192.168.148.158:10250','192.168.148.155:10250']
  - job_name: backend-direct
    metrics_path: /actuator/prometheus
    static_configs:
      - targets: ['192.168.148.153:30808']
YML
scp -o BatchMode=yes -o StrictHostKeyChecking=no /tmp/extra.yml kube@192.168.148.160:/tmp/extra.yml >/dev/null
rm -f /tmp/extra.yml

# 3) on .160: backup, install token, remove federate job, append direct jobs, restart
$S160 "echo kube | sudo -S bash -c '
set -e
cp /etc/prometheus/prometheus.yml /etc/prometheus/prometheus.yml.bak.mig
install -m 644 /tmp/scraper-token /etc/prometheus/scraper-token && rm -f /tmp/scraper-token
sed -i \"/- job_name: cluster-prometheus-federate/,\\\$d\" /etc/prometheus/prometheus.yml
cat /tmp/extra.yml >> /etc/prometheus/prometheus.yml && rm -f /tmp/extra.yml
promtool check config /etc/prometheus/prometheus.yml >/dev/null 2>&1 && echo CONFIG_OK || echo CONFIG_BAD
systemctl restart prometheus
sleep 3
systemctl is-active prometheus
'"
echo "=== MIGRATION .160 APPLIQUÉE ==="
