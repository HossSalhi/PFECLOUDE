# -*- coding: utf-8 -*-
"""Tests de la protection des sauvegardes (infra/helios-sot/tools/protect_backups.py). Aucun cluster : bash local + faux kubectl.

Lancer : python -I scripts/helios-ops/test_protect_backups.py
"""
import copy
import gzip
import importlib.util
import json
import os
import re
import subprocess
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.abspath(os.path.join(HERE, "..", "..", "infra", "helios-sot", "tools", "protect_backups.py"))
spec = importlib.util.spec_from_file_location("protect_backups", TOOL)
pb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pb)

BASH = next((p for p in (r"C:\Program Files\Git\bin\bash.exe", r"C:\Program Files\Git\usr\bin\bash.exe", "/bin/bash", "/usr/bin/bash") if os.path.exists(p)), None)


def posix(path):
    m = re.match(r"^([A-Za-z]):[\\/](.*)$", path)
    return "/%s/%s" % (m.group(1).lower(), m.group(2).replace("\\", "/")) if m else path


def make_dump(d, name, valid=True, age_s=0, sidecar=True):
    p = os.path.join(d, name)
    if valid:
        with gzip.open(p, "wb") as fh:
            fh.write(b"-- PostgreSQL database dump\n" * 50)
    else:
        with open(p, "wb") as fh:
            fh.write(b"\x1f\x8b\x08\x00 tronque")
    t = time.time() - age_s
    os.utime(p, (t, t))
    if sidecar:
        with open(p + ".sha256", "w") as fh:
            fh.write("0" * 64 + "  " + name + "\n")
    return p


def populate(d, n, corrupt_newest=0):
    names = []
    for i in range(n):                      # i = 0 le plus ancien
        name = "pfe_db-20261001-%06d.sql.gz" % i
        age = (n - i) * 3600
        newest_rank = n - 1 - i             # 0 = le plus récent
        make_dump(d, name, valid=newest_rank >= corrupt_newest, age_s=age)
        names.append(name)
    return names


