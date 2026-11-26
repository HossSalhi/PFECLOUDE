# -*- coding: utf-8 -*-
"""RÉCONCILIATION de la politique VM_DOWN entre TOUTES les couches — LECTURE SEULE.

Question posée : « le dépôt, le backend DÉPLOYÉ, le watchdog (script déployé ET en direct), le verrou d'armement, le script
d'activation, le pré-vol, le Bridge et l'hôte disent-ils exactement la même chose ? » Politique définitive attendue (consigne de
l'utilisateur du 6 nov. 2026, complétée le 17 nov. 2026 : `.153` devient AUTO) : `.155 .158 .162 .153` AUTO ; `.160` DETECT_ONLY ;
armement réel = exactement les QUATRE VM AUTO ; ancien agent :9999 absent.

  python infra/helios-sot/tools/policy_reconcile.py        code de sortie 0 = COHÉRENT, 1 = au moins un écart

Ce que l'outil lit : les fichiers du dépôt, le script du watchdog déployé, `GET /vm/state` (via un pod du backend, jeton jamais
affiché), la ConfigMap, le tag d'image et le CODE réellement présent dans l'image déployée (marqueurs de version dans les classes
`VmPowerPolicy` et `VmPowerWatch`), l'hôte (tâches, écouteurs 9099/9999) et le Bridge. Ce qu'il ne fait JAMAIS : écrire, démarrer,
arrêter, déployer.
"""
import importlib.util
import json
import os
import re
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # pragma: no cover
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
sys.path.insert(0, HERE)
import shadow_observe as so  # noqa: E402
import n3_preflight as n3  # noqa: E402

NAMES = ["k8s-worker1", "k8s-worker2", "k8s-lb2", "k8s-monitoring", "k8s-master"]
EXPECTED = {"k8s-worker1": "AUTO", "k8s-worker2": "AUTO", "k8s-lb2": "AUTO", "k8s-monitoring": "DETECT_ONLY", "k8s-master": "AUTO"}
# Armement réel attendu = les QUATRE VM AUTO. Tant que .153 n'est pas armée en production, l'outil le SIGNALE (écart « manquant ») :
# c'est le signal voulu pendant le déploiement du 17 nov. 2026 (watchdog hôte, image backend, puis armement par l'utilisateur).
ARMED = frozenset({"k8s-worker1", "k8s-worker2", "k8s-lb2", "k8s-master"})
LAYERS = [("yaml", "YAML"), ("java", "Java (dépôt)"), ("wd_repo", "watchdog (dépôt)"), ("wd_deployed", "watchdog (script déployé)"),
          ("wd_live", "watchdog (en direct)")]
# Marqueurs de la version « .160 en détection seule » dans les classes DÉPLOYÉES (absents de l'image 0.2.163-vm4auto).
POLICY_MARKER = b"overrideEntryForTest"
WATCH_MARKER = b"detectionOnlyReminder"
# Marqueur de la RÉVISION de politique attendue (constante POLICY_REVISION de VmPowerPolicy, présente dans le pool de constantes de la classe
# DÉPLOYÉE ; absente des images antérieures). 2026-11-17 : « .153 AUTO » (avant : DENY_RESTART) ; 2026-11-18 : cooldown de redémarrage 30 -> 5 min.
# Une image qui porte l'ancienne révision (« 2026-11-17-master-auto ») a donc encore un cooldown de 30 min : elle est signalée.
MASTER_MARKER = b"2026-11-18-cooldown-5min"


def image_digest_path(tag):
    """Fichier d'empreinte VÉRIFIÉE avant déploiement de l'image `pfe-backend:<tag>` : infra/helios-sot/IMAGE-<tag>.sha256."""
    return os.path.join(REPO, "infra", "helios-sot", "IMAGE-%s.sha256" % tag)


def expected_image_digest(tag, path=None):
    """Empreinte attendue de l'image `pfe-backend:<tag>` (ligne `contenu-<tag> = <sha256>` de SON fichier), ou None si le fichier ou la
    ligne est absent/illisible (= non établie = écart, fail-closed). 17 nov. 2026 : une image = un fichier. L'ancien fichier FIXE de la
    0.2.164 ne pouvait plus être atteint après le déploiement du 14 nov. (0.2.165-disk-e2e) ni après celui de `.153` (0.2.166-master153)."""
    if not tag:
        return None
    try:
        with open(path or image_digest_path(tag), encoding="utf-8") as fh:
            for ln in fh:
                m = re.match(r"contenu-%s\s*=\s*([0-9a-fA-F]{64})\b" % re.escape(tag), ln)
                if m:
                    return m.group(1).lower()
    except OSError:
        pass
    return None


