"""Test intégré ML + LLM : injecte chaos réel, appelle /predict, affiche résultat complet."""
import subprocess, time, json
import httpx

MASTER = "ssh -o StrictHostKeyChecking=no kube@192.168.148.153"
AI_SERVICE = "http://192.168.148.161:8000"

SCENARIOS = [
    {
        "name": "NO_INCIDENT",
        "namespace": "pfe", "app": "backend",
        "inject": None, "cleanup": None,
        "wait": 15,
    },
    {
        "name": "ERROR_RATE_5XX_SPIKE",
        "namespace": "pfe", "app": "backend",
        "inject": "for i in $(seq 1 200); do curl -s -m 3 -o /dev/null http://192.168.148.153:30880/api/nonexistent -H 'Authorization: Bearer fake' & done; wait",
        "cleanup": "sleep 10",
        "wait": 30,
    },
    {
        "name": "INGRESS_BACKEND_DOWN",
        "namespace": "pfe", "app": "backend",
        "inject": "kubectl -n pfe scale deployment/backend --replicas=0",
        "cleanup": "kubectl -n pfe scale deployment/backend --replicas=1; sleep 30",
        "wait": 40,
    },
    {
        "name": "POD_CRASH_LOOP",
        "namespace": "pfe", "app": "demo-app",
        "inject": "for i in 1 2 3; do P=$(kubectl -n pfe get pod -l app=demo-app -o jsonpath='{.items[0].metadata.name}'); kubectl -n pfe exec $P -- kill 1 2>/dev/null || true; sleep 18; done",
        "cleanup": "sleep 30",
        "wait": 35,
    },
    {
        "name": "DB_CONNECTION_REFUSED",
        "namespace": "pfe", "app": "backend",
        "inject": "kubectl -n pfe delete pod -l app=postgres --grace-period=0 --force 2>/dev/null; for i in $(seq 1 40); do curl -s -m 3 -o /dev/null http://192.168.148.153:30880/api/incidents -H 'Authorization: Bearer fake' & done; wait",
        "cleanup": "sleep 50",
        "wait": 50,
    },
]

def run_cmd(cmd):
    if not cmd:
        return
    full = f'{MASTER} "{cmd}"'
    print(f"  → {cmd[:90]}", flush=True)
    subprocess.run(full, shell=True, timeout=150, capture_output=True)

def predict(namespace, app):
    try:
        r = httpx.post(
            f"{AI_SERVICE}/predict",
            json={"namespace": namespace, "app": app, "window_minutes": 5},
            timeout=120,
        )
        return r.json()
    except Exception as e:
        return {"error": str(e)}

print("=" * 70)
print("TEST INTÉGRÉ ML + LLM — CAS RÉELS")
print("=" * 70)

results = []
for s in SCENARIOS:
    print(f"\n{'='*70}")
    print(f"SCÉNARIO ATTENDU : {s['name']}", flush=True)

    if s["inject"]:
        print("  [INJECT chaos]...", flush=True)
        run_cmd(s["inject"])
        print(f"  [WAIT {s['wait']}s Prometheus]...", flush=True)
        time.sleep(s["wait"])
    else:
        time.sleep(s["wait"])

    print("  [PREDICT — ML + LLM]...", flush=True)
    resp = predict(s["namespace"], s["app"])

    if "error" in resp:
        print(f"  ERREUR: {resp['error']}", flush=True)
        results.append({"expected": s["name"], "detected": "ERROR", "match": "✗"})
        continue

    incident      = resp.get("incident", False)
    incident_type = resp.get("incident_type", "NO_INCIDENT")
    severity      = resp.get("severity", "?")
    action        = resp.get("recommended_action", "?")
    confidence    = resp.get("confidence", 0)
    explanation   = resp.get("decision_explanation", "")[:100]
    exec_mode     = resp.get("execution_mode", "?")

    match = "✓" if incident_type == s["name"] else "✗"

    print(f"\n  ┌─ RÉSULTAT ML + LLM")
    print(f"  │  incident      : {incident}")
    print(f"  │  type détecté  : {incident_type}")
    print(f"  │  severity      : {severity}")
    print(f"  │  action        : {action}")
    print(f"  │  confidence    : {confidence:.2%}")
    print(f"  │  mode exéc.    : {exec_mode}")
    print(f"  │  explication   : {explanation}...")
    print(f"  └─ {match} attendu={s['name']} / détecté={incident_type}")

    results.append({
        "expected": s["name"],
        "detected": incident_type,
        "confidence": confidence,
        "action": action,
        "match": match,
    })

    if s["cleanup"]:
        print("  [CLEANUP]...", flush=True)
        run_cmd(s["cleanup"])

print(f"\n{'='*70}")
print("RÉSUMÉ FINAL — PIPELINE ML + LLM COMPLET")
print(f"{'='*70}")
print(f"{'Attendu':35s} {'Détecté':35s} {'Conf':6s} {'Match'}")
print("-" * 70)
ok = 0
for r in results:
    if r["match"] == "✓":
        ok += 1
    conf = f"{r.get('confidence',0):.0%}" if "confidence" in r else "-"
    print(f"{r['expected']:35s} {r['detected']:35s} {conf:6s} {r['match']}")

print(f"\nAccuracy pipeline complet : {ok}/{len(results)} = {100*ok//len(results)}%")
