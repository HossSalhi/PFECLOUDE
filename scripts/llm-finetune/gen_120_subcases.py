#!/usr/bin/env python3
"""Dataset 120 types AVEC SOUS-CAS : meme type -> causes distinctes -> ACTIONS DIFFERENTES.
Le modele apprend a RAISONNER l'action depuis le symptome (dynamique, PAS de lookup type->1 action).
Chaque sous-cas = (symptome distinct, action, diagnostic). Puis Groq enrichira l'explication.
-> sft_120_sub.jsonl
"""
import json, os, random
random.seed(42)
D=os.path.dirname(os.path.abspath(__file__))
DSYS="You are the automatic decision engine of a Kubernetes self-healing system. Reason from the symptoms and output: DIAGNOSTIC: <cause> | ACTION=<ACTION_NAME> | EXPLICATION: <how to fix>."
APP=["payment-svc","auth-api","checkout","frontend","orders","cart","user-service","inventory","gateway","notification","billing","search","catalog","recommendation","analytics","web","reporting"]
NS=["default","production","staging","backend","data","payments","web","api","team-a","core"]
NODE=["worker-1","worker-2","worker-3","node-a","node-b","gke-node-4","eks-node-2","master-1"]
DB=["postgres","mysql","mongodb","redis","cassandra","mariadb"]
CL=["prod-eu","prod-us","staging-cluster","gke-prod","eks-main","onprem-01","k8s-west","aks-prod"]
def N(): return random.choice([2,3,4,5,8,10,12,20,30,50,80])

