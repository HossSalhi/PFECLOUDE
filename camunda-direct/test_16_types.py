# -*- coding: utf-8 -*-
"""Injecte chaque type d'incident et verifie que le detecteur le CLASSE correctement."""
import paramiko, sys, time, json
def out(s): sys.stdout.buffer.write((str(s)+"\n").encode("utf-8","replace")); sys.stdout.flush()
def sec(t): out("\n" + "="*76 + "\n  " + t + "\n" + "="*76)
NS = "pfe"
k = paramiko.Ed25519Key.from_private_key_file("C:/Users/houss/.ssh/id_ed25519")
m = paramiko.SSHClient(); m.set_missing_host_key_policy(paramiko.AutoAddPolicy())
m.connect("192.168.148.153", username="kube", pkey=k, timeout=20, allow_agent=False, look_for_keys=False)
def rm(c,t=150):
    _,o,e=m.exec_command(c,timeout=t); return (o.read().decode(errors="replace")+e.read().decode(errors="replace")).strip()
mon = paramiko.SSHClient(); mon.set_missing_host_key_policy(paramiko.AutoAddPolicy())
mon.connect("192.168.148.160", username="kube", password="kube", timeout=20, allow_agent=False, look_for_keys=False)
def detecter():
    _,o,e=mon.exec_command("python3 /opt/helios/detecter.py", timeout=60)
    t = o.read().decode(errors="replace").strip()
    try: return json.loads(t)
    except: return {"type":"?", "preuve": t[:120]}

def appliquer(y): return rm("cat <<'EOF' | kubectl apply -f -\n" + y + "EOF")

def dep(nom, extra_spec="", extra_ctr="", image="stefanprodan/podinfo:6.7.0", cmd=""):
    return """apiVersion: apps/v1
kind: Deployment
metadata: {name: %s, namespace: pfe}
spec:
  replicas: 1
  selector: {matchLabels: {app: %s}}
  template:
    metadata: {labels: {app: %s}}
    spec:
%s      containers:
      - name: c
        image: %s
%s%s""" % (nom, nom, nom, extra_spec, image, cmd, extra_ctr)


def assainir():
    """Nettoie UNIQUEMENT les apps de test helios-*.

    NE JAMAIS toucher postgres/CNPG : un force-delete d'un replica le rend incapable
    de se resynchroniser (pg_rewind echoue) et fait perdre la haute disponibilite.
    Lecon apprise a la dure."""
    rm("kubectl get rs -n pfe --no-headers | grep '^helios-' | awk '$2>0 && $4==0 {print $1}' | "
       "xargs -r kubectl delete rs -n pfe --ignore-not-found >/dev/null 2>&1")
    rm("kubectl get pod -n pfe --no-headers | grep '^helios-' | "
       "awk '{split($2,a,\"/\"); if (a[1] != a[2]) print $1}' | "
       "xargs -r kubectl delete pod -n pfe --ignore-not-found >/dev/null 2>&1")

def restaurer(nom):
    """Retire explicitement les champs injectes. `kubectl apply` NE SUPPRIME PAS un
    champ absent du manifeste : il fusionne. Il faut un patch JSON 'remove'."""
    for chemin in ("/spec/template/spec/containers/0/envFrom",
                   "/spec/template/spec/containers/0/command",
                   "/spec/template/spec/containers/0/resources",
                   "/spec/template/spec/containers/0/readinessProbe",
                   "/spec/template/spec/initContainers"):
        rm("kubectl patch deployment %s -n pfe --type=json -p '[{\"op\":\"remove\",\"path\":\"%s\"}]' "
           ">/dev/null 2>&1" % (nom, chemin))

def attendre_sain(limite=150):
    """N'injecte rien tant que le detecteur ne dit pas NO_INCIDENT."""
    assainir()
    for i in range(int(limite/10)):
        time.sleep(10)
        t = detecter().get("type")
        if t == "NO_INCIDENT":
            return True
        if i % 4 == 3:
            out("   (assainissement : encore %s)" % t)
            assainir()
    out("   !! etat non sain avant injection : %s" % detecter().get("type"))
    return False

