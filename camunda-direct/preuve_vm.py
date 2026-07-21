# -*- coding: utf-8 -*-
"""PREUVE : Camunda detecte une VM KO et execute une commande SYSTEME dessus (SSH).
Filet de securite : si Camunda n'agit pas, on relance kubelet a la main et on le dit."""
import paramiko, sys, time, json, urllib.parse
def out(s): sys.stdout.buffer.write((str(s)+"\n").encode("utf-8","replace")); sys.stdout.flush()
def sec(t): out("\n" + "="*74 + "\n  " + t + "\n" + "="*74)
CIBLE = "worker2"; IP = "192.168.148.155"; NOM = "k8s-worker2"

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
w = paramiko.SSHClient(); w.set_missing_host_key_policy(paramiko.AutoAddPolicy())
w.connect(IP, username="kube", password="kube", timeout=20, allow_agent=False, look_for_keys=False)
def rw(c,t=60):
    _,o,e=w.exec_command(c,timeout=t)
    return "\n".join(l for l in (o.read().decode(errors="replace")+e.read().decode(errors="replace")).splitlines() if "password for" not in l).strip()

def audit(): return ra("echo kube | sudo -S grep -c 'restart kubelet' /var/log/helios-ssh-bridge.log 2>/dev/null || echo 0").strip().splitlines()[-1]
def instances(n=5):
    q = ("curl -s --max-time 20 -X POST http://localhost:8080/v2/process-instances/search -H 'Content-Type: application/json' "
         "-d '{\"filter\":{\"processDefinitionId\":\"helios-camunda-full-cycle\"},\"sort\":[{\"field\":\"startDate\",\"order\":\"DESC\"}],\"page\":{\"limit\":%d}}'" % n)
    try: return [str(i["processInstanceKey"]) for i in json.loads(ra(q)).get("items",[])]
    except: return []
def elements(key):
    q = ("curl -s --max-time 20 -X POST http://localhost:8080/v2/element-instances/search -H 'Content-Type: application/json' "
         "-d '{\"filter\":{\"processInstanceKey\":\"%s\"},\"page\":{\"limit\":40}}'" % key)
    try: return [i.get("elementId") for i in json.loads(ra(q)).get("items",[])]
    except: return []
def vars_of(key):
    q = ("curl -s --max-time 20 -X POST http://localhost:8080/v2/variables/search -H 'Content-Type: application/json' "
         "-d '{\"filter\":{\"processInstanceKey\":\"%s\"},\"page\":{\"limit\":40}}'" % key)
    try: return {i["name"]: str(i.get("value"))[:80] for i in json.loads(ra(q)).get("items",[])}
    except: return {}

sec("ETAT INITIAL")
out("   noeuds       : " + rm("kubectl get nodes --no-headers | awk '{print $1\"=\"$2}' | tr '\\n' ' '"))
out("   kubelet w2   : " + rw("systemctl is-active kubelet"))
audit0 = audit(); out("   audit bridge : %s ligne(s) 'restart kubelet'" % audit0)

sec("INJECTION : arret de kubelet sur worker2 (depuis la VM elle-meme)")
rw("echo kube | sudo -S systemctl stop kubelet")
time.sleep(4)
out("   kubelet w2   : " + rw("systemctl is-active kubelet"))

sec("ATTENTE : NotReady -> Prometheus -> Camunda -> SSH")
t0 = time.time(); vu_notready=False; agi=False; fin=None
while time.time()-t0 < 660:
    time.sleep(20)
    dt = int(time.time()-t0)
    etat = rm("kubectl get node %s --no-headers 2>/dev/null | awk '{print $2}'" % NOM)
    kub  = rw("systemctl is-active kubelet")
    aud  = audit()
    if etat == "NotReady" and not vu_notready:
        out("   [+%ds] Kubernetes declare %s NotReady" % (dt, NOM)); vu_notready=True
    if aud != audit0 and not agi:
        out("   [+%ds] >>> LA PASSERELLE A RECU UN 'restart kubelet' (declenche par Camunda)" % dt); agi=True
    if dt % 100 < 20:
        out("   [+%ds] noeud=%s kubelet=%s audit=%s" % (dt, etat or "?", kub, aud))
    if agi and kub == "active" and etat == "Ready":
        out("   [+%ds] >>> worker2 EST REVENU Ready" % dt); fin=dt; break

sec("ATTRIBUTION — journal de la passerelle SSH")
out(ra("echo kube | sudo -S tail -8 /var/log/helios-ssh-bridge.log 2>/dev/null"))

sec("ATTRIBUTION — instance Camunda")
trouve=False
for key in instances(6):
    els = elements(key)
    if "ActionNoeud" in els:
        v = vars_of(key)
        out("   instance %s -> %s" % (key, ", ".join(e for e in els if e.startswith("Fin")) or "en cours"))
        for n2 in ("origine","cible","ns","typeIA","actionIA","execCible","execCode","execOk","execNoeud"):
            if n2 in v: out("      %-12s = %s" % (n2, v[n2]))
        trouve=True; break
if not trouve:
    out("   aucune instance n'a emprunte la branche VM — parcours des 4 dernieres :")
    for key in instances(4):
        v = vars_of(key)
        out("     %s -> %s | origine=%s typeIA=%s" % (key, ", ".join(e for e in elements(key) if e.startswith("Fin")) or "en cours",
                                                      v.get("origine","?"), v.get("typeIA","-")))

sec("FILET DE SECURITE")
if rw("systemctl is-active kubelet") != "active":
    out("   Camunda n'a pas relance kubelet -> remise en etat MANUELLE")
    rw("echo kube | sudo -S systemctl start kubelet")
    for i in range(15):
        time.sleep(10)
        if rm("kubectl get node %s --no-headers | awk '{print $2}'" % NOM) == "Ready": break
else:
    out("   inutile : kubelet a ete relance par Camunda")
out("   kubelet w2 : " + rw("systemctl is-active kubelet"))
out("   noeuds     : " + rm("kubectl get nodes --no-headers | awk '{print $1\"=\"$2}' | tr '\\n' ' '"))
out("   apps pfe KO: " + (rm("kubectl get deploy -n pfe --no-headers | awk '{split($2,a,\"/\"); if(a[1]!=a[2]) print $1}' | tr '\\n' ' '") or "aucune"))
out("\n   RESULTAT : Camunda a agi sur la VM = %s%s" % ("OUI" if agi else "NON", " (retour Ready en %ds)" % fin if fin else ""))
m.close(); a.close(); w.close()
