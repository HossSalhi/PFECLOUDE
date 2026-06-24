#!/usr/bin/env python3
"""Attend que les 14 apps demo soient SAINES, puis injecte une panne REPARABLE par app
-> HELIOS detecte le type DISTINCT ET le resout (rollback / rollout restart / delete networkpolicy).

Injection PAR LOTS (staggered) : Zeebe (.161, mono-noeud) sature au-dela de ~6-8 workflows
simultanes (partitions failed -> action PENDING jamais executee -> incident bloque IN_PROGRESS).
On injecte 3-4 pannes, on laisse HELIOS les resoudre (~BATCH_WAIT s), puis le lot suivant.
"""
import subprocess, time, sys
NS = "pfe"
BATCH_WAIT = int(sys.argv[1]) if len(sys.argv) > 1 else 170
def k(*a, t=60):
    try: return subprocess.run(["kubectl","-n",NS,*a], capture_output=True, text=True, timeout=t).stdout.strip()
    except Exception as e: return f"ERR:{e}"
def kf(stdin, *a, t=60):
    try: return subprocess.run(["kubectl","-n",NS,*a], input=stdin, capture_output=True, text=True, timeout=t).stdout.strip()
    except Exception as e: return f"ERR:{e}"

APPS = ["helios-crashloop","helios-imagepull","helios-oom","helios-cpu","helios-service","helios-network",
        "helios-dns","helios-disk","helios-http5xx","helios-latency","helios-pending","helios-init",
        "helios-config","helios-unavailable","helios-db","helios-hpa"]
print("[1] attente apps SAINES...")
for _ in range(60):
    ready = [a for a in APPS if k("get","deploy",a,"-o","jsonpath={.status.readyReplicas}")=="1"]
    print(f"   pretes: {len(ready)}/{len(APPS)}")
    if len(ready) == len(APPS): break
    time.sleep(8)

def f_crashloop():
    return k("patch","deploy","helios-crashloop","--type=json","-p",
        '[{"op":"add","path":"/spec/template/spec/containers/0/command","value":["sh","-c","echo boot; exit 1"]}]')
def f_imagepull():  # registre injoignable -> connection refused instantane -> ImagePullBackOff au 1er poll
    return k("set","image","deploy/helios-imagepull","app=127.0.0.1:1/nope:helios")
def f_oom():
    # podinfo reste RUNNING (readiness OK -> pod Ready) pendant que tail /dev/zero remplit la RAM ->
    # conteneur OOMKilled (137) -> MEMORY_STRESS. Si tail remplacait podinfo, le pod serait NotReady
    # AVANT l'OOM et serait mal classe DEPLOYMENT_UNAVAILABLE.
    return k("patch","deploy","helios-oom","--type=json","-p",
        '[{"op":"add","path":"/spec/template/spec/containers/0/command","value":["sh","-c","./podinfo & tail /dev/zero"]}]')
def f_cpu():
    cpod = k("get","pod","-l","app=helios-cpu","-o","jsonpath={.items[0].metadata.name}")
    return cpod+" "+k("exec",cpod,"--","sh","-c",
        "nohup sh -c 'yes >/dev/null 2>&1 & p=$!; sleep 300; kill $p' >/dev/null 2>&1 & echo hog")
def f_service():
    return kf("apiVersion: networking.k8s.io/v1\nkind: NetworkPolicy\nmetadata: {name: helios-demo-service, namespace: pfe}\nspec: {podSelector: {matchLabels: {app: helios-service}}, policyTypes: [Ingress]}\n","apply","-f","-")
def f_network():
    return kf("apiVersion: networking.k8s.io/v1\nkind: NetworkPolicy\nmetadata: {name: helios-demo-network, namespace: pfe}\nspec: {podSelector: {matchLabels: {app: helios-network}}, policyTypes: [Ingress, Egress]}\n","apply","-f","-")
def f_dns():
    return kf("apiVersion: networking.k8s.io/v1\nkind: NetworkPolicy\nmetadata: {name: helios-demo-dns, namespace: pfe}\nspec:\n  podSelector: {matchLabels: {app: helios-dns}}\n  policyTypes: [Egress]\n  egress:\n    - to: []\n      ports: [{protocol: TCP, port: 80}, {protocol: TCP, port: 443}, {protocol: TCP, port: 9898}]\n","apply","-f","-")
