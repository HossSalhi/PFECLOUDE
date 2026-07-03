"""Prouve le ROUTAGE auto : incident NODE_DISK_FULL dont la description porte 'action=HOST_IMAGE_GC'
-> executeRecommendation -> mapActionType -> HOST_IMAGE_GC (PAS RESTART_VM, grace a l'exemption)
-> Camunda cree le pod privilegie -> crictl rmi --prune. AUCUNE action manuelle creee."""
import urllib.request, json, sys, time, paramiko
BASE="http://192.168.148.158:30880"; KEY="C:/Users/houss/.ssh/id_ed25519"; MASTER="192.168.148.153"
def out(s): sys.stdout.buffer.write((s+"\n").encode("utf-8","replace")); sys.stdout.flush()
def http(m,p,tok=None,body=None):
    d=json.dumps(body).encode() if body is not None else None
    r=urllib.request.Request(BASE+p,data=d,method=m,headers={"Content-Type":"application/json",**({"Authorization":"Bearer "+tok} if tok else {})})
    with urllib.request.urlopen(r,timeout=15) as x:
        t=x.read().decode(); return json.loads(t) if t.strip() else {}
k=paramiko.Ed25519Key.from_private_key_file(KEY); c=paramiko.SSHClient(); c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect(MASTER,username="kube",pkey=k,timeout=12,allow_agent=False,look_for_keys=False)
def sh(cmd,t=60): _,o,e=c.exec_command(cmd,timeout=t); return (o.read().decode(errors="replace")+e.read().decode(errors="replace")).strip()
tok=http("POST","/api/auth/login",body={"username":"superadmin","password":"Helios2026!"}).get("accessToken")
out("[1] cree incident NODE_DISK_FULL avec 'action=HOST_IMAGE_GC' dans la description (PAS d'action manuelle)")
inc=http("POST","/api/incidents",tok,{"title":"NODE_DISK_FULL: k8s-worker2","description":"Type: NODE_DISK_FULL | action=HOST_IMAGE_GC | disque plein",
    "severity":"MEDIUM","source":"AI","namespace":"k8s-nodes","app":"k8s-worker2","incidentType":"NODE_DISK_FULL","status":"OPEN"})
iid=inc.get("id"); out(f"  incident id={iid}")
out("[2] POST /execute (routage via mapActionType)")
http("POST",f"/api/incidents/{iid}/execute",tok)
out("[3] attente + verif : action = HOST_IMAGE_GC (pas RESTART_VM) + pod host-exec")
prim=sh("kubectl get pods -n pfe -l cnpg.io/instanceRole=primary -o jsonpath='{.items[0].metadata.name}'")
final=None
for i in range(18):
    time.sleep(6)
    row=sh(f"kubectl exec -n pfe {prim} -c postgres -- psql -U postgres -d pfe_db -t -A -c \"SELECT type||'|'||status||'|'||COALESCE(left(executor_result,90),'') FROM incident_action WHERE incident_id={iid} ORDER BY id DESC LIMIT 1\" 2>&1")
    pod=sh("kubectl -n pfe get pods -l app=helios-hostexec --no-headers 2>/dev/null")
    out(f"  +{(i+1)*6}s action={row or '(aucune)'}"+(f" | pod={pod.split()[0] if pod else '-'}" if pod else ""))
    if row and ('|COMPLETED|' in row or '|FAILED|' in row): final=row; break
c.close()
out("\n=== ATTENDU : type=HOST_IMAGE_GC (PAS RESTART_VM), status COMPLETED, executee dans le cluster ===")
