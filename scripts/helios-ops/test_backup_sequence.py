# -*- coding: utf-8 -*-
"""Tests de l'orchestrateur de sauvegardes (ordre, arrêt au premier échec, jamais deux ensemble) avec un environnement SIMULÉ.
Lancer : python -I scripts/helios-ops/test_backup_sequence.py"""
import datetime as dt
import importlib.util
import os
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.abspath(os.path.join(HERE, "..", "..", "infra", "helios-sot", "tools", "backup_sequence.py"))
spec = importlib.util.spec_from_file_location("backup_sequence", TOOL)
bs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bs)


class FakeEnv:
    def __init__(self, problems=None, fail_job=None, never_complete=None, no_new_file=None, tiny=None, probe_rc=None, no_ok_line=None,
                 active_before=None):
        self.t = 1_800_000_000.0
        self.events = []
        self.problems = problems or []
        self.fail_job, self.never_complete, self.no_new_file = fail_job, never_complete, no_new_file
        self.tiny, self.probe_rc, self.no_ok_line, self.active_before = tiny, probe_rc or {}, no_ok_line, active_before
        self.polls = {}
        self.files = {"A": {"name": "pfe_db-old-A.sql.gz", "size": 271000, "mtime": int(self.t) - 3600, "sha256": "a" * 64},
                      "B": {"name": "pfe_db-old-B.sql.gz", "size": 271000, "mtime": int(self.t) - 3600, "sha256": "b" * 64}}
        self.active = set()

    def now(self): return self.t
    def sleep(self, s): self.t += s
    def stamp(self): return f"{int(self.t) % 1000000:06d}"
    def precheck(self): self.events.append("precheck"); return list(self.problems)

    def active_backup_jobs(self):
        return list(self.active) + ([self.active_before] if self.active_before and not self.events.count("create:A") else [])

    def latest_dump(self, x): return self.files[x]

    def create_job(self, cj, name):
        x = "A" if cj == "postgres-backup" else "B"
        self.events.append("create:" + x)
        self.active.add(name)
        self.polls[name] = 0
        if self.no_new_file != x:
            size = 100 if self.tiny == x else 272000
            self.files[x] = {"name": f"pfe_db-new-{x}.sql.gz", "size": size, "mtime": int(self.t) + 2, "sha256": x * 64}

    def job_json(self, name):
        x = "A" if "-a-" in name else "B"
        self.polls[name] += 1
        if self.fail_job == x:
            return {"status": {"conditions": [{"type": "Failed", "status": "True"}]}}
        if self.never_complete == x:
            return {"status": {}}
        if self.polls[name] >= 2:
            self.active.discard(name)
            self.events.append("complete:" + x)
            return {"status": {"conditions": [{"type": "Complete", "status": "True"}]}}
        return {"status": {"active": 1}}

    def job_logs(self, name):
        x = "A" if "-a-" in name else "B"
        return "rien" if self.no_ok_line == x else ("[backup] OK: 264K" if x == "A" else "[backup-b] OK: 264K")

    def probe(self, x, name):
        self.events.append("probe:" + x)
        return self.probe_rc.get(x, 0), "RÉSULTAT : RESTAURABLE"


def run(env, apply=True):
    out = []
    rc = bs.run_sequence(env, apply, say=lambda *a: out.append(" ".join(str(x) for x in a)))
    return rc, "\n".join(out)


