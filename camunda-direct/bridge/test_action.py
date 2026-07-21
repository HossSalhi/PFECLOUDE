# -*- coding: utf-8 -*-
"""Verifie que la voie SSH execute TOUT, y compris ce que RBAC refusait."""
import paramiko, sys, os, time, json
def out(s): sys.stdout.buffer.write((str(s)+"\n").encode("utf-8","replace")); sys.stdout.flush()
def sec(t): out("\n" + "="*74 + "\n  " + t + "\n" + "="*74)
SP = os.path.dirname(__file__)
JETON = open(os.path.join(SP, "bridge_token.txt")).read().strip()
NS = "demo-ssh"; APP = "cible"

a = paramiko.SSHClient(); a.set_missing_host_key_policy(paramiko.AutoAddPolicy())
a.connect("192.168.148.161", username="kube", password="kube", timeout=20, allow_agent=False, look_for_keys=False)
def ra(c,t=120):
    _,o,e=a.exec_command(c,timeout=t)
    return "\n".join(l for l in (o.read().decode(errors="replace")+e.read().decode(errors="replace")).splitlines() if "password for" not in l).strip()
k = paramiko.Ed25519Key.from_private_key_file("C:/Users/houss/.ssh/id_ed25519")
m = paramiko.SSHClient(); m.set_missing_host_key_policy(paramiko.AutoAddPolicy())
m.connect("192.168.148.153", username="kube", pkey=k, timeout=20, allow_agent=False, look_for_keys=False)
def rm(c,t=120):
    _,o,e=m.exec_command(c,timeout=t); return (o.read().decode(errors="replace")+e.read().decode(errors="replace")).strip()

def appel(chemin, corps):
    b = json.dumps(corps).replace("'", "'\\''")
    r = ra("curl -s --max-time 100 -X POST http://127.0.0.1:8099%s -H 'Content-Type: application/json' -H 'X-Helios-Token: %s' -d '%s'" % (chemin, JETON, b))
    try: return json.loads(r)
    except: return {"stdout": r[:200], "exitCode": "?", "ok": False}

sec("PREPARATION")
rm("kubectl create namespace %s 2>/dev/null || true" % NS)
Y = """apiVersion: apps/v1
kind: Deployment
metadata: {name: cible, namespace: demo-ssh}
spec:
  replicas: 1
  selector: {matchLabels: {app: cible}}
  template:
    metadata: {labels: {app: cible}}
    spec:
      containers: [{name: c, image: 'stefanprodan/podinfo:6.7.0'}]
"""
rm("cat <<'EOF' | kubectl apply -f -\n" + Y + "EOF")
for i in range(12):
    time.sleep(6)
    if "1/1" == rm("kubectl get deploy %s -n %s -o jsonpath='{.status.readyReplicas}/{.spec.replicas}'" % (APP,NS)): break
out("   %s/%s pret" % (NS,APP))

sec("A. CE QUE RBAC REFUSAIT — la voie SSH le fait-elle ?")
SA = "--as=system:serviceaccount:pfe:camunda-direct"
essais = [
    ("lire les Secrets      ", "kubectl get secrets -n %s --no-headers | wc -l" % NS, "get secrets -n "+NS),
    ("creer un pod          ", "kubectl run test-ssh --image=stefanprodan/podinfo:6.7.0 -n %s --restart=Never 2>&1 | tail -1" % NS, "create pods -n "+NS),
    ("supprimer un deploy   ", "kubectl delete deployment cible -n %s 2>&1 | tail -1" % NS, "delete deployments -n "+NS),
]
for lib, cmd, canI in essais:
    rbac = rm("kubectl auth can-i %s %s" % (canI, SA))
    r = appel("/exec", {"host":"master","command":cmd})
    out("   %s RBAC=%-3s | SSH exit=%s -> %s" % (lib, rbac, r.get("exitCode"), (r.get("stdout") or r.get("stderr",""))[:55]))

sec("B. CATALOGUE D'ACTIONS (traduction IA -> commande reelle)")
rm("cat <<'EOF' | kubectl apply -f -\n" + Y + "EOF"); time.sleep(12)
for action in ["FORCE_RECREATE_DEPLOYMENT","SCALE_DEPLOYMENT","SCALE_DOWN","RESTART_POD",
               "REMOVE_NETWORK_POLICY","ROLLBACK_DEPLOYMENT","ACTION_QUI_NEXISTE_PAS"]:
    r = appel("/action", {"action":action,"ns":NS,"cible":APP,"noeud":"master"})
    out("   %-30s execute=%-5s exit=%-3s %s" % (action, r.get("execute"), r.get("exitCode"),
        (r.get("stdout") or r.get("stderr",""))[:60].replace("\n"," ")))
    time.sleep(3)

sec("C. ACTIONS NIVEAU NOEUD")
r = appel("/action", {"action":"CORDON_NODE","ns":NS,"cible":"k8s-worker2","noeud":"master"})
out("   CORDON_NODE   exit=%s %s" % (r.get("exitCode"), r.get("stdout","")[:60]))
out("   etat worker2  : " + rm("kubectl get node k8s-worker2 --no-headers | awk '{print $2}'"))
out("   " + rm("kubectl uncordon k8s-worker2"))
out("   etat worker2  : " + rm("kubectl get node k8s-worker2 --no-headers | awk '{print $2}'"))
r = appel("/action", {"action":"RESTART_KUBELET","ns":NS,"cible":APP,"noeud":"k8s-worker1"})
out("   RESTART_KUBELET sur worker1 exit=%s -> %s" % (r.get("exitCode"), r.get("stdout","")[:50]))
time.sleep(15)
out("   noeuds        : " + rm("kubectl get nodes --no-headers | awk '{print $1\"=\"$2}' | tr '\\n' ' '"))

sec("NETTOYAGE")
rm("kubectl delete namespace %s --ignore-not-found >/dev/null 2>&1 &" % NS)
time.sleep(10)
out("   noeuds     : " + rm("kubectl get nodes --no-headers | awk '{print $1\"=\"$2}' | tr '\\n' ' '"))
out("   apps pfe KO: " + (rm("kubectl get deploy -n pfe --no-headers | awk '{split($2,a,\"/\"); if(a[1]!=a[2]) print $1}' | tr '\\n' ' '") or "aucune"))
m.close(); a.close()
