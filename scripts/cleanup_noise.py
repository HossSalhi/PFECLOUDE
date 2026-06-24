import paramiko, sys
key = paramiko.Ed25519Key.from_private_key_file("C:/Users/houss/.ssh/id_ed25519")
def out(s): sys.stdout.buffer.write((s+"\n").encode("utf-8","replace")); sys.stdout.flush()
c=paramiko.SSHClient(); c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect("192.168.148.153",username="kube",pkey=key,timeout=10,allow_agent=False,look_for_keys=False)
def sql(q):
    cmd=f"kubectl exec -n pfe postgres-2 -c postgres -- psql postgresql://appuser:postgres@localhost/pfe_db -t -c \"{q}\""
    _,o,e=c.exec_command(cmd,timeout=40); return (o.read().decode(errors='replace')+e.read().decode(errors='replace')).strip()

out("[1] Tâches humaines PENDING -> CANCELLED")
out(sql("UPDATE human_task SET decision='CANCELLED', completed_at=now() WHERE decision='PENDING';"))
out("[2] Workflows RUNNING orphelins -> CANCELLED (stoppe recovery/dedup)")
out(sql("UPDATE workflow_instance SET status='CANCELLED' WHERE status='RUNNING';"))
out("[3] Incidents RESOLVED/CLOSED -> ARCHIVED")
out(sql("UPDATE incident SET status='ARCHIVED' WHERE status IN ('RESOLVED','CLOSED');"))

out("\n=== etat apres nettoyage ===")
out("incidents: "+sql("SELECT status, count(*) FROM incident GROUP BY status;"))
out("taches PENDING: "+sql("SELECT count(*) FROM human_task WHERE decision='PENDING';"))
out("workflows RUNNING: "+sql("SELECT count(*) FROM workflow_instance WHERE status='RUNNING';"))
c.close()