class SequenceTest(unittest.TestCase):
    def test_nominal_ordre_strict_creation_attente_preuve_puis_la_suivante(self):
        env = FakeEnv()
        rc, out = run(env)
        self.assertEqual(rc, 0, out)
        self.assertEqual(env.events, ["precheck", "create:A", "complete:A", "probe:A", "create:B", "complete:B", "probe:B"])
        self.assertIn("restaurables", out)

    def test_dry_run_ne_cree_aucun_job(self):
        env = FakeEnv()
        rc, out = run(env, apply=False)
        self.assertEqual(rc, 0)
        self.assertEqual(env.events, ["precheck"])
        self.assertIn("aucun Job créé", out)

    def test_precontrole_en_echec_ne_cree_rien(self):
        env = FakeEnv(problems=["espace libre insuffisant"])
        rc, out = run(env)
        self.assertEqual(rc, 1)
        self.assertEqual(env.events, ["precheck"])

    def test_si_a_echoue_b_n_est_jamais_lancee(self):
        env = FakeEnv(fail_job="A")
        rc, out = run(env)
        self.assertEqual(rc, 1)
        self.assertNotIn("create:B", env.events)
        self.assertIn("ÉCHOUÉ", out)

    def test_delai_depasse_arrete_la_sequence(self):
        env = FakeEnv(never_complete="A")
        rc, out = run(env)
        self.assertEqual(rc, 1)
        self.assertNotIn("create:B", env.events)
        self.assertIn("n'est pas Complete", out)

    def test_preuve_de_restauration_en_echec_arrete_avant_la_suivante(self):
        env = FakeEnv(probe_rc={"A": 1})
        rc, out = run(env)
        self.assertEqual(rc, 1)
        self.assertEqual(env.events, ["precheck", "create:A", "complete:A", "probe:A"])
        self.assertIn("n'est PAS prouvée", out)

    def test_aucun_nouveau_fichier_ou_taille_anormale_ou_pas_de_ligne_ok(self):
        for kw, frag in (({"no_new_file": "A"}, "aucun NOUVEAU fichier"), ({"tiny": "A"}, "taille anormale"), ({"no_ok_line": "A"}, "ligne « OK »")):
            env = FakeEnv(**kw)
            rc, out = run(env)
            self.assertEqual(rc, 1, kw)
            self.assertIn(frag, out)
            self.assertNotIn("create:B", env.events)

    def test_un_job_deja_actif_empeche_toute_creation(self):
        env = FakeEnv(active_before="postgres-backup-123")
        rc, out = run(env)
        self.assertEqual(rc, 1)
        self.assertNotIn("create:A", env.events)
        self.assertIn("jamais deux sauvegardes en même temps", out)


class PuresTest(unittest.TestCase):
    def test_etat_du_job(self):
        self.assertEqual(bs.job_state({"status": {"conditions": [{"type": "Complete", "status": "True"}]}}), "complete")
        self.assertEqual(bs.job_state({"status": {"conditions": [{"type": "Failed", "status": "True"}]}}), "failed")
        self.assertEqual(bs.job_state({"status": {"conditions": [{"type": "Complete", "status": "False"}]}}), "running")
        self.assertEqual(bs.job_state({}), "running")

    def test_taille_plausible(self):
        self.assertTrue(bs.size_plausible(271000, 268000))
        self.assertFalse(bs.size_plausible(5000, 268000))
        self.assertFalse(bs.size_plausible(700000, 268000))
        self.assertFalse(bs.size_plausible(None, 268000))
        self.assertTrue(bs.size_plausible(20000, None))
        self.assertFalse(bs.size_plausible(10000, None))

    def test_fenetre_des_sauvegardes_planifiees(self):
        d = lambda h, m: dt.datetime(2026, 11, 6, h, m, tzinfo=dt.timezone.utc)
        self.assertTrue(bs.in_scheduled_window(d(2, 5)))
        self.assertTrue(bs.in_scheduled_window(d(2, 38)))
        self.assertFalse(bs.in_scheduled_window(d(16, 5)))
        self.assertFalse(bs.in_scheduled_window(d(2, 45)))

    def test_ligne_ok_du_journal(self):
        self.assertTrue(bs.has_ok_line("[backup] OK: 264K"))
        self.assertTrue(bs.has_ok_line("x\n[backup-b] OK: 1M"))
        self.assertFalse(bs.has_ok_line("[backup] demarrage"))
        self.assertFalse(bs.has_ok_line(None))


class SourceTest(unittest.TestCase):
    def test_le_code_ne_supprime_rien_et_ne_cree_que_des_jobs(self):
        with open(TOOL, encoding="utf-8") as fh:
            code = fh.read().split('"""', 2)[2]
        for interdit in ('"delete"', "rm -rf", "kubectl delete", '"patch"', '"apply"', '"scale"'):
            self.assertNotIn(interdit, code)
        self.assertEqual(code.count('"create", "job"'), 1)


if __name__ == "__main__":
    unittest.main()