@unittest.skipIf(BASH is None, "bash introuvable")
class RotationSureTest(unittest.TestCase):
    def run_rotation(self, d, used, extra_env=None):
        env = dict(os.environ, BK_DIR=posix(d), BK_USED_OVERRIDE=str(used))
        env.update(extra_env or {})
        p = subprocess.run([BASH, "-c", "set -euo pipefail\n" + pb.ROTATION], capture_output=True, text=True, env=env, timeout=60, encoding="utf-8", errors="replace")
        return p.returncode, p.stdout + p.stderr

    def listing(self, d):
        return sorted(f for f in os.listdir(d) if f.startswith("pfe_db-") and f.endswith(".sql.gz"))

    def test_volume_peu_occupe_aucune_suppression_meme_avec_40_dumps(self):
        with tempfile.TemporaryDirectory() as d:
            populate(d, 40)
            rc, out = self.run_rotation(d, 10)
            self.assertEqual(0, rc, out)
            self.assertEqual(40, len(self.listing(d)))
            self.assertIn("aucune suppression", out)

    def test_alerte_a_80_pourcent_sans_suppression(self):
        with tempfile.TemporaryDirectory() as d:
            populate(d, 40)
            rc, out = self.run_rotation(d, 85)
            self.assertEqual(0, rc, out)
            self.assertIn("ALERTE", out)
            self.assertEqual(40, len(self.listing(d)))

    def test_volume_plein_ne_supprime_que_les_plus_anciens_au_dela_de_30_et_respecte_les_dumps_proteges(self):
        with tempfile.TemporaryDirectory() as d:
            names = populate(d, 40)
            with open(os.path.join(d, ".protected"), "w") as fh:
                fh.write(names[0] + "\n" + names[3] + "\n")
            rc, out = self.run_rotation(d, 95)
            self.assertEqual(0, rc, out)
            reste = self.listing(d)
            # 10 plus anciens candidats ; 2 protégés ⇒ 8 supprimés, 32 restent
            self.assertEqual(32, len(reste), out)
            for n in names[10:]:
                self.assertIn(n, reste)                        # les 30 plus récents sont intacts
            self.assertIn(names[0], reste)
            self.assertIn(names[3], reste)
            self.assertNotIn(names[1], reste)
            self.assertFalse(os.path.exists(os.path.join(d, names[1] + ".sha256")))
            self.assertTrue(os.path.exists(os.path.join(d, names[0] + ".sha256")))   # empreinte du dump protégé conservée
            self.assertIn("protege", out)

    def test_rotation_annulee_si_les_dumps_recents_ne_sont_pas_sains(self):
        with tempfile.TemporaryDirectory() as d:
            populate(d, 40, corrupt_newest=1)                   # le plus récent est tronqué : 4/5 seulement
            rc, out = self.run_rotation(d, 95)
            self.assertEqual(0, rc, out)
            self.assertEqual(40, len(self.listing(d)))
            self.assertIn("ANNULEE", out)

    def test_pas_de_suppression_sous_le_seuil_de_30_dumps_meme_volume_plein(self):
        with tempfile.TemporaryDirectory() as d:
            populate(d, 25)
            rc, out = self.run_rotation(d, 99)
            self.assertEqual(0, rc, out)
            self.assertEqual(25, len(self.listing(d)))

    def test_dossier_vide_et_valeur_d_occupation_invalide_ne_cassent_pas_le_job(self):
        with tempfile.TemporaryDirectory() as d:
            rc, out = self.run_rotation(d, "")
            self.assertEqual(0, rc, out)
            populate(d, 8)
            rc, out = self.run_rotation(d, "n/a")
            self.assertEqual(0, rc, out)
            self.assertEqual(8, len(self.listing(d)))

    def test_partial_et_protected_ne_sont_jamais_touches(self):
        with tempfile.TemporaryDirectory() as d:
            populate(d, 40)
            make_dump(d, "pfe_db-20260901-000000.sql.gz.partial", age_s=10 ** 6, sidecar=False)
            open(os.path.join(d, ".protected"), "w").close()
            self.run_rotation(d, 99)
            self.assertTrue(os.path.exists(os.path.join(d, "pfe_db-20260901-000000.sql.gz.partial")))
            self.assertTrue(os.path.exists(os.path.join(d, ".protected")))

    def test_scripts_complets_syntaxe_bash(self):
        for name, script in (("A", pb.script_a()), ("B", pb.script_b())):
            p = subprocess.run([BASH, "-n", "-c", script], capture_output=True, text=True, timeout=30)
            self.assertEqual(0, p.returncode, "%s : %s" % (name, p.stderr))


class StructureDesScriptsTest(unittest.TestCase):
    def test_ordre_ecriture_controle_rotation(self):
        for name, s in (("A", pb.script_a()), ("B", pb.script_b())):
            i_partial = s.index('T=$F.partial')
            i_gzip = s.index('gzip -t "$T"')
            i_size = s.index('-gt 10240')
            i_mv = s.index('mv "$T" "$F"')
            i_sha = s.index('sha256sum "pfe_db-$TS.sql.gz"')
            i_rot = s.index("ROTATION SÛRE")
            self.assertLess(i_partial, i_gzip, name)
            self.assertLess(i_gzip, i_size, name)
            self.assertLess(i_size, i_mv, name)
            self.assertLess(i_mv, i_sha, name)
            self.assertLess(i_sha, i_rot, "la rotation ne peut précéder la validation du dump du jour (%s)" % name)
            self.assertIn("set -euo pipefail", s)

    def test_aucune_suppression_aveugle(self):
        for name, s in (("A", pb.script_a()), ("B", pb.script_b())):
            self.assertNotIn("xargs -r rm", s, name)
            self.assertNotIn("tail -n +8", s, name)
            rms = re.findall(r"\brm\b[^\n]*", s)
            self.assertEqual(2, len(rms), "%s : %r" % (name, rms))
            self.assertTrue(any(r.startswith('rm -f "$T"') for r in rms))           # le .partial du jour seulement
            self.assertTrue(any('rm -f -- "$f" "$f.sha256"' in r for r in rms))     # dans la rotation gardée
            i_rm = s.index('rm -f -- "$f"')
            self.assertLess(s.index('[ "$USED" -ge "$HIGH_PCT" ]'), i_rm)
            self.assertLess(s.index('.protected'), i_rm)
            self.assertLess(s.index('gzip -t "$f"'), i_rm)

    def test_seuils_par_defaut(self):
        self.assertIn('KEEP_MIN="${KEEP_MIN:-30}"', pb.ROTATION)
        self.assertIn('HIGH_PCT="${HIGH_PCT:-90}"', pb.ROTATION)
        self.assertIn('WARN_PCT="${WARN_PCT:-80}"', pb.ROTATION)
        self.assertIn('GOOD_NEWER_MIN="${GOOD_NEWER_MIN:-5}"', pb.ROTATION)


