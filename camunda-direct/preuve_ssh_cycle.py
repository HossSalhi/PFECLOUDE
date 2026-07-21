# -*- coding: utf-8 -*-
"""PREUVE du cycle complet en voie SSH (v12) :
  1. incident applicatif dans un namespace arbitraire -> execute par SSH + trace
  2. les 2 garde-fous refusent toujours (ils sont un CHOIX, pas une limite technique)"""
import paramiko, sys, time, json
def out(s): sys.stdout.buffer.write((str(s)+"\n").encode("utf-8","replace")); sys.stdout.flush()
def sec(t): out("\n" + "="*74 + "\n  " + t + "\n" + "="*74)
k = paramiko.Ed25519Key.from_private_key_file("C:/Users/houss/.ssh/id_ed25519")
m = paramiko.SSHClient(); m.set_missing_host_key_policy(paramiko.AutoAddPolicy())
m.connect("192.168.148.153", username="kube", pkey=k, timeout=20, allow_agent=False, look_for_keys=False)
def rm(c,t=120):
    _,o,e=m.exec_command(c,timeout=t); return (o.read().decode(errors="replace")+e.read().decode(errors="replace")).strip()
a = paramiko.SSHClient(); a.set_missing_host_key_policy(paramiko.AutoAddPolicy())
a.connect("192.168.148.161", username="kube", password="kube", timeout=20, allow_agent=False, look_for_keys=False)
def ra(c,t=120):
    _,o,e=a.exec_command(c,timeout=t)
    return "\n".join(l for l in (o.read().decode(errors="replace")+e.read().decode(errors="replace")).splitlines() if "password for" not in l).strip()
def ann(ns,nom,x): return rm("kubectl get deploy %s -n %s -o jsonpath='{.metadata.annotations.helios\\.camunda/%s}' 2>/dev/null" % (nom,ns,x))
def instances(n=5):
    q = ("curl -s --max-time 20 -X POST http://localhost:8080/v2/process-instances/search -H 'Content-Type: application/json' "
         "-d '{\"filter\":{\"processDefinitionId\":\"helios-camunda-full-cycle\"},\"sort\":[{\"field\":\"startDate\",\"order\":\"DESC\"}],\"page\":{\"limit\":%d}}'" % n)
    try: return [str(i["processInstanceKey"]) for i in json.loads(ra(q)).get("items",[])]
    except: return []
def els(key):
    q = ("curl -s --max-time 20 -X POST http://localhost:8080/v2/element-instances/search -H 'Content-Type: application/json' "
         "-d '{\"filter\":{\"processInstanceKey\":\"%s\"},\"page\":{\"limit\":40}}'" % key)
    try: return [i.get("elementId") for i in json.loads(ra(q)).get("items",[])]
    except: return []
def vs(key):
    q = ("curl -s --max-time 20 -X POST http://localhost:8080/v2/variables/search -H 'Content-Type: application/json' "
         "-d '{\"filter\":{\"processInstanceKey\":\"%s\"},\"page\":{\"limit\":40}}'" % key)
    try: return {i["name"]: str(i.get("value"))[:90] for i in json.loads(ra(q)).get("items",[])}
    except: return {}
def casser(ns, nom, creer=True):
    if creer: rm("kubectl create namespace %s 2>/dev/null || true" % ns)
    Y = ("apiVersion: apps/v1\nkind: Deployment\nmetadata: {name: %s, namespace: %s}\nspec:\n  replicas: 1\n"
         "  selector: {matchLabels: {app: %s}}\n  template:\n    metadata: {labels: {app: %s}}\n"
         "    spec:\n      containers: [{name: c, image: 'nginx:0.0.0-inexistante'}]\n" % (nom,ns,nom,nom))
    return rm("cat <<'EOF' | kubectl apply -f -\n" + Y + "EOF")
def nettoyer():
    rm("kubectl delete deploy ssh-demo -n demo-ssh2 --ignore-not-found >/dev/null 2>&1")
    rm("kubectl delete deploy ssh-garde -n kube-public --ignore-not-found >/dev/null 2>&1")

nettoyer(); time.sleep(8)

sec("1. CYCLE COMPLET EN VOIE SSH (namespace arbitraire)")
out("   " + casser("demo-ssh2", "ssh-demo"))
t0=time.time(); ok=False
for i in range(16):
    time.sleep(15)
    if ann("demo-ssh2","ssh-demo","executedAt"): ok=True; break
    if i%3==0: out("   [+%ds] attente du cycle..." % int(time.time()-t0))
if ok:
    out("   [+%ds] CYCLE EXECUTE" % int(time.time()-t0))
    for x in ("aiType","aiAction","origine","result"):
        out("      %-10s = %s" % (x, ann("demo-ssh2","ssh-demo",x)))
    for key in instances(4):
        if "ExecuterSSH" in els(key):
            v = vs(key)
            out("   instance %s -> %s" % (key, ", ".join(e for e in els(key) if e.startswith("Fin")) or "en cours"))
            for n2 in ("execFait","execOk","execCode","execCmd","traceOk"):
                if n2 in v: out("      %-9s = %s" % (n2, v[n2]))
            break
    out("   >>> 1 : EXECUTION PAR SSH CONFIRMEE")
else:
    out("   >>> 1 : ECHEC")
rm("kubectl delete deploy ssh-demo -n demo-ssh2 --ignore-not-found >/dev/null 2>&1")
time.sleep(30)

sec("2. LES GARDE-FOUS REFUSENT-ILS TOUJOURS ? (namespace systeme)")
out("   " + casser("kube-public", "ssh-garde", creer=False))
t0=time.time(); agi=False; protege=False
for i in range(18):
    time.sleep(15)
    if ann("kube-public","ssh-garde","executedAt"):
        out("   [+%ds] !!! ACTION EXECUTEE — garde-fou inefficace" % int(time.time()-t0)); agi=True; break
    if any("FinProtege" in els(kk) for kk in instances(3)):
        out("   [+%ds] VERDICT : FinProtege (refus assume)" % int(time.time()-t0)); protege=True; break
    if i%4==0: out("   [+%ds] aucune action (attendu)..." % int(time.time()-t0))
out("   parcours : " + " | ".join("%s->%s" % (kk[-6:], ",".join(e for e in els(kk) if e.startswith("Fin")) or "en cours") for kk in instances(4)))
out("   >>> 2 : %s" % ("GARDE-FOU ACTIF — Camunda PEUT tout faire mais REFUSE ici" if protege and not agi else ("ECHEC" if agi else "a verifier")))

sec("JOURNAL D'AUDIT DE LA PASSERELLE")
out(ra("echo kube | sudo -S tail -8 /var/log/helios-ssh-bridge.log 2>/dev/null"))

sec("NETTOYAGE")
nettoyer()
rm("kubectl delete namespace demo-ssh2 --ignore-not-found >/dev/null 2>&1 &")
time.sleep(12)
out("   noeuds      : " + rm("kubectl get nodes --no-headers | awk '{print $1\"=\"$2}' | tr '\\n' ' '"))
out("   apps pfe KO : " + (rm("kubectl get deploy -n pfe --no-headers | awk '{split($2,a,\"/\"); if(a[1]!=a[2]) print $1}' | tr '\\n' ' '") or "aucune"))
out("   coredns     : " + rm("kubectl get deploy coredns -n kube-system -o jsonpath='{.status.readyReplicas}/{.spec.replicas}'"))
out("\n   RESULTAT : execution SSH=%s | garde-fou=%s" % ("OK" if ok else "KO", "ACTIF" if protege and not agi else "a verifier"))
m.close(); a.close()
