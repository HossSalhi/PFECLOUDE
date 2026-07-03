"""Test POD_STUCK_TERMINATING : on pose un finalizer sur un pod d'une app saine puis on le supprime
=> pod bloqué Terminating. HELIOS doit detecter + retirer le finalizer (FORCE_DELETE_POD) => RESOLVED.
Nettoyage garanti (retrait manuel du finalizer si HELIOS n'a pas fini)."""
import paramiko, sys, time
KEY="C:/Users/houss/.ssh/id_ed25519"; MASTER="192.168.148.153"
def out(s): sys.stdout.buffer.write((s+"\n").encode("utf-8","replace")); sys.stdout.flush()
k=paramiko.Ed25519Key.from_private_key_file(KEY); c=paramiko.SSHClient(); c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect(MASTER,username="kube",pkey=k,timeout=12,allow_agent=False,look_for_keys=False)
def run(cmd,t=90): _,o,e=c.exec_command(cmd,timeout=t); return (o.read().decode(errors="replace")+e.read().decode(errors="replace")).strip()
prim=run("kubectl get pods -n pfe -l cnpg.io/instanceRole=primary -o jsonpath='{.items[0].metadata.name}'")
def sql(q): return run(f"kubectl exec -n pfe {prim} -c postgres -- psql -U postgres -d pfe_db -t -A -c \"{q}\" 2>&1").strip()
# choisir une app avec un pod Running
APP=None; POD=None
for a in ["helios-service","helios-latency","helios-http5xx","helios-cpu","helios-dns","helios-db"]:
    p=run(f"kubectl -n pfe get pod -l app={a} --field-selector=status.phase=Running -o jsonpath='{{.items[0].metadata.name}}' 2>/dev/null")
    if p and "helios" in p: APP,POD=a,p; break
out(f"[cible] app={APP} pod={POD}")
base=sql("SELECT COALESCE(MAX(id),0) FROM incident"); out(f"[baseline] max id={base}")
try:
    out("[1] pose finalizer + delete -> Terminating bloque")
    out(run(f"kubectl -n pfe patch pod {POD} -p '{{\"metadata\":{{\"finalizers\":[\"helios.io/stuck-test\"]}}}}'"))
    out(run(f"kubectl -n pfe delete pod {POD} --wait=false 2>&1"))
    time.sleep(3)
    out("etat pod: "+run(f"kubectl -n pfe get pod {POD} -o jsonpath='{{.metadata.deletionTimestamp}} phase={{.status.phase}}' 2>&1"))
    out("\n[2] attente detection+resolution (marge stuck=45s + poll). Fenetre 6 min")
    final=None
    for i in range(24):
        time.sleep(15)
        row=sql(f"SELECT id||'|'||incident_type||'|'||status FROM incident WHERE app='{APP}' AND id>{base} ORDER BY id DESC LIMIT 1")
        gone=run(f"kubectl -n pfe get pod {POD} --no-headers 2>&1")
        out(f"  +{(i+1)*15:3d}s inc={row or '(aucun)'} | pod={'GONE' if 'NotFound' in gone or not gone else gone.split()[2] if len(gone.split())>2 else gone[:30]}")
        if row and row.split('|')[-1] in ("RESOLVED","RESOLUTION_FAILED"): final=row; break
    out("\n[3] logs")
    out(run("kubectl -n pfe logs -l app=backend --tail=400 | grep -iE 'STUCK_TERMINATING|FORCE_DELETE_POD|finalizer|stuck-terminating' | tail -n 10") or "(rien)")
finally:
    # nettoyage : si le pod existe encore, retirer le finalizer nous-memes
    still=run(f"kubectl -n pfe get pod {POD} --no-headers 2>&1")
    if still and "NotFound" not in still:
        out("\n[cleanup] retrait manuel du finalizer (securite)")
        out(run(f"kubectl -n pfe patch pod {POD} -p '{{\"metadata\":{{\"finalizers\":null}}}}' 2>&1"))
out("\n[final] incident + pod")
out(sql(f"SELECT id||'|'||incident_type||'|'||status FROM incident WHERE app='{APP}' ORDER BY id DESC LIMIT 2"))
out("pod: "+run(f"kubectl -n pfe get pod {POD} --no-headers 2>&1"))
c.close()
out("\n=== ATTENDU : POD_STUCK_TERMINATING -> RESOLVED, pod GONE (finalizer retire par HELIOS) ===")
