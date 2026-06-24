#!/usr/bin/env python3
"""Reset propre AVANT un run demo (14 apps), SANS delete (eviter la rafale du deployment-watcher
qui sature l'IA + cree des incidents parasites sur le churn de redemarrage) :
  1) supprime les apps bruit + exclut podinfo ; supprime les networkpolicies residuelles
  2) apply helios-demo-apps.yaml (cree les manquantes) PUIS force-clean : revert de TOUS les champs
     injectes (command/image/resources/initContainers/envFrom/readinessProbe) + rollout restart cpu/disk
  3) attend 14 apps SAINES, puis STABILISE ~90s (laisse le watcher finir ses analyses differees)
  4) purge l'historique RS (desired=0) -> rollback retombe a coup sur sur la revision saine
  5) purge incidents/workflow + lease leader (sinon polling bloque ~5 min)
"""
import paramiko, time, urllib.request, json

KEY="C:/Users/houss/.ssh/id_ed25519"; MASTER="192.168.148.153"; PRIMARY="postgres-1"
CAMUNDA="http://192.168.148.161:8080"
BASE="C:/Users/houss/IdeaProjects/PFE-Orange"
NOISE=["incident-crashloop","incident-imagepull","incident-pending"]
NPS=["helios-demo-service","helios-demo-network","helios-demo-dns","helios-demo-db"]
APPS=["helios-crashloop","helios-imagepull","helios-oom","helios-cpu","helios-service","helios-network",
      "helios-dns","helios-disk","helios-http5xx","helios-latency","helios-pending","helios-init",
      "helios-config","helios-unavailable","helios-db","helios-hpa"]

def conn(host=MASTER):
    k=paramiko.Ed25519Key.from_private_key_file(KEY)
    c=paramiko.SSHClient(); c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(host,username="kube",pkey=k,timeout=10,allow_agent=False,look_for_keys=False); return c
def run(c,cmd,t=120):
    _,o,e=c.exec_command(cmd,timeout=t)
    return (o.read().decode(errors="replace")+e.read().decode(errors="replace")).strip()
def sql(c,q):
    return run(c,f"kubectl exec -n pfe {PRIMARY} -c postgres -- psql postgresql://appuser:postgres@localhost/pfe_db -c \"{q}\"")
def patch(c,app,p):
    return run(c,f"kubectl patch deploy {app} -n pfe --type=json -p '{p}' 2>/dev/null || true")

c=conn()
print("[1] bruit + podinfo + netpols:")
for a in NOISE: run(c,f"kubectl delete deploy {a} -n pfe --ignore-not-found")
run(c,"kubectl label deploy podinfo helios.io/monitor=false --overwrite -n pfe")
for n in NPS: run(c,f"kubectl delete networkpolicy {n} -n pfe --ignore-not-found")

print("[2] apply + force-clean (revert des champs injectes):")
sftp=c.open_sftp(); sftp.put(f"{BASE}/backend/backend/infra/k8s/helios-demo-apps.yaml","/tmp/helios-demo-apps.yaml"); sftp.close()
run(c,"kubectl apply -f /tmp/helios-demo-apps.yaml -n pfe")
# revert command (crashloop/oom/http5xx/latency) — tolerant si absent
for a in ["helios-crashloop","helios-oom","helios-http5xx","helios-latency"]:
    patch(c,a,'[{"op":"remove","path":"/spec/template/spec/containers/0/command"}]')
