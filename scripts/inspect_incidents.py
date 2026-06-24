import paramiko, sys
key = paramiko.Ed25519Key.from_private_key_file("C:/Users/houss/.ssh/id_ed25519")
def out(s): sys.stdout.buffer.write((s+"\n").encode("utf-8","replace")); sys.stdout.flush()
c=paramiko.SSHClient(); c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect("192.168.148.153",username="kube",pkey=key,timeout=10,allow_agent=False,look_for_keys=False)
def sql(q):
    cmd=f"kubectl exec -n pfe postgres-2 -c postgres -- psql postgresql://appuser:postgres@localhost/pfe_db -t -c \"{q}\""
    _,o,e=c.exec_command(cmd,timeout=30); return (o.read().decode(errors='replace')+e.read().decode(errors='replace')).strip()
out("=== repartition des types (vrai ML ou tout generique ?) ===")
out(sql("SELECT incident_type, severity, status, count(*) FROM incident GROUP BY incident_type, severity, status ORDER BY 4 DESC;"))
out("\n=== exemple : 1 incident detaille (explication reelle) ===")
out(sql("SELECT id, incident_type, severity, title, explanation FROM incident ORDER BY id DESC LIMIT 1;").replace("|","\n  "))
c.close()
