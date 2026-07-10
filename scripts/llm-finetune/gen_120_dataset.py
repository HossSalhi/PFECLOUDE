#!/usr/bin/env python3
"""Dataset 10K+ pour les 120 types HELIOS. Chaque type -> (ACTION, diagnostic, explication) ecrits
par MOI (expert SRE). Generation de scenarios varies/realistes par type pour le volume.
-> sft_120.jsonl  (incident varie -> DIAGNOSTIC + ACTION= + EXPLICATION)
"""
import json, os, random, itertools
random.seed(42)
D=os.path.dirname(os.path.abspath(__file__))
DSYS="You are the automatic decision engine of a Kubernetes self-healing system. Output: DIAGNOSTIC: <cause> | ACTION=<ACTION_NAME> | EXPLICATION: <how to fix>."

# pools pour varier les scenarios
APP=["payment-svc","auth-api","checkout","frontend","orders","cart","user-service","inventory",
     "gateway","notification","billing","search","catalog","recommendation","analytics","web","api-server-app"]
NS=["default","production","staging","backend","data","monitoring","payments","web","api","team-a"]
NODE=["worker-1","worker-2","worker-3","node-a","node-b","gke-node-4","eks-node-2","master-1"]
DB=["postgres","mysql","mongodb","redis","cassandra","mariadb"]
def N(): return random.choice([2,3,4,5,8,10,12,20,30,50])

