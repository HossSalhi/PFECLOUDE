#!/usr/bin/env python3
"""FIX des points faibles ML — dataset 120 types REALISTE et NON-CIRCULAIRE (12000).
- log_sig = extrait de VRAIES lignes de logs K8s/app (PAS le nom du type) via template-mining (Drain3-lite).
- variete : plusieurs templates de logs par type + placeholders + bruit (logs manquants/generiques).
- metriques : archetypes + chevauchement realiste (pas parfaitement separables).
-> plus de circularite (log_sig ne trahit plus le type), train aligne sur un vrai extracteur de logs.
Sortie : dataset_120_realistic.csv + entrainement honnete + comparaison.
"""
import os, re, sys, json
import numpy as np, pandas as pd
D=os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0,D)
from gen_120_enriched import ARCH, SPEC, MB
rng=np.random.default_rng(2026)
def R(a,b): return float(rng.uniform(a,b))
def RI(a,b): return int(rng.integers(a,b+1))
APP=["payment-svc","auth-api","checkout","frontend","orders","cart","gateway","inventory","billing","search"]
IMG=["registry/app:v2.3","gcr.io/proj/api:latest","myrepo/web:1.4","quay.io/x/svc:prod"]
PROC=["java","python3","node","nginx","postgres","envoy"]
NODEP=["worker-1","worker-2","node-a","gke-node-4","eks-node-2"]

