#!/usr/bin/env python3
"""Clean slate AVANT un test manuel (l'utilisateur declenche la panne lui-meme) :
  - detecte le PRIMARY CNPG dynamiquement (le primary a pu basculer hors postgres-1)
  - annule les process Camunda actifs (evite les orphelins)
  - purge incidents + workflow + lease leader (sinon le polling reste bloque ~TTL)
NE re-applique PAS les apps, NE declenche AUCUNE panne."""
import paramiko, time, urllib.request, json

KEY="C:/Users/houss/.ssh/id_ed25519"; MASTER="192.168.148.153"
CAMUNDA="http://192.168.148.161:8080"

def conn(host=MASTER):
    k=paramiko.Ed25519Key.from_private_key_file(KEY)
    c=paramiko.SSHClient(); c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(host,username="kube",pkey=k,timeout=10,allow_agent=False,look_for_keys=False); return c
def run(c,cmd,t=120):
    _,o,e=c.exec_command(cmd,timeout=t)
    return (o.read().decode(errors="replace")+e.read().decode(errors="replace")).strip()

c=conn()
print("[1] detection du PRIMARY CNPG:")
primary=run(c,"kubectl get pods -n pfe -l cnpg.io/instanceRole=primary -o jsonpath='{.items[0].metadata.name}'").strip().strip("'")
if not primary:
    primary="postgres-1"; print("   [warn] primary non detecte -> fallback postgres-1")
print(f"   primary = {primary}")
def sql(q):
    return run(c,f"kubectl exec -n pfe {primary} -c postgres -- psql postgresql://appuser:postgres@localhost/pfe_db -c \"{q}\"")

print("[2] annulation des process Camunda actifs:")
def cam_post(path, body):
    req=urllib.request.Request(CAMUNDA+path, data=json.dumps(body).encode(), headers={"Content-Type":"application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as r: return r.status, r.read().decode()
    except urllib.error.HTTPError as e: return e.code, ""
    except Exception as e: return None, str(e)[:80]
cancelled=0
for _ in range(40):
    st,resp=cam_post("/v2/process-instances/search", {"filter":{"state":"ACTIVE"},"page":{"limit":100}})
    if st!=200:
        print(f"   [warn] PI search status={st} resp={resp[:120]}"); break
    j=json.loads(resp); items=j.get("items",[]); total=j.get("page",{}).get("totalItems",0)
    if not items:
        print(f"   active PIs restants: {total}"); break
    for it in items:
        s,_=cam_post(f"/v2/process-instances/{it['processInstanceKey']}/cancellation", {})
        if s in (200,204): cancelled+=1
    time.sleep(1)
print(f"   process Camunda annules: {cancelled}")

print("[3] purge incidents + lease leader:")
tx=("BEGIN; DELETE FROM human_task; DELETE FROM alert; DELETE FROM incident_action; "
    "DELETE FROM incident_history; DELETE FROM incident_decision; DELETE FROM workflow_logs; "
    "DELETE FROM workflow_instance; DELETE FROM incident; "
    "DELETE FROM leader_lease WHERE lease_name='aiops-scheduler'; COMMIT;")
print(sql(tx))
print("incidents restants:",sql("SELECT count(*) FROM incident;"))
c.close()
print("DONE clean slate (table vide, lease purgee, aucune panne declenchee)")
