#!/usr/bin/env python3
"""Declenche UN incident precis ; HELIOS le detecte et le resout TOUT SEUL (autonome).

Usage (PowerShell ou autre) :
    python scripts/inject_one.py <type>
    python scripts/inject_one.py --list

Chaque appel est autonome : (1) remet l'app cible SAINE, (2) attend qu'elle soit prete,
(3) injecte la panne. Puis HELIOS detecte (~30s) et resout via Camunda -> kubectl, SANS
aucune intervention. Tu n'as qu'UNE commande a taper.

Pre-requis : kubectl configure sur le contexte du cluster (teste : `kubectl -n pfe get deploy`).
"""
import subprocess, sys, time

NS = "pfe"
GOOD_IMG = "stefanprodan/podinfo:6.7.0"

# type -> (app, incident HELIOS, action de resolution)
TYPES = {
    "crashloop":   ("helios-crashloop",   "POD_CRASH_LOOP",                     "ROLLBACK_DEPLOYMENT (rollout undo)"),
    "imagepull":   ("helios-imagepull",   "POD_IMAGE_PULL_BACKOFF",             "ROLLBACK_DEPLOYMENT (rollout undo)"),
    "oom":         ("helios-oom",         "MEMORY_STRESS",                      "ROLLBACK_DEPLOYMENT (rollout undo)"),
    "cpu":         ("helios-cpu",         "CPU_STRESS",                         "FORCE_RECREATE (rollout restart)"),
    "service":     ("helios-service",     "SERVICE_UNAVAILABLE",                "REMOVE_NETWORK_POLICY"),
    "network":     ("helios-network",     "NETWORK_PARTITION",                  "REMOVE_NETWORK_POLICY"),
    "dns":         ("helios-dns",         "DNS_FAILURE",                        "REMOVE_NETWORK_POLICY"),
    "disk":        ("helios-disk",        "NODE_DISK_PRESSURE",                 "DELETE_CRASHLOOPING_PODS (delete pods)"),
    "http5xx":     ("helios-http5xx",     "HIGH_ERROR_RATE",                    "ROLLBACK_DEPLOYMENT (rollout undo)"),
    "latency":     ("helios-latency",     "LATENCY_DEGRADATION",                "ROLLBACK_DEPLOYMENT (rollout undo)"),
    "pending":     ("helios-pending",     "POD_PENDING_INSUFFICIENT_RESOURCES", "ROLLBACK_DEPLOYMENT (rollout undo)"),
    "init":        ("helios-init",        "POD_INIT_CONTAINER_FAILURE",         "ROLLBACK_DEPLOYMENT (rollout undo)"),
    "config":      ("helios-config",      "POD_CONFIG_ERROR",                   "ROLLBACK_DEPLOYMENT (rollout undo)"),
    "unavailable": ("helios-unavailable", "DEPLOYMENT_UNAVAILABLE",             "ROLLBACK_DEPLOYMENT (rollout undo)"),
    "db":          ("helios-db",          "DATABASE_CONNECTIVITY",              "REMOVE_NETWORK_POLICY"),
    "hpa":         ("helios-hpa",         "AUTOSCALING",                        "SCALE_DEPLOYMENT (+1 replica)"),
}
NETPOL = {"service": "helios-demo-service", "network": "helios-demo-network",
          "dns": "helios-demo-dns", "db": "helios-demo-db"}
HOG = "nohup sh -c 'yes >/dev/null 2>&1 & p=$!; sleep 300; kill $p' >/dev/null 2>&1 & echo hog"


def k(*a, t=60):
    try:
        return subprocess.run(["kubectl", "-n", NS, *a], capture_output=True, text=True, timeout=t).stdout.strip()
    except Exception as e:
        return f"ERR:{e}"


def kf(stdin, *a, t=60):
    try:
        return subprocess.run(["kubectl", "-n", NS, *a], input=stdin, capture_output=True, text=True, timeout=t).stdout.strip()
    except Exception as e:
        return f"ERR:{e}"


