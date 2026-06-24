import paramiko, sys
key = paramiko.Ed25519Key.from_private_key_file("C:/Users/houss/.ssh/id_ed25519")
def out(s): sys.stdout.buffer.write((s+"\n").encode("utf-8","replace")); sys.stdout.flush()
c=paramiko.SSHClient(); c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect("192.168.148.153",username="kube",pkey=key,timeout=10,allow_agent=False,look_for_keys=False)
def sql(q):
    cmd=f"kubectl exec -n pfe postgres-2 -c postgres -- psql postgresql://appuser:postgres@localhost/pfe_db -t -c \"{q}\""
    _,o,e=c.exec_command(cmd,timeout=30); return (o.read().decode(errors='replace')+e.read().decode(errors='replace')).strip()
out("=== tables referencant incident (FK) + delete rule ===")
q=("SELECT tc.table_name, rc.delete_rule FROM information_schema.referential_constraints rc "
   "JOIN information_schema.table_constraints tc ON rc.constraint_name=tc.constraint_name "
   "JOIN information_schema.constraint_column_usage ccu ON rc.unique_constraint_name=ccu.constraint_name "
   "WHERE ccu.table_name='incident';")
out(sql(q))
c.close()
