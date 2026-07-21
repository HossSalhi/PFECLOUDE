# -*- coding: utf-8 -*-
"""MESURE (sans rien supposer) des faits qui conditionnent le traitement des limites :
  1. une NetworkPolicy casse-t-elle la readiness sous Calico ? (=> detectable par 'replicas' ?)
  2. Loki peut-il attribuer les erreurs a un pod/namespace precis ? (=> 3e signal possible ?)
  3. que repond l'IA sur un blocage reseau et sur une saturation de repliques ?"""
import paramiko, sys, time, json, urllib.parse
def out(s): sys.stdout.buffer.write((str(s)+"\n").encode("utf-8","replace")); sys.stdout.flush()
def sec(t): out("\n" + "="*74 + "\n  " + t + "\n" + "="*74)

k = paramiko.Ed25519Key.from_private_key_file("C:/Users/houss/.ssh/id_ed25519")
m = paramiko.SSHClient(); m.set_missing_host_key_policy(paramiko.AutoAddPolicy())
m.connect("192.168.148.153", username="kube", pkey=k, timeout=20, allow_agent=False, look_for_keys=False)
def rm(c,t=120):
    _,o,e=m.exec_command(c,timeout=t); return (o.read().decode(errors="replace")+e.read().decode(errors="replace")).strip()
a = paramiko.SSHClient(); a.set_missing_host_key_policy(paramiko.AutoAddPolicy())
a.connect("192.168.148.161", username="kube", password="kube", timeout=20, allow_agent=False, look_for_keys=False)
def ra(c,t=150):
    _,o,e=a.exec_command(c,timeout=t)
    return "\n".join(l for l in (o.read().decode(errors="replace")+e.read().decode(errors="replace")).splitlines() if "password for" not in l).strip()

APP="helios-latency"; NS="pfe"

# ---------- 1. NETPOL vs READINESS ----------
sec("1. UNE NETWORKPOLICY CASSE-T-ELLE LA READINESS ? (Calico)")
out("   sonde de readiness actuelle :")
out("   " + rm("kubectl get deploy %s -n %s -o jsonpath='{.spec.template.spec.containers[0].readinessProbe}'" % (APP,NS)))
Y = """apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: mesure-denyall
  namespace: pfe
spec:
  podSelector:
    matchLabels:
      app: helios-latency
  policyTypes:
  - Ingress
  - Egress
"""
out("   " + rm("cat <<'EOF' | kubectl apply -f -\n" + Y + "EOF"))
for i in range(6):
    time.sleep(15)
    st = rm("kubectl get deploy %s -n %s -o jsonpath='ready={.status.readyReplicas} unavail={.status.unavailableReplicas}'" % (APP,NS))
    out("   [+%ds] %s" % (15*(i+1), st))
    if "unavail=1" in st: break
casse = "unavail=1" in st
out("   >>> deny-all ingress+egress casse la readiness : %s" % ("OUI (donc detectable par le chemin replicas)" if casse else "NON (angle mort confirme)"))

# ---------- 3a. REPONSE DE L'IA SUR UN BLOCAGE RESEAU ----------
sec("3a. QUE REPOND L'IA SUR CE BLOCAGE RESEAU ?")
body = json.dumps({"namespace": NS, "app": APP, "windowMinutes": 5})
r = ra("curl -s --max-time 90 -X POST http://localhost:8000/predict -H 'Content-Type: application/json' -d '%s'" % body)
try:
    d = json.loads(r)
    out("   incident_type : %s" % d.get("incident_type"))
    out("   action        : %s" % (d.get("final_executable_action") or d.get("recommended_action")))
    out("   confiance     : %s" % d.get("confidence"))
    act = str(d.get("final_executable_action") or d.get("recommended_action") or "")
    out("   >>> branche qui serait empruntee : %s" % ("NETWORK" if "NETWORK" in act else ("SCALE" if "SCALE" in act else "defaut (restart)")))
except Exception as ex:
    out("   reponse brute : " + r[:400])
rm("kubectl delete networkpolicy mesure-denyall -n %s --ignore-not-found >/dev/null 2>&1" % NS)
out("   netpol retiree")

# ---------- 2. LOKI : ATTRIBUTION ----------
sec("2. LOKI PEUT-IL ATTRIBUER LES ERREURS A UN POD PRECIS ?")
q = 'topk(3, sum by (filename) (count_over_time({job="k8s-containers"} |~ "(?i)(error|exception|panic)" [5m])))'
r = ra("curl -s --max-time 25 -G 'http://192.168.148.160:3100/loki/api/v1/query' --data-urlencode 'query=%s'" % q)
try:
    d = json.loads(r).get("data", {}).get("result", [])
    out("   series : %d" % len(d))
    for s in d[:3]:
        out("     filename=%s  valeur=%s" % (s["metric"].get("filename","?"), s["value"][1]))
    if d:
        f = d[0]["metric"].get("filename","")
        out("\n   parsing du filename : %s" % f)
        out("   -> segments '/' : %s" % f.split("/"))
except Exception as ex:
    out("   " + r[:400])

# ---------- 3b. SATURATION DE REPLIQUES ----------
sec("3b. QUE REPOND L'IA SUR UNE SATURATION (pour la branche SCALE) ?")
out("   " + rm("kubectl scale deployment %s -n %s --replicas=3" % (APP,NS)))
time.sleep(45)
r = ra("curl -s --max-time 90 -X POST http://localhost:8000/predict -H 'Content-Type: application/json' -d '%s'" % body)
try:
    d = json.loads(r)
    act = str(d.get("final_executable_action") or d.get("recommended_action") or "")
    out("   incident_type : %s | action : %s" % (d.get("incident_type"), act))
    out("   >>> branche : %s" % ("SCALE" if "SCALE" in act else ("NETWORK" if "NETWORK" in act else "defaut")))
except: out("   " + r[:300])
rm("kubectl scale deployment %s -n %s --replicas=1 >/dev/null 2>&1" % (APP,NS))

sec("REMISE EN ETAT")
for i in range(12):
    time.sleep(8)
    st = rm("kubectl get deploy %s -n %s -o jsonpath='{.status.readyReplicas}/{.spec.replicas}'" % (APP,NS))
    if st == "1/1": break
out("   %s : %s | netpols : %s" % (APP, st, rm("kubectl get networkpolicy -n %s --no-headers 2>/dev/null | wc -l" % NS)))
m.close(); a.close()