RESULTATS = []
def essai(nom_test, attendu, injection, attente=70, nettoyage=None, cible_attendue=None):
    sec("%s  (attendu : %s)" % (nom_test, attendu))
    if not attendre_sain():
        out("   >>> IGNORE : etat de depart non sain")
        RESULTATS.append((nom_test, attendu, "etat-sale", False)); return False
    injection()
    trouve = None
    t0 = time.time()
    for i in range(int(attente/10)):
        time.sleep(10)
        d = detecter()
        if d.get("type") == attendu:
            trouve = d; break
        if i % 3 == 0:
            out("   [+%ds] detecte pour l'instant : %s" % (int(time.time()-t0), d.get("type")))
    d = trouve or detecter()
    ok = d.get("type") == attendu
    out("   -> type=%s ns=%s cible=%s" % (d.get("type"), d.get("ns"), d.get("cible")))
    out("   -> preuve : %s" % d.get("preuve"))
    out("   >>> %s" % ("OK" if ok else "ECHEC (attendu %s)" % attendu))
    RESULTATS.append((nom_test, attendu, d.get("type"), ok))
    if nettoyage: nettoyage()
    assainir()
    return ok

sec("ETAT INITIAL")
out("   detecteur : " + detecter().get("type"))

# ---------------------------------------------------------------- 1
essai("1. IMAGE PULL", "POD_IMAGE_PULL_BACKOFF",
      lambda: rm("kubectl set image deployment/helios-imagepull -n pfe '*=nginx:0.0.0-nexistepas'"),
      nettoyage=lambda: rm("kubectl set image deployment/helios-imagepull -n pfe '*=stefanprodan/podinfo:6.7.0' >/dev/null"))
# ---------------------------------------------------------------- 2
essai("2. CRASH LOOP", "POD_CRASH_LOOP",
      lambda: appliquer(dep("helios-crashloop", cmd='        command: ["sh","-c","echo demarrage; exit 1"]\n')),
      attente=110,
      nettoyage=lambda: restaurer("helios-crashloop"))
# ---------------------------------------------------------------- 3
essai("3. CONFIG ERROR", "POD_CONFIG_ERROR",
      lambda: appliquer(dep("helios-config", extra_ctr='        envFrom:\n        - configMapRef: {name: configmap-absente}\n')),
      nettoyage=lambda: restaurer("helios-config"))
# ---------------------------------------------------------------- 4
essai("4. MEMORY / OOM", "MEMORY_STRESS",
      lambda: appliquer(dep("helios-oom",
              cmd='        command: ["sh","-c","tail /dev/zero"]\n',
              extra_ctr='        resources: {limits: {memory: 32Mi}}\n')),
      attente=110,
      nettoyage=lambda: restaurer("helios-oom"))
# ---------------------------------------------------------------- 5
essai("5. INIT CONTAINER", "POD_INIT_CONTAINER_FAILURE",
      lambda: appliquer(dep("helios-init",
              extra_spec='      initContainers:\n      - name: init\n        image: stefanprodan/podinfo:6.7.0\n        command: ["sh","-c","exit 1"]\n')),
      attente=110,
      nettoyage=lambda: restaurer("helios-init"))
# ---------------------------------------------------------------- 6
essai("6. PENDING", "POD_PENDING_INSUFFICIENT_RESOURCES",
      lambda: appliquer(dep("helios-pending", extra_ctr='        resources: {requests: {cpu: "100"}}\n')),
      nettoyage=lambda: restaurer("helios-pending"))
