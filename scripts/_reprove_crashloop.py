"""Re-preuve 0.2.129 (IA UP) : crashloop -> l'IA classe POD_CRASH_LOOP + recommande ROLLBACK_DEPLOYMENT
-> Camunda execute -> RESOLVED. Non-regression du 16/16 apres que l'IA fait autorite sur l'action."""
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
base=sql("SELECT COALESCE(MAX(id),0) FROM incident"); out(f"[baseline] max id={base}, IA /health={run(m,'curl -s -o /dev/null -w %{http_code} http://192.168.148.161:8000/health')}")
out("\n[inject crashloop]")
out(run(m,"kubectl -n pfe patch deploy helios-crashloop --type=json "
    "-p '[{\"op\":\"add\",\"path\":\"/spec/template/spec/containers/0/command\",\"value\":[\"sh\",\"-c\",\"echo boot; exit 1\"]}]'"))
out("\n[attente detection+resolution IA->Camunda, 8 min]")
final=None
for i in range(32):
    time.sleep(15)
    row=sql(f"SELECT id||'|'||incident_type||'|'||status FROM incident WHERE app='helios-crashloop' AND id>{base} ORDER BY id DESC LIMIT 1")
    out(f"  +{(i+1)*15:3d}s  {row or '(aucun)'}")
    if row and row.split('|')[-1] in ("RESOLVED","RESOLUTION_FAILED"): final=row; break
out("\n[log: prediction IA + action executee]")
out(run(m,"kubectl -n pfe logs -l app=backend --tail=500 | grep -iE 'AI prediction|Remediation plan|ROLLBACK|Auto-resolved|helios-crashloop' | tail -n 12") or "(rien)")
out("\n[etat final]")
out(run(m,"kubectl -n pfe get deploy helios-crashloop --no-headers"))
out(sql("SELECT id||'|'||incident_type||'|'||status FROM incident WHERE app='helios-crashloop' ORDER BY id DESC LIMIT 2"))
m.close()
out("\n=== ATTENDU : POD_CRASH_LOOP -> RESOLVED (l'IA a recommande ROLLBACK, Camunda a execute) ===")
