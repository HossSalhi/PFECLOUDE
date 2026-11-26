# -*- coding: utf-8 -*-
"""DÉSARMEMENT CIBLÉ : retire des noms de HELIOS_VMPOWER_ACTIVE_VMS. DRY-RUN PAR DÉFAUT.

Pourquoi cet outil plutôt que `apply_vm4_auto.sh --stage activate` : `activate` RÉÉCRIT aussi ENABLED=true et DRYRUN=false, donc il
peut RÉARMER ce qui ne l'est plus. Celui-ci ne fait que RETIRER :
  - il n'AJOUTE jamais un nom, ne touche NI à ENABLED NI à DRYRUN NI à aucune autre clé (74 clés dans la ConfigMap, une seule change) ;
  - il refuse un nom hors de la table fermée de la politique, un retrait qui ne change rien (code 0, rien n'est fait) et un résultat
    VIDE (désarmement total) sauf `--allow-empty` ;
  - sans `--apply` il n'écrit RIEN : il affiche l'objet modifié, le diff exact, le correctif JSON envoyé, l'effet sur le backend
    (redémarrage progressif), l'interruption attendue et le retour arrière.

  python restrict_active_vms.py --remove k8s-monitoring            dry-run : diff exact, rien n'est écrit
  python restrict_active_vms.py --remove k8s-monitoring --apply    sauvegarde, correctif de la ConfigMap, redémarrage progressif du
                                                                    backend, vérification DANS chaque pod que la nouvelle valeur est lue

Une seule clé est modifiée (`merge patch`, jamais `apply`) ; le redémarrage est NÉCESSAIRE : le backend lit ses variables
d'environnement au démarrage (envFrom ConfigMap).
Retour arrière : l'ancienne valeur est enregistrée dans `<dossier de sauvegarde>/active-vms-<horodatage>.json` ; la commande exacte
(patch + redémarrage) est affichée à la fin.
"""
import argparse
import datetime as dt
import difflib
import json
import os
import subprocess
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # pragma: no cover
    pass

KEY = "HELIOS_VMPOWER_ACTIVE_VMS"
SIBLINGS = ("HELIOS_VMPOWER_ENABLED", "HELIOS_VMPOWER_DRYRUN")
KNOWN = ("k8s-worker2", "k8s-worker1", "k8s-lb2", "k8s-monitoring", "k8s-master")   # table fermée de la politique
DEFAULT_BACKUP_DIR = r"C:\Users\houss\helios-state\backups"


def kubectl_cmd():
    raw = os.environ.get("KUBECTL_CMD")
    return json.loads(raw) if raw else ["kubectl"]


