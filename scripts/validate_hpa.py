"""Valide HPA : inject charge -> HPA scale podinfo 2->N."""
import subprocess, time, datetime, sys
def out(s): sys.stdout.buffer.write((s+"\n").encode("utf-8","replace")); sys.stdout.flush()
def ts(): return datetime.datetime.now().strftime("%H:%M:%S")
def k(*a):
    try: return subprocess.run(["kubectl",*a],capture_output=True,text=True,timeout=30).stdout.strip()
    except Exception as e: return f"ERR:{e}"

out(f"[{ts()}] inject hpa (HPA + charge CPU)...")
r = subprocess.run(["powershell","-ExecutionPolicy","Bypass","-File","scripts/chaos/helios-chaos.ps1","hpa"],capture_output=True,text=True,timeout=120)
out(r.stdout.strip()[:300])

start_reps = None
maxreps = 0
for i in range(30):  # ~2.5 min
    time.sleep(5)
    reps = k("get","deploy","podinfo","-n","pfe","-o","jsonpath={.status.replicas}")
    hpa = k("get","hpa","podinfo","-n","pfe","--no-headers")
    try: r_int = int(reps)
    except: r_int = 0
    if start_reps is None: start_reps = r_int
    if r_int > maxreps: maxreps = r_int
    out(f"[{ts()}] replicas={reps} | hpa: {hpa[:80]}")
    if r_int >= 4: out(f"[{ts()}] >>> HPA a scale a {r_int} repliques !"); break

out(f"\n[{ts()}] restore hpa...")
r = subprocess.run(["powershell","-ExecutionPolicy","Bypass","-File","scripts/chaos/helios-chaos.ps1","hpa","-Restore"],capture_output=True,text=True,timeout=120)
out(r.stdout.strip()[:200])
out(f"\n=== RESULTAT hpa : {start_reps} -> max {maxreps} repliques ({'OK scale' if maxreps>start_reps else 'PAS de scale'}) ===")