# ---------------------------------------------------------------- 7
essai("7. SERVICE UNAVAILABLE", "SERVICE_UNAVAILABLE",
      lambda: appliquer("apiVersion: v1\nkind: Service\nmetadata: {name: helios-service, namespace: pfe}\n"
                        "spec:\n  selector: {app: aucun-pod-ne-correspond}\n  ports: [{port: 9898}]\n"),
      nettoyage=lambda: rm("kubectl delete svc helios-service -n pfe --ignore-not-found >/dev/null"))
# ---------------------------------------------------------------- 8
def charge_cpu():
    p = rm("kubectl get pod -n pfe -l app=helios-cpu --no-headers | head -1 | awk '{print $1}'")
    rm("kubectl exec -n pfe %s -- sh -c 'nohup sh -c \"for i in 1 2 3; do yes >/dev/null & done; sleep 300; killall yes\" >/dev/null 2>&1 & echo ok'" % p)
essai("8. CPU STRESS", "CPU_STRESS", charge_cpu, attente=210,
      nettoyage=lambda: rm("for p in $(kubectl get pod -n pfe -l app=helios-cpu -o name); do kubectl exec -n pfe ${p#pod/} -- sh -c 'killall yes 2>/dev/null; true'; done 2>/dev/null"))
# ---------------------------------------------------------------- 9
essai("9. AUTOSCALING", "AUTOSCALING",
      lambda: rm("kubectl patch hpa backend-hpa -n pfe -p '{\"spec\":{\"maxReplicas\":1}}'"),
      nettoyage=lambda: rm("kubectl patch hpa backend-hpa -n pfe -p '{\"spec\":{\"maxReplicas\":2}}' >/dev/null"))
# ---------------------------------------------------------------- 10
def erreurs5xx():
    p = rm("kubectl get pod -n pfe -l app=helios-http5xx --no-headers | head -1 | awk '{print $1}'")
    rm("kubectl exec -n pfe %s -- sh -c 'nohup sh -c \"for i in $(seq 1 400); do wget -qO- http://localhost:9898/status/500 >/dev/null 2>&1; done\" >/dev/null 2>&1 & echo ok'" % p)
essai("10. HIGH ERROR RATE", "HIGH_ERROR_RATE", erreurs5xx, attente=200)
# ---------------------------------------------------------------- 11
def lenteur():
    p = rm("kubectl get pod -n pfe -l app=helios-latency --no-headers | head -1 | awk '{print $1}'")
    rm("kubectl exec -n pfe %s -- sh -c 'nohup sh -c \"for i in $(seq 1 60); do wget -qO- --timeout=10 http://localhost:9898/delay/3 >/dev/null 2>&1; done\" >/dev/null 2>&1 & echo ok'" % p)
essai("11. LATENCE", "LATENCY_DEGRADATION", lenteur, attente=200)
# ---------------------------------------------------------------- 12
essai("12. DEPLOYMENT UNAVAILABLE", "DEPLOYMENT_UNAVAILABLE",
      lambda: appliquer(dep("helios-unavailable",
              extra_ctr='        readinessProbe:\n          httpGet: {path: /healthz, port: 9999}\n          periodSeconds: 5\n')),
      nettoyage=lambda: restaurer("helios-unavailable"))

sec("SYNTHESE")
for nom, att, obt, ok in RESULTATS:
    out("   %-28s attendu=%-34s obtenu=%-34s %s" % (nom[:28], att, obt, "OK" if ok else "ECHEC"))
out("\n   TOTAL : %d/%d" % (sum(1 for r in RESULTATS if r[3]), len(RESULTATS)))

sec("REMISE EN ETAT")
for i in range(20):
    time.sleep(15)
    ko = rm("kubectl get deploy -n pfe --no-headers | awk '{split($2,a,\"/\"); if(a[1]!=a[2]) print $1}' | tr '\\n' ' '")
    out("   apps KO : %s" % (ko or "AUCUNE"))
    if not ko: break
out("   detecteur final : " + detecter().get("type"))
m.close(); mon.close()
