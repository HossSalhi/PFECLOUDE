# -*- coding: utf-8 -*-
"""Manifeste des changements locaux du dépôt (LECTURE SEULE) : preuve que rien n'a été écrasé.

  python -I scripts/helios-ops/repo_manifest.py --out C:\\Users\\houss\\helios-state\\snapshots\\repo-manifest-AVANT.json
  python -I scripts/helios-ops/repo_manifest.py --compare C:\\Users\\houss\\helios-state\\snapshots\\repo-manifest-AVANT.json

Ce que fait l'outil : liste (git ls-files -m -o --exclude-standard) les fichiers MODIFIÉS et NON SUIVIS, calcule leur SHA-256 (taille et date
seulement pour les fichiers > 50 Mo : journaux de sondes qui grossissent en continu) et enregistre HEAD et le statut. `--compare` relit le
manifeste et classe chaque fichier : identique / modifié depuis / ajouté depuis / disparu depuis. Il n'écrit rien dans le dépôt, ne lance
aucun `git add/commit/reset/clean/stash`.
"""
import argparse
import datetime as dt
import hashlib
import json
import os
import subprocess
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # pragma: no cover
    pass

BIG = 50 * 1024 * 1024


def git(*args, cwd):
    p = subprocess.run(["git", *args], cwd=cwd, capture_output=True, timeout=120)
    if p.returncode != 0:
        raise SystemExit("git %s : %s" % (" ".join(args), p.stderr.decode("utf-8", "replace")[:300]))
    return p.stdout


def repo_root():
    here = os.path.dirname(os.path.abspath(__file__))
    return git("rev-parse", "--show-toplevel", cwd=here).decode().strip()


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def entry(root, rel):
    full = os.path.join(root, rel)
    try:
        st = os.stat(full)
    except OSError:
        return None
    if os.path.isdir(full):
        return {"dir": True}
    e = {"size": st.st_size, "mtime": int(st.st_mtime)}
    if st.st_size <= BIG:
        try:
            e["sha256"] = sha256(full)
        except OSError:
            e["unreadable"] = True
    else:
        e["large_no_hash"] = True
    return e


def take(root):
    mod = [p for p in git("ls-files", "-m", "-z", cwd=root).decode("utf-8", "replace").split("\0") if p]
    unt = [p for p in git("ls-files", "-o", "--exclude-standard", "-z", cwd=root).decode("utf-8", "replace").split("\0") if p]
    entries = {}
    for rel in mod + unt:
        e = entry(root, rel)
        if e is not None:
            entries[rel] = e
    return {"created": dt.datetime.now().isoformat(timespec="seconds"), "head": git("rev-parse", "HEAD", cwd=root).decode().strip(),
            "branch": git("branch", "--show-current", cwd=root).decode().strip(), "tracked_modified": mod, "untracked": unt, "entries": entries}


def compare(old, new):
    out = {"identical": [], "changed": [], "added": [], "removed": [], "large_growing": []}
    oe, ne = old["entries"], new["entries"]
    for p, e in oe.items():
        n = ne.get(p)
        if n is None:
            out["removed"].append(p)
        elif e.get("large_no_hash") or n.get("large_no_hash"):
            (out["large_growing"] if (e.get("size"), e.get("mtime")) != (n.get("size"), n.get("mtime")) else out["identical"]).append(p)
        elif e.get("sha256") == n.get("sha256"):
            out["identical"].append(p)
        else:
            out["changed"].append(p)
    for p in ne:
        if p not in oe:
            out["added"].append(p)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out")
    ap.add_argument("--compare")
    ap.add_argument("--json", help="avec --compare : écrit aussi le résultat JSON")
    a = ap.parse_args()
    root = repo_root()
    cur = take(root)
    if a.out:
        os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
        with open(a.out, "w", encoding="utf-8") as fh:
            json.dump(cur, fh, ensure_ascii=False)
        print("manifeste écrit : %s | HEAD %s | %d fichiers modifiés, %d non suivis, %d entrées" % (
            a.out, cur["head"][:7], len(cur["tracked_modified"]), len(cur["untracked"]), len(cur["entries"])))
    if a.compare:
        with open(a.compare, encoding="utf-8") as fh:
            old = json.load(fh)
        r = compare(old, cur)
        print("HEAD avant %s / maintenant %s" % (old["head"][:7], cur["head"][:7]))
        print("identiques : %d | modifiés depuis : %d | ajoutés depuis : %d | disparus depuis : %d | journaux en croissance : %d" % (
            len(r["identical"]), len(r["changed"]), len(r["added"]), len(r["removed"]), len(r["large_growing"])))
        for k in ("changed", "removed"):
            for p in r[k]:
                print("  [%s] %s" % (k, p))
        if a.json:
            with open(a.json, "w", encoding="utf-8") as fh:
                json.dump(r, fh, ensure_ascii=False, indent=1)
    if not a.out and not a.compare:
        print("rien à faire : utiliser --out et/ou --compare")


if __name__ == "__main__":
    main()
