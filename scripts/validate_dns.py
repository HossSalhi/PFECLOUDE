"""Valide dns : DNS coupe -> readiness nslookup echoue -> NotReady -> DEPLOYMENT_UNAVAILABLE."""
import subprocess, time, datetime, sys
PS1="scripts/chaos/helios-chaos.ps1"
def out(s): sys.stdout.buffer.write((s+"\n").encode("utf-8","replace")); sys.stdout.flush()
def ts(): return datetime.datetime.now().strftime("%H:%M:%S")
def sql(q):
    cmd=["kubectl","exec","-n","pfe","postgres-1","-c","postgres","--","psql","postgresql://appuser:postgres@localhost/pfe_db","-t","-c",q]
    try: return subprocess.run(cmd,capture_output=True,text=True,timeout=40).stdout.strip()
    except Exception as e: return f"ERR:{e}"
def toolkit(test,restore=False):
    a=["powershell","-ExecutionPolicy","Bypass","-File",PS1,test]
    if restore:a.append("-Restore")
    try: return subprocess.run(a,capture_output=True,text=True,timeout=120).stdout.strip()
    except Exception as e: return f"ERR:{e}"
def max_id():
    r=sql("SELECT COALESCE(MAX(id),0) FROM incident WHERE app='podinfo';")
    return int(r) if r.strip().isdigit() else 0

# cleanup leftover evicted pods
subprocess.run(["kubectl","delete","pod","-n","pfe","--field-selector","status.phase=Failed","-l","app=podinfo","--ignore-not-found"],capture_output=True,text=True,timeout=30)
out(f"[{ts()}] attente cooldown 210s..."); time.sleep(210)
base=max_id()
out(f"[{ts()}] baseline={base} -> inject dns")
out(toolkit("dns")[:250])
new_id=None;dtype=None;final=None
for i in range(48):
    time.sleep(5)
    row=sql(f"SELECT id||'~'||incident_type||'~'||status FROM incident WHERE app='podinfo' AND id>{base} ORDER BY id DESC LIMIT 1;")
    ready=subprocess.run(["kubectl","get","deploy","podinfo","-n","pfe","-o","jsonpath={.status.readyReplicas}"],capture_output=True,text=True,timeout=20).stdout.strip()
    if "~" in row:
        p=[x.strip() for x in row.split("~")]
        if len(p)>=3:
            if new_id!=p[0]: new_id=p[0];dtype=p[1];out(f"[{ts()}] DETECTE #{p[0]} {p[1]} {p[2]} | ready={ready}")
            final=p[2]
            if p[2] in ("RESOLVED","RESOLUTION_FAILED"): out(f"[{ts()}] FINAL {p[2]}"); break
out(f"[{ts()}] restore dns..."); out(toolkit("dns",restore=True)[:150])
out(f"\n=== RESULTAT dns : {dtype or 'AUCUN'} / {final or '-'} ===")
