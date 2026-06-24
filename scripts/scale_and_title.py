"""Scale podinfo -> 0 puis capture le TITRE exact du nouvel incident cree."""
import paramiko, time, datetime, sys
key = paramiko.Ed25519Key.from_private_key_file("C:/Users/houss/.ssh/id_ed25519")
def ssh():
    c = paramiko.SSHClient(); c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect("192.168.148.153", username="kube", pkey=key, timeout=10, allow_agent=False, look_for_keys=False)
    return c
def run(c, cmd):
    _, o, _ = c.exec_command(cmd, timeout=20)
    return o.read().decode(errors="replace").strip()
def ts(): return datetime.datetime.now().strftime("%H:%M:%S")
def out(s): sys.stdout.buffer.write((s+"\n").encode("utf-8","replace"))

c = ssh()
base = run(c, "kubectl exec -n pfe postgres-1 -c postgres -- psql postgresql://appuser:postgres@localhost/pfe_db -t -c \"SELECT COALESCE(MAX(id),0) FROM incident WHERE app='podinfo';\"").strip()
base_id = int(base) if base.isdigit() else 0
out(f"[{ts()}] Baseline id={base_id}")
out(f"[{ts()}] kubectl scale deployment podinfo -n pfe --replicas=0")
run(c, "kubectl scale deployment podinfo -n pfe --replicas=0")
c.close()

for i in range(80):
    time.sleep(5)
    c = ssh()
    row = run(c, f"kubectl exec -n pfe postgres-1 -c postgres -- psql postgresql://appuser:postgres@localhost/pfe_db -t -c \"SELECT id||'~'||title||'~'||incident_type||'~'||severity||'~'||status FROM incident WHERE app='podinfo' AND id > {base_id} ORDER BY id DESC LIMIT 1;\"").strip()
    c.close()
    if not row:
        continue
    parts = row.split("~")
    if len(parts) < 5:
        continue
    iid, title, itype, sev, status = [p.strip() for p in parts[:5]]
    out(f"[{ts()}] #{iid} | TITRE = \"{title}\" | type={itype} | severity={sev} | status={status}")
    if status in ("RESOLVED", "RESOLUTION_FAILED"):
        out(f"[{ts()}] === FINAL : \"{title}\" ({status}) ===")
        break
