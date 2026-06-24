"""
Lance les 14 tests de chaos pour que HELIOS les RESOLVE TOUS lui-meme.
Pour les pannes persistantes (crashloop/memory/network/service/dns/hpa), la panne est
levee ~50s apres le declenchement (PENDANT la verification HELIOS) -> HELIOS constate le
retablissement -> RESOLVED. Les autres s'auto-resolvent (panne transitoire).
Usage : python scripts/run_all_14.py
"""
import subprocess, time, datetime, sys
PS1 = "scripts/chaos/helios-chaos.ps1"
def out(s): sys.stdout.buffer.write((s+"\n").encode("utf-8","replace")); sys.stdout.flush()
def ts(): return datetime.datetime.now().strftime("%H:%M:%S")
def ps(test, restore=False, t=180):
    a=["powershell","-ExecutionPolicy","Bypass","-File",PS1,test]
    if restore: a.append("-Restore")
    try: return subprocess.run(a,capture_output=True,text=True,timeout=t).stdout.strip()
    except Exception as e: return f"ERR:{e}"
def kubectl(*a):
    try: return subprocess.run(["kubectl",*a],capture_output=True,text=True,timeout=30).stdout.strip()
    except Exception as e: return f"ERR:{e}"
def sql(q): return kubectl("exec","-n","pfe","postgres-2","-c","postgres","--","psql","postgresql://appuser:postgres@localhost/pfe_db","-t","-A","-c",q)
def max_id():
    r=sql("SELECT COALESCE(MAX(id),0) FROM incident;"); return int(r) if r.strip().isdigit() else 0
def podinfo_ready(): return kubectl("get","deploy","podinfo","-n","pfe","-o","jsonpath={.status.readyReplicas}").strip()=="2"

# INTEGRITE : on n'enleve PLUS la panne pendant la verif (plus de faux RESOLVED). La panne
# PERSISTE -> HELIOS doit la corriger lui-meme (rollback) ou escalader honnetement. On fait le
# teardown (restauration baseline) UNIQUEMENT APRES le verdict de HELIOS = hygiene de test, pas une triche.
TEARDOWN={"crashloop","memory","service","network","dns","hpa","eviction","node"}
ORDER=["podkill","container","http503","latency","cpu","disk","db",
       "eviction","node","hpa","crashloop","memory","service","network","dns"]

out(f"[{ts()}] === 14 TESTS — HELIOS doit RESOUDRE ou ESCALADER lui-meme (panne persistante) ===")
out("setup..."); ps("setup"); time.sleep(15)
results=[]
for name in ORDER:
    out(f"\n========== {name} ==========")
    base=max_id(); out(f"[{ts()}] declenche (baseline={base}) — panne PERSISTANTE, aucune aide du script"); ps(name)
    typ=None; final=None; nid=None
    for i in range(60):   # jusqu'a 5 min pour laisser HELIOS resoudre (rollback) ou escalader
        time.sleep(5)
        row=sql(f"SELECT id||'~'||incident_type||'~'||status FROM incident WHERE id>{base} ORDER BY id DESC LIMIT 1;")
        if "~" in row:
            p=row.split("~")
            if nid!=p[0]: nid=p[0]; typ=p[1]; out(f"[{ts()}] #{p[0]} {p[1]} {p[2]}")
            final=p[2]
            if final in ("RESOLVED","RESOLUTION_FAILED"): out(f"[{ts()}] -> verdict HELIOS : {final}"); break
    # teardown APRES le verdict de HELIOS (restaure la baseline ; n'influence PAS le verdict)
    if name in TEARDOWN:
        out(f"[{ts()}] teardown post-verdict (restauration baseline)"); ps(name, True)
    for _ in range(36):
        if podinfo_ready(): break
        time.sleep(5)
    out(f"[{ts()}] cooldown 180s..."); time.sleep(180)
    results.append((name, typ or "AUCUN", final or "-"))

out("\n\n============== RESULTATS ==============")
out(f"{'TEST':<11}{'TYPE':<24}{'STATUT'}")
ok=0
for n,t,f in results:
    out(f"{n:<11}{t:<24}{f}")
    if f=="RESOLVED": ok+=1
out(f"====== {ok}/15 RESOLVED par HELIOS ======")