def f_disk():
    dpod = k("get","pod","-l","app=helios-disk","-o","jsonpath={.items[0].metadata.name}")
    return dpod+" "+k("exec",dpod,"--","sh","-c","dd if=/dev/zero of=/data/fill bs=1M count=120 2>/dev/null; echo filled", t=60)
def f_http5xx():
    return k("patch","deploy","helios-http5xx","--type=json","-p",
        '[{"op":"add","path":"/spec/template/spec/containers/0/command","value":["./podinfo","--random-error=true"]}]')
def f_latency():
    return k("patch","deploy","helios-latency","--type=json","-p",
        '[{"op":"add","path":"/spec/template/spec/containers/0/command","value":["./podinfo","--random-delay=true"]}]')
def f_pending():
    return k("patch","deploy","helios-pending","--type=json","-p",
        '[{"op":"replace","path":"/spec/template/spec/containers/0/resources","value":{"requests":{"cpu":"2","memory":"32Mi"},"limits":{"cpu":"2","memory":"96Mi"}}}]')
def f_init():
    return k("patch","deploy","helios-init","--type=json","-p",
        '[{"op":"replace","path":"/spec/template/spec/initContainers/0/command","value":["sh","-c","echo init; exit 1"]}]')
def f_config():
    return k("patch","deploy","helios-config","--type=json","-p",
        '[{"op":"replace","path":"/spec/template/spec/containers/0/envFrom","value":[{"configMapRef":{"name":"helios-missing-cm"}}]}]')
def f_unavailable():
    return k("patch","deploy","helios-unavailable","--type=json","-p",
        '[{"op":"replace","path":"/spec/template/spec/containers/0/readinessProbe/httpGet/path","value":"/nonexistent-404-helios"}]')
def f_db():  # egress autorise DNS(53)+web(80/443) mais PAS la base (5432) -> DATABASE_CONNECTIVITY
    return kf("apiVersion: networking.k8s.io/v1\nkind: NetworkPolicy\nmetadata: {name: helios-demo-db, namespace: pfe}\nspec:\n  podSelector: {matchLabels: {app: helios-db}}\n  policyTypes: [Egress]\n  egress:\n    - to: []\n      ports: [{protocol: UDP, port: 53}, {protocol: TCP, port: 53}, {protocol: TCP, port: 80}, {protocol: TCP, port: 443}]\n","apply","-f","-")
def f_hpa():  # hog CPU sur l'app autoscale -> HELIOS scale up (AUTOSCALING).
    # 300s : la detection CPU (ML + cAdvisor) exige ~2-3 min de charge soutenue (cf helios-cpu).
    # Le SCALE n'arrete pas le hog ; la resolution se confirme via le scale (app saine 2/2) puis
    # la charge retombe a la fin du hog. Reconcile = filet de securite si la verif Camunda deborde.
    hpod = k("get","pod","-l","app=helios-hpa","-o","jsonpath={.items[0].metadata.name}")
    return hpod+" "+k("exec",hpod,"--","sh","-c",
        "nohup sh -c 'yes >/dev/null 2>&1 & p=$!; sleep 300; kill $p' >/dev/null 2>&1 & echo hog")

# Lots <=4 pour ne pas saturer Zeebe (mono-noeud). On melange rollback/restart/policy par lot.
BATCHES = [
    [("crashloop",f_crashloop),("imagepull",f_imagepull),("oom",f_oom),("cpu",f_cpu)],
    [("service",f_service),("network",f_network),("dns",f_dns),("disk",f_disk)],
    [("http5xx",f_http5xx),("latency",f_latency),("pending",f_pending)],
    [("init",f_init),("config",f_config),("unavailable",f_unavailable),("db",f_db),("hpa",f_hpa)],
]
print(f"[2] injection PAR LOTS (BATCH_WAIT={BATCH_WAIT}s entre lots)...")
for bi, batch in enumerate(BATCHES, 1):
    print(f" -- lot {bi}/{len(BATCHES)} --")
    for name, fn in batch:
        print(f"   {name}: {fn()}")
    if bi < len(BATCHES):
        print(f"   ...attente {BATCH_WAIT}s (HELIOS resout le lot avant le suivant)")
        time.sleep(BATCH_WAIT)
print("DONE - HELIOS detecte + resout chaque type distinct (14), par lots")