# TYPE -> liste de SOUS-CAS (symptome, ACTION, diagnostic) ; action VARIE selon la cause
S={
"POD_CRASH_LOOP":[("{app} CrashLoopBackOff right after a new deploy, panic loading config","ROLLBACK","config regression in the new revision"),
 ("{app} CrashLoopBackOff, logs show it can't reach its dependency {db}","CHECK_ENDPOINTS","a required dependency is unreachable"),
 ("{app} CrashLoopBackOff because its liveness probe kills it before it warms up","FIX_CONFIG","liveness probe too aggressive")],
"POD_OOM_KILLED":[("{app} OOMKilled, memory grows steadily over hours until it dies","RESTART_POD","application memory leak"),
 ("{app} OOMKilled at startup, its memory limit is set too low","TIGHTEN_MEMORY_LIMITS","under-provisioned memory limit"),
 ("{app} OOMKilled only during traffic peaks in {ns}","SCALE_UP","load-driven memory pressure")],
"POD_CPU_STRESS":[("{app} CPU pinned at its limit, one hot loop consuming CPU","RESTART_POD","runaway process, restart to purge"),
 ("{app} CPU-throttled constantly, limit too low for the workload","INCREASE_CPU","under-provisioned CPU"),
 ("{app} CPU high across all replicas under load","SCALE_UP","load exceeds capacity")],
"POD_MEMORY_LEAK":[("{app} heap grows unbounded, needs periodic restarts","RESTART_POD","memory leak, restart reclaims memory")],
"POD_PENDING_INSUFFICIENT_RESOURCES":[("{app} Pending, 0/{n} nodes have enough cpu/memory","SCALE_UP","cluster lacks capacity"),
 ("{app} Pending due to a nodeSelector/affinity that matches no node","FIX_CONFIG","wrong scheduling constraint"),
 ("{app} Pending waiting on a PVC that never binds","FIX_STORAGECLASS","storage not provisioned")],
"POD_INIT_CONTAINER_FAILURE":[("{app} stuck Init:CrashLoopBackOff, init step returns non-zero","FIX_CONFIG","init container command/config wrong"),
 ("{app} init container waits forever for a missing secret","RECREATE_SECRET","referenced secret missing")],
"POD_CONFIG_ERROR":[("{app} CreateContainerConfigError, invalid env value","FIX_CONFIG","bad env/config value"),
 ("{app} broke right after a config change","ROLLBACK","config regression")],
"POD_IMAGE_PULL_BACKOFF":[("{app} ImagePullBackOff, tag does not exist in the registry","UPDATE_IMAGE","wrong image tag"),
 ("{app} ImagePullBackOff, pull access denied","RECREATE_SECRET","missing/invalid pull secret")],
"POD_ERR_IMAGE_PULL":[("{app} ErrImagePull, repository not found","UPDATE_IMAGE","wrong image reference"),
 ("{app} ErrImagePull from the private registry, unauthorized","RECREATE_SECRET","registry credentials missing")],
"POD_STUCK_TERMINATING":[("{app}-{n} stuck Terminating, a finalizer never completes","FORCE_DELETE_POD","stuck finalizer"),
 ("{app} stuck Terminating after node {node} went down","FORCE_DELETE_POD","pod on a dead node")],
"POD_EVICTED":[("{app} Evicted, node {node} under disk pressure","FREE_DISK_SPACE","node disk pressure"),
 ("{app} Evicted, node {node} under memory pressure","DRAIN_NODE","node memory pressure")],
"POD_FAILED_PROBE":[("{app} readiness probe hits a wrong path and never passes","FIX_CONFIG","misconfigured probe")],
"POD_READINESS_FAILURE":[("{app} Running but never Ready, its dependency {db} is down","CHECK_ENDPOINTS","dependency unreachable"),
 ("{app} never Ready, readiness threshold too strict","FIX_CONFIG","readiness misconfigured")],
"POD_LIVENESS_FAILURE":[("{app} restarted repeatedly by a failing liveness probe","FIX_CONFIG","liveness probe misconfigured"),
 ("{app} liveness fails after a regression","ROLLBACK","regression makes app unhealthy")],
"POD_CONTAINER_RUNTIME_ERROR":[("RunContainerError across pods on {node}","RESTART_CONTAINERD","node container runtime error")],
"POD_RESTART_STORM":[("burst of restarts across {app} right after a deploy","ROLLBACK","regression causing the storm"),
 ("{app} restart storm because {db} it depends on is flapping","CHECK_ENDPOINTS","flapping dependency")],
"POD_SECURITY_CONTEXT_ERROR":[("{app} fails: runAsNonRoot set but image runs as root","FIX_CONFIG","securityContext mismatch")],
"POD_SECRET_MISSING":[("{app} fails: secret not found in {ns}","RECREATE_SECRET","missing secret")],
"POD_CONFIGMAP_MISSING":[("{app} fails: configmap not found in {ns}","RECREATE_CONFIGMAP","missing configmap")],
"POD_TOO_MANY_RESTARTS":[("{app} restarted {n} times, persistent regression","ROLLBACK","persistent fault from a regression"),
 ("{app} high restarts due to OOM each time","TIGHTEN_MEMORY_LIMITS","repeated OOM")],
"DEPLOYMENT_UNAVAILABLE":[("{app} 0/{n} available, new image crashes","ROLLBACK","bad new image"),
 ("{app} unavailable, replicas Pending for lack of resources","SCALE_UP","insufficient capacity")],
"DEPLOYMENT_ROLLOUT_STUCK":[("{app} rollout stuck, new pods never Ready","ROLLBACK","faulty new revision"),
 ("{app} rollout stuck on a wrong image tag","UPDATE_IMAGE","bad image reference")],
"DEPLOYMENT_ROLLBACK_REQUIRED":[("{app} regression after deploy, revert needed","ROLLBACK","faulty release")],
"REPLICA_SATURATION":[("{app} requests {n} replicas but half stay Pending","SCALE_DOWN","more replicas than schedulable")],
"HPA_SCALING_FAILURE":[("{app} HPA shows unknown targets, no metrics","FIX_CONFIG","metrics-server unavailable")],
"HPA_OVER_SCALING":[("HPA scaled {app} to {n} replicas needlessly","SCALE_DOWN","HPA thresholds too aggressive")],
"HPA_UNDER_SCALING":[("{app} overloaded but HPA won't add replicas","SCALE_UP","HPA thresholds too high")],
"DEPLOYMENT_VERSION_MISMATCH":[("{app} has pods on two versions after a partial rollout","ROLLBACK","version skew, unify revision")],
"DEPLOYMENT_PROGRESS_DEADLINE_EXCEEDED":[("{app} ProgressDeadlineExceeded on a failing rollout","ROLLBACK","rollout failed to progress")],
"SERVICE_UNAVAILABLE":[("{app} service returns errors, its backend pods are crashing","RESTART_POD","backends down"),
 ("{app} service unreachable, a NetworkPolicy blocks it","FIX_NETWORK_POLICY","policy blocks traffic")],
"SERVICE_NO_ENDPOINTS":[("{app} service has 0 endpoints, selector matches no pod","FIX_SERVICE_SELECTOR","selector/label mismatch")],
"NETWORK_PARTITION":[("pods in {ns} isolated from {app}, ingress+egress cut","FIX_NETWORK_POLICY","policy caused partition"),
 ("{node} partitioned from the cluster network","RESTART_NODE_VM","node network partition")],
"NETWORK_PACKET_LOSS":[("high packet loss to {app}, node MTU misconfigured","FIX_CONFIG","MTU/network misconfig")],
"NETWORK_HIGH_LATENCY":[("high pod-to-pod latency across {node}","INVESTIGATE_LOGS","network latency, investigate path")],
"NETWORK_POLICY_BLOCKING":[("new NetworkPolicy cut access to {app} in {ns}","FIX_NETWORK_POLICY","overly strict policy")],
"DNS_FAILURE":[("{app} cannot resolve service names, CoreDNS crashed","FIX_DNS","CoreDNS down"),
 ("{app} DNS fails, a policy blocks port 53","FIX_NETWORK_POLICY","policy blocks DNS")],
"DNS_SLOW_RESPONSE":[("DNS lookups slow, CoreDNS overloaded","SCALE_UP","add CoreDNS replicas")],
"INGRESS_UNAVAILABLE":[("ingress 502 for {app}, controller pod down","RESTART_SERVICE","ingress controller down")],
"INGRESS_5XX_ERRORS":[("ingress 5xx for {app} after a backend deploy","ROLLBACK","bad backend release"),
 ("ingress 5xx for {app} under a traffic surge","SCALE_UP","backends overloaded")],
"LOAD_BALANCER_UNAVAILABLE":[("{app} LB not routing, no healthy backends","CHECK_ENDPOINTS","no healthy targets")],
"LOAD_BALANCER_OVERLOAD":[("{app} LB saturated under traffic","SCALE_UP","scale backends")],
"TCP_CONNECTION_FAILURE":[("connection refused on {app}:{n}, no endpoints","CHECK_ENDPOINTS","no ready endpoints")],
"NODE_NOT_READY":[("{node} NotReady, kubelet lease expired","RESTART_KUBELET","kubelet down"),
 ("{node} NotReady after losing network","RESTART_NODE_VM","node network/host issue"),
 ("{node} NotReady due to disk pressure","FREE_DISK_SPACE","disk pressure")],
"NODE_HIGH_CPU":[("{node} CPU at 100%, pods degraded","DRAIN_NODE","host CPU saturation, reschedule")],
"NODE_HIGH_MEMORY":[("{node} memory near 100%, evictions imminent","DRAIN_NODE","host memory pressure")],
"NODE_DISK_PRESSURE":[("{node} DiskPressure, evicting pods","FREE_DISK_SPACE","node disk pressure")],
"NODE_MEMORY_PRESSURE":[("{node} MemoryPressure condition true","DRAIN_NODE","memory pressure")],
"NODE_PID_PRESSURE":[("{node} PIDPressure, cannot fork","RESTART_NODE_VM","PID exhaustion")],
"NODE_DISK_FULL":[("{node} disk 100% full, kubelet failing","FREE_DISK_SPACE","node disk full")],
"NODE_NETWORK_DOWN":[("{node} network down, unreachable","RESTART_NODE_VM","node network down")],
"NODE_REBOOT":[("{node} rebooted unexpectedly","INVESTIGATE_LOGS","verify recovery")],
"NODE_KERNEL_PANIC":[("kernel panic on {node}, host hung","REBOOT_NODE","kernel panic")],
"NODE_TIME_DRIFT":[("clock drift on {node}, TLS failing","FIX_CONFIG","NTP not synced")],
"NODE_KUBELET_DOWN":[("kubelet service stopped on {node}","RESTART_KUBELET","kubelet down")],
"NODE_CONTAINERD_DOWN":[("containerd down on {node}, containers not starting","RESTART_CONTAINERD","runtime down")],
"NODE_FILESYSTEM_READONLY":[("{node} root filesystem went read-only","REBOOT_NODE","disk error, fs ro")],
"NODE_UNREACHABLE":[("{node} unreachable from the control plane","RESTART_NODE_VM","node unreachable")],
"API_SERVER_DOWN":[("kube-apiserver not responding, kubectl fails","ESCALATE_RESTART_APISERVER","apiserver static pod down")],
"API_SERVER_HIGH_LATENCY":[("apiserver p99 latency high, etcd slow","ESCALATE_TUNE_ETCD","etcd bottleneck")],
"ETCD_DOWN":[("an etcd member is down","ESCALATE_RESTORE_ETCD","etcd member failure")],
"ETCD_QUORUM_LOST":[("etcd lost quorum, cluster read-only","ESCALATE_RESTORE_QUORUM","majority of members down")],
"ETCD_HIGH_LATENCY":[("etcd fsync latency high, control plane slow","ESCALATE_TUNE_ETCD","etcd disk latency")],
"ETCD_DATABASE_CORRUPTION":[("etcd reports database corruption","ESCALATE_RESTORE_ETCD","corrupted db, restore from backup")],
"CONTROLLER_MANAGER_DOWN":[("kube-controller-manager down, no reconcile","ESCALATE","cm static pod down")],
"SCHEDULER_DOWN":[("kube-scheduler down, pods stuck Pending","ESCALATE","scheduler static pod down")],
"CERTIFICATE_EXPIRED":[("apiserver/kubelet cert expired, x509 errors","RENEW_CERTIFICATE","expired cluster cert")],
"CERTIFICATE_NEAR_EXPIRATION":[("cluster cert expires in {n} days","RENEW_CERTIFICATE","renew proactively")],
"LEADER_ELECTION_FAILURE":[("controller-manager cannot acquire its lease","ESCALATE","leader lease/etcd issue")],
"ADMISSION_WEBHOOK_FAILURE":[("a failing admission webhook blocks all writes","FIX_CONFIG","fix/remove broken webhook")],
"PVC_PENDING":[("PVC for {app} Pending, no default StorageClass","FIX_STORAGECLASS","storageclass/provisioner issue")],
"PVC_LOST":[("PVC for {app} shows Lost","ESCALATE","binding lost, restore from backup")],
"PV_LOST":[("PV backing {app} is Lost","ESCALATE","volume lost, restore from snapshot")],
"STORAGE_CLASS_ERROR":[("StorageClass provisioner error for {app}","FIX_STORAGECLASS","misconfigured storageclass")],
"STORAGE_CAPACITY_EXCEEDED":[("{app} volume full, writes failing","EXTEND_STORAGE","volume out of space")],
"VOLUME_MOUNT_FAILURE":[("MountVolume.SetUp failed for {app}, permission denied","FIX_VOLUME_MOUNT","mount permission/driver issue")],
"READ_ONLY_VOLUME":[("{app} volume mounted read-only, cannot write","FIX_VOLUME_MOUNT","volume ro, remount")],
"CSI_DRIVER_FAILURE":[("CSI driver pods failing, attach errors","RESTART_SERVICE","csi driver down")],
"SNAPSHOT_FAILURE":[("volume snapshot for {app} failed","INVESTIGATE_LOGS","snapshot controller error")],
"DATABASE_CONNECTIVITY":[("{app} cannot reach {db} service","CHECK_ENDPOINTS","db service endpoints issue")],
"DB_CONNECTION_REFUSED":[("connection refused to {db}, it is not listening","RESTART_SERVICE","db down/not listening")],
"DB_CONNECTION_POOL_EXHAUSTION":[("{app} pool to {db} exhausted, limit too small","TUNE_DB","raise pool/max connections"),
 ("{app} pool exhausted because connections leak","RESTART_POD","connection leak, restart to reset pool")],
"DB_SLOW_QUERIES":[("{db} slow queries, missing index","TUNE_DB","optimize/index queries")],
"DB_HIGH_CPU":[("{db} CPU at 100% from heavy queries","TUNE_DB","optimize queries"),
 ("{db} CPU high under sustained load","SCALE_UP","scale the database")],
"DB_HIGH_MEMORY":[("{db} memory near limit, OOM risk","TIGHTEN_MEMORY_LIMITS","raise/tune memory")],
"DB_DISK_FULL":[("{db} disk full, writes failing","FREE_DISK_SPACE","db out of storage")],
"DB_REPLICATION_LAG":[("{db} replica lag {n}s","TUNE_DB","tune replication / reduce write load")],
"DB_PRIMARY_DOWN":[("{db} primary is down","PROMOTE_DB_REPLICA","failover to a replica")],
"DB_DEADLOCK":[("{db} deadlocks detected for {app}","TUNE_DB_TIMEOUTS","tune lock timeouts/retries")],
"DB_TRANSACTION_TIMEOUT":[("{db} transaction timeouts under contention","TUNE_DB_TIMEOUTS","tune timeouts, reduce contention")],
"DB_TOO_MANY_CONNECTIONS":[("{db} too many connections error","TUNE_DB","raise max_connections/add pooler")],
"LATENCY_DEGRADATION":[("{app} latency degraded right after a deploy","ROLLBACK","regression slowed it"),
 ("{app} latency high under increased load","SCALE_UP","capacity-driven latency")],
"HIGH_ERROR_RATE_5XX":[("{app} 5xx spiking after a release","ROLLBACK","faulty release"),
 ("{app} 5xx under a traffic surge","SCALE_UP","overload causing errors"),
 ("{app} 5xx because dependency {db} is down","CHECK_ENDPOINTS","failing dependency")],
"HIGH_ERROR_RATE_4XX":[("{app} 4xx surge after a route change","FIX_CONFIG","bad route/config")],
"HIGH_RESPONSE_TIME":[("{app} slow responses under load","SCALE_UP","needs more capacity")],
"REQUEST_TIMEOUT":[("{app} requests time out under load","SCALE_UP","overload"),
 ("{app} timeouts waiting on a slow dependency","INVESTIGATE_LOGS","slow dependency")],
"THROUGHPUT_DROP":[("{app} throughput dropped {n}%","INVESTIGATE_LOGS","find the bottleneck")],
"TRAFFIC_SPIKE":[("sudden traffic spike overloading {app}","SCALE_UP","absorb the spike")],
"MEMORY_LEAK_APPLICATION":[("{app} leaks memory over time","RESTART_POD","restart, fix leak")],
"THREAD_POOL_EXHAUSTION":[("{app} thread pool exhausted, requests queued","SCALE_UP","scale / raise pool")],
"CACHE_FAILURE":[("cache {db} down for {app}","RESTART_SERVICE","restart cache")],
"SECRET_EXPOSED":[("a secret for {app} was exposed","RECREATE_SECRET","rotate the secret")],
"UNAUTHORIZED_ACCESS":[("unauthorized access attempts on {ns}","FIX_RBAC_PERMISSIONS","tighten RBAC, investigate")],
"RBAC_MISCONFIGURATION":[("{app} serviceaccount over-privileged","FIX_RBAC_PERMISSIONS","correct the RBAC")],
"PRIVILEGED_CONTAINER":[("privileged container detected in {ns}","ISOLATE_POD","isolate, fix securityContext")],
"MALICIOUS_POD":[("shell/suspicious binary executed in {app} pod","DELETE_MALICIOUS_POD","malicious pod, delete")],
"CRYPTO_MINING_DETECTED":[("crypto miner detected on {node}","CORDON_AND_ISOLATE","cordon node, isolate")],
"DDOS_ATTACK":[("DDoS flood hitting {app}","BLOCK_TRAFFIC","rate-limit/block attacker")],
"IMAGE_VULNERABILITY":[("critical CVE in {app} image","UPDATE_IMAGE","patch the image")],
"TLS_CERTIFICATE_EXPIRED":[("{app} TLS cert expired","RENEW_CERTIFICATE","renew TLS cert")],
"SUSPICIOUS_NETWORK_ACTIVITY":[("{app} contacting unknown external IPs","ISOLATE_POD","isolate, investigate")],
"CASCADING_FAILURE":[("failure cascading from {app} across {ns}","ESCALATE","isolate root cause, restore in order")],
"RESOURCE_CONTENTION":[("noisy-neighbor contention on {node}","DRAIN_NODE","rebalance workloads"),
 ("resource contention across {ns} under load","SCALE_UP","add capacity")],
"DEPENDENCY_FAILURE":[("{app} failing because a downstream dependency is down","CHECK_ENDPOINTS","failed dependency")],
"PARTIAL_CLUSTER_OUTAGE":[("{n} nodes and services down, partial outage","ESCALATE","assess scope, restore")],
"COMPLETE_CLUSTER_OUTAGE":[("complete outage, API and nodes down","ESCALATE_RESTORE_QUORUM","restore control-plane first")],
"MULTI_NODE_FAILURE":[("{n} nodes went down together","ESCALATE","rally/restart nodes, rebalance")],
"MULTI_SERVICE_DEGRADATION":[("many services degraded at once in {ns}","ESCALATE","find common cause")],
"ANOMALOUS_TRAFFIC_PATTERN":[("anomalous traffic pattern from {app}","ISOLATE_POD","isolate source, investigate")],
"PREDICTED_NODE_FAILURE":[("prediction: {node} likely to fail soon","DRAIN_NODE","drain preemptively")],
"PREDICTED_DISK_EXHAUSTION":[("prediction: {app} volume will fill in {n}h","EXTEND_STORAGE","extend storage preemptively")],
}

