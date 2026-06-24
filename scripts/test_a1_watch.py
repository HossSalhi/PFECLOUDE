"""
Test A1 : scale podinfo -> 0, puis surveille HELIOS jusqu'à RESOLVED.
"""
import paramiko, time, datetime

key = paramiko.Ed25519Key.from_private_key_file("C:/Users/houss/.ssh/id_ed25519")

def ssh():
    c = paramiko.SSHClient(); c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect("192.168.148.153", username="kube", pkey=key, timeout=10, allow_agent=False, look_for_keys=False)
    return c

def run(c, cmd):
    _, o, e = c.exec_command(cmd, timeout=20)
    out = o.read().decode().strip()
    err = e.read().decode().strip()
    return out

def ts():
    return datetime.datetime.now().strftime("%H:%M:%S")

# --- Pre-check ---
c = ssh()
print(f"[{ts()}] Pre-check...")
print("  podinfo:", run(c, "kubectl get deployment podinfo -n pfe"))
sql = "SELECT id, incident_type, status FROM incident WHERE app='podinfo' AND status NOT IN ('RESOLVED','CLOSED') ORDER BY id DESC LIMIT 2;"
open_incidents = run(c, f'kubectl exec -n pfe postgres-1 -c postgres -- psql postgresql://appuser:postgres@localhost/pfe_db -c "{sql}"')
print("  active incidents:", open_incidents)

# --- Trigger ---
print(f"\n[{ts()}] Scale podinfo -> 0 (Test A1)")
run(c, "kubectl scale deployment podinfo -n pfe --replicas=0")
print("  scaled to 0")
c.close()

t_start = time.time()

# --- Watch loop (max 5 min) ---
print(f"\n[{ts()}] Watching for HELIOS to detect + resolve...")
prev_status = None
for i in range(60):
    time.sleep(5)
    c = ssh()
    pods = run(c, "kubectl get deployment podinfo -n pfe --no-headers")
    sql = "SELECT id, incident_type, status, updated_at FROM incident WHERE app='podinfo' ORDER BY id DESC LIMIT 1;"
    row = run(c, f'kubectl exec -n pfe postgres-1 -c postgres -- psql postgresql://appuser:postgres@localhost/pfe_db -c "{sql}"')
    c.close()

    elapsed = int(time.time() - t_start)
    # Extract status from row
    status = "none"
    if "|" in row:
        parts = [p.strip() for p in row.split("\n") if "|" in p and "status" not in p.lower()]
        if parts:
            cols = [c.strip() for c in parts[0].split("|")]
            if len(cols) >= 3:
                status = cols[2]

    if status != prev_status:
        print(f"  [{ts()}] T+{elapsed}s | podinfo={pods.split()[-3] if pods.split() else '?'} | incident status: {status}")
        prev_status = status

    if status == "RESOLVED":
        print(f"\n[{ts()}] RESOLVED en {elapsed}s depuis le test !")
        break
    if status == "RESOLUTION_FAILED":
        print(f"\n[{ts()}] RESOLUTION_FAILED a T+{elapsed}s :(")
        break

print(f"\n[{ts()}] Test terminé.")
