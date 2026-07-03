"""PREUVE 0.2.128 : « Camunda DETECTE, pas l'IA ».
Scenario : on COUPE le service IA (.161), on injecte un crashloop, et on prouve que
Camunda DETECTE quand meme + REMEDIE en repli DETERMINISTE (POD_CRASH_LOOP -> ROLLBACK -> RESOLVED),
le tout AVEC l'IA a l'arret. Puis on rallume l'IA et on verifie l'etat final.
L'IA est TOUJOURS redemarree a la fin (finally)."""
import paramiko, sys, time
KEY="C:/Users/houss/.ssh/id_ed25519"; MASTER="192.168.148.153"; AI="192.168.148.161"
def out(s): sys.stdout.buffer.write((s+"\n").encode("utf-8","replace")); sys.stdout.flush()
def cli(h):
    k=paramiko.Ed25519Key.from_private_key_file(KEY); c=paramiko.SSHClient(); c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(h,username="kube",pkey=k,timeout=12,allow_agent=False,look_for_keys=False); return c
def run(c,cmd,sudo=False,t=90):
    if sudo: cmd="echo 'kube' | sudo -S "+cmd
    _,o,e=c.exec_command(cmd,timeout=t); return (o.read().decode(errors="replace")+e.read().decode(errors="replace")).strip()
m=cli(MASTER); a=cli(AI)
prim=run(m,"kubectl get pods -n pfe -l cnpg.io/instanceRole=primary -o jsonpath='{.items[0].metadata.name}'")
def sql(q): return run(m,f"kubectl exec -n pfe {prim} -c postgres -- psql -U postgres -d pfe_db -t -c \"{q}\" 2>&1")
def health(): return run(m,"curl -s -o /dev/null -w '%{http_code}' http://192.168.148.161:8000/health 2>&1 || echo DOWN")
try:
    base=sql("SELECT COALESCE(MAX(id),0) FROM incident"); out(f"[baseline] max incident id = {base.strip()}")
    out("\n[1] STOP service IA (.161)")
    out(run(a,"systemctl stop pfe-ai-service.service",sudo=True))
    time.sleep(4); out(f"    IA /health = {health()}  (attendu != 200)")
    out("\n[2] injection crashloop sur helios-crashloop (kubectl, cote master)")
    out(run(m,"kubectl -n pfe patch deploy helios-crashloop --type=json "
        "-p '[{\"op\":\"add\",\"path\":\"/spec/template/spec/containers/0/command\",\"value\":[\"sh\",\"-c\",\"echo boot; exit 1\"]}]'"))
    out("\n[3] attente detection+remediation AVEC IA DOWN (poll<=120s + Camunda). Fenetre 8 min.")
    seen_id=None; final=None
    for i in range(32):  # 32 x 15s = 480s
        time.sleep(15)
        row=sql(f"SELECT id||'|'||incident_type||'|'||status FROM incident WHERE app='helios-crashloop' AND id>{base.strip()} ORDER BY id DESC LIMIT 1").strip()
        hh=health()
        out(f"    +{(i+1)*15:3d}s  IA={hh:4s}  incident={row or '(aucun)'}")
        if row:
            seen_id=row.split('|')[0]
            if row.split('|')[-1] in ("RESOLVED","RESOLUTION_FAILED"): final=row; break
    out("\n[4] logs backend : preuve du repli DETERMINISTE (IA injoignable)")
    out(run(m,"kubectl -n pfe logs -l app=backend --tail=400 | grep -iE 'repli DETERMINISTE|repli DÉTERMINISTE|injoignable|CRASH_LOOP|ROLLBACK' | tail -n 12") or "(rien)")
finally:
    out("\n[5] REDEMARRAGE service IA (.161) — garanti")
    out(run(a,"systemctl start pfe-ai-service.service",sudo=True,t=60))
    time.sleep(6); out(f"    IA /health = {health()}  (attendu 200)")
out("\n[6] etat final")
out(run(m,"kubectl -n pfe get deploy helios-crashloop --no-headers 2>&1"))
out(sql("SELECT id||'|'||app||'|'||incident_type||'|'||status FROM incident WHERE app='helios-crashloop' ORDER BY id DESC LIMIT 3"))
m.close(); a.close()
out("\n=== ATTENDU : incident POD_CRASH_LOOP cree + RESOLVED PENDANT que l'IA etait DOWN ===")