WRAP=["{s}.","Incident detected: {s}.","Alert: {s}.","Monitoring: {s}.","On cluster {cluster}: {s}.","[{cluster}] {s}.",
 "Ops alert on {cluster}: {s}.","{s} (cluster {cluster}).","PagerDuty: {s}.","{s} — decide.","{s} in {cluster}.",
 "On cluster {cluster} ({node}): {s}.","In namespace {ns} on {cluster}: {s}.","Alert from {cluster}/{ns}: {s}.",
 "{s} — cluster {cluster}, node {node}.","Cluster {cluster}, ns {ns}: {s}.","[{cluster}/{node}] {s}.",
 "Team {ns} on {cluster}: {s}.","Prod alert ({cluster}, {node}): {s}."]
def fill(t):
    return (t.replace("{app}",random.choice(APP)).replace("{ns}",random.choice(NS)).replace("{node}",random.choice(NODE))
             .replace("{db}",random.choice(DB)).replace("{cluster}",random.choice(CL)).replace("{n}",str(N())))
PER_TYPE=int(os.environ.get("PER_TYPE","80"))   # cible UNIFORME par TYPE (equilibre)
rows=[]
for typ,subs in S.items():
    k=len(subs)
    targets=[PER_TYPE//k]*k
    for i in range(PER_TYPE%k): targets[i]+=1   # repartir les 80 entre les sous-cas
    for (symp,act,diag),target in zip(subs,targets):
        seen=set(); got=0; tries=0
        while got<target and tries<target*50:
            tries+=1
            w=fill(random.choice(WRAP)).replace("{s}",fill(symp))
            if w in seen: continue
            seen.add(w); got+=1
            rows.append((typ,w,act,diag))
random.shuffle(rows)
path=os.path.join(D,"sft_120_sub.jsonl")
with open(path,"w",encoding="utf-8") as f:
    for typ,inc,act,diag in rows:
        f.write(json.dumps({"messages":[{"role":"system","content":DSYS},
            {"role":"user","content":f"Incident: {inc} Decide the remediation action."},
            {"role":"assistant","content":f"DIAGNOSTIC: {diag} | ACTION={act} | EXPLICATION: (a enrichir)"}]},ensure_ascii=False)+"\n")
import collections
nsub=sum(len(v) for v in S.values())
multi=sum(1 for v in S.values() if len(set(a for _,a,_ in v))>1)
acts=collections.Counter(r[2] for r in rows)
print(f"lignes: {len(rows)} | types: {len(S)} | sous-cas total: {nsub}")
print(f"types avec ACTION VARIABLE (>1 action selon la cause): {multi}/120")
print(f"actions distinctes: {len(acts)}")
# REPARTITION par type (equilibre ?)
tc=collections.Counter(r[0] for r in rows)
vals=sorted(tc.values())
print("\n=== REPARTITION PAR TYPE ===")
print(f"  min: {vals[0]} | max: {vals[-1]} | moyenne: {sum(vals)//len(vals)} exemples/type")
print(f"  types < 50 exemples: {sum(1 for v in vals if v<50)} / 120")
hist=collections.Counter(vals)
print(f"  distribution (exemples/type : nb de types): {dict(sorted(hist.items()))}")
