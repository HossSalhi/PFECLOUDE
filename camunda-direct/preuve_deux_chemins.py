# -*- coding: utf-8 -*-
"""Valide les DEUX chemins de detection de la v8 : replicas indisponibles ET saturation CPU."""
import paramiko, sys, time, json, urllib.parse
APP = "helios-latency"; NS = "pfe"
def out(s): sys.stdout.buffer.write((str(s)+"\n").encode("utf-8","replace")); sys.stdout.flush()
def sec(t): out("\n" + "="*74 + "\n  " + t + "\n" + "="*74)

k = paramiko.Ed25519Key.from_private_key_file("C:/Users/houss/.ssh/id_ed25519")
m = paramiko.SSHClient(); m.set_missing_host_key_policy(paramiko.AutoAddPolicy())
m.connect("192.168.148.153", username="kube", pkey=k, timeout=20, allow_agent=False, look_for_keys=False)
def rm(cmd, t=120): _, o, e = m.exec_command(cmd, timeout=t); return (o.read().decode(errors="replace")+e.read().decode(errors="replace")).strip()
a = paramiko.SSHClient(); a.set_missing_host_key_policy(paramiko.AutoAddPolicy())
a.connect("192.168.148.161", username="kube", password="kube", timeout=20, allow_agent=False, look_for_keys=False)
def ra(cmd, t=180):
    _, o, e = a.exec_command(cmd, timeout=t)
    return "\n".join(l for l in (o.read().decode(errors="replace")+e.read().decode(errors="replace")).splitlines()
                     if "password for" not in l).strip()
def ann(x): return rm("kubectl get deploy %s -n %s -o jsonpath='{.metadata.annotations.helios\\.camunda/%s}'" % (APP, NS, x))
def q(x): return ra("curl -s --max-time 10 'http://192.168.148.160:9090/api/v1/query?query=%s'" % urllib.parse.quote(x))

def annuler_bloquees():
    qq = ("curl -s --max-time 20 -X POST http://localhost:8080/v2/process-instances/search -H 'Content-Type: application/json' "
          "-d '{\"filter\":{\"processDefinitionId\":\"helios-camunda-full-cycle\",\"state\":\"ACTIVE\"},\"page\":{\"limit\":30}}'")
    try:
        keys = [str(i["processInstanceKey"]) for i in json.loads(ra(qq)).get("items", [])]
        for kk in keys:
            ra(f"curl -s -o /dev/null --max-time 15 -X POST http://localhost:8080/v2/process-instances/{kk}/cancellation -H 'Content-Type: application/json' -d '{{}}'")
        return len(keys)
    except: return -1

sec("NETTOYAGE PREALABLE")
out("   instances bloquees annulees : %d" % annuler_bloquees())

# ================= CHEMIN 1 : REPLICAS =================
sec("CHEMIN 1/2 — REPLICAS INDISPONIBLES (non-regression)")
b = ann("executedAt"); out("   executedAt AVANT : %s" % (b or "(vide)"))
out("   > " + rm("kubectl set image deployment/%s -n %s '*=stefanprodan/podinfo:0.0.0-p1'" % (APP, NS)))
t0 = time.time(); ok1 = False
for i in range(18):
    time.sleep(12)
    n = ann("executedAt")
    if n and n != b: out("   [+%ds] CYCLE EXECUTE" % int(time.time()-t0)); ok1 = True; break
    if i % 4 == 0: out("   [+%ds] ..." % int(time.time()-t0))
if ok1:
    out("   origine : %s | IA : %s -> %s | exec : %s" % (ann("origine") or "?", ann("aiType"), ann("aiAction"), ann("result")))
    out("   >>> CHEMIN REPLICAS OK")
else:
    out("   >>> CHEMIN REPLICAS : ECHEC")
rm("kubectl set image deployment/%s -n %s '*=stefanprodan/podinfo:6.7.0' >/dev/null 2>&1" % (APP, NS))
for i in range(12):
    time.sleep(8)
    if rm("kubectl get deploy %s -n %s -o jsonpath='{.status.readyReplicas}/{.spec.replicas}'" % (APP, NS)) == "1/1": break
out("   app restauree")

# ================= CHEMIN 2 : CPU =================
sec("CHEMIN 2/2 — SATURATION CPU (nouvelle capacite)")
time.sleep(30)
b2 = ann("executedAt"); out("   executedAt AVANT : %s" % (b2 or "(vide)"))
pods = [p for p in rm("kubectl get pod -n %s -l app=%s -o jsonpath='{.items[*].metadata.name}'" % (NS, APP)).split() if p]
out("   pod cible : %s" % pods)
for p in pods:
    rm("kubectl exec -n %s %s -- sh -c 'nohup sh -c \"for i in 1 2 3; do yes >/dev/null & done; sleep 420; killall yes\" >/dev/null 2>&1 & echo ok' 2>/dev/null || true" % (NS, p))
out("   charge CPU lancee")
t0 = time.time(); vu = False
CPUQ = 'sum by (namespace,pod) (rate(container_cpu_usage_seconds_total{container!="POD",container!=""}[3m])) > 0.15'
for i in range(16):
    time.sleep(20)
    if APP in q(CPUQ):
        out("   [+%ds] PROMETHEUS voit la saturation CPU" % int(time.time()-t0)); vu = True; break
    if i % 3 == 0:
        cur = q('sum by (pod) (rate(container_cpu_usage_seconds_total{namespace="pfe",pod=~"%s.*",container!="POD",container!=""}[3m]))' % APP)
        v = cur.split('"value":[')[1].split(']')[0].split(',')[1].strip('"')[:6] if '"value"' in cur else "?"
        out("   [+%ds] CPU du pod : %s (seuil 0.15)" % (int(time.time()-t0), v))
ok2 = False
if vu:
    for i in range(14):
        time.sleep(15)
        n = ann("executedAt")
        if n and n != b2: out("   [+%ds] CYCLE EXECUTE" % int(time.time()-t0)); ok2 = True; break
        if i % 3 == 0: out("   [+%ds] cycle en cours..." % int(time.time()-t0))
if ok2:
    out("   origine : %s | IA : %s -> %s | exec : %s" % (ann("origine") or "?", ann("aiType"), ann("aiAction"), ann("result")))
    out("   >>> CHEMIN CPU OK")
elif vu:
    out("   >>> Prometheus a vu, mais pas d'action (l'IA a peut-etre repondu NO_INCIDENT)")
else:
    out("   >>> seuil CPU non atteint")

sec("NETTOYAGE FINAL")
for p in pods:
    rm("kubectl exec -n %s %s -- sh -c 'killall yes 2>/dev/null; true' 2>/dev/null || true" % (NS, p))
rm("kubectl set image deployment/%s -n %s '*=stefanprodan/podinfo:6.7.0' >/dev/null 2>&1" % (APP, NS))
for i in range(12):
    time.sleep(8)
    st = rm("kubectl get deploy %s -n %s -o jsonpath='{.status.readyReplicas}/{.spec.replicas}'" % (APP, NS))
    if st == "1/1": break
out("   %s : %s" % (APP, st))
out("   apps KO : " + (rm("kubectl get deploy -n pfe --no-headers | awk '{split($2,a,\"/\"); if(a[1]!=a[2]) print $1}' | tr '\\n' ' '") or "aucune"))
out("   instances bloquees : %d" % max(0, annuler_bloquees()))
out("\n   RESULTAT : chemin replicas = %s | chemin CPU = %s" % ("OK" if ok1 else "KO", "OK" if ok2 else ("vu mais pas d'action" if vu else "KO")))
m.close(); a.close()