def deviations(f):
    """Pure : liste des écarts par rapport à la politique définitive. `None`/absent = non établi = écart (fail-closed)."""
    out = []
    for key, label in LAYERS:
        layer = f.get(key) or {}
        for vm in NAMES:
            if layer.get(vm) != EXPECTED[vm]:
                out.append(f"{label} : {vm} = {layer.get(vm)} (attendu {EXPECTED[vm]})")
    actives = f.get("active_vms")
    if actives is None:
        out.append("ACTIVE_VMS illisible")
    elif set(actives) != ARMED:
        extra, missing = sorted(set(actives) - ARMED), sorted(ARMED - set(actives))
        out.append("ACTIVE_VMS non conforme" + (f" : en trop {extra}" if extra else "") + (f" ; manquant {missing}" if missing else "")
                   + (" — .160 ARMÉE : INTERDIT (détection seule)" if "k8s-monitoring" in set(actives) else ""))
    if f.get("enabled") != "true" or f.get("dryrun") != "false":
        out.append(f"armement : ENABLED={f.get('enabled')} DRYRUN={f.get('dryrun')} (attendu true / false)")
    bm = f.get("backend_markers")
    if not bm:
        out.append("code du backend déployé illisible")
    else:
        if not bm.get("policy_new"):
            out.append("backend déployé : VmPowerPolicy ancienne (.160 AUTO) — déployer l'image 0.2.164-vm3auto")
        if not bm.get("watch_new"):
            out.append("backend déployé : VmPowerWatch ancien (pas de flux DETECT_ONLY)")
        if not bm.get("master_auto"):
            out.append(f"backend déployé : VmPowerPolicy sans « .153 AUTO » ni cooldown de redémarrage de 5 min (révision {MASTER_MARKER.decode()} absente) "
                       "— déployer l'image « cooldown 5 min » (0.2.167-cooldown5)")
    exp, got, tag = f.get("image_digest_expected"), f.get("image_digest"), f.get("image_tag") or "?"
    if exp and got and exp != got:
        out.append(f"contenu de l'image déployée ≠ empreinte vérifiée de {tag} "
                   f"(déployée {got[:12]}…, attendue {exp[:12]}…) — le code qui tourne n'est pas celui qui a été contrôlé")
    elif not got:
        out.append("empreinte du contenu de l'image déployée non établie")
    elif not exp:
        out.append(f"aucune empreinte vérifiée pour l'image déployée {tag} : la calculer AVANT déploiement "
                   f"(image_digest.py --local <copie>\\target\\classes --write-expected {tag}) — fichier infra/helios-sot/IMAGE-{tag}.sha256")
    if set(f.get("apply_allowlist") or []) != ARMED:
        out.append(f"script d'activation : liste {sorted(f.get('apply_allowlist') or [])} (attendu {sorted(ARMED)})")
    if set(f.get("preflight_targets") or []) != ARMED:
        out.append(f"pré-vol : cibles {sorted(f.get('preflight_targets') or [])} (attendu {sorted(ARMED)})")
    br = f.get("bridge") or {}
    if br.get("sha") != so.TARGET_BRIDGE_SHA12 or br.get("jeton") != "0" or br.get("actif") != "active":
        out.append("Bridge non conforme : " + json.dumps(br))
    h = f.get("host") or {}
    if h.get("ecouteur_9099") != 1:
        out.append("le watchdog n'écoute pas sur :9099")
    if h.get("ecouteur_9999") != 0:
        out.append("un écouteur :9999 (ancien agent) répond")
    t = h.get("taches") or {}
    if t.get("HELIOS-Watchdog") != "Running":
        out.append(f"tâche HELIOS-Watchdog = {t.get('HELIOS-Watchdog')} (attendu Running)")
    for old in ("HELIOS-Watchdog-HTTP", "HELIOS-NodeAutoheal"):
        if t.get(old) not in ("Disabled", None):
            out.append(f"ancienne tâche {old} = {t.get(old)} (attendu Disabled)")
    if f.get("pods_ready") != 2:
        out.append(f"backend : {f.get('pods_ready')} pod(s) prêt(s) (attendu 2)")
    return out


def java_modes(path):
    with open(path, encoding="utf-8") as fh:
        src = fh.read()
    return {m.group(1): m.group(2) for m in re.finditer(
        r'new Entry\("([^"]+)",\s*"[^"]+",\s*Mode\.([A-Z_]+),', src)}


def watchdog_modes(path):
    with open(path, encoding="utf-8") as fh:
        s = fh.read()
    blk = s.split("# --- BEGIN VM_POLICY", 1)[1].split("# --- END VM_POLICY", 1)[0]
    return {m.group(1): m.group(2) for m in re.finditer(r'"([^"]+)":\s*\{"ip":\s*"[^"]+",\s*"mode":\s*"([A-Z_]+)"', blk)}


def apply_allowlist(path):
    with open(path, encoding="utf-8") as fh:
        bloc = fh.read().split("activate)", 1)[1].split("esac", 1)[0]
    m = re.search(r"^\s*((?:k8s-[a-z0-9]+\|)+k8s-[a-z0-9]+)\)\s*;;", bloc, re.M)
    return set(m.group(1).split("|")) if m else set()


def pod_class_bytes(pod, rel, errors):
    import subprocess
    try:
        p = subprocess.run(["kubectl", "exec", "-n", so.NS, pod, "--", "cat", "/app/classes/com/orange/pfe/backend/service/vmpower/" + rel],
                           capture_output=True, timeout=60)
        return p.stdout if p.returncode == 0 and p.stdout else None
    except Exception as ex:  # noqa: BLE001
        errors.append(f"{rel}: {type(ex).__name__}")
        return None


