"""
Validation live des tests de chaos : pour chaque test -> inject, surveille un
nouvel incident HELIOS (type + statut final), restaure, attend le cooldown.
Tout via kubectl LOCAL (pas de ssh). Resultats en fin.
"""
import subprocess, time, datetime, sys

TESTS = ["memory", "disk", "network", "http503"]
PS1 = "scripts/chaos/helios-chaos.ps1"

def out(s): sys.stdout.buffer.write((s+"\n").encode("utf-8","replace")); sys.stdout.flush()
def ts(): return datetime.datetime.now().strftime("%H:%M:%S")

def sql(q):
    cmd = ["kubectl","exec","-n","pfe","postgres-1","-c","postgres","--","psql",
           "postgresql://appuser:postgres@localhost/pfe_db","-t","-c",q]
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=40).stdout.strip()
    except Exception as e:
        return f"ERR:{e}"

def toolkit(test, restore=False):
    args = ["powershell","-ExecutionPolicy","Bypass","-File",PS1,test]
    if restore: args.append("-Restore")
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=180)
        return r.stdout.strip()
    except Exception as e:
        return f"ERR:{e}"

def max_id():
    r = sql("SELECT COALESCE(MAX(id),0) FROM incident WHERE app='podinfo';")
    return int(r) if r.strip().isdigit() else 0

results = []
for t in TESTS:
    out(f"\n========== TEST: {t} ==========")
    base = max_id()
    out(f"[{ts()}] baseline id={base} -> injection")
    toolkit(t)
    detected_type = None
    final_status = None
    new_id = None
    for i in range(40):  # ~3.3 min max
        time.sleep(5)
        row = sql(f"SELECT id||'~'||incident_type||'~'||status FROM incident WHERE app='podinfo' AND id > {base} ORDER BY id DESC LIMIT 1;")
        if "~" in row:
            p = [x.strip() for x in row.split("~")]
            if len(p) >= 3:
                if new_id != p[0]:
                    new_id = p[0]; detected_type = p[1]
                    out(f"[{ts()}] DETECTE #{p[0]} type={p[1]} status={p[2]}")
                final_status = p[2]
                if p[2] in ("RESOLVED","RESOLUTION_FAILED"):
                    out(f"[{ts()}] FINAL #{p[0]} -> {p[2]}")
                    break
    if new_id is None:
        out(f"[{ts()}] AUCUN incident detecte pour '{t}'")
        results.append((t, "AUCUN", "-"))
    else:
        results.append((t, detected_type, final_status))
    out(f"[{ts()}] restauration...")
    toolkit(t, restore=True)
    out(f"[{ts()}] cooldown 200s avant test suivant...")
    time.sleep(200)

out("\n\n================ RESULTATS ================")
out(f"{'TEST':<12}{'TYPE DETECTE':<26}{'STATUT FINAL'}")
for t, typ, st in results:
    out(f"{t:<12}{typ:<26}{st}")
out("==========================================")
