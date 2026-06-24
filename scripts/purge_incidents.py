import paramiko, sys
key = paramiko.Ed25519Key.from_private_key_file("C:/Users/houss/.ssh/id_ed25519")
def out(s): sys.stdout.buffer.write((s+"\n").encode("utf-8","replace")); sys.stdout.flush()
c=paramiko.SSHClient(); c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect("192.168.148.153",username="kube",pkey=key,timeout=10,allow_agent=False,look_for_keys=False)
def sql(q):
    cmd=f"kubectl exec -n pfe postgres-2 -c postgres -- psql postgresql://appuser:postgres@localhost/pfe_db -c \"{q}\""
    _,o,e=c.exec_command(cmd,timeout=60); return (o.read().decode(errors='replace')+e.read().decode(errors='replace')).strip()
# Transaction atomique : si un FK manque -> tout est annule
tx=("BEGIN; "
    "DELETE FROM human_task; "
    "DELETE FROM alert; "
    "DELETE FROM incident_action; "
    "DELETE FROM incident_history; "
    "DELETE FROM incident_decision; "
    "DELETE FROM workflow_logs; "
    "DELETE FROM workflow_instance; "
    "DELETE FROM incident; "
    "COMMIT;")
out("=== purge transactionnelle ===")
out(sql(tx))
out("\n=== etat final ===")
out("incidents: "+sql("SELECT count(*) FROM incident;"))
out("workflows: "+sql("SELECT count(*) FROM workflow_instance;"))
out("human_task: "+sql("SELECT count(*) FROM human_task;"))
c.close()