# 120 types : "TYPE": (ACTION, diagnostic, explication, [3 gabarits de symptome])
C={
# A. Pods
"POD_CRASH_LOOP":("ROLLBACK","the pod starts then crashes repeatedly, usually a bad image or config regression","roll back to the last healthy revision after checking the crash logs",
 ["{app} in {ns} is in CrashLoopBackOff, restarting every {n}s with exit code 1","pod {app}-{id} crashes right after start; events show Back-off restarting failed container","after deploying {app} all its pods enter CrashLoopBackOff and never get Ready"]),
"POD_OOM_KILLED":("TIGHTEN_MEMORY_LIMITS","the container exceeds its memory limit and is OOMKilled by the kernel","raise the memory limit or fix the leak, then roll the faulty revision back",
 ["container in {app} ({ns}) was OOMKilled, exit code 137, memory over its limit","{app} pods keep getting OOMKilled under load in {ns}","events show OOMKilled for {app}; memory.working_set exceeded the limit"]),
"POD_CPU_STRESS":("SCALE_UP","one or more pods saturate their CPU causing throttling and slow responses","scale horizontally (HPA) or raise CPU limits and purge the hot process",
 ["{app} in {ns} is CPU-throttled, cpu usage pinned at the limit","pods of {app} at 100% CPU, requests timing out","cpu throttling seconds rising for {app} in {ns}"]),
"POD_MEMORY_LEAK":("RESTART_POD","the application memory grows unbounded over time until it is killed","restart the pod to reclaim memory, then fix the leak in the app",
 ["{app} memory grows steadily until OOM every few hours in {ns}","slow memory leak in {app}, RSS climbing with no plateau","{app} needs periodic restarts, memory never released"]),
"POD_PENDING_INSUFFICIENT_RESOURCES":("SCALE_UP","the pod cannot be scheduled because no node has enough cpu/memory","add nodes/capacity or lower the resource requests",
 ["{app} stuck Pending: 0/{n} nodes available, insufficient cpu","pod {app}-{id} unschedulable, insufficient memory on all nodes","{app} Pending in {ns}, FailedScheduling insufficient resources"]),
"POD_INIT_CONTAINER_FAILURE":("FIX_CONFIG","an init container fails so the main container never starts","fix the init container command/config and redeploy",
 ["{app} stuck in Init:CrashLoopBackOff in {ns}","init container of {app} fails, main container never starts","pod {app}-{id} Init:Error, init step returns non-zero"]),
"POD_CONFIG_ERROR":("FIX_CONFIG","a wrong env var or config value makes the container fail at startup","correct the ConfigMap/env and roll back to the valid revision",
 ["{app} fails with CreateContainerConfigError in {ns}","{app} crashes reading an invalid env var/config value","bad configuration injected into {app}, container won't start"]),
"POD_IMAGE_PULL_BACKOFF":("UPDATE_IMAGE","the image cannot be pulled (wrong name/tag or missing credentials)","fix the image reference or the pull secret",
 ["{app} in ImagePullBackOff in {ns}, pull access denied","pod {app}-{id} cannot pull image, manifest unknown","ImagePullBackOff for {app}, wrong tag or registry auth"]),
"POD_ERR_IMAGE_PULL":("UPDATE_IMAGE","the registry/image reference is invalid so the pull errors out","verify the registry and image, fix the reference, redeploy",
 ["ErrImagePull for {app} in {ns}, image not found","{app} cannot pull image from the private registry","repository does not exist for {app} image"]),
"POD_STUCK_TERMINATING":("FORCE_DELETE_POD","the pod is stuck Terminating because a finalizer or node is unresponsive","force-delete the pod (remove the finalizer)",
 ["{app}-{id} stuck Terminating for {n} minutes in {ns}","pod won't delete, remains Terminating, finalizer pending","{app} stuck Terminating after node {node} went down"]),
"POD_EVICTED":("FREE_DISK_SPACE","the kubelet evicted the pod due to node resource pressure","free node resources and recreate the evicted pod",
 ["{app}-{id} Evicted in {ns}, node under disk pressure","many Evicted pods for {app} on {node}","pod evicted, the node was low on ephemeral storage"]),
"POD_FAILED_PROBE":("FIX_CONFIG","a readiness/liveness probe is misconfigured or too strict","fix the probe path/thresholds and redeploy",
 ["{app} probe failing, readiness never succeeds in {ns}","liveness/readiness probe wrong for {app}, pod not Ready","probe misconfig on {app}, endpoint returns non-200"]),
"POD_READINESS_FAILURE":("FIX_CONFIG","the pod never becomes Ready because a dependency or readiness check fails","check dependencies and fix the readiness probe, then redeploy",
 ["{app} pods Running but never Ready in {ns}","readiness failing for {app}, dependency not reachable","{app} stuck 0/1 Ready, readiness check failing"]),
"POD_LIVENESS_FAILURE":("FIX_CONFIG","the liveness probe fails so the kubelet keeps killing the container","fix the app or liveness probe and roll back if needed",
 ["{app} killed by liveness probe repeatedly in {ns}","liveness failing for {app}, container restarted by kubelet","{app} restart loop caused by a failing liveness probe"]),
"POD_CONTAINER_RUNTIME_ERROR":("RESTART_CONTAINERD","the container runtime on the node errors when creating/starting containers","restart containerd/cri-o on the affected node",
 ["{app} fails with RunContainerError on {node}","containerd errors creating containers on {node}","CreateContainerError across pods on node {node}"]),
"POD_RESTART_STORM":("ROLLBACK","many pods restart in a burst after a regression","identify the cause in logs/events and roll back if a regression",
 ["restart storm: {app} pods restarting dozens of times in {ns}","sudden burst of restarts across {app} after a deploy","{app} restart count spiking cluster-wide"]),
"POD_SECURITY_CONTEXT_ERROR":("FIX_CONFIG","the pod's securityContext denies a required capability/UID","fix the securityContext (runAsUser/fsGroup/caps) and redeploy",
 ["{app} fails: container has runAsNonRoot and image runs as root","permission denied for {app} due to securityContext in {ns}","{app} cannot start, seteuid/operation not permitted"]),
"POD_SECRET_MISSING":("RECREATE_SECRET","a referenced Secret does not exist so the pod cannot start","recreate the missing Secret",
 ["{app} fails: secret \"{app}-creds\" not found in {ns}","pod {app}-{id} CreateContainerConfigError, missing secret","{app} references a Secret that was deleted"]),
"POD_CONFIGMAP_MISSING":("RECREATE_CONFIGMAP","a referenced ConfigMap does not exist so the pod cannot start","recreate the missing ConfigMap",
 ["{app} fails: configmap \"{app}-config\" not found in {ns}","{app} won't start, referenced ConfigMap missing","pod stuck, ConfigMap for {app} was removed"]),
"POD_TOO_MANY_RESTARTS":("ROLLBACK","a pod accumulates a high restart count from a persistent fault","investigate logs/events and roll back to a stable revision",
 ["{app}-{id} has restarted {n} times in {ns}","high restart count on {app}, persistent failure","{app} RESTARTS column keeps climbing"]),
# B. Deployments
"DEPLOYMENT_UNAVAILABLE":("ROLLBACK","the deployment has no available replicas after a bad rollout","rolling-restart or roll back to the last working version",
 ["deployment {app} shows 0/{n} available in {ns}","{app} unavailable, all replicas failing after deploy","Deployment {app} has minimum availability not met"]),
"DEPLOYMENT_ROLLOUT_STUCK":("ROLLBACK","the rollout is blocked on a bad image or failing new pods","fix the image/config or force the rollout, roll back if needed",
 ["rollout of {app} stuck, new pods never Ready in {ns}","{app} rollout not progressing for {n} minutes","deployment {app} stuck waiting for new ReplicaSet"]),
"DEPLOYMENT_ROLLBACK_REQUIRED":("ROLLBACK","the current version is faulty and must be reverted","roll back to the last stable revision",
 ["latest {app} release is broken in {ns}, revert needed","{app} regression after deploy, rollback required","new {app} version failing, must roll back"]),
"REPLICA_SATURATION":("SCALE_DOWN","more replicas are requested than the cluster can schedule","reduce replicas to the schedulable count",
 ["{app} requests {n} replicas but only half can schedule in {ns}","replica saturation for {app}, pods Pending","too many replicas for {app}, cluster can't fit them"]),
"HPA_SCALING_FAILURE":("FIX_CONFIG","the HPA can't scale because metrics-server/metrics are unavailable","restore the metrics source (metrics-server) so HPA works",
 ["HPA for {app} shows unknown/unknown targets in {ns}","{app} HPA failing: no metrics from metrics-server","autoscaler for {app} cannot fetch metrics"]),
"HPA_OVER_SCALING":("SCALE_DOWN","the HPA scales up too aggressively and over-provisions","tune the HPA thresholds and scale down",
 ["HPA scaled {app} to {n} replicas needlessly in {ns}","{app} over-scaled by HPA, wasted resources","aggressive HPA scaling for {app}"]),
"HPA_UNDER_SCALING":("SCALE_UP","the HPA does not scale up enough under load","lower the HPA target thresholds to scale up sooner",
 ["{app} overloaded but HPA won't add replicas in {ns}","HPA under-scaling {app}, latency rising","{app} needs more replicas, HPA thresholds too high"]),
"DEPLOYMENT_VERSION_MISMATCH":("ROLLBACK","replicas run mixed versions after a partial rollout","unify the version by rolling back/forward to one revision",
 ["{app} has pods on two different versions in {ns}","version skew across {app} replicas","half of {app} on old image, half on new"]),
"DEPLOYMENT_PROGRESS_DEADLINE_EXCEEDED":("ROLLBACK","the rollout exceeded its progressDeadlineSeconds","fix the deployment then relaunch, roll back if needed",
 ["{app} ProgressDeadlineExceeded in {ns}","deployment {app} failed to progress in time","{app} rollout timed out, deadline exceeded"]),
# C. Services & Reseau
"SERVICE_UNAVAILABLE":("CHECK_ENDPOINTS","the service returns errors because its backend pods are unavailable","verify backend pods and remove any blocking policy",
 ["service {app} returns connection refused in {ns}","{app} service unavailable, backends down","calls to {app} service failing"]),
"SERVICE_NO_ENDPOINTS":("FIX_SERVICE_SELECTOR","the service has no endpoints because its selector matches no pod","fix the Service selector/labels to match the pods",
 ["service {app} has 0 endpoints in {ns}","{app} service selector matches no running pod","kubectl shows no endpoints for {app}"]),
"NETWORK_PARTITION":("FIX_NETWORK_POLICY","connectivity is cut between pods/nodes (often a NetworkPolicy)","restore connectivity by fixing/removing the blocking policy",
 ["pods in {ns} cannot reach {app}, network partition","split-brain connectivity between {node} and the rest","{app} isolated, ingress+egress blocked"]),
"NETWORK_PACKET_LOSS":("FIX_CONFIG","packets are dropped on the node network (MTU/driver)","fix the node network/MTU configuration",
 ["high packet loss reaching {app} in {ns}","intermittent drops between pods on {node}","retransmits spiking, packet loss to {app}"]),
"NETWORK_HIGH_LATENCY":("INVESTIGATE_LOGS","network latency between services is abnormally high","analyze traffic and optimize the network path",
 ["p99 network latency to {app} very high in {ns}","slow pod-to-pod latency across {node}","{app} calls slow, network RTT elevated"]),
"NETWORK_POLICY_BLOCKING":("FIX_NETWORK_POLICY","a NetworkPolicy blocks legitimate traffic","correct or remove the blocking NetworkPolicy",
 ["{app} traffic blocked by a NetworkPolicy in {ns}","new NetworkPolicy cut access to {app}","egress to {app} denied by policy"]),
"DNS_FAILURE":("FIX_DNS","in-cluster DNS resolution fails (CoreDNS or a policy blocking :53)","restart CoreDNS or remove the policy blocking port 53",
 ["{app} cannot resolve service names in {ns}","DNS lookups failing cluster-wide, CoreDNS issue","nslookup fails inside {app} pods"]),
"DNS_SLOW_RESPONSE":("SCALE_UP","CoreDNS is overloaded causing slow lookups","add CoreDNS replicas / enable caching",
 ["DNS resolution slow for {app} in {ns}","CoreDNS latency high, lookups timing out","slow name resolution across the cluster"]),
"INGRESS_UNAVAILABLE":("RESTART_SERVICE","the ingress controller is down so external routes fail","restart the ingress controller",
 ["ingress returns 502 for {app} in {ns}","ingress-nginx controller pod down","external access to {app} broken, ingress unavailable"]),
"INGRESS_5XX_ERRORS":("ROLLBACK","the ingress returns 5xx after a bad backend release","roll back the faulty backend / shed traffic",
 ["ingress 5xx rate spiking for {app} in {ns}","502/503 through ingress after {app} deploy","many 5xx at the ingress for {app}"]),
"LOAD_BALANCER_UNAVAILABLE":("CHECK_ENDPOINTS","the LoadBalancer service has no healthy backends","verify the selector/endpoints and readiness",
 ["LoadBalancer for {app} not routing in {ns}","{app} LB has no healthy targets","external LB for {app} returns errors"]),
"LOAD_BALANCER_OVERLOAD":("SCALE_UP","the load balancer backends are overloaded","scale up backends to absorb the load",
 ["{app} LB saturated under traffic in {ns}","backends behind LB overloaded for {app}","{app} LB dropping connections under load"]),
"TCP_CONNECTION_FAILURE":("CHECK_ENDPOINTS","TCP connections to the service fail (no endpoints/port)","verify endpoints, ports and readiness",
 ["connection refused on {app}:{n} in {ns}","TCP connect to {app} times out","{app} port not accepting connections"]),
# D. Noeuds
"NODE_NOT_READY":("RESTART_NODE_VM","a node is NotReady (kubelet/lease stale), its pods are stranded","restart the node VM (or drain it) to restore it",
 ["node {node} is NotReady, its pods stuck","{node} went NotReady, kubelet lease expired","node {node} NotReady for {n} minutes"]),
"NODE_HIGH_CPU":("DRAIN_NODE","a node's CPU is saturated degrading all its pods","drain the node to reschedule, or add capacity",
 ["node {node} CPU pinned at 100%, pods slow","host CPU saturation on {node}","{node} overloaded, all pods degraded"]),
"NODE_HIGH_MEMORY":("DRAIN_NODE","a node's memory is exhausted risking evictions","drain the node and rebalance workloads",
 ["node {node} memory near 100%, evictions imminent","host memory pressure on {node}","{node} out of memory, pods at risk"]),
"NODE_DISK_PRESSURE":("FREE_DISK_SPACE","the node has DiskPressure and starts evicting pods","free disk (image gc, logs) on the node",
 ["node {node} has DiskPressure, evicting pods","disk pressure taint on {node}","{node} low on disk, pods evicted"]),
"NODE_MEMORY_PRESSURE":("DRAIN_NODE","the node has MemoryPressure and evicts pods","drain and rebalance to relieve pressure",
 ["node {node} MemoryPressure condition true","memory pressure evictions on {node}","{node} under memory pressure"]),
"NODE_PID_PRESSURE":("RESTART_NODE_VM","the node is out of PIDs, new processes fail","restart the node VM to clear the PID exhaustion",
 ["node {node} PIDPressure, cannot fork","PID exhaustion on {node}","{node} out of process IDs"]),
"NODE_DISK_FULL":("FREE_DISK_SPACE","the node disk is full, kubelet and pods fail","free disk space (prune images/logs) on the node",
 ["node {node} disk 100% full","no space left on device on {node}","{node} filesystem full, kubelet failing"]),
"NODE_NETWORK_DOWN":("RESTART_NODE_VM","the node lost network connectivity","restart the node VM / fix its networking",
 ["node {node} network down, unreachable","{node} lost its network interface","no connectivity to/from {node}"]),
"NODE_REBOOT":("INVESTIGATE_LOGS","a node rebooted unexpectedly; verify it rejoined healthy","check logs and confirm the node recovered its pods",
 ["node {node} rebooted unexpectedly","{node} restarted, pods rescheduled","unexpected reboot of {node}"]),
"NODE_KERNEL_PANIC":("REBOOT_NODE","the node kernel panicked and is hung","reboot the node and investigate the kernel logs",
 ["kernel panic on {node}, host hung","{node} kernel panic, unresponsive","hardware/kernel panic detected on {node}"]),
"NODE_TIME_DRIFT":("FIX_CONFIG","the node clock drifted, breaking TLS/tokens","fix NTP/chrony to correct the clock",
 ["clock drift on {node}, TLS handshakes failing","{node} time out of sync by {n}s","NTP not synced on {node}"]),
"NODE_KUBELET_DOWN":("RESTART_KUBELET","the kubelet service is down so the node stops reporting","restart the kubelet on the node",
 ["kubelet down on {node}, node NotReady","{node} kubelet service stopped","kubelet not running on {node}"]),
"NODE_CONTAINERD_DOWN":("RESTART_CONTAINERD","the container runtime is down so no containers run","restart containerd on the node",
 ["containerd down on {node}, containers not starting","{node} runtime stopped","cri runtime unavailable on {node}"]),
"NODE_FILESYSTEM_READONLY":("REBOOT_NODE","the node filesystem went read-only (disk error)","reboot the node and check the disk",
 ["{node} root filesystem is read-only","disk errors forced {node} fs read-only","cannot write on {node}, fs ro"]),
"NODE_UNREACHABLE":("RESTART_NODE_VM","the node is unreachable from the control plane","restart the node VM to restore it",
 ["node {node} unreachable, taint node.kubernetes.io/unreachable","control plane lost contact with {node}","{node} unreachable for {n} minutes"]),
# E. Control Plane
"API_SERVER_DOWN":("ESCALATE_RESTART_APISERVER","the kube-apiserver is down, the cluster API is unavailable","escalate: restart the apiserver static pod, never restart app pods",
 ["kube-apiserver not responding, kubectl fails","API server down on the control plane","cluster API unreachable, apiserver crashed"]),
"API_SERVER_HIGH_LATENCY":("ESCALATE_TUNE_ETCD","apiserver requests are slow, usually etcd is the bottleneck","escalate: tune etcd disk/network, scale apiserver",
 ["apiserver p99 latency very high","slow kubectl responses, apiserver latency","API server requests timing out"]),
"ETCD_DOWN":("ESCALATE_RESTORE_ETCD","an etcd member is down threatening cluster state","escalate: restore the etcd member (never restart app pods)",
 ["an etcd member is down on the control plane","etcd pod not running","etcd unavailable, writes failing"]),
"ETCD_QUORUM_LOST":("ESCALATE_RESTORE_QUORUM","etcd lost quorum, the cluster is read-only/broken","escalate: restore at least 2 healthy masters to regain quorum",
 ["etcd quorum lost, cluster read-only","only 1/3 etcd members healthy","etcd cannot elect a leader, no quorum"]),
"ETCD_HIGH_LATENCY":("ESCALATE_TUNE_ETCD","etcd write latency is high, degrading the control plane","escalate: optimize etcd disk (fsync) and network",
 ["etcd fsync latency very high","slow etcd writes, control plane sluggish","etcd disk latency alerts firing"]),
"ETCD_DATABASE_CORRUPTION":("ESCALATE_RESTORE_ETCD","the etcd database is corrupted","escalate: restore etcd from the latest backup",
 ["etcd reports database corruption","etcd db checksum mismatch","etcd data corrupted, member won't start"]),
"CONTROLLER_MANAGER_DOWN":("ESCALATE","the controller-manager is down so controllers stop reconciling","escalate: restart the controller-manager static pod",
 ["kube-controller-manager down","controllers not reconciling, cm crashed","controller-manager pod not running"]),
"SCHEDULER_DOWN":("ESCALATE","the scheduler is down so new pods stay Pending","escalate: restart the scheduler static pod",
 ["kube-scheduler down, pods stuck Pending","no pods being scheduled, scheduler crashed","scheduler pod not running"]),
"CERTIFICATE_EXPIRED":("RENEW_CERTIFICATE","a cluster certificate expired, breaking auth/TLS","renew the expired certificate and restart affected components",
 ["kubelet/apiserver cert expired, x509 errors","certificate expired, TLS handshakes failing","expired cert blocking control-plane comms"]),
"CERTIFICATE_NEAR_EXPIRATION":("RENEW_CERTIFICATE","a certificate will expire soon","renew it proactively before it expires",
 ["cluster cert expires in {n} days","certificate near expiration warning","renew cert soon to avoid outage"]),
"LEADER_ELECTION_FAILURE":("ESCALATE","a control-plane component cannot acquire its leader lease","escalate: investigate the lease/etcd and restart the component",
 ["controller-manager leader election failing","no leader elected for scheduler","leader lease stuck, component idle"]),
"ADMISSION_WEBHOOK_FAILURE":("FIX_CONFIG","a failing admission webhook blocks all API writes","fix or remove the broken webhook configuration",
 ["admission webhook failing, all creates blocked","validating webhook down, apply rejected","webhook timeout blocking deployments"]),
# F. Stockage
"PVC_PENDING":("FIX_STORAGECLASS","the PVC stays Pending because no volume is provisioned","fix the StorageClass / rebind to the default SC",
 ["PVC for {app} stuck Pending in {ns}","no volume provisioned for {app} PVC","{app} PVC Pending, StorageClass issue"]),
"PVC_LOST":("ESCALATE","the PVC/PV binding was lost, data at risk","escalate: restore from snapshot/backup",
 ["PVC for {app} shows Lost in {ns}","{app} lost its persistent volume claim","PVC Lost, pod cannot mount data"]),
"PV_LOST":("ESCALATE","the underlying PersistentVolume was lost","escalate: restore the volume from backup/snapshot",
 ["PV backing {app} is Lost","persistent volume for {app} disappeared","PV Released/Lost, data unavailable"]),
"STORAGE_CLASS_ERROR":("FIX_STORAGECLASS","the StorageClass is misconfigured or missing a provisioner","fix the StorageClass/provisioner",
 ["StorageClass provisioner error for {app}","no default StorageClass, PVCs fail","StorageClass misconfigured in {ns}"]),
"STORAGE_CAPACITY_EXCEEDED":("EXTEND_STORAGE","the volume is full and writes fail","extend the volume / free space",
 ["{app} volume full in {ns}, writes failing","storage capacity exceeded for {app}","PV out of space for {app}"]),
"VOLUME_MOUNT_FAILURE":("FIX_VOLUME_MOUNT","the pod cannot mount its volume (perm/driver)","fix permissions/CSI driver and remount",
 ["MountVolume.SetUp failed for {app} in {ns}","{app} cannot mount its PVC","volume mount error on {app}, permission denied"]),
"READ_ONLY_VOLUME":("FIX_VOLUME_MOUNT","the volume is mounted read-only, the app cannot write","fix the mount options / underlying disk and remount",
 ["{app} volume mounted read-only in {ns}","cannot write, {app} volume is ro","read-only filesystem for {app} data"]),
"CSI_DRIVER_FAILURE":("RESTART_SERVICE","the CSI driver is failing so volumes can't attach","restart the CSI driver pods",
 ["CSI driver pods failing, attach errors","volume attach failing, CSI driver down","{app} PVC cannot attach, CSI error"]),
"SNAPSHOT_FAILURE":("INVESTIGATE_LOGS","a volume snapshot failed","check the snapshot controller logs and retry",
 ["volume snapshot for {app} failed","snapshot controller error in {ns}","backup snapshot failed for {app}"]),
# G. Base de donnees
"DATABASE_CONNECTIVITY":("CHECK_ENDPOINTS","apps cannot reach the database service","verify the DB service endpoints and network",
 ["{app} cannot connect to {db} in {ns}","database connectivity lost for {app}","{db} unreachable from {app} pods"]),
"DB_CONNECTION_REFUSED":("RESTART_SERVICE","the DB refuses connections (down or not listening)","verify the DB is up and restart it if needed",
 ["connection refused to {db} in {ns}","{db} refusing connections for {app}","{app} gets ECONNREFUSED from {db}"]),
"DB_CONNECTION_POOL_EXHAUSTION":("TUNE_DB","all DB connections are used up, new requests block","raise the pool/max_connections or restart the app to reset it",
 ["{app} connection pool to {db} exhausted","too many open connections, {db} pool full","{app} blocked waiting for a DB connection"]),
"DB_SLOW_QUERIES":("TUNE_DB","slow queries degrade the database","optimize/index the slow queries",
 ["{db} slow queries piling up for {app}","high query latency on {db} in {ns}","{db} slow query log growing"]),
"DB_HIGH_CPU":("SCALE_UP","the database CPU is saturated","scale the DB / optimize load",
 ["{db} CPU at 100% in {ns}","database {db} CPU-bound, queries slow","{db} overloaded, high CPU"]),
"DB_HIGH_MEMORY":("TIGHTEN_MEMORY_LIMITS","the database memory is exhausted","raise limits or tune memory usage",
 ["{db} memory near limit in {ns}","database {db} high memory, OOM risk","{db} memory pressure"]),
"DB_DISK_FULL":("FREE_DISK_SPACE","the database disk is full and writes fail","free/extend the DB storage",
 ["{db} disk full in {ns}, writes failing","database {db} out of storage","{db} PV full, cannot write"]),
"DB_REPLICATION_LAG":("TUNE_DB","the replica lags far behind the primary","tune replication / reduce write load",
 ["{db} replica lag {n}s in {ns}","replication lag high on {db}","{db} standby far behind primary"]),
"DB_PRIMARY_DOWN":("PROMOTE_DB_REPLICA","the primary database is down","promote a healthy replica to primary (failover)",
 ["{db} primary is down in {ns}","database primary crashed, failover needed","{db} master unavailable"]),
"DB_DEADLOCK":("TUNE_DB_TIMEOUTS","transactions deadlock, blocking each other","tune lock timeouts / retry logic",
 ["{db} deadlocks detected for {app}","transaction deadlock on {db}","{db} lock wait / deadlock errors"]),
"DB_TRANSACTION_TIMEOUT":("TUNE_DB_TIMEOUTS","transactions time out under contention","tune statement/lock timeouts and reduce contention",
 ["{db} transaction timeouts for {app}","queries timing out on {db}","{db} statement timeout errors"]),
"DB_TOO_MANY_CONNECTIONS":("TUNE_DB","the DB hit its max connections limit","raise max_connections or add a pooler",
 ["{db} FATAL: too many connections","{db} max connections reached in {ns}","{app} rejected: {db} connection limit hit"]),
# H. Performance
"LATENCY_DEGRADATION":("ROLLBACK","end-to-end latency degraded after a change","identify the slow component and roll back the faulty revision",
 ["{app} latency degraded after deploy in {ns}","p99 latency doubled for {app}","{app} response times climbing"]),
"HIGH_ERROR_RATE_5XX":("ROLLBACK","server-side 5xx errors spiked after a release","roll back the faulty release and shed traffic",
 ["{app} 5xx error rate spiking in {ns}","500/503 errors surge for {app} after deploy","high server error rate on {app}"]),
"HIGH_ERROR_RATE_4XX":("FIX_CONFIG","client 4xx errors spiked (bad route/config change)","fix the config/route causing the 4xx",
 ["{app} 4xx rate high in {ns}","surge of 400/404 for {app}","many client errors on {app} after change"]),
"HIGH_RESPONSE_TIME":("SCALE_UP","response time is high due to insufficient capacity","scale up to absorb the load",
 ["{app} response time high under load in {ns}","{app} slow, needs more replicas","high response time on {app}"]),
"REQUEST_TIMEOUT":("SCALE_UP","requests time out due to overload or a slow dependency","scale up / investigate the slow dependency",
 ["{app} requests timing out in {ns}","gateway timeouts calling {app}","{app} 504 timeouts under load"]),
"THROUGHPUT_DROP":("INVESTIGATE_LOGS","throughput dropped sharply","investigate the bottleneck (logs/metrics)",
 ["{app} throughput dropped {n}% in {ns}","sudden RPS drop for {app}","{app} processing far fewer requests"]),
"TRAFFIC_SPIKE":("SCALE_UP","a sudden traffic spike overloads the app","scale up to absorb the spike",
 ["traffic spike hitting {app} in {ns}","{app} RPS surged {n}x","sudden load spike on {app}"]),
"MEMORY_LEAK_APPLICATION":("RESTART_POD","the application leaks memory over time","restart to reclaim memory and fix the app leak",
 ["{app} memory leaking over hours in {ns}","{app} heap growing unbounded","application memory leak in {app}"]),
"THREAD_POOL_EXHAUSTION":("SCALE_UP","the app's thread/worker pool is exhausted","scale up or raise the pool size",
 ["{app} thread pool exhausted in {ns}","{app} no free workers, requests queued","worker pool full on {app}"]),
"CACHE_FAILURE":("RESTART_SERVICE","the cache layer failed, degrading performance","restart/repair the cache service",
 ["cache ({db}) down for {app} in {ns}","cache misses 100%, cache failed","{app} slow, cache layer unavailable"]),
# I. Securite
"SECRET_EXPOSED":("RECREATE_SECRET","a secret was exposed and must be rotated","rotate/recreate the secret and audit access",
 ["a Secret for {app} was exposed in {ns}","credentials leaked, rotate {app} secret","exposed API key in {app}"]),
"UNAUTHORIZED_ACCESS":("FIX_RBAC_PERMISSIONS","unauthorized access attempts detected","tighten RBAC and investigate the source",
 ["unauthorized API access to {ns}","forbidden calls from an unknown identity","suspicious unauthorized access on {app}"]),
"RBAC_MISCONFIGURATION":("FIX_RBAC_PERMISSIONS","RBAC is misconfigured (too much or too little access)","correct the Role/RoleBinding",
 ["RBAC misconfig grants excess access in {ns}","{app} serviceaccount over-privileged","forbidden due to wrong RBAC for {app}"]),
"PRIVILEGED_CONTAINER":("ISOLATE_POD","a privileged container is running, a security risk","isolate the pod and fix its securityContext",
 ["privileged container detected in {ns}","{app} runs privileged: true","pod with host access flagged in {ns}"]),
"MALICIOUS_POD":("DELETE_MALICIOUS_POD","a malicious pod/behavior was detected","delete the malicious pod and investigate",
 ["Falco flagged a shell in {app} pod in {ns}","malicious activity in a pod on {node}","suspicious binary executed in {app}"]),
"CRYPTO_MINING_DETECTED":("CORDON_AND_ISOLATE","crypto-mining activity detected on a node","cordon the node, isolate and investigate",
 ["crypto miner detected on {node}","xmrig process found in {app} pod","mining traffic from {ns} detected"]),
"DDOS_ATTACK":("BLOCK_TRAFFIC","a DDoS flood is overwhelming the service","rate-limit/block the malicious traffic",
 ["DDoS flood hitting {app} in {ns}","abnormal request flood on {app}","{app} overwhelmed by attack traffic"]),
"IMAGE_VULNERABILITY":("UPDATE_IMAGE","a running image has a known critical vulnerability","update/patch the vulnerable image",
 ["critical CVE in {app} image in {ns}","{app} running a vulnerable image","image scan flags {app} as vulnerable"]),
"TLS_CERTIFICATE_EXPIRED":("RENEW_CERTIFICATE","an application TLS certificate expired","renew the TLS certificate",
 ["{app} TLS cert expired in {ns}","HTTPS failing for {app}, cert expired","expired TLS certificate on {app}"]),
"SUSPICIOUS_NETWORK_ACTIVITY":("ISOLATE_POD","suspicious network activity from a pod","isolate the pod and investigate the traffic",
 ["unusual outbound traffic from {app} in {ns}","{app} contacting unknown external IPs","suspicious network connections from {ns}"]),
# J. AIOps avances
"CASCADING_FAILURE":("ESCALATE","a failure is cascading across services","escalate: isolate the root cause and restore in order",
 ["failure cascading from {app} across {ns}","one failure triggering many others","cascading outage spreading in the cluster"]),
"RESOURCE_CONTENTION":("SCALE_UP","multiple workloads contend for the same resources","add capacity / rebalance to relieve contention",
 ["heavy resource contention on {node}","{app} and neighbors fighting for CPU","noisy-neighbor contention in {ns}"]),
"DEPENDENCY_FAILURE":("INVESTIGATE_LOGS","a downstream dependency failed, breaking the app","identify the failing dependency and act on it",
 ["{app} failing because a dependency is down in {ns}","upstream dependency error breaking {app}","{app} errors traced to a failed dependency"]),
"PARTIAL_CLUSTER_OUTAGE":("ESCALATE","part of the cluster is down","escalate: assess scope and restore the affected components",
 ["partial outage: {n} nodes and services down","a zone of the cluster is unavailable","partial cluster outage in progress"]),
"COMPLETE_CLUSTER_OUTAGE":("ESCALATE_RESTORE_QUORUM","the whole cluster is down","escalate: restore control-plane quorum first, then workloads",
 ["complete cluster outage, API and nodes down","total outage, nothing responding","cluster fully down, control plane lost"]),
"MULTI_NODE_FAILURE":("ESCALATE","several nodes failed at once","escalate: rally/restart the failed nodes and rebalance",
 ["{n} worker nodes went down together","multi-node failure in the cluster","several nodes NotReady simultaneously"]),
"MULTI_SERVICE_DEGRADATION":("ESCALATE","many services degrade at once","escalate: find the common cause and restore",
 ["multiple services degraded across {ns}","widespread degradation, many apps slow","several services failing together"]),
"ANOMALOUS_TRAFFIC_PATTERN":("ISOLATE_POD","an anomalous traffic pattern was detected","isolate the source and investigate",
 ["anomalous traffic pattern from {app}","unusual traffic shape detected in {ns}","AI flagged abnormal traffic on {app}"]),
"PREDICTED_NODE_FAILURE":("DRAIN_NODE","a node is predicted to fail soon","preemptively drain the node before it fails",
 ["prediction: {node} likely to fail soon","degrading health signals on {node}","{node} failure predicted, act preemptively"]),
"PREDICTED_DISK_EXHAUSTION":("EXTEND_STORAGE","disk is predicted to fill up soon","preemptively extend storage / clean up",
 ["prediction: {app} volume will fill in {n}h","disk exhaustion forecast for {node}","predicted storage saturation for {app}"]),
}

