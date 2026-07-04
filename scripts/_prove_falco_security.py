"""PREUVE end-to-end : DÉTECTION SÉCURITÉ Falco -> incident HELIOS -> IA -> Camunda -> RESOLVED.

Tout se declenche DANS le cluster (kubectl en SSH sur le master). On cree un workload de test
(helios-sectest, busybox root), on declenche une VRAIE action malveillante (lecture /etc/shadow),
et on prouve la chaine : Falco (eBPF) detecte -> Falcosidekick POST /api/webhooks/falco -> le backend
cree un incident MALICIOUS_POD avec action executable DELETE_CRASHLOOPING_PODS -> Camunda remplace le
pod compromis -> incident RESOLVED. Verifie aussi que le backend (app critique) n'est PAS auto-remedie.
"""
import sys, time, paramiko
KEY="C:/Users/houss/.ssh/id_ed25519"; MASTER="192.168.148.153"
def out(s): sys.stdout.buffer.write((s+"\n").encode("utf-8","replace")); sys.stdout.flush()
k=paramiko.Ed25519Key.from_private_key_file(KEY); c=paramiko.SSHClient(); c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect(MASTER,username="kube",pkey=k,timeout=12,allow_agent=False,look_for_keys=False)
def sh(cmd,t=90): _,o,e=c.exec_command(cmd,timeout=t); return (o.read().decode(errors="replace")+e.read().decode(errors="replace")).strip()
prim=sh("kubectl -n pfe get pods -l cnpg.io/instanceRole=primary -o jsonpath='{.items[0].metadata.name}'")
def q(sql): return sh(f"kubectl exec -n pfe {prim} -c postgres -- psql -U postgres -d pfe_db -t -A -F'|' -c \"{sql}\"")

out("[1] workload de test (busybox root) dans pfe")
out(sh("kubectl -n pfe get deploy helios-sectest >/dev/null 2>&1 && echo existe || kubectl -n pfe create deploy helios-sectest --image=busybox:1.36 -- sh -c 'sleep 3600'"))
sh("kubectl -n pfe patch deploy helios-sectest --type=json -p='[{\"op\":\"replace\",\"path\":\"/spec/template/spec/containers/0/resources\",\"value\":{\"requests\":{\"cpu\":\"10m\",\"memory\":\"32Mi\"},\"limits\":{\"cpu\":\"50m\",\"memory\":\"64Mi\"}}},{\"op\":\"add\",\"path\":\"/spec/template/spec/containers/0/securityContext\",\"value\":{\"runAsUser\":0}}]' 2>&1")
sh("kubectl -n pfe rollout status deploy/helios-sectest --timeout=90s")
pod=sh("kubectl -n pfe get pods -l app=helios-sectest -o jsonpath='{.items[0].metadata.name}'")
out(f"  pod cible = {pod}")

out("[2] DÉCLENCHEUR sécurité DANS le conteneur : lecture de /etc/shadow (root)")
out("  "+sh(f"kubectl -n pfe exec {pod} -- sh -c 'cat /etc/shadow >/dev/null 2>&1 && echo SHADOW_READ_OK'"))

out("[3] suivi Falco -> incident -> Camunda -> RESOLVED")
final=None
for i in range(24):
    time.sleep(6)
    row=q("SELECT id||'|'||incident_type||'|'||status FROM incident WHERE source='OTHER' AND app='helios-sectest' AND status<>'ARCHIVED' ORDER BY id DESC LIMIT 1")
    act=q("SELECT type||'/'||status FROM incident_action WHERE incident_id=(SELECT max(id) FROM incident WHERE source='OTHER' AND app='helios-sectest') ORDER BY id DESC LIMIT 1")
    curpod=sh("kubectl -n pfe get pods -l app=helios-sectest -o jsonpath='{.items[0].metadata.name}'")
    out(f"  +{(i+1)*6}s inc={row} action={act} pod={curpod}")
    if row and '|RESOLVED' in row: final=row; break

out("\n[4] AUTO-PROTECTION : le backend (app critique) est-il IGNORÉ par Falco ?")
prot=sh("kubectl -n pfe logs deploy/backend --tail=300 2>/dev/null | grep -c 'ignored-protected-app' || echo 0")
noinc=q("SELECT count(*) FROM incident WHERE source='OTHER' AND app IN ('backend','frontend') AND status NOT IN ('ARCHIVED','CLOSED')")
out(f"  events backend ignorés (log)≈{prot} | incidents backend actifs={noinc} (attendu 0)")
c.close()
out("\n=== ATTENDU : incident MALICIOUS_POD helios-sectest -> RESOLVED (pod remplacé), backend intact ===")
out("RESULTAT: "+(final or "NON RESOLU"))
