# -*- coding: utf-8 -*-
"""EMPREINTE DE CONTENU d'une image backend (indépendante du build Jib) — LECTURE SEULE.

L'empreinte = SHA-256 de la liste triée des lignes « <sha256 du fichier>  <classes|resources>/<chemin> » de TOUTES les classes et
ressources de l'application. Elle se calcule à l'identique :
  - sur un dossier `target/classes` local (la copie « minimale » vérifiée AVANT déploiement),
  - dans un pod du backend en marche (`/app/classes` + `/app/resources`), APRÈS déploiement.
Si les deux empreintes sont égales, le code qui tourne est EXACTEMENT celui qui a été vérifié.

  python image_digest.py --local C:\\Users\\houss\\helios-minimal-build-0.2.164\\target\\classes
  python image_digest.py --pod backend-74966df5f8-pxmvz            (le premier pod prêt si omis : --pod auto)
  python image_digest.py --pod auto --expect <empreinte>           code de sortie 0 si égale, 1 sinon
  python image_digest.py --local … --diff-pod auto                 liste les fichiers qui diffèrent de l'image en marche
  python image_digest.py --local C:\\Users\\houss\\helios-minimal-build-0.2.166-master153\\target\\classes --write-expected 0.2.166-master153
                                                                   crée infra/helios-sot/IMAGE-0.2.166-master153.sha256 (jamais écrasé)
Lecture seule (`kubectl exec … find/sha256sum`), SAUF `--write-expected TAG` : il exige --local (l'empreinte attendue vient du BUILD vérifié,
jamais du pod en marche : le contrôle serait circulaire) et crée UN fichier du dépôt, lu ensuite par policy_reconcile.py.
"""
import argparse
import datetime
import hashlib
import os
import re
import subprocess
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # pragma: no cover
    pass

NS = "pfe"
HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
TAG_RE = re.compile(r"^[0-9][0-9A-Za-z._-]{0,63}$")
POD_SCRIPT = ("cd /app/classes && find . -type f -exec sha256sum {} + | sed 's#  \\./#  classes/#'; "
              "cd /app/resources 2>/dev/null && find . -type f -exec sha256sum {} + | sed 's#  \\./#  resources/#'")


def digest_of(files):
    """Pure : `files` = {chemin: sha256} -> (empreinte globale, nombre de fichiers)."""
    lines = "".join(f"{h}  {p}\n" for p, h in sorted(files.items()))
    return hashlib.sha256(lines.encode("utf-8")).hexdigest(), len(files)


def parse_sums(text):
    out = {}
    for ln in text.splitlines():
        h, _, p = ln.rstrip("\n").partition("  ")
        if p and len(h) == 64:
            out[p] = h
    return out


def local_files(root):
    res = {}
    for d, _, fs in os.walk(root):
        for f in fs:
            full = os.path.join(d, f)
            rel = os.path.relpath(full, root).replace("\\", "/")
            with open(full, "rb") as fh:
                res[("classes/" if f.endswith(".class") else "resources/") + rel] = hashlib.sha256(fh.read()).hexdigest()
    return res


def pod_name(arg):
    if arg != "auto":
        return arg
    p = subprocess.run(["kubectl", "get", "pods", "-n", NS, "-l", "app=backend", "-o",
                        "jsonpath={range .items[?(@.status.containerStatuses[0].ready==true)]}{.metadata.name}{\"\\n\"}{end}"],
                       capture_output=True, text=True, encoding="utf-8", timeout=60)
    names = p.stdout.split()
    if not names:
        raise SystemExit("aucun pod backend prêt")
    return names[0]


def pod_files(pod):
    p = subprocess.run(["kubectl", "exec", "-i", "-n", NS, pod, "--", "sh", "-s"], input=POD_SCRIPT, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=180)
    if p.returncode != 0:
        raise SystemExit("lecture du pod impossible : " + p.stderr.strip()[:200])
    return parse_sums(p.stdout)


def expected_file_text(tag, digest, n, source, date):
    """Pure : contenu du fichier infra/helios-sot/IMAGE-<tag>.sha256 (format lu par policy_reconcile.expected_image_digest)."""
    return (
        f"# Empreinte de CONTENU attendue de l'image backend pfe-backend:{tag} (établie le {date} depuis le build local ci-dessous).\n"
        f"# Calcul : python infra/helios-sot/tools/image_digest.py --local {source}\n"
        "# Contrôle après déploiement : python infra/helios-sot/tools/image_digest.py --pod auto --expect <empreinte ci-dessous>   (code 0 = conforme)\n"
        f"contenu-{tag} = {digest}   # {n} fichiers (classes + ressources)\n")


def write_expected_file(tag, digest, n, source, repo=None, date=None):
    """Crée infra/helios-sot/IMAGE-<tag>.sha256. Retourne (ok, chemin, message). Ne remplace JAMAIS un fichier existant."""
    if not TAG_RE.match(tag or ""):
        return False, None, f"étiquette invalide : {tag!r}"
    path = os.path.join(repo or REPO, "infra", "helios-sot", "IMAGE-%s.sha256" % tag)
    if os.path.exists(path):
        return False, path, "existe déjà : je ne l'écrase pas (une empreinte attendue ne se réécrit pas)"
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "x", encoding="utf-8", newline="\n") as fh:
        fh.write(expected_file_text(tag, digest, n, source, date or datetime.date.today().isoformat()))
    return True, path, "créé"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--local")
    ap.add_argument("--pod")
    ap.add_argument("--expect")
    ap.add_argument("--diff-pod", metavar="POD|auto")
    ap.add_argument("--write-expected", metavar="TAG", help="avec --local : enregistre l'empreinte du BUILD dans infra/helios-sot/IMAGE-<TAG>.sha256")
    a = ap.parse_args(argv)
    if bool(a.local) == bool(a.pod):
        ap.error("exactement un de --local / --pod")
    if a.write_expected and not a.local:
        ap.error("--write-expected exige --local : l'empreinte attendue vient du BUILD vérifié, jamais du pod en marche (contrôle circulaire)")
    files = local_files(a.local) if a.local else pod_files(pod_name(a.pod))
    d, n = digest_of(files)
    print(f"empreinte du contenu : {d}\nfichiers : {n} (source : {'dossier ' + a.local if a.local else 'pod ' + pod_name(a.pod)})")
    if a.write_expected:
        ok, path, msg = write_expected_file(a.write_expected, d, n, a.local)
        print(f"fichier d'empreinte attendue : {path} : {msg}")
        if not ok:
            return 2
    if a.diff_pod:
        other = pod_files(pod_name(a.diff_pod))
        diff = sorted(p for p in set(files) & set(other) if files[p] != other[p])
        print(f"écart avec l'image en marche : {len(diff)} fichier(s) différent(s), {len(set(files) - set(other))} en plus, {len(set(other) - set(files))} en moins")
        for p in diff:
            print("  ≠", p)
    if a.expect:
        ok = d == a.expect.strip().lower()
        print("CONFORME à l'empreinte attendue" if ok else "NON CONFORME : l'empreinte attendue est " + a.expect)
        return 0 if ok else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