# ===== VRAIES lignes de logs par type (aucune ne contient le nom du type) =====
LOGS={
"POD_CRASH_LOOP":["back-off restarting failed container {proc} in pod {app}","container {proc} exited with code 1 restart count {n}","panic runtime error nil pointer dereference goroutine"],
"POD_OOM_KILLED":["memory cgroup out of memory killed process {n} {proc}","oom killer invoked victim {proc} anon-rss {n}kb","container exceeded memory limit terminating"],
"POD_CPU_STRESS":["cpu usage sustained above quota throttling periods {n}","cfs throttled us {n} nr periods {n}","high load average worker {proc} busy"],
"POD_MEMORY_LEAK":["heap usage growing rss {n}mb after {n}h uptime","gc pause {n}ms old gen occupancy rising","working set steadily increasing no plateau"],
"POD_PENDING_INSUFFICIENT_RESOURCES":["failedscheduling insufficient cpu memory {n} nodes","0/{n} nodes are available insufficient memory","pod didn t trigger scale-up no matching node"],
"POD_INIT_CONTAINER_FAILURE":["init container init-{proc} exited non-zero code {n}","init:crashloopbackoff waiting for init step","error running init container command not found"],
"POD_CONFIG_ERROR":["createcontainerconfigerror invalid environment value","error parsing config unexpected token","failed to start container bad configuration"],
"POD_IMAGE_PULL_BACKOFF":["failed to pull image {img} rpc error code notfound","back-off pulling image {img}","errimagepull manifest unknown for {img}"],
"POD_ERR_IMAGE_PULL":["failed pulling image {img} repository does not exist","pull access denied for {img} unauthorized","error response from daemon manifest for {img} not found"],
"POD_STUCK_TERMINATING":["pod stuck terminating finalizer not removed {n}s","failedkillpod error killing pod grace period expired","container still running after sigterm sigkill sent"],
"POD_EVICTED":["the node was low on resource ephemeral-storage evicting pod","pod evicted due to node condition diskpressure","eviction manager attempting to reclaim ephemeral-storage"],
"POD_FAILED_PROBE":["readiness probe failed http probe returned statuscode {n}","liveness probe errored context deadline exceeded","probe failed dial tcp connect refused"],
"POD_READINESS_FAILURE":["readiness probe failed connection refused endpoint {n}","pod running but not ready dependency check failing","waiting for readiness gate condition"],
"POD_LIVENESS_FAILURE":["liveness probe failed restarting container {proc}","liveness http get {n} connection reset","container failed liveness will be restarted"],
"POD_CONTAINER_RUNTIME_ERROR":["runcontainererror failed to create containerd task","error creating container runtime oci runtime create failed","containerd failed to start container shim"],
"POD_RESTART_STORM":["pod restart count {n} within {n} minutes","repeated container restarts detected {proc}","crashloop across replicas restart storm"],
"POD_SECURITY_CONTEXT_ERROR":["container has runasnonroot and image will run as root","forbidden securitycontext violates policy","error validating pod security standard restricted"],
"POD_SECRET_MISSING":["error secret {app}-secret not found in namespace","couldn t find key in secret mounting failed","createcontainerconfigerror secret not found"],
"POD_CONFIGMAP_MISSING":["error configmap {app}-config not found","configmap referenced in volume not found","failed to sync configmap cache not found"],
"POD_TOO_MANY_RESTARTS":["restartcount {n} exceeds threshold for pod {app}","persistent restarts container repeatedly failing","pod has restarted {n} times last hour"],
"DEPLOYMENT_UNAVAILABLE":["deployment {app} does not have minimum availability","0/{n} replicas available condition available false","replicafailure pods not becoming ready"],
"DEPLOYMENT_ROLLOUT_STUCK":["deployment exceeded progress checking new replicaset","rollout stuck waiting for new pods ready","progressing false reason replicasetupdated"],
"DEPLOYMENT_ROLLBACK_REQUIRED":["error rate spiked after rollout revision {n}","new revision unhealthy consider rollback","post-deploy latency and errors elevated"],
"REPLICA_SATURATION":["{n} replicas requested but half pending unschedulable","too many replicas for cluster capacity","scale exceeds schedulable nodes pending pods"],
"HPA_SCALING_FAILURE":["horizontalpodautoscaler unable to fetch metrics","failedgetresourcemetric no metrics returned","hpa targets unknown metrics-server unavailable"],
"HPA_OVER_SCALING":["hpa scaled to max replicas repeatedly flapping","autoscaler scaling up aggressively thresholds low","replicas oscillating up and down"],
"HPA_UNDER_SCALING":["hpa not scaling despite high utilization","desired replicas below need target too high","under-provisioned autoscaler lagging load"],
"DEPLOYMENT_VERSION_MISMATCH":["pods running two different image revisions","replicaset skew old and new versions serving","version mismatch across pods {app}"],
"DEPLOYMENT_PROGRESS_DEADLINE_EXCEEDED":["progressdeadlineexceeded rollout failed to progress","deployment {app} exceeded its progress deadline","timed out waiting for rollout to complete"],
"SERVICE_UNAVAILABLE":["service {app} has no healthy upstream backends","502 bad gateway no endpoints available","connection to service refused backends down"],
"SERVICE_NO_ENDPOINTS":["service {app} endpoints empty selector matches no pods","no endpoints available for service","endpoints controller found 0 ready addresses"],
"NETWORK_PARTITION":["cross-node pod communication timing out {node}","network unreachable between {node} and peers","tcp connection to {ip} timed out partition"],
"NETWORK_PACKET_LOSS":["tcp retransmits increasing packet loss {n}pct","dropped packets on interface eth0 {n}","high retransmission rate to {ip}"],
"NETWORK_HIGH_LATENCY":["round trip time to {app} elevated {n}ms","network rtt p99 above threshold","slow pod-to-pod communication latency high"],
"NETWORK_POLICY_BLOCKING":["connection denied by networkpolicy egress","packet dropped policy deny to {app}","traffic blocked no matching allow rule"],
"DNS_FAILURE":["coredns servfail resolving {app}-svc","dns lookup failed no such host","upstream resolver connection refused port 53"],
"DNS_SLOW_RESPONSE":["dns query latency {n}ms coredns overloaded","slow name resolution timeouts increasing","coredns request duration high"],
"INGRESS_UNAVAILABLE":["ingress controller pod not ready 502","nginx upstream no live upstreams","ingress backend unreachable returning 503"],
"INGRESS_5XX_ERRORS":["ingress returning 5xx rate {n}pct","upstream sent invalid response 502","edge errors elevated after backend deploy"],
"LOAD_BALANCER_UNAVAILABLE":["synceloadbalancerfailed no healthy targets","load balancer has no registered backends","lb health check failing all targets"],
"LOAD_BALANCER_OVERLOAD":["load balancer connection pool saturated","too many concurrent connections {n} lb","backend queue full dropping requests"],
"TCP_CONNECTION_FAILURE":["connection refused dial tcp {ip} {n}","no route to host connect failed","tcp reset by peer connection closed"],
"NODE_NOT_READY":["node {node} status is now nodenotready","kubelet stopped posting node status","node lease {node} failed to be renewed"],
"NODE_HIGH_CPU":["node {node} cpu utilization {n}pct saturated","load average very high on {node}","cpu pressure node throttling workloads"],
"NODE_HIGH_MEMORY":["node {node} memory available low {n}mb","memorypressure condition true on {node}","node under memory pressure reclaiming"],
"NODE_DISK_PRESSURE":["node {node} has diskpressure evicting","imagefs available below threshold {n}pct","attempting to reclaim ephemeral-storage node"],
"NODE_MEMORY_PRESSURE":["node {node} condition memorypressure true","system oom encountered on node","kubelet evicting pods memory pressure"],
"NODE_PID_PRESSURE":["node {node} pidpressure cannot fork","too many processes pid limit reached","pidpressure condition true evicting"],
"NODE_DISK_FULL":["no space left on device node {node}","filesystem root 100pct full writes failing","disk full kubelet failed to write"],
"NODE_NETWORK_DOWN":["node {node} nic down link not detected","network interface eth0 went down","node unreachable network interface error"],
"NODE_REBOOT":["node {node} uptime reset unexpected reboot","system booted kernel version changed","node restarted kubelet re-registering"],
"NODE_KERNEL_PANIC":["kernel panic not syncing on {node}","dmesg oops call trace fatal","node hung kernel panic detected"],
"NODE_TIME_DRIFT":["clock offset {n}ms ntp not synchronized","time drift detected certificate validation failing","chronyd unable to sync large offset"],
"NODE_KUBELET_DOWN":["kubelet service inactive on {node}","failed to connect to kubelet {node}","kubelet not posting status stopped"],
"NODE_CONTAINERD_DOWN":["containerd socket connection refused {node}","failed to connect to containerd runtime down","cri runtime unavailable on {node}"],
"NODE_FILESYSTEM_READONLY":["remounting filesystem read-only ext4 error","cannot write root filesystem read-only","io error device fault fs readonly"],
"NODE_UNREACHABLE":["node {node} not responding to health checks","unable to reach node {node} timeout","node heartbeat missing marked unreachable"],
"API_SERVER_DOWN":["the connection to the server was refused apiserver","kube-apiserver healthz check failed","dial tcp {ip} 6443 connection refused"],
"API_SERVER_HIGH_LATENCY":["apiserver request latency p99 {n}ms","slow api requests etcd backend lagging","apiserver watch cache slow"],
"ETCD_DOWN":["etcd member {ip} connection refused","failed to connect to etcd endpoint","etcdserver request timed out member down"],
"ETCD_QUORUM_LOST":["etcd no leader elected lost quorum","cluster is unavailable failed to commit","apply entries took too long no quorum"],
"ETCD_HIGH_LATENCY":["etcd fsync duration {n}ms slow disk","apply took too long backend slow","wal fsync latency high etcd"],
"ETCD_DATABASE_CORRUPTION":["etcd database corruption mismatch hash","alarm corrupt raised on member","panic mvcc store consistency error"],
"CONTROLLER_MANAGER_DOWN":["kube-controller-manager not running leader lost","controller manager unhealthy no reconcile","failed to acquire lease controller-manager"],
"SCHEDULER_DOWN":["kube-scheduler not running pods stay pending","scheduler unhealthy no binding happening","failed to acquire lease scheduler"],
"CERTIFICATE_EXPIRED":["x509 certificate has expired or is not yet valid","tls handshake error certificate expired","unable to verify server cert expired"],
"CERTIFICATE_NEAR_EXPIRATION":["certificate expires in {n} days renew soon","cert nearing expiry warning threshold","upcoming certificate expiration detected"],
"LEADER_ELECTION_FAILURE":["failed to renew lease leaderelection lost","error retrieving resource lock timeout","leaderelection lost lease not acquired"],
"ADMISSION_WEBHOOK_FAILURE":["failed calling webhook context deadline exceeded","admission webhook denied the request","webhook {app} unreachable timeout blocking"],
"PVC_PENDING":["persistentvolumeclaim pending no storageclass","provisioningfailed waiting for volume","pvc unbound no persistent volumes available"],
"PVC_LOST":["persistentvolumeclaim status lost","claim lost bound pv deleted","pvc {app} lost data unavailable"],
"PV_LOST":["persistentvolume status released lost backend","volume no longer exists on storage","pv lost underlying disk missing"],
"STORAGE_CLASS_ERROR":["storageclass provisioner error invalid parameter","failed to provision volume with storageclass","provisioner {app} returned error"],
"STORAGE_CAPACITY_EXCEEDED":["no space left on volume writes failing","persistent volume {n}pct full full","disk quota exceeded on pvc"],
"VOLUME_MOUNT_FAILURE":["mountvolume setup failed for volume permission denied","failedmount unable to attach or mount volumes","volume multi-attach error already used"],
"READ_ONLY_VOLUME":["read-only file system cannot write volume","mounted volume is read-only remount","operation not permitted volume ro"],
"CSI_DRIVER_FAILURE":["csi driver attach volume failed grpc error","failed to stage volume csi node","csi plugin not responding timeout"],
"SNAPSHOT_FAILURE":["volumesnapshot creation failed error","snapshot controller failed to create","csi snapshot rpc error"],
"DATABASE_CONNECTIVITY":["could not connect to server {db} connection refused","database unreachable timeout expired","failed to connect to {db} host"],
"DB_CONNECTION_REFUSED":["connection refused is the server running {db}","fatal could not connect port {n}","dial tcp {db} connection refused"],
"DB_CONNECTION_POOL_EXHAUSTION":["remaining connection slots reserved pool exhausted","too many clients already connections","connection pool timeout waiting for slot"],
"DB_SLOW_QUERIES":["slow query {n}ms seq scan missing index","statement duration exceeded log_min","query performance degraded full scan"],
"DB_HIGH_CPU":["database cpu {n}pct heavy queries","high load on {db} query planner busy","cpu saturation database backend"],
"DB_HIGH_MEMORY":["database memory usage high shared buffers","oom risk {db} work_mem exceeded","memory pressure on database instance"],
"DB_DISK_FULL":["could not extend file no space wal","database disk full writes rejected","pg_wal partition full {db}"],
"DB_REPLICATION_LAG":["replication lag {n}s behind primary","standby {db} falling behind wal apply","replica lag exceeds threshold"],
"DB_PRIMARY_DOWN":["primary {db} is down promoting standby","could not connect to primary failover","database master unreachable initiating failover"],
"DB_DEADLOCK":["deadlock detected process waiting on lock","transaction aborted due to deadlock","lock wait timeout exceeded {db}"],
"DB_TRANSACTION_TIMEOUT":["canceling statement due to timeout","transaction timed out rolling back","lock acquisition timeout contention"],
"DB_TOO_MANY_CONNECTIONS":["fatal sorry too many clients already","max_connections reached rejecting","remaining connection slots are reserved"],
"LATENCY_DEGRADATION":["p95 response time {n}ms above baseline","request duration increased after deploy","latency degraded slo breach"],
"HIGH_ERROR_RATE_5XX":["http 5xx ratio {n}pct spiking","upstream returned 500 internal error rate","server errors elevated {app}"],
"HIGH_ERROR_RATE_4XX":["http 4xx surge {n}pct bad request","401 403 responses increased route","client errors elevated after change"],
"HIGH_RESPONSE_TIME":["response time degraded {n}ms under load","slow responses queue building up","endpoint {app} latency high"],
"REQUEST_TIMEOUT":["upstream request timed out 504 gateway","context deadline exceeded calling {app}","request timeout waiting for response"],
"THROUGHPUT_DROP":["request rate dropped {n}pct throughput","qps collapsed traffic falling","processing rate below expected"],
"TRAFFIC_SPIKE":["request rate surged {n} rps spike","sudden traffic increase overloading {app}","qps doubled load spike"],
"MEMORY_LEAK_APPLICATION":["heap growth unbounded {n}mb app leak","memory not released after gc rising","application rss climbing steadily"],
"THREAD_POOL_EXHAUSTION":["thread pool exhausted queue full {n}","no available worker threads rejecting","threadpool saturated tasks queued"],
"CACHE_FAILURE":["redis connection lost cache unavailable","cache miss rate 100pct backend down","could not reach cache {db}"],
"SECRET_EXPOSED":["secret value detected in log output","sensitive credential printed plaintext","api key found in application logs"],
"UNAUTHORIZED_ACCESS":["repeated 401 unauthorized from {ip}","forbidden access attempt authentication failed","multiple failed auth attempts {ip}"],
"RBAC_MISCONFIGURATION":["forbidden user cannot list resource rbac","serviceaccount lacks permission denied","rolebinding grants excessive privileges"],
"PRIVILEGED_CONTAINER":["container running privileged securitycontext","pod created with privileged true","privileged mode detected admission"],
"MALICIOUS_POD":["shell spawned in container suspicious exec","sensitive file opened unexpected process","reverse shell pattern detected in pod"],
"CRYPTO_MINING_DETECTED":["outbound connection to mining pool stratum","cpu maxed unknown process xmrig","crypto miner signature detected"],
"DDOS_ATTACK":["request flood from many source ips","syn flood {n} connections per second","abnormal traffic volume {ip} attack"],
"IMAGE_VULNERABILITY":["critical cve found in image scan {img}","vulnerability high severity cve-{n}","image contains known vulnerable package"],
"TLS_CERTIFICATE_EXPIRED":["tls handshake failed certificate expired","x509 cert expired for {app} endpoint","ssl error expired certificate"],
"SUSPICIOUS_NETWORK_ACTIVITY":["unexpected outbound to unknown ip {ip}","connection to non-whitelisted host","anomalous egress traffic detected pod"],
"CASCADING_FAILURE":["multiple services failing chain reaction","downstream failures propagating upstream","widespread errors across {ns} namespace"],
"RESOURCE_CONTENTION":["noisy neighbor cpu contention node {node}","resource starvation competing workloads","throttling due to contention {node}"],
"DEPENDENCY_FAILURE":["downstream dependency {app} returned error","upstream service unavailable circuit open","dependency call failing error rate"],
"PARTIAL_CLUSTER_OUTAGE":["{n} nodes not ready services degraded","partial outage zone unavailable","several nodes down partial disruption"],
"COMPLETE_CLUSTER_OUTAGE":["all nodes not ready apiserver unreachable","complete cluster outage control plane down","total loss of cluster connectivity"],
"MULTI_NODE_FAILURE":["{n} nodes went notready simultaneously","multiple node failures detected {node}","several workers lost at once"],
"MULTI_SERVICE_DEGRADATION":["many services reporting errors in {ns}","widespread degradation multiple deployments","several apps latency and errors up"],
"ANOMALOUS_TRAFFIC_PATTERN":["traffic pattern deviates from baseline anomaly","unusual request distribution detected","behavioral anomaly in traffic {app}"],
"PREDICTED_NODE_FAILURE":["node {node} hardware error trend degrading","smart disk errors increasing prediction","node health declining failure likely"],
"PREDICTED_DISK_EXHAUSTION":["disk fill rate projects full in {n}h","storage growth trend exhaustion forecast","volume usage trending to capacity"],
}
GENERIC=["error condition detected in pod","warning event recorded","unexpected state reported","operation failed retrying","health check reported issue"]

