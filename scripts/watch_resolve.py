"""Surveille podinfo jusqu'a detection + RESOLVED, mesure les temps."""
import paramiko, time, datetime
key = paramiko.Ed25519Key.from_private_key_file("C:/Users/houss/.ssh/id_ed25519")
def ssh():
    c = paramiko.SSHClient(); c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect("192.168.148.153", username="kube", pkey=key, timeout=10, allow_agent=False, look_for_keys=False)
    return c
def run(c, cmd):
    _, o, _ = c.exec_command(cmd, timeout=20)
    return o.read().decode(errors="replace").strip()
def ts(): return datetime.datetime.now().strftime("%H:%M:%S")

# Baseline: highest incident id now
c = ssh()
base = run(c, "kubectl exec -n pfe postgres-1 -c postgres -- psql postgresql://appuser:postgres@localhost/pfe_db -t -c \"SELECT COALESCE(MAX(id),0) FROM incident WHERE app='podinfo';\"").strip()
c.close()
base_id = int(base) if base.isdigit() else 0
print(f"[{ts()}] Baseline incident id = {base_id}. Surveillance jusqu'a un NOUVEL incident RESOLVED...")

t_detect = None
new_id = None
for i in range(80):  # max ~6-7 min
    time.sleep(5)
    c = ssh()
    row = run(c, f"kubectl exec -n pfe postgres-1 -c postgres -- psql postgresql://appuser:postgres@localhost/pfe_db -t -c \"SELECT id||'|'||incident_type||'|'||status FROM incident WHERE app='podinfo' AND id > {base_id} ORDER BY id DESC LIMIT 1;\"").strip()
    pods = run(c, "kubectl get deployment podinfo -n pfe --no-headers")
    c.close()
    if not row:
        continue
    parts = row.split("|")
    if len(parts) < 3:
        continue
    iid, itype, status = parts[0].strip(), parts[1].strip(), parts[2].strip()
    if new_id is None:
        new_id = iid
        t_detect = time.time()
        ready = pods.split()[1] if len(pods.split()) > 1 else "?"
        print(f"[{ts()}] DETECTE incident #{iid} ({itype}) status={status} | podinfo READY={ready}")
    if status == "RESOLVED":
        dt = int(time.time() - t_detect)
        print(f"[{ts()}] >>> RESOLVED en {dt}s apres detection ! podinfo={pods.split()[1] if len(pods.split())>1 else '?'}")
        break
    if status == "RESOLUTION_FAILED":
        print(f"[{ts()}] >>> RESOLUTION_FAILED :(")
        break
print(f"[{ts()}] Fin.")