class FauxKubectl:
    def __init__(self):
        base = {"metadata": {"name": ""}, "spec": {"jobTemplate": {"spec": {"template": {"spec": {"containers": [
            {"command": ["/bin/bash", "-c"], "args": ["ANCIEN SCRIPT"]}]}}}}}}
        self.objs = {}
        for n in pb.CRONJOBS:
            o = copy.deepcopy(base)
            o["metadata"]["name"] = n
            o["spec"]["jobTemplate"]["spec"]["template"]["spec"]["containers"][0]["args"][0] = "ANCIEN SCRIPT " + n
            self.objs[n] = o
        self.writes = []

    def __call__(self, *a, **k):
        if a[0] == "get" and a[1] == "cronjob":
            return 0, json.dumps(self.objs[a[2]]), ""
        if a[0] == "patch":
            patch = json.loads(a[a.index("-p") + 1])
            self.writes.append((a[2], patch))
            assert patch[0]["op"] == "test"                                   # précondition vérifiée avant de remplacer
            self.objs[a[2]]["spec"]["jobTemplate"]["spec"]["template"]["spec"]["containers"][0]["args"][0] = patch[1]["value"]
            return 0, "patched", ""
        return 1, "", "commande inattendue %r" % (a,)


class ApplyRollbackTest(unittest.TestCase):
    def test_apply_exporte_remplace_relit_puis_rollback_restaure(self):
        fk = FauxKubectl()
        with tempfile.TemporaryDirectory() as d:
            old_dir, pb.SNAP_DIR = pb.SNAP_DIR, d
            try:
                lines = []
                saved = pb.run_apply(say=lines.append, kc=fk, now="TEST")
                self.assertEqual(2, len(fk.writes))
                for n in pb.CRONJOBS:
                    self.assertEqual(pb.new_script(n), pb.get_script(fk.objs[n]))
                    self.assertTrue(os.path.isfile(saved[n]))
                for n, path in saved.items():
                    pb.run_rollback(path, say=lines.append, kc=fk)
                    self.assertEqual("ANCIEN SCRIPT " + n, pb.get_script(fk.objs[n]))
            finally:
                pb.SNAP_DIR = old_dir

    def test_le_plan_n_ecrit_rien(self):
        fk = FauxKubectl()
        old = pb.kubectl
        pb.kubectl = fk
        try:
            lines = []
            pb.run_plan(say=lines.append)
            self.assertEqual([], fk.writes)
            self.assertTrue(any("script modifié" in l for l in lines))
            self.assertTrue(any("DRY-RUN terminé" in l for l in lines))
        finally:
            pb.kubectl = old

    def test_un_cronjob_inattendu_est_refuse_au_rollback(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "x.json")
            with open(p, "w") as fh:
                json.dump({"metadata": {"name": "autre"}, "spec": {"jobTemplate": {"spec": {"template": {"spec": {"containers": [{"args": ["x"]}]}}}}}}, fh)
            with self.assertRaises(SystemExit):
                pb.run_rollback(p, kc=FauxKubectl())


if __name__ == "__main__":
    unittest.main()
