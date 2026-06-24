import paramiko, sys
key = paramiko.Ed25519Key.from_private_key_file("C:/Users/houss/.ssh/id_ed25519")
def out(s): sys.stdout.buffer.write((s+"\n").encode("utf-8","replace")); sys.stdout.flush()
c=paramiko.SSHClient(); c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect("192.168.148.153",username="kube",pkey=key,timeout=10,allow_agent=False,look_for_keys=False)
def sql(q):
    cmd=f"kubectl exec -n pfe postgres-2 -c postgres -- psql postgresql://appuser:postgres@localhost/pfe_db -t -c \"{q}\""
    _,o,e=c.exec_command(cmd,timeout=25); return (o.read().decode(errors='replace')+e.read().decode(errors='replace')).strip()
out("=== lease actuel ===")
out(sql("SELECT lease_name, holder, renewed_at FROM leader_lease;"))
out("\n=== liberation du lease perime (force re-election immediate) ===")
out(sql("DELETE FROM leader_lease WHERE lease_name='aiops-scheduler';"))
out("OK - un pod backend vivant va acquerir le lease au prochain cycle (~30s)")
c.close()
