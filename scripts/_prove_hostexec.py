"""PREUVE : Camunda exécute une commande HÔTE (image-gc) DANS LE CLUSTER via un pod privilégié.
Incident noeud k8s-worker2 -> action HOST_IMAGE_GC -> POST /execute -> Camunda -> l'exécuteur crée
un pod privilégié busybox sur worker2 qui lance 'crictl rmi --prune' dans l'espace hôte."""
import urllib.request, json, sys, time, paramiko
BASE = "http://192.168.148.158:30880"
KEY = "C:/Users/houss/.ssh/id_ed25519"; MASTER = "192.168.148.153"
def out(s): sys.stdout.buffer.write((s+"\n").encode("utf-8","replace")); sys.stdout.flush()
def http(method, path, tok=None, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE+path, data=data, method=method,
        headers={"Content-Type":"application/json", **({"Authorization":"Bearer "+tok} if tok else {})})
    with urllib.request.urlopen(req, timeout=15) as r:
        t = r.read().decode()
        return json.loads(t) if t.strip() else {}
# SSH pour verifier logs/pods
k = paramiko.Ed25519Key.from_private_key_file(KEY); c = paramiko.SSHClient(); c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect(MASTER, username="kube", pkey=k, timeout=12, allow_agent=False, look_for_keys=False)
def sh(cmd, t=60): _,o,e=c.exec_command(cmd,timeout=t); return (o.read().decode(errors="replace")+e.read().decode(errors="replace")).strip()

out("[1] login"); tok = http("POST","/api/auth/login",body={"username":"superadmin","password":"Helios2026!"}).get("accessToken")
out("  token="+("OK" if tok else "KO")); assert tok
out("[2] cree incident noeud k8s-worker2 (NODE_DISK_FULL)")
inc = http("POST","/api/incidents",tok,{"title":"NODE_DISK_FULL: k8s-worker2","description":"Type: NODE_DISK_FULL | disque plein",
    "severity":"MEDIUM","source":"AI","namespace":"k8s-nodes","app":"k8s-worker2","incidentType":"NODE_DISK_FULL","status":"OPEN"})
iid = inc.get("id"); out(f"  incident id={iid}")
out("[3] cree action HOST_IMAGE_GC (PENDING, executable)")
act = http("POST","/api/actions",tok,{"incidentId":iid,"type":"HOST_IMAGE_GC","description":"host image-gc sur le noeud (in-cluster)"})
out(f"  action id={act.get('id')} type={act.get('type')}")
out("[4] POST /execute -> Camunda execute la commande hote via pod privilegie")
http("POST",f"/api/incidents/{iid}/execute",tok)
out("[5] attente + preuve (pod host-exec sur worker2 + logs backend)")
seen=False
for i in range(20):
    time.sleep(6)
    pods = sh("kubectl -n pfe get pods -l app=helios-hostexec -o wide --no-headers 2>/dev/null")
    lg = sh("kubectl -n pfe logs -l app=backend --tail=200 | grep -iE 'host-exec' | tail -n 4")
    if pods or lg:
        seen=True
        out(f"  +{(i+1)*6}s POD: {pods or '(supprime apres exec)'}")
        if lg: out("  LOG: "+lg.replace(chr(10),' | '))
        if 'image-gc' in lg and ('Succeeded' in lg or 'RESOLVED' in lg or 'DANS LE CLUSTER' in lg): break
out("\n[6] etat final incident + action")
prim=sh("kubectl get pods -n pfe -l cnpg.io/instanceRole=primary -o jsonpath='{.items[0].metadata.name}'")
out(sh(f"kubectl exec -n pfe {prim} -c postgres -- psql -U postgres -d pfe_db -t -A -c \"SELECT a.type,a.status,a.executor_result FROM incident_action a WHERE a.incident_id={iid}\" 2>&1")[:500])
c.close()
out("\n=== ATTENDU : pod helios-hostexec cree sur k8s-worker2 + log 'host-exec ... image-gc ... DANS LE CLUSTER' ===")
