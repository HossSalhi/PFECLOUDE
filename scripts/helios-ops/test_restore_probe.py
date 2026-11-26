# -*- coding: utf-8 -*-
"""Tests de la preuve de restaurabilité (fonctions pures : aucun cluster, aucun nœud). Lancer : python -I scripts/helios-ops/test_restore_probe.py"""
import importlib.util
import os
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.abspath(os.path.join(HERE, "..", "..", "infra", "helios-sot", "tools", "restore_probe.py"))
spec = importlib.util.spec_from_file_location("restore_probe", TOOL)
rp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rp)

DUMP = """--
-- PostgreSQL database dump
--
-- Dumped from database version 16.10
-- Dumped by pg_dump version 16.10

SET statement_timeout = 0;
CREATE TABLE public.incident (
    id bigint NOT NULL,
    title character varying(255)
);
CREATE TABLE public.leader_lease (
    lease_name character varying(64) NOT NULL
);
CREATE TABLE public.vide (
    id integer
);

COPY public.incident (id, title) FROM stdin;
1\tPremier
2\tSecond
3\tTroisieme
\\.

COPY public.leader_lease (lease_name) FROM stdin;
aiops-scheduler
\\.

--
-- PostgreSQL database dump complete
--
"""


class AnalyseTest(unittest.TestCase):
    def test_compte_les_lignes_de_chaque_bloc_copy_et_les_tables(self):
        a = rp.analyze_dump(DUMP)
        self.assertEqual(a["tables_created"], ["public.incident", "public.leader_lease", "public.vide"])
        self.assertEqual(a["copy_rows"], {"public.incident": 3, "public.leader_lease": 1})
        self.assertTrue(a["footer_ok"])
        self.assertFalse(a["unterminated_copy"])
        self.assertEqual(a["pg_dump_version"], "16.10")

    def test_dump_tronque_est_detecte(self):
        a = rp.analyze_dump(DUMP[:DUMP.index("Troisieme")])
        self.assertFalse(a["footer_ok"])
        self.assertTrue(a["unterminated_copy"])

    def test_une_ligne_de_donnees_qui_ressemble_a_du_sql_n_est_pas_comptee_comme_table(self):
        sql = "COPY public.t (c) FROM stdin;\nCREATE TABLE public.faux (\n\\.\n"
        a = rp.analyze_dump(sql)
        self.assertEqual(a["tables_created"], [])
        self.assertEqual(a["copy_rows"], {"public.t": 1})


class ComparaisonTest(unittest.TestCase):
    def test_comptages_identiques_aucun_ecart_tables_vides_incluses(self):
        a = rp.analyze_dump(DUMP)
        self.assertEqual(rp.compare(a["copy_rows"], {"incident": 3, "leader_lease": 1, "vide": 0}), [])

    def test_ligne_manquante_ou_en_trop_est_un_ecart(self):
        a = rp.analyze_dump(DUMP)
        p = rp.compare(a["copy_rows"], {"incident": 2, "leader_lease": 1, "vide": 0})
        self.assertEqual(len(p), 1)
        self.assertIn("incident", p[0])
        self.assertEqual(len(rp.compare(a["copy_rows"], {"incident": 3, "leader_lease": 1, "vide": 5})), 1)

    def test_table_du_dump_absente_de_la_base_restauree(self):
        a = rp.analyze_dump(DUMP)
        p = rp.compare(a["copy_rows"], {"incident": 3})
        self.assertTrue(any("ABSENTE" in x and "leader_lease" in x for x in p))

    def test_lecture_des_comptages(self):
        self.assertEqual(rp.parse_counts("incident|3\nleader_lease|1\nbruit sans compte\nx|abc\n"), {"incident": 3, "leader_lease": 1})


def good_facts():
    a = rp.analyze_dump(DUMP)
    return {"sha_ok": True, "gzip_ok": True, "analysis": a, "restore_rc": 0, "restored_counts": {"incident": 3, "leader_lease": 1, "vide": 0},
            "count_problems": rp.compare(a["copy_rows"], {"incident": 3, "leader_lease": 1, "vide": 0})}


class VerdictTest(unittest.TestCase):
    def test_cas_nominal_est_restaurable(self):
        self.assertEqual(rp.verdict(good_facts()), [])

    def test_chaque_defaillance_est_un_echec_distinct(self):
        for mutate, fragment in (
            (lambda f: f.update(sha_ok=False), "somme de contrôle"),
            (lambda f: f.update(gzip_ok=False), "gzip"),
            (lambda f: f["analysis"].update(footer_ok=False), "pied de page"),
            (lambda f: f["analysis"].update(unterminated_copy=True), "COPY non terminé"),
            (lambda f: f.update(restore_rc=3, restore_err="ERROR: relation exists"), "restauration a échoué"),
            (lambda f: f.update(count_problems=["incident : restaurée 2 ligne(s), le dump en contient 3"]), "incident"),
            (lambda f: f.update(restored_counts={"incident": 3, "leader_lease": 1}), "ensemble des tables"),
            (lambda f: f.update(restored_counts={}), "aucun comptage"),
        ):
            f = good_facts()
            mutate(f)
            self.assertTrue(any(fragment in w for w in rp.verdict(f)), (fragment, rp.verdict(f)))

    def test_dump_sans_table_est_refuse(self):
        f = good_facts()
        f["analysis"] = rp.analyze_dump("-- vide\n-- PostgreSQL database dump complete\n")
        self.assertTrue(any("aucune table" in w for w in rp.verdict(f)))


class GardeFousSourceTest(unittest.TestCase):
    """L'outil ne doit pouvoir écrire que sous /run et ne supprimer que son propre dossier."""

    def test_le_code_ne_touche_jamais_le_volume_de_donnees(self):
        with open(TOOL, encoding="utf-8") as fh:
            src = fh.read()
        code = src.split('"""', 2)[2]                       # hors docstring
        self.assertNotIn("/var/lib/postgresql/data", code)
        self.assertIn("/run/restore-probe-", code)
        self.assertEqual(code.count("rm -rf"), 1)
        self.assertIn("case \\\"$D\\\" in /run/restore-probe-*)", code)    # suppression gardée par un motif strict
        self.assertNotIn("kubectl\", \"delete", code)
        self.assertNotIn("\"delete\"", code)


if __name__ == "__main__":
    unittest.main()
