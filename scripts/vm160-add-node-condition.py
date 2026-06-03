#!/usr/bin/env python3
# Add kube_node_status_condition to VM160's Prometheus federation match[] so the
# AIOps node_not_ready_count feature populates -> HELIOS can detect a cluster
# node/VM that has gone down (VM_DOWN). Run as root (writes /etc/prometheus).
import sys
P = "/etc/prometheus/prometheus.yml"
s = open(P).read()
if "kube_node_status_condition" in s:
    print("already present"); sys.exit(0)
marker = "        - kube_deployment_status_replicas_unavailable\n"
if marker not in s:
    print("ERROR: federation marker not found"); sys.exit(1)
s = s.replace(marker, marker + "        - kube_node_status_condition\n", 1)
open(P, "w").write(s)
print("added kube_node_status_condition to federation")
