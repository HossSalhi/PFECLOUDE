# Host Services (Legacy)

This directory documents the older workstation Docker-based runtime.

It is no longer the target architecture.

The project now targets an offline **5-VM** layout:

- `k8s-master`
- `k8s-worker1`
- `k8s-worker2`
- `monitoring`
- `ai-camunda`

MinIO should now live on the monitoring VM, and Camunda should live on the AI/Camunda VM.
Keep this directory only as historical reference while you migrate fully off Docker.
