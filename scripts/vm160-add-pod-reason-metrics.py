#!/usr/bin/env python3
# Federate KSM container/pod "reason" metrics to VM160's central Prometheus so HELIOS
# can name the REAL root cause (ImagePullBackOff, ErrImagePull, CrashLoopBackOff,
# CreateContainerConfigError, OOMKilled, Evicted, ...) in the AI "Why" explanation --
# accurately and durably, for any incident. The cluster Prometheus (.158:30090) already
# scrapes these from kube-state-metrics; only the federation match[] was filtering them.
# Run as root (writes /etc/prometheus). Idempotent.
import sys

P = "/etc/prometheus/prometheus.yml"
s = open(P).read()
marker = "        - kube_node_status_condition\n"
adds = [
    "        - kube_pod_container_status_waiting_reason\n",
    "        - kube_pod_container_status_last_terminated_reason\n",
    "        - kube_pod_status_reason\n",
]
if marker not in s:
    print("ERROR: federation marker (kube_node_status_condition) not found")
    sys.exit(1)
to_add = "".join(a for a in adds if a not in s)
if not to_add:
    print("already present")
    sys.exit(0)
s = s.replace(marker, marker + to_add, 1)
open(P, "w").write(s)
print("added to federation match[]:", [a.strip() for a in adds if a in to_add])