def patch(app, p):
    return k("patch", "deploy", app, "--type=json", "-p", p)


def wait_ready(app, tries=20):
    for _ in range(tries):
        if k("get", "deploy", app, "-o", "jsonpath={.status.readyReplicas}") == "1":
            return True
        time.sleep(4)
    return False


def heal(t, app):
    """Remet UNIQUEMENT l'app cible a l'etat sain (revert de la panne precedente)."""
    if t in NETPOL:
        k("delete", "networkpolicy", NETPOL[t], "--ignore-not-found")
    if t in ("crashloop", "oom", "http5xx", "latency"):
        k("patch", "deploy", app, "--type=json", "-p",
          '[{"op":"remove","path":"/spec/template/spec/containers/0/command"}]')
    elif t == "imagepull":
        k("set", "image", f"deploy/{app}", f"app={GOOD_IMG}")
    elif t == "pending":
        patch(app, '[{"op":"replace","path":"/spec/template/spec/containers/0/resources","value":{"requests":{"cpu":"10m","memory":"32Mi"},"limits":{"cpu":"200m","memory":"96Mi"}}}]')
    elif t == "init":
        patch(app, '[{"op":"replace","path":"/spec/template/spec/initContainers/0/command","value":["sh","-c","exit 0"]}]')
    elif t == "config":
        patch(app, '[{"op":"replace","path":"/spec/template/spec/containers/0/envFrom","value":[{"configMapRef":{"name":"helios-config-cm"}}]}]')
    elif t == "unavailable":
        patch(app, '[{"op":"replace","path":"/spec/template/spec/containers/0/readinessProbe/httpGet/path","value":"/healthz"}]')
    elif t in ("cpu", "disk", "hpa"):
        if t == "hpa":
            k("scale", f"deploy/{app}", "--replicas=1")
        k("rollout", "restart", f"deploy/{app}")