CLUSTER=["prod-eu","prod-us","staging-cluster","gke-prod","eks-main","onprem-01","dev-cluster","k8s-west","cluster-a","aks-prod"]
WRAP=["{s}.","Incident detected: {s}.","Alert: {s}.","Monitoring reports: {s}.","{s} — please decide the action.",
 "In production, {s}.","{s}. What is the remediation?","On cluster {cluster}: {s}.","[{cluster}] {s}.",
 "Ops alert on {cluster}: {s}.","{s} (cluster {cluster}).","PagerDuty: {s} on {cluster}."]
def fill(t):
    return (t.replace("{app}",random.choice(APP)).replace("{ns}",random.choice(NS))
             .replace("{node}",random.choice(NODE)).replace("{db}",random.choice(DB))
             .replace("{n}",str(N())).replace("{id}",str(random.randint(1000,9999)))
             .replace("{svc}",random.choice(APP)).replace("{cluster}",random.choice(CLUSTER)))

PER_TYPE=int(os.environ.get("PER_TYPE","88"))
rows=[]
for typ,(act,diag,expl,symps) in C.items():
    seen=set(); got=0; tries=0
    while got<PER_TYPE and tries<PER_TYPE*20:
        tries+=1
        s=fill(random.choice(symps)); w=fill(random.choice(WRAP)).replace("{s}",s)
        if w in seen: continue
        seen.add(w); rows.append((typ,w,act,diag,expl)); got+=1

random.shuffle(rows)
path=os.path.join(D,"sft_120.jsonl")
with open(path,"w",encoding="utf-8") as f:
    for typ,inc,act,diag,expl in rows:
        f.write(json.dumps({"messages":[{"role":"system","content":DSYS},
            {"role":"user","content":f"Incident: {inc} Decide the remediation action."},
            {"role":"assistant","content":f"DIAGNOSTIC: {diag} | ACTION={act} | EXPLICATION: {expl}"}]},ensure_ascii=False)+"\n")

from collections import Counter
print(f"DATASET 120 types : {len(rows)} lignes -> {path}")
print(f"types couverts: {len(C)}/120 | ~{len(rows)//len(C)} exemples/type")
print("exemple:")
print(" ", rows[0][1][:90])
print("  ->", f"DIAGNOSTIC: {rows[0][3]} | ACTION={rows[0][2]} | EXPLICATION: {rows[0][4]}"[:180])
