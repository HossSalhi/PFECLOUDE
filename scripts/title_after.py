"""Capture le titre du nouvel incident apres une action (id > base donne)."""
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

# baseline = last RESOLVED/known id before the user's delete = 3420
BASE = 3420
c = ssh()
out(f"[{ts()}] podinfo: " + run(c, "kubectl get deployment podinfo -n pfe --no-headers"))
out(f"[{ts()}] Recherche d'un incident id > {BASE} ...")
c.close()

found = None
for i in range(80):
    c = ssh()
    row = run(c, f"kubectl exec -n pfe postgres-1 -c postgres -- psql postgresql://appuser:postgres@localhost/pfe_db -t -c \"SELECT id||'~'||title||'~'||incident_type||'~'||severity||'~'||status FROM incident WHERE app='podinfo' AND id > {BASE} ORDER BY id DESC LIMIT 1;\"").strip()
    pods = run(c, "kubectl get deployment podinfo -n pfe --no-headers")
    c.close()
    if row and "~" in row:
        parts = [p.strip() for p in row.split("~")]
        if len(parts) >= 5:
            iid, title, itype, sev, status = parts[:5]
            if found != status:
                out(f"[{ts()}] #{iid} | TITRE = \"{title}\" | type={itype} | sev={sev} | status={status} | podinfo={pods.split()[1] if len(pods.split())>1 else '?'}")
                found = status
            if status in ("RESOLVED", "RESOLUTION_FAILED"):
                out(f"[{ts()}] === FINAL : \"{title}\" ({status}) ===")
                break
    time.sleep(5)
else:
    out(f"[{ts()}] Aucun nouvel incident detecte (cooldown actif ? podinfo deja repare ?)")
