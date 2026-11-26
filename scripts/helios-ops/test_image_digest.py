# -*- coding: utf-8 -*-
"""Tests de l'empreinte de contenu d'image. Lancer : python -I scripts/helios-ops/test_image_digest.py"""
import hashlib
import importlib.util
import os
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.abspath(os.path.join(HERE, "..", "..", "infra", "helios-sot", "tools", "image_digest.py"))
spec = importlib.util.spec_from_file_location("image_digest", TOOL)
idg = importlib.util.module_from_spec(spec)
spec.loader.exec_module(idg)


class DigestTest(unittest.TestCase):
    def test_empreinte_stable_independante_de_l_ordre_et_sensible_a_chaque_octet(self):
        a = {"classes/x/A.class": "a" * 64, "resources/application.properties": "b" * 64}
        b = dict(reversed(list(a.items())))
        self.assertEqual(idg.digest_of(a), idg.digest_of(b))
        c = dict(a, **{"classes/x/A.class": "c" + "a" * 63})
        self.assertNotEqual(idg.digest_of(a)[0], idg.digest_of(c)[0])
        self.assertNotEqual(idg.digest_of(a)[0], idg.digest_of({**a, "classes/x/B.class": "d" * 64})[0])
        self.assertEqual(idg.digest_of(a)[1], 2)

    def test_lecture_locale_et_lecture_de_pod_donnent_la_meme_empreinte(self):
        with tempfile.TemporaryDirectory() as root:
            os.makedirs(os.path.join(root, "com", "orange"))
            for rel, data in (("com/orange/A.class", b"\xca\xfe\xba\xbe-A"), ("com/orange/B$C.class", b"\xca\xfe-BC"), ("application.properties", b"k=v\n")):
                with open(os.path.join(root, *rel.split("/")), "wb") as fh:
                    fh.write(data)
            local = idg.local_files(root)
            # ce que `find … sha256sum` + sed produirait dans le pod pour les mêmes fichiers
            pod_text = "".join(f"{hashlib.sha256(d).hexdigest()}  {p}\n" for p, d in (
                ("classes/com/orange/A.class", b"\xca\xfe\xba\xbe-A"), ("classes/com/orange/B$C.class", b"\xca\xfe-BC"),
                ("resources/application.properties", b"k=v\n")))
            self.assertEqual(idg.digest_of(local), idg.digest_of(idg.parse_sums(pod_text)))

    def test_ligne_illisible_est_ignoree(self):
        self.assertEqual(idg.parse_sums("bruit\n" + "a" * 64 + "  classes/A.class\ncourt  classes/B.class\n"), {"classes/A.class": "a" * 64})

    def test_le_script_pod_est_en_lecture_seule(self):
        script = idg.POD_SCRIPT
        for interdit in ("rm ", "mv ", "kill", "tee", "chmod", "sed -i", "cp "):
            self.assertNotIn(interdit, script)
        self.assertEqual(script.count(">"), script.count("2>/dev/null"))      # aucune redirection d'écriture, hormis l'erreur standard


class FichierDEmpreinteAttendueTest(unittest.TestCase):
    """`--write-expected` : l'empreinte attendue vient du BUILD (--local), le fichier n'est créé qu'une fois et jamais écrasé."""

    def test_contenu_du_fichier(self):
        t = idg.expected_file_text("0.2.166-master153", "a" * 64, 305, r"C:\copie\target\classes", "2026-11-17")
        self.assertIn("contenu-0.2.166-master153 = " + "a" * 64, t)
        self.assertIn("pfe-backend:0.2.166-master153", t)
        self.assertIn("2026-11-17", t)
        self.assertTrue(t.endswith("\n"))

    def test_creation_unique_jamais_d_ecrasement(self):
        with tempfile.TemporaryDirectory() as repo:
            ok, path, msg = idg.write_expected_file("0.9.9-test", "b" * 64, 3, "x", repo=repo, date="2026-11-17")
            self.assertTrue(ok, msg)
            self.assertEqual(path, os.path.join(repo, "infra", "helios-sot", "IMAGE-0.9.9-test.sha256"))
            avant = open(path, "rb").read()
            ok2, _, msg2 = idg.write_expected_file("0.9.9-test", "c" * 64, 3, "x", repo=repo, date="2026-11-18")
            self.assertFalse(ok2)
            self.assertIn("ne l'écrase pas", msg2)
            self.assertEqual(open(path, "rb").read(), avant, "le fichier existant est INTACT")

    def test_etiquette_invalide_refusee_sans_rien_ecrire(self):
        with tempfile.TemporaryDirectory() as repo:
            for mauvaise in ("", "../evil", "a b", "x/y", "-1", "0" * 80):
                ok, path, _ = idg.write_expected_file(mauvaise, "d" * 64, 1, "x", repo=repo)
                self.assertFalse(ok, mauvaise)
            self.assertFalse(os.path.exists(os.path.join(repo, "infra")), "aucun dossier créé pour une étiquette refusée")

    def test_write_expected_exige_le_build_jamais_le_pod(self):
        with self.assertRaises(SystemExit) as cm:
            idg.main(["--pod", "backend-x", "--write-expected", "1.0"])      # refus AVANT tout appel kubectl
        self.assertEqual(cm.exception.code, 2)

    def test_de_bout_en_bout_depuis_un_dossier_local(self):
        with tempfile.TemporaryDirectory() as repo, tempfile.TemporaryDirectory() as classes:
            os.makedirs(os.path.join(classes, "com"))
            with open(os.path.join(classes, "com", "A.class"), "wb") as fh:
                fh.write(b"\xca\xfe-A")
            ancien, idg.REPO = idg.REPO, repo
            try:
                self.assertEqual(idg.main(["--local", classes, "--write-expected", "0.9.9-e2e"]), 0)
                self.assertEqual(idg.main(["--local", classes, "--write-expected", "0.9.9-e2e"]), 2, "second appel : refus")
            finally:
                idg.REPO = ancien
            attendu, _ = idg.digest_of(idg.local_files(classes))
            with open(os.path.join(repo, "infra", "helios-sot", "IMAGE-0.9.9-e2e.sha256"), encoding="utf-8") as fh:
                self.assertIn("contenu-0.9.9-e2e = " + attendu, fh.read())


if __name__ == "__main__":
    unittest.main()