def inject(t, app):
    if t == "crashloop":
        return patch(app, '[{"op":"add","path":"/spec/template/spec/containers/0/command","value":["sh","-c","echo boot; exit 1"]}]')
    if t == "imagepull":
        return k("set", "image", f"deploy/{app}", "app=127.0.0.1:1/nope:helios")
    if t == "oom":
        # Keep podinfo RUNNING (so readiness passes -> pod stays Ready) while `tail /dev/zero` fills RAM
        # -> the container gets OOMKilled (exit 137) -> MEMORY_STRESS. If tail replaced podinfo, the pod
        # would be NotReady (no app) BEFORE the OOM and get mislabeled DEPLOYMENT_UNAVAILABLE.
        return patch(app, '[{"op":"add","path":"/spec/template/spec/containers/0/command","value":["sh","-c","./podinfo & tail /dev/zero"]}]')
    if t == "cpu":
        pod = k("get", "pod", "-l", f"app={app}", "-o", "jsonpath={.items[0].metadata.name}")
        return pod + " " + k("exec", pod, "--", "sh", "-c", HOG)
    if t == "service":
        return kf("apiVersion: networking.k8s.io/v1\nkind: NetworkPolicy\nmetadata: {name: helios-demo-service, namespace: pfe}\nspec: {podSelector: {matchLabels: {app: helios-service}}, policyTypes: [Ingress]}\n", "apply", "-f", "-")
    if t == "network":
        return kf("apiVersion: networking.k8s.io/v1\nkind: NetworkPolicy\nmetadata: {name: helios-demo-network, namespace: pfe}\nspec: {podSelector: {matchLabels: {app: helios-network}}, policyTypes: [Ingress, Egress]}\n", "apply", "-f", "-")
    if t == "dns":
        return kf("apiVersion: networking.k8s.io/v1\nkind: NetworkPolicy\nmetadata: {name: helios-demo-dns, namespace: pfe}\nspec:\n  podSelector: {matchLabels: {app: helios-dns}}\n  policyTypes: [Egress]\n  egress:\n    - to: []\n      ports: [{protocol: TCP, port: 80}, {protocol: TCP, port: 443}, {protocol: TCP, port: 9898}]\n", "apply", "-f", "-")
    if t == "disk":
        pod = k("get", "pod", "-l", f"app={app}", "-o", "jsonpath={.items[0].metadata.name}")
        return pod + " " + k("exec", pod, "--", "sh", "-c", "dd if=/dev/zero of=/data/fill bs=1M count=120 2>/dev/null; echo filled")
    if t == "http5xx":
        return patch(app, '[{"op":"add","path":"/spec/template/spec/containers/0/command","value":["./podinfo","--random-error=true"]}]')
    if t == "latency":
        return patch(app, '[{"op":"add","path":"/spec/template/spec/containers/0/command","value":["./podinfo","--random-delay=true"]}]')
    if t == "pending":
        return patch(app, '[{"op":"replace","path":"/spec/template/spec/containers/0/resources","value":{"requests":{"cpu":"2","memory":"32Mi"},"limits":{"cpu":"2","memory":"96Mi"}}}]')
    if t == "init":
        return patch(app, '[{"op":"replace","path":"/spec/template/spec/initContainers/0/command","value":["sh","-c","echo init; exit 1"]}]')
    if t == "config":
        return patch(app, '[{"op":"replace","path":"/spec/template/spec/containers/0/envFrom","value":[{"configMapRef":{"name":"helios-missing-cm"}}]}]')
    if t == "unavailable":
        return patch(app, '[{"op":"replace","path":"/spec/template/spec/containers/0/readinessProbe/httpGet/path","value":"/nonexistent-404-helios"}]')
    if t == "db":
        return kf("apiVersion: networking.k8s.io/v1\nkind: NetworkPolicy\nmetadata: {name: helios-demo-db, namespace: pfe}\nspec:\n  podSelector: {matchLabels: {app: helios-db}}\n  policyTypes: [Egress]\n  egress:\n    - to: []\n      ports: [{protocol: UDP, port: 53}, {protocol: TCP, port: 53}, {protocol: TCP, port: 80}, {protocol: TCP, port: 443}]\n", "apply", "-f", "-")
    if t == "hpa":
        pod = k("get", "pod", "-l", f"app={app}", "-o", "jsonpath={.items[0].metadata.name}")
        return pod + " " + k("exec", pod, "--", "sh", "-c", HOG)
    return "ERR: type inconnu"


def list_types():
    print("Types (16) :")
    for tname, (app, inc, fix) in TYPES.items():
        print(f"  {tname:<12} -> {inc:<34} | HELIOS: {fix}")


def main():
    arg = sys.argv[1] if len(sys.argv) > 1 else ""
    if arg in ("", "-h", "--help"):
        print("Usage: python scripts/inject_one.py <type>")
        list_types()
        sys.exit(1)
    if arg in ("--list", "-l"):
        list_types()
        sys.exit(0)
    if arg not in TYPES:
        print(f"Type inconnu : '{arg}'")
        list_types()
        sys.exit(1)

    app, inc, fix = TYPES[arg]
    print(f">> [{arg}] cible={app} | incident attendu={inc} | resolution HELIOS={fix}")
    print("   [1/3] remise a l'etat sain de l'app cible...")
    heal(arg, app)
    print("   [2/3] attente que l'app soit prete...")
    if not wait_ready(app):
        print(f"   (avertissement : {app} pas 'ready' a temps, on injecte quand meme)")
    print("   [3/3] injection de la panne...")
    print("        ", inject(arg, app))
    print(f">> OK. HELIOS va detecter (~30s) puis resoudre {inc} TOUT SEUL via Camunda -> kubectl.")
    print("   Suivre l'UI HELIOS, ou : kubectl -n pfe get incidents 2>/dev/null")


if __name__ == "__main__":
    main()