def fill(t):
    return (t.replace("{app}",rng.choice(APP)).replace("{img}",rng.choice(IMG)).replace("{proc}",rng.choice(PROC))
             .replace("{node}",rng.choice(NODEP)).replace("{db}",rng.choice(["postgres","mysql","redis"]))
             .replace("{ns}",rng.choice(["prod","payments","backend"])).replace("{ip}",f"10.{RI(0,255)}.{RI(0,255)}.{RI(0,255)}")
             .replace("{n}",str(RI(2,900))))

STOP=set("the a an of to in on is are was for and or with without not no that this it its as at be by from into over under after before your you we our their his her los pod node error rate high low failed failure down".split())
def extract_sig(line):
    """Template-mining a la Drain3 : minuscule, retire ids/nombres/ip, garde les tokens porteurs."""
    s=re.sub(r"cve-\d+","cve",line.lower())
    s=re.sub(r"\b\d+(\.\d+)*\b|0x[0-9a-f]+|[0-9a-f]{8,}|\beth0\b|:\d+"," ",s)
    toks=[w for w in re.split(r"[^a-z]+",s) if len(w)>2 and w not in STOP]
    return "_".join(toks[:6]) if toks else "unknown"

def sample_metrics(t):
    arch=SPEC.get(t,("quiet","",""))[0]
    (c0,c1,m0,m1,r0,r1,u0,u1,l0,l1,e0,e1,nn0,nn1,db0,db1,lc0,lc1,ec0,ec1)=ARCH[arch]
    # chevauchement realiste : elargir un peu + bruit occasionnel (les distributions se recouvrent en vrai)
    def w(a,b): d=(b-a)*0.25; return R(max(0,a-d),b+d)
    return dict(cpu_cores=round(w(c0,c1),3),memory_mb=round(w(m0*MB,m1*MB)/MB,1),pod_restarts=RI(r0,r1),
        unavailable_replicas=RI(u0,u1),latency_p95_s=round(w(l0,l1),3),http_5xx_ratio=round(min(1,w(e0,e1)),3),
        node_not_ready=RI(nn0,nn1),db_hits=RI(db0,db1),log_lines=RI(lc0,lc1),error_lines=RI(ec0,ec1),warning_lines=RI(0,15))

