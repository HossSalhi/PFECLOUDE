#!/usr/bin/env python3
# Add `honor_labels: true` to the kubernetes-pods scrape job so kube-state-metrics'
# own namespace/pod labels are preserved (otherwise Prometheus overwrites them with
# the KSM pod's identity, breaking all per-app KSM metrics: restarts, unavailable
# replicas, pending pods...). Then the AIOps queries {namespace="pfe",pod=~"app-.*"} work.
import subprocess, sys

NS, CM = "monitoring", "prometheus-incluster-config"
y = subprocess.run(
    ["kubectl", "-n", NS, "get", "cm", CM, "-o", "jsonpath={.data.prometheus\\.yml}"],
    capture_output=True, text=True).stdout

head = y.split("kubernetes-cadvisor")[0]
if "honor_labels: true" in head:
    print("already patched"); sys.exit(0)

marker = "  - job_name: kubernetes-pods\n    scrape_interval: 30s\n"
if marker not in y:
    print("ERROR: kubernetes-pods marker not found"); sys.exit(1)

y2 = y.replace(marker, marker + "    honor_labels: true\n", 1)
open("/tmp/prom.yml", "w").write(y2)
print("edited prometheus.yml (honor_labels added)")
