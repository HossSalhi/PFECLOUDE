import paramiko, sys
key = paramiko.Ed25519Key.from_private_key_file("C:/Users/houss/.ssh/id_ed25519")
def out(s): sys.stdout.buffer.write((s+"\n").encode("utf-8","replace")); sys.stdout.flush()
c=paramiko.SSHClient(); c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect("192.168.148.153",username="kube",pkey=key,timeout=10,allow_agent=False,look_for_keys=False)
def sql(q):
    cmd=f"kubectl exec -n pfe postgres-2 -c postgres -- psql postgresql://appuser:postgres@localhost/pfe_db -t -A -c \"{q}\""
    _,o,e=c.exec_command(cmd,timeout=30); return (o.read().decode(errors='replace')+e.read().decode(errors='replace')).strip()
out("=== colonnes de workflow_logs ===")
out(sql("SELECT column_name FROM information_schema.columns WHERE table_name='workflow_logs' ORDER BY ordinal_position;"))
out("\n=== workflow lie a l'incident #3567 ===")
out(sql("SELECT id, status, orchestration_state FROM workflow_instance WHERE incident_id=3567;"))
out("\n=== TRACE REELLE (toutes colonnes texte du 1er log) ===")
out(sql("SELECT * FROM workflow_logs WHERE workflow_instance_id IN (SELECT id FROM workflow_instance WHERE incident_id=3567) ORDER BY id LIMIT 30;"))
c.close()
