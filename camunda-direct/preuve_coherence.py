# -*- coding: utf-8 -*-
"""GARDE-FOU DE COHERENCE :
  1. NON-REGRESSION : vrai ImagePull -> premisse confirmee par kubelet -> action EXECUTEE
  2. GARDE-FOU      : blocage DNS reel -> l'IA dit IMAGE (a tort) -> AUCUNE action, FinIncoherent"""
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

def ann(ns,nom,x): return rm("kubectl get deploy %s -n %s -o jsonpath='{.metadata.annotations.helios\\.camunda/%s}' 2>/dev/null" % (nom,ns,x))
def instances():
    q = ("curl -s --max-time 20 -X POST http://localhost:8080/v2/process-instances/search -H 'Content-Type: application/json' "
         "-d '{\"filter\":{\"processDefinitionId\":\"helios-camunda-full-cycle\"},\"sort\":[{\"field\":\"startDate\",\"order\":\"DESC\"}],\"page\":{\"limit\":6}}'")
    try: return [str(i["processInstanceKey"]) for i in json.loads(ra(q)).get("items",[])]
    except: return []
def elements(key):
    q = ("curl -s --max-time 20 -X POST http://localhost:8080/v2/element-instances/search -H 'Content-Type: application/json' "
         "-d '{\"filter\":{\"processInstanceKey\":\"%s\"},\"page\":{\"limit\":40}}'" % key)
    try: return [i.get("elementId") for i in json.loads(ra(q)).get("items",[])]
    except: return []
def fins(n=4):
    r=[]
    for key in instances()[:n]:
        f=[e for e in elements(key) if e.startswith("Fin")]
        r.append("%s -> %s" % (key, ", ".join(f) or "en cours"))
    return r
def nettoyer():
    rm("kubectl delete networkpolicy bloque-dns -n pfe --ignore-not-found >/dev/null 2>&1")
    rm("kubectl delete deploy net-probe -n pfe --ignore-not-found >/dev/null 2>&1")
    rm("kubectl delete deploy cam-demo -n demo-camunda --ignore-not-found >/dev/null 2>&1")

nettoyer(); time.sleep(10)

# ================= 1. NON-REGRESSION =================
sec("1. NON-REGRESSION : vrai ImagePull (premisse CONFIRMEE) -> action attendue")
rm("kubectl create namespace demo-camunda 2>/dev/null || true")
Y = """apiVersion: apps/v1
kind: Deployment
metadata: {name: cam-demo, namespace: demo-camunda}
spec:
  replicas: 1
  selector: {matchLabels: {app: cam-demo}}
  template:
    metadata: {labels: {app: cam-demo}}
    spec:
      containers:
      - {name: c, image: nginx:0.0.0-inexistante}
"""
out("   " + rm("cat <<'EOF' | kubectl apply -f -\n" + Y + "EOF"))
t0=time.time(); ok1=False
for i in range(14):
    time.sleep(15)
    if ann("demo-camunda","cam-demo","executedAt"): ok1=True; break
    if i%3==0: out("   [+%ds] attente..." % int(time.time()-t0))
if ok1:
    out("   [+%ds] ACTION EXECUTEE : %s -> %s (%s)" % (int(time.time()-t0),
        ann("demo-camunda","cam-demo","aiType"), ann("demo-camunda","cam-demo","aiAction"), ann("demo-camunda","cam-demo","result")))
    out("   >>> 1 : NON-REGRESSION OK — le garde-fou ne bloque pas un diagnostic correct")
else:
    out("   >>> 1 : ECHEC — le garde-fou bloque a tort")
rm("kubectl delete deploy cam-demo -n demo-camunda --ignore-not-found >/dev/null 2>&1")
time.sleep(45)

# ================= 2. GARDE-FOU =================
sec("2. GARDE-FOU : blocage DNS reel, l'IA dira IMAGE a tort -> AUCUNE action attendue")
Y2 = """apiVersion: apps/v1
kind: Deployment
metadata: {name: net-probe, namespace: pfe}
spec:
  replicas: 1
  selector: {matchLabels: {app: net-probe}}
  template:
    metadata: {labels: {app: net-probe}}
    spec:
      containers:
      - name: c
        image: stefanprodan/podinfo:6.7.0
        readinessProbe:
          exec:
            command: ["/bin/sh","-c","nslookup kubernetes.default.svc.cluster.local >/dev/null 2>&1 || exit 1"]
          initialDelaySeconds: 5
          periodSeconds: 10
          failureThreshold: 2
"""
out("   " + rm("cat <<'EOF' | kubectl apply -f -\n" + Y2 + "EOF"))
for i in range(12):
    time.sleep(10)
    if "ready=1" in rm("kubectl get deploy net-probe -n pfe -o jsonpath='ready={.status.readyReplicas}'"): break
out("   app prete")
NP = """apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata: {name: bloque-dns, namespace: pfe}
spec:
  podSelector: {matchLabels: {app: net-probe}}
  policyTypes: [Egress]
  egress:
  - to: [{ipBlock: {cidr: 10.0.0.0/8}}]
    ports: [{protocol: TCP, port: 443}]
"""
out("   " + rm("cat <<'EOF' | kubectl apply -f -\n" + NP + "EOF"))
for i in range(8):
    time.sleep(10)
    st = rm("kubectl get deploy net-probe -n pfe -o jsonpath='unavail={.status.unavailableReplicas}'")
    if "unavail=1" in st: break
out("   incident reseau reel confirme : %s" % st)
out("   (kubelet ne signale AUCUN ImagePull dans pfe -> la premisse de l'IA sera contredite)")
# on attend le VERDICT (FinIncoherent), pas un delai fixe : la metrique doit d'abord
# remonter jusqu'a kube-state-metrics, ce qui prend ~40s de plus que kubectl.
t0=time.time(); agi=False; incoh=False
for i in range(24):
    time.sleep(15)
    if ann("pfe","net-probe","executedAt"):
        out("   [+%ds] !!! ACTION EXECUTEE — GARDE-FOU INEFFICACE" % int(time.time()-t0)); agi=True; break
    if any("FinIncoherent" in elements(kk) for kk in instances()[:3]):
        out("   [+%ds] VERDICT : FinIncoherent" % int(time.time()-t0)); incoh=True; break
    if i%3==0: out("   [+%ds] aucune action (attendu)..." % int(time.time()-t0))
out("\n   parcours des dernieres instances :")
for l in fins(5): out("     " + l)
if not agi and incoh:
    out("   >>> 2 : GARDE-FOU VALIDE — diagnostic contredit, route vers FinIncoherent, ZERO action")
elif not agi:
    out("   >>> 2 : aucune action (bon) mais FinIncoherent non observe")
else:
    out("   >>> 2 : ECHEC")

sec("NETTOYAGE")
nettoyer()
rm("kubectl delete namespace demo-camunda --ignore-not-found >/dev/null 2>&1 &")
time.sleep(12)
out("   netpols pfe : %s | apps pfe KO : %s" % (rm("kubectl get networkpolicy -n pfe --no-headers 2>/dev/null | wc -l"),
    rm("kubectl get deploy -n pfe --no-headers | awk '{split($2,a,\"/\"); if(a[1]!=a[2]) print $1}' | tr '\\n' ' '") or "aucune"))
out("\n   RESULTAT : non-regression=%s | garde-fou=%s" % ("OK" if ok1 else "KO", "OK" if (not agi and incoh) else "a verifier"))
m.close(); a.close()