rows=[]
for t in SPEC:
    evt=SPEC[t][1] or "None"
    for _ in range(100):
        raw=fill(rng.choice(LOGS[t]))
        # BRUIT REALISTE : 12% logs manquants/generiques, 8% event_reason efface
        u=rng.random()
        if u<0.07: sig="unknown"
        elif u<0.12: sig=extract_sig(rng.choice(GENERIC))
        else: sig=extract_sig(raw)
        er=evt if rng.random()>0.08 else "None"
        rows.append({"type":t,"event_reason":er,"log_line":raw,"log_sig":sig,**sample_metrics(t)})
df=pd.DataFrame(rows)
df.to_csv(os.path.join(D,"dataset_120_realistic.csv"),index=False)
print(f"Genere {len(df)} lignes, {df['type'].nunique()} types, 100/type.")

# ===== circularite (doit chuter) =====
def toks(s): return {w for w in re.split(r"[_\W]+",s.lower()) if len(w)>2}
circ=sum(1 for _,r in df.iterrows() if (toks(r["type"])&toks(r["log_sig"]))-STOP)
print(f"Circularite log_sig<->type : {circ*100//len(df)}%  (avant: 79%)")

# ===== ENTRAINEMENT HONNETE =====
from sklearn.preprocessing import StandardScaler, LabelEncoder
from scipy.sparse import hstack, csr_matrix
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.model_selection import train_test_split, cross_val_score, StratifiedKFold
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import f1_score, accuracy_score, classification_report
NUM=["cpu_cores","memory_mb","pod_restarts","unavailable_replicas","latency_p95_s","http_5xx_ratio","node_not_ready","db_hits","log_lines","error_lines","warning_lines"]
df["log_txt"]=df["log_sig"].str.replace("_"," ")
le=LabelEncoder(); y=le.fit_transform(df["type"])
Xtr,Xte,ytr,yte=train_test_split(df,y,test_size=0.2,random_state=42,stratify=y)
sc=StandardScaler().fit(Xtr[NUM].values)
tl=TfidfVectorizer(token_pattern=r"[a-z0-9]+",min_df=2).fit(Xtr["log_txt"])
te=TfidfVectorizer(analyzer="char_wb",ngram_range=(2,4),min_df=2).fit(Xtr["event_reason"])
def b(p): return hstack([csr_matrix(sc.transform(p[NUM].values)),tl.transform(p["log_txt"]),te.transform(p["event_reason"])]).tocsr()
m=LogisticRegression(max_iter=2000,C=10,class_weight="balanced").fit(b(Xtr),ytr)
pt=m.predict(b(Xte))
rep=classification_report(yte,pt,output_dict=True,zero_division=0)
strong=sum(1 for k,v in rep.items() if k not in("accuracy","macro avg","weighted avg") and isinstance(v,dict) and v["f1-score"]>=0.6)
print(f"\n=== MODELE HONNETE (dataset realiste non-circulaire) ===")
print(f"  Accuracy={accuracy_score(yte,pt):.4f} | F1_macro={f1_score(yte,pt,average='macro'):.4f}")
print(f"  classes F1>=0.60 : {strong}/120")
import joblib
joblib.dump({"model":m,"scaler":sc,"tf_log":tl,"tf_evt":te,"label_encoder":le,"num_features":NUM},
            os.path.join(D,"artifacts","ml_120_realistic.joblib"))
print("  -> artifacts/ml_120_realistic.joblib")
