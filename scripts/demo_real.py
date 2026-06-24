"""Lance UN podkill propre et capture TOUT le detail reel de l'incident + la trace Camunda."""
import paramiko, time, datetime, sys
key = paramiko.Ed25519Key.from_private_key_file("C:/Users/houss/.ssh/id_ed25519")
def out(s): sys.stdout.buffer.write((s+"\n").encode("utf-8","replace")); sys.stdout.flush()
def ts(): return datetime.datetime.now().strftime("%H:%M:%S")
c=paramiko.SSHClient(); c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect("192.168.148.153",username="kube",pkey=key,timeout=10,allow_agent=False,look_for_keys=False)
def run(cmd,t=30):
    _,o,_=c.exec_command(cmd,timeout=t); return o.read().decode(errors="replace").strip()
def sql(q):
    return run(f"kubectl exec -n pfe postgres-2 -c postgres -- psql postgresql://appuser:postgres@localhost/pfe_db -t -A -F'|' -c \"{q}\"")

base=sql("SELECT COALESCE(MAX(id),0) FROM incident WHERE app='podinfo';")
base=int(base) if base.isdigit() else 0
out(f"[{ts()}] baseline id={base}")
out(f"[{ts()}] >>> POD KILL : kubectl delete pod -l app=podinfo")
run("kubectl delete pod -n pfe -l app=podinfo")

iid=None; final=None
for i in range(50):
    time.sleep(5)
    row=sql(f"SELECT id||'~'||incident_type||'~'||severity||'~'||status FROM incident WHERE app='podinfo' AND id>{base} ORDER BY id DESC LIMIT 1;")
    if "~" in row:
        p=row.split("~")
        if iid!=p[0]: iid=p[0]; out(f"[{ts()}] DETECTE #{p[0]} type={p[1]} sev={p[2]} status={p[3]}")
        final=p[3]
        if final in ("RESOLVED","RESOLUTION_FAILED"): break

if iid:
    out("\n========== DETAIL REEL DE L'INCIDENT (ce que l'IA a produit) ==========")
    out(sql(f"SELECT 'TITRE      : '||title FROM incident WHERE id={iid};"))
    out(sql(f"SELECT 'TYPE       : '||incident_type FROM incident WHERE id={iid};"))
    out(sql(f"SELECT 'SEVERITE   : '||severity FROM incident WHERE id={iid};"))
    out(sql(f"SELECT 'STATUT     : '||status FROM incident WHERE id={iid};"))
    out(sql(f"SELECT 'EXPLICATION: '||COALESCE(explanation, description) FROM incident WHERE id={iid};"))
    out("\n========== TRACE CAMUNDA REELLE (workflow_logs) ==========")
    trace=sql(f"SELECT message FROM workflow_logs WHERE workflow_instance_id=(SELECT id FROM workflow_instance WHERE incident_id={iid} ORDER BY id DESC LIMIT 1) ORDER BY id;")
    out(trace if trace else "(pas de trace)")
c.close()
out(f"\n[{ts()}] === Resultat : #{iid} -> {final} ===")