def collect(errors):
    f = {}
    f["yaml"] = {n: e.get("mode") for n, e in n3.policy_entries().items()}
    f["java"] = java_modes(os.path.join(REPO, "backend", "backend", "src", "main", "java", "com", "orange", "pfe", "backend", "service",
                                        "vmpower", "VmPowerPolicy.java"))
    f["wd_repo"] = watchdog_modes(os.path.join(REPO, "infra", "helios-sot", "watchdog", "helios-vm-watchdog.py"))
    f["wd_deployed"] = watchdog_modes(r"C:\Users\houss\helios-vm-watchdog.py")
    rc, out, err = so.kube("get", "pods", "-n", so.NS, "-o", "json")
    ready = []
    if rc == 0:
        for it in json.loads(out).get("items", []):
            if it["metadata"]["name"].startswith("backend-"):
                cs = (it["status"].get("containerStatuses") or [{}])[0]
                if cs.get("ready"):
                    ready.append(it["metadata"]["name"])
    f["pods_ready"] = len(ready)
    live = so.collect_vm_state(ready[0] if ready else None, errors)
    f["wd_live"] = {n: v.get("mode") for n, v in live.items()}
    cfg = so.collect_config(errors)
    acts = cfg.get("HELIOS_VMPOWER_ACTIVE_VMS")
    f["active_vms"] = None if acts is None else {x.strip() for x in acts.split(",") if x.strip()}
    f["enabled"], f["dryrun"] = cfg.get("HELIOS_VMPOWER_ENABLED"), cfg.get("HELIOS_VMPOWER_DRYRUN")
    if ready:
        pol = pod_class_bytes(ready[0], "VmPowerPolicy.class", errors)
        wat = pod_class_bytes(ready[0], "VmPowerWatch.class", errors)
        f["backend_markers"] = None if (pol is None or wat is None) else {
            "policy_new": POLICY_MARKER in pol, "watch_new": WATCH_MARKER in wat, "master_auto": MASTER_MARKER in pol}
    rc, out, _ = so.kube("get", "deploy", "backend", "-n", so.NS, "-o", "jsonpath={.spec.template.spec.containers[0].image}")
    f["image"] = out.strip()
    f["image_tag"] = f["image"].rsplit(":", 1)[1] if ":" in f["image"] else None
    f["image_digest_expected"] = expected_image_digest(f["image_tag"])
    try:
        spec = importlib.util.spec_from_file_location("image_digest", os.path.join(HERE, "image_digest.py"))
        idg = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(idg)
        f["image_digest"] = idg.digest_of(idg.pod_files(ready[0]))[0] if ready else None
    except SystemExit as ex:
        errors.append(f"empreinte d'image : {ex}")
        f["image_digest"] = None
    f["apply_allowlist"] = apply_allowlist(os.path.join(HERE, "apply_vm4_auto.sh"))
    f["preflight_targets"] = set(n3.TARGETS)
    f["bridge"] = so.collect_bridge(errors)
    f["host"] = so.collect_host(errors)
    return f


def main():
    errors = []
    f = collect(errors)
    print("=== RÉCONCILIATION DE LA POLITIQUE (lecture seule) ===")
    head = ("VM", "attendu") + tuple(label for _, label in LAYERS)
    rows = [(vm, EXPECTED[vm]) + tuple((f.get(k) or {}).get(vm) for k, _ in LAYERS) for vm in NAMES]
    w = [max(len(str(r[i])) for r in rows + [head]) for i in range(len(head))]
    fmt = "  ".join("{:<%d}" % x for x in w)
    print(fmt.format(*head))
    for r in rows:
        print(fmt.format(*[str(c) for c in r]))
    print(f"\narmement  : ENABLED={f.get('enabled')} DRYRUN={f.get('dryrun')} ACTIVE_VMS={sorted(f.get('active_vms') or [])}")
    print(f"image     : {f.get('image')} | marqueurs de code dans l'image : {json.dumps(f.get('backend_markers'))} | pods prêts : {f.get('pods_ready')}")
    print(f"contenu   : empreinte déployée {str(f.get('image_digest'))[:16]}… | attendue ({f.get('image_tag')}) {str(f.get('image_digest_expected'))[:16]}…")
    print(f"outils    : script d'activation={sorted(f.get('apply_allowlist') or [])} pré-vol={sorted(f.get('preflight_targets') or [])}")
    h = f.get("host") or {}
    print(f"hôte      : 9099={h.get('ecouteur_9099')} 9999={h.get('ecouteur_9999')} tâches={json.dumps(h.get('taches'))}")
    print(f"bridge    : {json.dumps(f.get('bridge'))}")
    dev = deviations(f)
    for e in errors:
        print(f"[ATTENTION] collecte incomplète : {e}")
    if dev:
        print(f"\nÉCARTS ({len(dev)}) :")
        for d in dev:
            print("  ✗", d)
        return 1
    print("\nCOHÉRENT : toutes les couches appliquent la politique définitive (.155 .158 .162 .153 AUTO ; .160 DETECT_ONLY).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
