"""Surveille un NOUVEL incident podinfo (id > base) et affiche titre/type/status."""
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
# show top CPU of podinfo pods
out(f"[{ts()}] baseline id={base_id}")
c.close()

last = None
for i in range(60):  # ~5 min
    c = ssh()
    row = run(c, f"kubectl exec -n pfe postgres-1 -c postgres -- psql postgresql://appuser:postgres@localhost/pfe_db -t -c \"SELECT id||'~'||title||'~'||incident_type||'~'||severity||'~'||status FROM incident WHERE app='podinfo' AND id > {base_id} ORDER BY id DESC LIMIT 1;\"").strip()
    c.close()
    if row and "~" in row:
        p = [x.strip() for x in row.split("~")]
        if len(p) >= 5:
            key_state = p[0]+p[4]
            if key_state != last:
                out(f"[{ts()}] #{p[0]} | \"{p[1]}\" | type={p[2]} | sev={p[3]} | {p[4]}")
                last = key_state
            if p[4] in ("RESOLVED","RESOLUTION_FAILED"):
                out(f"[{ts()}] === FINAL: {p[2]} ({p[4]}) ===")
                break
    time.sleep(5)
else:
    out(f"[{ts()}] Aucun incident detecte (signal insuffisant ou cooldown).")