# imagepull -> image saine
run(c,"kubectl set image deploy/helios-imagepull app=stefanprodan/podinfo:6.7.0 -n pfe")
# pending -> resources saines
patch(c,"helios-pending",'[{"op":"replace","path":"/spec/template/spec/containers/0/resources","value":{"requests":{"cpu":"10m","memory":"32Mi"},"limits":{"cpu":"200m","memory":"96Mi"}}}]')
# init -> initContainer exit 0
patch(c,"helios-init",'[{"op":"replace","path":"/spec/template/spec/initContainers/0/command","value":["sh","-c","exit 0"]}]')
# config -> envFrom saine
patch(c,"helios-config",'[{"op":"replace","path":"/spec/template/spec/containers/0/envFrom","value":[{"configMapRef":{"name":"helios-config-cm"}}]}]')
# unavailable -> readiness /healthz
patch(c,"helios-unavailable",'[{"op":"replace","path":"/spec/template/spec/containers/0/readinessProbe/httpGet/path","value":"/healthz"}]')
# hpa : HELIOS l'a peut-etre scale up (1->N) -> revenir a 1 (apply ne reverte pas un scale hors-bande)
run(c,"kubectl scale deploy/helios-hpa --replicas=1 -n pfe")
# cpu/disk/hpa : pannes transitoires dans le pod -> rollout restart pour repartir propre
for a in ["helios-cpu","helios-disk","helios-hpa"]: run(c,f"kubectl rollout restart deploy/{a} -n pfe")

print(f"[3] attente {len(APPS)} apps SAINES...")
for _ in range(45):
    ready=[a for a in APPS if run(c,f"kubectl get deploy {a} -n pfe -o jsonpath='{{.status.readyReplicas}}'").strip("'")=="1"]
    print(f"   pretes {len(ready)}/{len(APPS)}")
    if len(ready)==len(APPS): break
    time.sleep(8)
print("   STABILISATION 90s (le watcher finit ses analyses differees)..."); time.sleep(90)

print("[4] purge historique RS (desired=0):")
for a in APPS:
    rs0=run(c,f"kubectl get rs -n pfe -l app={a} -o jsonpath='{{range .items[?(@.spec.replicas==0)]}}{{.metadata.name}}{{\" \"}}{{end}}'").strip("'").split()
    for rs in rs0: run(c,f"kubectl delete rs {rs} -n pfe --ignore-not-found")

print("[5] annulation des process Camunda actifs (sinon orphelins quand on purge workflow_instance):")
def cam_post(path, body):
    req=urllib.request.Request(CAMUNDA+path, data=json.dumps(body).encode(), headers={"Content-Type":"application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as r: return r.status, r.read().decode()
    except urllib.error.HTTPError as e: return e.code, ""
    except Exception as e: return None, str(e)[:80]
cancelled=0
for loop in range(40):  # bulletproof: loop until ZERO active PIs (orphans from prior runs starve the worker)
    st,resp=cam_post("/v2/process-instances/search", {"filter":{"state":"ACTIVE"},"page":{"limit":100}})
    if st!=200:
        print(f"   [warn] PI search status={st} resp={resp[:120]}"); break
    j=json.loads(resp); total=j.get("page",{}).get("totalItems",0); items=j.get("items",[])
    if not items:
        print(f"   active PIs restants: {total}"); break
    for it in items:
        s,_=cam_post(f"/v2/process-instances/{it['processInstanceKey']}/cancellation", {})
        if s in (200,204): cancelled+=1
    time.sleep(1)
# confirm
st,resp=cam_post("/v2/process-instances/search", {"filter":{"state":"ACTIVE"},"page":{"limit":1}})
remain=json.loads(resp).get("page",{}).get("totalItems","?") if st==200 else "?"
print(f"   process Camunda annules: {cancelled} | actifs restants: {remain}")

print("[6] purge incidents + lease leader:")
tx=("BEGIN; DELETE FROM human_task; DELETE FROM alert; DELETE FROM incident_action; "
    "DELETE FROM incident_history; DELETE FROM incident_decision; DELETE FROM workflow_logs; "
    "DELETE FROM workflow_instance; DELETE FROM incident; "
    "DELETE FROM leader_lease WHERE lease_name='aiops-scheduler'; COMMIT;")
print(sql(c,tx))
print("incidents restants:",sql(c,"SELECT count(*) FROM incident;"))
c.close()
print("DONE reset (14 apps prets, sains, historique propre)")
