# -*- coding: utf-8 -*-
"""Une sonde de readiness rend-elle le blocage reseau DETECTABLE ?
Et calibrage du bruit Loki hors namespaces systeme."""
import paramiko, sys, time, json
def out(s): sys.stdout.buffer.write((str(s)+"\n").encode("utf-8","replace")); sys.stdout.flush()
def sec(t): out("\n" + "="*74 + "\n  " + t + "\n" + "="*74)
k = paramiko.Ed25519Key.from_private_key_file("C:/Users/houss/.ssh/id_ed25519")
m = paramiko.SSHClient(); m.set_missing_host_key_policy(paramiko.AutoAddPolicy())
m.connect("192.168.148.153", username="kube", pkey=k, timeout=20, allow_agent=False, look_for_keys=False)
def rm(c,t=120):
    _,o,e=m.exec_command(c,timeout=t); return (o.read().decode(errors="replace")+e.read().decode(errors="replace")).strip()
a = paramiko.SSHClient(); a.set_missing_host_key_policy(paramiko.AutoAddPolicy())
a.connect("192.168.148.161", username="kube", password="kube", timeout=20, allow_agent=False, look_for_keys=False)
def ra(c,t=150):
    _,o,e=a.exec_command(c,timeout=t)
    return "\n".join(l for l in (o.read().decode(errors="replace")+e.read().decode(errors="replace")).splitlines() if "password for" not in l).strip()
NS="pfe"; APP="net-probe"

sec("A. APP DE TEST AVEC SONDE DE READINESS DEPENDANTE DU RESEAU")
rm("kubectl delete deploy %s -n %s --ignore-not-found >/dev/null 2>&1" % (APP,NS)); time.sleep(5)
Y = """apiVersion: apps/v1
kind: Deployment
metadata:
  name: net-probe
  namespace: pfe
  labels: {helios.io/monitor: "false"}
spec:
  replicas: 1
  selector:
    matchLabels:
      app: net-probe
  template:
    metadata:
      labels:
        app: net-probe
    spec:
      containers:
      - name: c
        image: stefanprodan/podinfo:6.7.0
        ports:
        - containerPort: 9898
        readinessProbe:
          exec:
            command: ["/bin/sh","-c","nslookup kubernetes.default.svc.cluster.local >/dev/null 2>&1 || exit 1"]
          initialDelaySeconds: 5
          periodSeconds: 10
          failureThreshold: 2
"""
out("   " + rm("cat <<'EOF' | kubectl apply -f -\n" + Y + "EOF"))
ok=False
for i in range(12):
    time.sleep(10)
    st = rm("kubectl get deploy %s -n %s -o jsonpath='ready={.status.readyReplicas}'" % (APP,NS))
    if "ready=1" in st: ok=True; break
out("   app prete (sonde DNS OK) : %s" % ok)
if not ok:
    out("   pods : " + rm("kubectl get pod -n %s -l app=%s --no-headers" % (NS,APP)))

sec("B. BLOCAGE DNS PAR NETWORKPOLICY -> la sonde doit echouer")
Y2 = """apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: bloque-dns
  namespace: pfe
spec:
  podSelector:
    matchLabels:
      app: net-probe
  policyTypes:
  - Egress
  egress:
  - to:
    - ipBlock:
        cidr: 10.0.0.0/8
    ports:
    - protocol: TCP
      port: 443
"""
out("   " + rm("cat <<'EOF' | kubectl apply -f -\n" + Y2 + "EOF"))
casse=False
for i in range(10):
    time.sleep(12)
    st = rm("kubectl get deploy %s -n %s -o jsonpath='ready={.status.readyReplicas} unavail={.status.unavailableReplicas}'" % (APP,NS))
    out("   [+%ds] %s" % (12*(i+1), st))
    if "unavail=1" in st: casse=True; break
out("   >>> blocage DNS detectable par le chemin 'replicas' : %s" % ("OUI" if casse else "NON"))

sec("C. QUE DIT L'IA SUR CET INCIDENT RESEAU REEL ?")
if casse:
    time.sleep(30)
    body = json.dumps({"namespace": NS, "app": APP, "windowMinutes": 5})
    r = ra("curl -s --max-time 90 -X POST http://localhost:8000/predict -H 'Content-Type: application/json' -d '%s'" % body)
    try:
        d = json.loads(r); act = str(d.get("final_executable_action") or d.get("recommended_action") or "")
        out("   incident_type : %s" % d.get("incident_type"))
        out("   action        : %s  (confiance %s)" % (act, d.get("confidence")))
        out("   >>> branche empruntee dans le cycle REEL : %s"
            % ("NETWORK" if "NETWORK" in act else ("SCALE" if "SCALE" in act else "defaut (restart)")))
    except: out("   " + r[:400])
else:
    out("   (non applicable)")

sec("D. CALIBRAGE DU BRUIT LOKI (hors namespaces systeme)")
for q,lib in [('topk(5, sum by (filename) (count_over_time({job="k8s-containers", filename=~".*_(pfe|default|aiops)_.*"} |~ "(?i)(error|exception|panic|fatal)" [5m])))', "apps (pfe/default/aiops)"),
              ('topk(3, sum by (filename) (count_over_time({job="k8s-containers"} |~ "(?i)(error|exception|panic|fatal)" [5m])))', "tous namespaces")]:
    r = ra("curl -s --max-time 25 -G 'http://192.168.148.160:3100/loki/api/v1/query' --data-urlencode 'query=%s'" % q)
    out("   -- %s" % lib)
    try:
        for s in json.loads(r).get("data",{}).get("result",[]):
            fn = s["metric"].get("filename","?").split("/")[-1]
            out("      %-70s %s" % (fn[:70], s["value"][1]))
    except: out("      " + r[:200])

sec("NETTOYAGE")
rm("kubectl delete networkpolicy bloque-dns -n %s --ignore-not-found >/dev/null 2>&1" % NS)
rm("kubectl delete deploy %s -n %s --ignore-not-found >/dev/null 2>&1" % (APP,NS))
time.sleep(8)
out("   netpols : %s | apps pfe KO : %s" % (rm("kubectl get networkpolicy -n %s --no-headers 2>/dev/null | wc -l" % NS),
    rm("kubectl get deploy -n pfe --no-headers | awk '{split($2,a,\"/\"); if(a[1]!=a[2]) print $1}' | tr '\\n' ' '") or "aucune"))
m.close(); a.close()
