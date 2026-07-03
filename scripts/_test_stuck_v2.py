"""Test POD_STUCK_TERMINATING v2 : pod STANDALONE (pas de ReplicaSet -> pas de churn -> pas de faux
disk) sous app=helios-latency, avec finalizer. On le supprime -> bloque Terminating -> HELIOS doit
detecter + retirer le finalizer (FORCE_DELETE_POD) -> RESOLVED. Nettoyage garanti."""
import paramiko, sys, time
KEY="C:/Users/houss/.ssh/id_ed25519"; MASTER="192.168.148.153"; APP="helios-http5xx"; POD="helios-stuck-probe"
def out(s): sys.stdout.buffer.write((s+"\n").encode("utf-8","replace")); sys.stdout.flush()
k=paramiko.Ed25519Key.from_private_key_file(KEY); c=paramiko.SSHClient(); c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect(MASTER,username="kube",pkey=k,timeout=12,allow_agent=False,look_for_keys=False)
def run(cmd,t=90): _,o,e=c.exec_command(cmd,timeout=t); return (o.read().decode(errors="replace")+e.read().decode(errors="replace")).strip()
prim=run("kubectl get pods -n pfe -l cnpg.io/instanceRole=primary -o jsonpath='{.items[0].metadata.name}'")
def sql(q): return run(f"kubectl exec -n pfe {prim} -c postgres -- psql -U postgres -d pfe_db -t -A -c \"{q}\" 2>&1").strip()
manifest=("apiVersion: v1\\nkind: Pod\\nmetadata:\\n  name: "+POD+"\\n  namespace: pfe\\n  labels:\\n    app: "+APP+
          "\\n  finalizers:\\n  - helios.io/stuck-test\\nspec:\\n  terminationGracePeriodSeconds: 1\\n  containers:\\n"
          "  - name: app\\n    image: stefanprodan/podinfo:6.7.0\\n    command: [\\\"./podinfo\\\"]\\n")
base=sql("SELECT COALESCE(MAX(id),0) FROM incident"); out(f"[baseline] max id={base}")
try:
    out("[1] cree pod standalone + finalizer")
    out(run(f"printf '{manifest}' | kubectl apply -f - 2>&1"))
    run("sleep 4")
    out("[2] delete -> Terminating bloque (finalizer)")
    out(run(f"kubectl -n pfe delete pod {POD} --grace-period=1 --wait=false 2>&1"))
    time.sleep(3)
    out("etat: "+run(f"kubectl -n pfe get pod {POD} -o jsonpath='del={{.metadata.deletionTimestamp}}' 2>&1"))
    out("\n[3] attente detection+resolution. Fenetre 6 min")
    final=None
    for i in range(24):
        time.sleep(15)
        row=sql(f"SELECT id||'|'||incident_type||'|'||status FROM incident WHERE app='{APP}' AND id>{base} ORDER BY id DESC LIMIT 1")
        pod=run(f"kubectl -n pfe get pod {POD} --no-headers 2>&1")
        st="GONE" if "NotFound" in pod else (pod.split()[2] if len(pod.split())>2 else "?")
        out(f"  +{(i+1)*15:3d}s inc={row or '(aucun)'} | pod={st}")
        if row and row.split('|')[-1] in ("RESOLVED","RESOLUTION_FAILED"): final=row; break
    out("\n[4] logs")
    out(run("kubectl -n pfe logs -l app=backend --tail=500 | grep -iE 'STUCK_TERMINATING|FORCE_DELETE_POD|finalizer' | tail -n 10") or "(rien)")
finally:
    still=run(f"kubectl -n pfe get pod {POD} --no-headers 2>&1")
    if still and "NotFound" not in still:
        out("\n[cleanup] retrait manuel finalizer")
        out(run(f"kubectl -n pfe patch pod {POD} -p '{{\"metadata\":{{\"finalizers\":null}}}}' 2>&1"))
out("\n[final]")
out(sql(f"SELECT id||'|'||incident_type||'|'||status||'|'||left(explanation,120) FROM incident WHERE app='{APP}' ORDER BY id DESC LIMIT 1"))
out("pod: "+run(f"kubectl -n pfe get pod {POD} --no-headers 2>&1"))
c.close()
out("\n=== ATTENDU : POD_STUCK_TERMINATING -> RESOLVED, pod GONE ===")
