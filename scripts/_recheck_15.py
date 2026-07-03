import paramiko, sys, time
KEY="C:/Users/houss/.ssh/id_ed25519"; MASTER="192.168.148.153"
def out(s): sys.stdout.buffer.write((s+"\n").encode("utf-8","replace")); sys.stdout.flush()
def cli(h):
    k=paramiko.Ed25519Key.from_private_key_file(KEY); c=paramiko.SSHClient(); c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(h,username="kube",pkey=k,timeout=12,allow_agent=False,look_for_keys=False); return c
def run(c,cmd,t=90): _,o,e=c.exec_command(cmd,timeout=t); return (o.read().decode(errors="replace")+e.read().decode(errors="replace")).strip()
m=cli(MASTER)
prim=run(m,"kubectl get pods -n pfe -l cnpg.io/instanceRole=primary -o jsonpath='{.items[0].metadata.name}'")
def sql(q): return run(m,f"kubectl exec -n pfe {prim} -c postgres -- psql -U postgres -d pfe_db -t -c \"{q}\" 2>&1").strip()
for i in range(12):  # up to 3 min
    st=sql("SELECT status FROM incident WHERE id=15")
    out(f"+{i*15:3d}s  incident#15 status = {st}")
    if st in ("RESOLVED","RESOLUTION_FAILED"): break
    time.sleep(15)
out("\n[actions de l'incident 15]")
out(sql("SELECT action_type||'|'||status FROM action WHERE incident_id=15 ORDER BY id"))
out("\n[remediation/verify logs #15]")
out(run(m,"kubectl -n pfe logs -l app=backend --tail=600 | grep -E '\\[15\\]|helios-crashloop' | tail -n 15") or "(rien)")
m.close()