def kube(*args, timeout=120):
    p = subprocess.run(kubectl_cmd() + list(args), capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
    return p.returncode, p.stdout, p.stderr


def parse_names(raw):
    return [x.strip() for x in (raw or "").split(",") if x.strip()]


def plan(current_raw, remove, allow_empty=False):
    """Pure. Retourne (nouvelle_valeur, erreurs). N'ajoute JAMAIS un nom ; conserve l'ordre des noms restants."""
    errors = []
    cur = parse_names(current_raw)
    bad = [n for n in remove if n not in KNOWN]
    if bad:
        errors.append(f"nom(s) hors de la table fermée de la politique : {bad} (noms exacts : {list(KNOWN)})")
    if not remove:
        errors.append("aucun nom à retirer")
    new = [n for n in cur if n not in remove]
    if not new and not allow_empty and not errors:
        errors.append("le résultat serait VIDE (désarmement total) : ajouter --allow-empty si c'est voulu")
    assert set(new) <= set(cur), "invariant : cet outil n'ajoute jamais un nom"
    return ",".join(new), errors


def unified(cm_name, cm, new_value, ns):
    def render(value):
        lines = ["data:"]
        for k in SIBLINGS:
            lines.append(f'  {k}: "{cm["data"].get(k)}"')
        lines.append(f'  {KEY}: "{value}"')
        lines.append(f"  # … {len(cm['data']) - 3} autres clés : inchangées")
        return lines
    return "\n".join(difflib.unified_diff(render(cm["data"].get(KEY)), render(new_value),
                                          f"configmap/{cm_name} ({ns}) - actuel", f"configmap/{cm_name} ({ns}) - après", lineterm="", n=3))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--remove", required=True, help="noms exacts à retirer de ACTIVE_VMS, séparés par des virgules")
    ap.add_argument("--apply", action="store_true", help="exécuter (sinon : dry-run, rien n'est écrit)")
    ap.add_argument("--allow-empty", action="store_true", help="autoriser un résultat vide (désarmement total)")
    ap.add_argument("--namespace", default="pfe")
    ap.add_argument("--configmap", default="backend-config")
    ap.add_argument("--deployment", default="backend")
    ap.add_argument("--backup-dir", default=DEFAULT_BACKUP_DIR)
    a = ap.parse_args(argv)
    ns, cmn, dep = a.namespace, a.configmap, a.deployment

    rc, out, err = kube("get", "configmap", cmn, "-n", ns, "-o", "json")
    if rc != 0:
        print("REFUS : ConfigMap illisible :", err.strip()[:200])
        return 1
    cm = json.loads(out)
    current = cm["data"].get(KEY)
    if current is None:
        print(f"REFUS : la clé {KEY} est absente de la ConfigMap")
        return 1
    remove = parse_names(a.remove)
    new, errors = plan(current, remove, a.allow_empty)
    print(f"=== DÉSARMEMENT CIBLÉ ({'APPLICATION' if a.apply else 'DRY-RUN : rien n est écrit'}) ===")
    print(f"machine : cluster Kubernetes (namespace {ns}) | nature : {'MODIFIANTE' if a.apply else 'LECTURE SEULE'}")
    print(f"valeur actuelle  {KEY} = {current!r}")
    if errors:
        for e in errors:
            print("REFUS :", e)
        return 1
    if new == ",".join(parse_names(current)):
        print("Rien à faire : aucun des noms demandés n'est armé. Aucune modification.")
        return 0
    rc, out, _ = kube("get", "deployment", dep, "-n", ns, "-o", "json")
    d = json.loads(out) if rc == 0 else {}
    strat = (d.get("spec") or {}).get("strategy") or {}
    ru = strat.get("rollingUpdate") or {}
    replicas = (d.get("spec") or {}).get("replicas")

    print("\nOBJETS MODIFIÉS")
    print(f"  1. configmap/{cmn} ({ns}) : UNE clé ({KEY}) ; {len(cm['data']) - 1} autres clés inchangées dont "
          + ", ".join(f"{k}={cm['data'].get(k)!r}" for k in SIBLINGS))
    print(f"  2. deployment/{dep} ({ns}) : redémarrage progressif (annotation kubectl.kubernetes.io/restartedAt sur le modèle de pod ⇒ nouveau ReplicaSet)")
    print("  AUCUN autre objet : ni Secret, ni Service, ni watchdog, ni VM, ni base de données.")
    print("\nDIFF EXACT")
    print(unified(cmn, cm, new, ns))
    print("\nCORRECTIF ENVOYÉ (merge patch, jamais apply)")
    print("  " + json.dumps({"data": {KEY: new}}))
    print("\nEFFET SUR LE BACKEND")
    print(f"  le backend lit ACTIVE_VMS au démarrage (envFrom ConfigMap) ⇒ redémarrage NÉCESSAIRE ; stratégie {strat.get('type')} "
          f"maxSurge={ru.get('maxSurge')} maxUnavailable={ru.get('maxUnavailable')}, {replicas} réplicas : les pods sont remplacés UN À LA FOIS,")
    print("  l'autre continue de servir (PodDisruptionBudget minAvailable=1) ⇒ pas de coupure de l'API, capacité réduite ≈ 2 à 3 min au total.")
    print("  VmPowerWatch : pause jusqu'à ≈ 60 s si le leader est remplacé (bail non libéré à la mort du pod), puis re-confirmation d'un arrêt")
    print("  (≥ 2 observations espacées d'au moins 30 s) avant toute décision : aucune décision n'est perdue, aucun démarrage n'est déclenché.")
    print("\nRISQUE : faible (redémarrage progressif d'un déploiement stateless). RETOUR ARRIÈRE : réécrire l'ancienne valeur puis redémarrer :")
    print(f"  kubectl -n {ns} patch configmap {cmn} --type merge -p '{json.dumps({'data': {KEY: current}})}'")
    print(f"  kubectl -n {ns} rollout restart deployment/{dep}")
    if not a.apply:
        print("\nDRY-RUN terminé : rien n'a été écrit. Ajouter --apply pour exécuter.")
        return 0

    os.makedirs(a.backup_dir, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    bak = os.path.join(a.backup_dir, f"active-vms-{stamp}.json")
    with open(bak, "w", encoding="utf-8") as fh:
        json.dump({"configmap": f"{ns}/{cmn}", "resourceVersion": cm["metadata"].get("resourceVersion"),
                   "keys": {k: cm["data"].get(k) for k in (*SIBLINGS, KEY)}, "ts": stamp}, fh, ensure_ascii=False, indent=2)
    print(f"\nsauvegarde de l'ancienne valeur : {bak}")
    rc, out, err = kube("patch", "configmap", cmn, "-n", ns, "--type", "merge", "-p", json.dumps({"data": {KEY: new}}))
    if rc != 0:
        print("ÉCHEC du correctif :", err.strip()[:300])
        return 1
    rc, out, _ = kube("get", "configmap", cmn, "-n", ns, "-o", "json")
    after = json.loads(out)["data"] if rc == 0 else {}
    if after.get(KEY) != new or any(after.get(k) != cm["data"].get(k) for k in SIBLINGS):
        print("ÉCHEC de la vérification : la ConfigMap ne contient pas la valeur attendue — AUCUN redémarrage lancé.", {k: after.get(k) for k in (*SIBLINGS, KEY)})
        return 1
    print(f"ConfigMap corrigée et relue : {KEY} = {after[KEY]!r} (ENABLED/DRYRUN inchangés)")
    rc, out, err = kube("rollout", "restart", f"deployment/{dep}", "-n", ns)
    if rc != 0:
        print("ÉCHEC du redémarrage :", err.strip()[:300])
        return 1
    rc, out, err = kube("rollout", "status", f"deployment/{dep}", "-n", ns, "--timeout=300s", timeout=330)
    print(out.strip()[-300:] or err.strip()[-300:])
    if rc != 0:
        print("ATTENTION : le redémarrage n'est pas terminé dans le délai — vérifier ; retour arrière ci-dessus.")
        return 1
    rc, out, _ = kube("get", "pods", "-n", ns, "-l", f"app={dep}", "-o", "jsonpath={range .items[*]}{.metadata.name}{\"\\n\"}{end}")
    ok = True
    for pod in out.split():
        rc, val, err = kube("exec", "-n", ns, pod, "--", "printenv", KEY)
        got = val.strip() if rc == 0 else None
        print(f"  pod {pod} : {KEY} = {got!r}" + ("  ✓" if got == new else "  ✗"))
        ok = ok and got == new
    print("\nRÉSULTAT :", "désarmement effectif dans tous les pods." if ok else "ÉCART — un pod ne lit pas la nouvelle valeur : retour arrière conseillé.")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
