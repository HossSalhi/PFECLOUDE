# Manual Next Steps (Updated 2026-04-14)

> Legacy note: this file still describes the earlier workstation-hosted Docker and host-services flow.
> For the current offline 5-VM target architecture, use [infra/vms/README.md](/c:/Users/houss/IdeaProjects/PFE-Orange/backend/backend/infra/vms/README.md) first, then treat this file as historical context only.

This file reflects the current lab state after the Wave 1 to Wave 4 implementation work completed in the repo and on the cluster, plus the April 14 pre-test readiness recovery attempt.

## 1. Completed Automatically

1. VM and cluster hardening
   - Collected VM evidence under `investigation_outputs/2026-04-12-wave1-vm-hardening/`
   - Disabled unattended upgrade timers on `k8s-master`, `k8s-worker1`, and `k8s-worker2`
   - Added `infra/scripts/validate-stability.ps1` for repeatable stability checks

2. Host-side services
   - Added a separate host-services stack under `infra/host-services/`
   - MinIO is running on the workstation and healthy on `9000/9001`
   - Camunda orchestration is running on the workstation and healthy on `8088/26500`
   - Verified on 2026-04-14 that the host ports are listening locally again

3. Cluster networking and backend HA
   - Identified the real blocker for new pod scheduling: expired Calico CNI token on all three nodes
   - Refreshed `calico-node` one node at a time on `k8s-master`, `k8s-worker1`, and `k8s-worker2`
   - Restored backend HA placement: one backend pod now runs on each worker
   - Removed the duplicate backend PodDisruptionBudget that was causing ambiguous PDB warnings

4. Backend deployment and orchestration code
   - Implemented Camunda-aware orchestration, remediation executor, workflow state tracking, RBAC, PDB, anti-affinity, topology spread, and security tightening in the backend codebase
   - Built backend image `pfe-backend:0.1.1`
   - Imported `pfe-backend:0.1.1` into containerd on all 3 lab nodes
   - Rolled the cluster deployment to `pfe-backend:0.1.1`
   - Verified both running backend pods use the new image ID `sha256:8c25025fdc928284cb486eb785fc32900a3b065ac8feb7331153a0e8ded6e3d6`
   - Verified unauthenticated `POST /api/tasks/1/approve` now returns `401`
   - Fixed backend database connectivity by aligning the live `postgres` role password with the existing Kubernetes secrets
   - Applied the orchestration schema delta from `V2__remediation_orchestration.sql` directly to `pfe_db`
   - Verified authenticated `/api/incidents`, `/api/tasks`, `/api/workflows`, and `/api/test/db` now respond successfully

5. CNPG backup scaffolding
   - Added repo-managed CNPG backup resources under `infra/k8s/`
   - Applied `ScheduledBackup` for `pfe/postgres`
   - Added restore smoke-test manifest and helper scripts
   - Confirmed the first scheduled backup was created and attempted
   - Added `infra/scripts/pretest-readiness.ps1` to capture host reachability, CNPG state, backend placement, and backup readiness in one pass
   - On 2026-04-14, recovered the writable primary path to `postgres-1` and restored the `postgres-rw` service endpoint after a stuck failover
   - On 2026-04-14, refreshed the worker1 `calico-node` pod again so the recreated `postgres-2` pod could get a sandbox and continue startup

## 2. Current Blockers That Still Need Manual Action

1. Windows firewall rule creation
   - Root cause for failed CNPG backup: pods can reach the host only if the Windows firewall allows the Calico pod CIDRs, not just the VM subnet
   - The failed backup evidence points to pod-to-host timeout on `http://192.168.148.1:9000`
   - A direct test from `ai-service` on 2026-04-14 still timed out to:
     - `192.168.148.1:9000`
     - `192.168.148.1:8088`
     - `192.168.148.1:26500`
   - The current shell is not elevated, so this remains a host-admin step
   - Run `infra/scripts/configure-host-firewall.ps1` from an elevated PowerShell session
   - The script now includes all required source CIDRs:
     - `192.168.148.0/24`
     - `192.168.126.0/24`
     - `192.168.194.0/24`

