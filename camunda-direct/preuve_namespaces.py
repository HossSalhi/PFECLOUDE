# -*- coding: utf-8 -*-
"""PREUVE v9 :
  A. chemin 'replicas' sur un namespace INCONNU du backend -> Camunda seul detecte ET execute
  B. garde-fou : meme incident dans un namespace SYSTEME -> detecte mais AUCUNE action"""
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
def ra(c,t=120):
    _,o,e=a.exec_command(c,timeout=t)
    return "\n".join(l for l in (o.read().decode(errors="replace")+e.read().decode(errors="replace")).splitlines() if "password for" not in l).strip()

def casser(ns, nom, creer_ns=True):
    if creer_ns: rm("kubectl create namespace %s 2>/dev/null || true" % ns)
    y = """apiVersion: apps/v1
kind: Deployment
metadata:
  name: {nom}
  namespace: {ns}
spec:
  replicas: 1
  selector:
    matchLabels:
      app: {nom}
  template:
    metadata:
      labels:
        app: {nom}
    spec:
      containers:
      - name: c
        image: nginx:0.0.0-inexistante
""".format(nom=nom, ns=ns)
    r = rm("cat <<'EOF' | kubectl apply -f -\n" + y + "EOF")
    if "created" not in r and "configured" not in r:
        out("   !! creation KO : " + r[:200])
    return r

def confirmer(ns, nom):
    """verifie que l'incident est REEL avant d'attendre Camunda"""
    for i in range(10):
        time.sleep(6)
        st = rm("kubectl get deploy %s -n %s -o jsonpath='ready={.status.readyReplicas} unavail={.status.unavailableReplicas}'" % (nom, ns))
        if "unavail=1" in st:
            out("   incident reel confirme : %s | pod : %s" % (st, rm("kubectl get pod -n %s -l app=%s --no-headers | awk '{print $3}'" % (ns, nom))))
            return True
    out("   !! incident NON confirme : " + st); return False

def ann(ns, nom, x):
    return rm("kubectl get deploy %s -n %s -o jsonpath='{.metadata.annotations.helios\\.camunda/%s}' 2>/dev/null" % (nom, ns, x))

def instances_recentes():
    q = ("curl -s --max-time 20 -X POST http://localhost:8080/v2/process-instances/search -H 'Content-Type: application/json' "
         "-d '{\"filter\":{\"processDefinitionId\":\"helios-camunda-full-cycle\"},\"sort\":[{\"field\":\"startDate\",\"order\":\"DESC\"}],\"page\":{\"limit\":6}}'")
    try: return json.loads(ra(q)).get("items", [])
    except: return []

def elements(key):
    q = ("curl -s --max-time 20 -X POST http://localhost:8080/v2/element-instances/search -H 'Content-Type: application/json' "
         "-d '{\"filter\":{\"processInstanceKey\":\"%s\"},\"page\":{\"limit\":40}}'" % key)
    try: return [i.get("elementId") for i in json.loads(ra(q)).get("items", [])]
    except: return []

def nettoyer():
    rm("kubectl delete deployment cam-demo -n demo-camunda --ignore-not-found >/dev/null 2>&1")
    rm("kubectl delete namespace demo-camunda --ignore-not-found >/dev/null 2>&1 &")
    rm("kubectl delete deployment cam-garde -n kube-public --ignore-not-found >/dev/null 2>&1")

# ============================================================
sec("A. NAMESPACE INCONNU DU BACKEND  (demo-camunda)")
nettoyer(); time.sleep(8)
casser("demo-camunda", "cam-demo")
confirmer("demo-camunda", "cam-demo")
out("   le backend HELIOS ne surveille PAS ce namespace -> si une action a lieu, elle vient de Camunda")
t0 = time.time(); okA = False
for i in range(14):
    time.sleep(15)
    if ann("demo-camunda", "cam-demo", "executedAt"):
        out("   [+%ds] ACTION EXECUTEE PAR CAMUNDA" % int(time.time()-t0)); okA = True; break
    if i % 3 == 0: out("   [+%ds] attente du cycle..." % int(time.time()-t0))
if okA:
    out("     aiType     : " + ann("demo-camunda","cam-demo","aiType"))
    out("     aiAction   : " + ann("demo-camunda","cam-demo","aiAction"))
    out("     result     : " + ann("demo-camunda","cam-demo","result"))
    out("     executedAt : " + ann("demo-camunda","cam-demo","executedAt"))
    out("   >>> A : CAMUNDA SEUL AGIT SUR UN NAMESPACE ARBITRAIRE — VALIDE")
else:
    out("   >>> A : ECHEC (aucune annotation)")
rm("kubectl delete deployment cam-demo -n demo-camunda --ignore-not-found >/dev/null 2>&1")
time.sleep(20)

# ============================================================
sec("B. GARDE-FOU : meme incident dans un namespace SYSTEME (kube-public)")
casser("kube-public", "cam-garde", creer_ns=False)
confirmer("kube-public", "cam-garde")
out("   attendu : Camunda DETECTE mais n'execute AUCUNE action (escalade humaine)")
t0 = time.time(); vu = False
for i in range(12):
    time.sleep(15)
    if ann("kube-public", "cam-garde", "executedAt"):
        out("   [+%ds] !!! UNE ACTION A ETE EXECUTEE — GARDE-FOU INEFFICACE" % int(time.time()-t0)); vu = True; break
    if i % 3 == 0: out("   [+%ds] aucune action (attendu)..." % int(time.time()-t0))
out("\n   parcours des dernieres instances Camunda :")
for inst in instances_recentes()[:4]:
    key = str(inst.get("processInstanceKey")); els = elements(key)
    fin = [e for e in els if e in ("FinProtege","FinExec","FinSain")]
    out("     %s -> %s" % (key, ", ".join(fin) or "en cours"))
protege = any("FinProtege" in elements(str(i.get("processInstanceKey"))) for i in instances_recentes())
if not vu and protege:
    out("   >>> B : GARDE-FOU VALIDE — detecte, route vers FinProtege, ZERO action sur le systeme")
elif not vu:
    out("   >>> B : aucune action executee (bon), mais FinProtege non observe dans les 4 dernieres instances")
else:
    out("   >>> B : ECHEC")

sec("NETTOYAGE")
nettoyer(); time.sleep(10)
out("   deploiements residuels : " + (rm("kubectl get deploy -A --no-headers | grep -E 'cam-demo|cam-garde' | wc -l")))
out("   apps pfe KO : " + (rm("kubectl get deploy -n pfe --no-headers | awk '{split($2,a,\"/\"); if(a[1]!=a[2]) print $1}' | tr '\\n' ' '") or "aucune"))
out("   coredns     : " + rm("kubectl get deploy coredns -n kube-system -o jsonpath='{.status.readyReplicas}/{.spec.replicas}'"))
out("\n   RESULTAT : A(namespace arbitraire)=%s | B(garde-fou systeme)=%s" % ("OK" if okA else "KO", "KO" if vu else "OK"))
m.close(); a.close()