2. CNPG failover is now also blocked by the same host reachability issue
   - On 2026-04-14 the cluster entered failover from `postgres-2` to `postgres-1`
   - The writable path has since been restored:
     - `currentPrimary=postgres-1`
     - `postgres-rw` now points to `192.168.126.9:5432`
     - `postgres-1` is no longer in recovery
   - However, the cluster is still not fully healthy:
     - `phase=Waiting for the instances to become active`
     - `postgres-2` is being reintroduced as a replica but is not yet ready
   - The remaining readiness blocker still traces back to host reachability:
     - `postgres-2` is trying to archive old WAL files to `http://192.168.148.1:9000`
     - scheduled backup remains `walArchivingFailing`
   - Until pod-to-host reachability is fixed, CNPG will remain degraded even though writes work again

3. CNPG backup and restore gate
   - After the firewall fix, re-run `infra/scripts/pretest-readiness.ps1`
   - Confirm these CNPG checks all pass together:
     - `postgres-rw` still has an endpoint
     - `postgres-1` remains writable
     - `postgres-2` becomes `1/1 Ready`
     - `kubectl -n pfe get cluster postgres` shows `Ready=True`
   - Then confirm at least one `Backup` resource reaches `Completed`
   - Run the restore smoke test with `infra/scripts/run-cnpg-restore-smoke.ps1`
   - Keep `REMEDIATION_AUTOMATION_ENABLED=false` until this gate passes

4. VMware resource resizing
   - VM sizing still has to be changed manually in VMware Workstation
   - Target sizing:
     - `k8s-master = 4 vCPU / 6 GiB RAM`
     - `k8s-worker1 = 3 vCPU / 6 GiB RAM`
     - `k8s-worker2 = 3 vCPU / 6 GiB RAM`

5. Stability gate validation
   - Run `infra/scripts/validate-stability.ps1` for the full 30-minute window
   - Keep a longer multi-day observation window for the graduation demo environment
   - Do not start this 30-minute window until the firewall fix is done and CNPG is back to a writable healthy state

6. Camunda runtime enablement
   - Backend code and host-side Camunda are ready, but `CAMUNDA_ENABLED` remains `false`
   - Do not enable it until the backup/firewall gate passes
   - After that, enable `CAMUNDA_ENABLED=true` in `backend-config`, redeploy backend, and test the approval-only workflow path before any automation is allowed

7. Flyway follow-up
   - The live database schema is now correct, but Flyway did not create `flyway_schema_history` automatically during startup
   - Future schema changes should not assume Flyway is currently enforcing migration history in the lab until this is investigated

## 3. Recommended Manual Sequence

1. Run elevated PowerShell and execute:
   - `.\infra\scripts\configure-host-firewall.ps1`

2. Re-run the consolidated readiness check:
   - `.\infra\scripts\pretest-readiness.ps1`
   - Confirm these checks turn green:
     - `FirewallRules`
     - `PodToHost`
     - `CNPGCluster`
     - `PostgresRWEndpoint`
     - `PostgresRecoveryState`
     - `CNPGBackup`

3. Re-run backup validation:
   - `kubectl -n pfe get scheduledbackup,backup`
   - `.\infra\scripts\run-cnpg-restore-smoke.ps1 -Kubeconfig investigation_outputs/2026-04-11-manual-steps-automation/kubeconfig-master`

4. Run the full stability window:
   - `.\infra\scripts\validate-stability.ps1 -Kubeconfig investigation_outputs/2026-04-11-manual-steps-automation/kubeconfig-master -ObservationMinutes 30`

5. After backup success, enable manual Camunda orchestration only:
   - set `CAMUNDA_ENABLED=true`
   - keep `REMEDIATION_AUTOMATION_ENABLED=false`
   - redeploy backend
   - test incident creation plus approval flow end-to-end

## 4. Quick Reality Check

- Backend HA is restored and currently healthy on both workers
- Host-side MinIO and Camunda are up
- Host-side firewall rules are still missing for the VM subnet and Calico pod CIDRs
- CNPG writes are back on `postgres-1`, but the cluster is still degraded while `postgres-2` rejoins
- CNPG scheduled backup and WAL archiving are still blocked by missing pod-to-host reachability to MinIO
- Automatic remediation is still intentionally disabled
