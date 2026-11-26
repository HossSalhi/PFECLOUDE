# -*- coding: utf-8 -*-
"""Tests de scripts/helios-ops/repair_prometheus_wal_160.py : decision (fonction pure), parsing du releve et GARDE-FOUS du
script de reparation (aucune suppression, retour arriere present). Aucun reseau, aucune VM, aucune ecriture.

Lancer : python -I test_repair_prometheus_wal_160.py -v        (depuis scripts/helios-ops)
"""
import importlib.util
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("repair_wal_mod", os.path.join(HERE, "repair_prometheus_wal_160.py"))
R = importlib.util.module_from_spec(spec)
spec.loader.exec_module(R)

SORTIE = """ACTIVE=active
NOW=1794464000
WALMB=1424
SEGS=17
FREEMB=2700
LASTBLOCK=1794463202
CORRUPT=5
FAILED=7
DIRDATA=oui
"""


def faits(**kw):
    f = R.parse_releve(SORTIE)
    f.update(kw)
    return f


class ParsingTests(unittest.TestCase):
    def test_parse_releve(self):
        f = R.parse_releve(SORTIE)
        self.assertEqual((f["actif"], f["wal_mo"], f["segments"], f["libre_mo"], f["corruptions"], f["echecs"], f["dossier"]),
                         (True, 1424, 17, 2700, 5, 7, True))
        self.assertEqual(f["maintenant"] - f["derniere_coupe"], 798)

    def test_parse_valeurs_manquantes(self):
        f = R.parse_releve("ACTIVE=inactive\nNOW=1\nLASTBLOCK=\nCORRUPT=\n")
        self.assertFalse(f["actif"])
        self.assertIsNone(f["derniere_coupe"])
        self.assertIsNone(f["corruptions"])
        self.assertIsNone(R.parse_releve("")["maintenant"])


class DecisionTests(unittest.TestCase):
    def test_apres_une_coupe_recente_c_est_autorise(self):
        ok, refus, avert, attente = R.decider(faits(maintenant=1794463202 + 120))
        self.assertTrue(ok, refus)
        self.assertEqual((refus, avert, attente), ([], [], None))

    def test_hors_fenetre_refuse_et_annonce_la_prochaine(self):
        ok, refus, avert, attente = R.decider(faits(maintenant=1794463202 + 3600))
        self.assertFalse(ok)
        self.assertEqual(attente, 60)                                   # prochaine coupe : +2 h, soit dans 60 min
        self.assertIn("pas dans la fenetre", refus[0])

    def test_now_accepte_hors_fenetre_avec_avertissement(self):
        ok, refus, avert, _ = R.decider(faits(maintenant=1794463202 + 3600), now_force=True)
        self.assertTrue(ok, refus)
        self.assertIn("perdue", avert[0])

    def test_pas_de_corruption_pas_de_reparation(self):
        self.assertFalse(R.decider(faits(corruptions=0, maintenant=1794463300))[0])
        self.assertFalse(R.decider(faits(corruptions=None, maintenant=1794463300))[0])

    def test_wal_petit(self):
        self.assertFalse(R.decider(faits(wal_mo=120, maintenant=1794463300))[0])

    def test_prometheus_inactif_ou_dossier_absent(self):
        self.assertFalse(R.decider(faits(actif=False, maintenant=1794463300))[0])
        self.assertFalse(R.decider(faits(dossier=False, maintenant=1794463300))[0])

    def test_espace_libre_insuffisant(self):
        self.assertFalse(R.decider(faits(libre_mo=100, maintenant=1794463300))[0])

    def test_coupe_inconnue(self):
        self.assertFalse(R.decider(faits(derniere_coupe=None))[0])
        self.assertTrue(R.decider(faits(derniere_coupe=None), now_force=True)[0])

    def test_constantes_figees(self):
        self.assertEqual((R.FENETRE_APRES_COUPE_S, R.WAL_MIN_MO, R.LIBRE_MIN_MO), (600, 200, 300))
        self.assertEqual(R.DONNEES, "/var/lib/prometheus/metrics2")


class VerificationTests(unittest.TestCase):
    """--verify : ne compte que les echecs survenus DEPUIS le redemarrage (les anciens ne prouvent rien)."""

    def test_pas_de_coupe_depuis_le_redemarrage(self):
        ok, c = R.verifier(faits(demarrage=1000, derniere_coupe=900, echecs_depuis=0, wal_mo=5))
        self.assertFalse(ok)
        self.assertIn("aucune coupe", c[0])
        self.assertFalse(R.verifier(faits(demarrage=None, derniere_coupe=2000))[0])

    def test_coupe_reussie_avec_checkpoint(self):
        ok, c = R.verifier(faits(demarrage=1000, derniere_coupe=2000, echecs_depuis=0, wal_mo=60, checkpoints_depuis=1))
        self.assertTrue(ok, c)
        self.assertIn("se tronque", c[-1])

    def test_une_coupe_sans_checkpoint_ne_prouve_rien(self):
        """Constate le 12 nov. 2026 : aux coupes de 10:00Z et 11:00Z (moins de 3 segments) Prometheus n'a fait AUCUN checkpoint ;
        ni l'absence d'echec ni un WAL < 400 Mo ne prouvent la reparation."""
        for ck in (0, None):
            ok, c = R.verifier(faits(demarrage=1000, derniere_coupe=2000, echecs_depuis=0, wal_mo=253, checkpoints_depuis=ck))
            self.assertFalse(ok)
            self.assertIn("pas encore ete exercee", c[0])

    def test_echec_de_compactage_depuis_le_redemarrage(self):
        ok, c = R.verifier(faits(demarrage=1000, derniere_coupe=2000, echecs_depuis=1, wal_mo=60, checkpoints_depuis=1))
        self.assertFalse(ok)
        self.assertIn("echouee", c[0])

    def test_wal_encore_gros(self):
        ok, c = R.verifier(faits(demarrage=1000, derniere_coupe=2000, echecs_depuis=0, wal_mo=1500, checkpoints_depuis=1))
        self.assertFalse(ok)

    def test_les_anciennes_erreurs_ne_comptent_pas(self):
        """corruptions (5 h) peut rester > 0 apres la reparation : seule echecs_depuis compte."""
        ok, _ = R.verifier(faits(demarrage=1000, derniere_coupe=2000, echecs_depuis=0, wal_mo=60, corruptions=3, echecs=8,
                                 checkpoints_depuis=2))
        self.assertTrue(ok)

    def test_parse_des_nouvelles_cles(self):
        f = R.parse_releve(SORTIE + "START=1794462112\nFAILSINCE=0\nCKPTSINCE=2\n")
        self.assertEqual((f["demarrage"], f["echecs_depuis"], f["checkpoints_depuis"]), (1794462112, 0, 2))
        self.assertIsNone(R.parse_releve(SORTIE)["demarrage"])


class TransportTests(unittest.TestCase):
    """Le bug du 12 nov. 2026 : le script bash partait en CRLF (mode texte sous Windows) et bash le rejetait."""

    def test_le_script_part_en_octets_sans_retour_chariot(self):
        recu = {}

        def faux_run(cmd, input=None, capture_output=None, timeout=None, text=None):
            recu["input"], recu["text"] = input, text
            return mock.Mock(returncode=0, stdout=b"ok\n", stderr=b"")
        with mock.patch.object(R.subprocess, "run", faux_run):
            rc, out, err = R.ssh("bash -s", entree="echo un\r\necho deux\n")
        self.assertIsInstance(recu["input"], bytes)
        self.assertNotIn(b"\r", recu["input"])
        self.assertEqual(recu["input"], b"echo un\necho deux\n")
        self.assertIsNone(recu["text"])                                  # jamais de mode texte
        self.assertEqual((rc, out, err), (0, "ok\n", ""))

    def test_les_scripts_bash_sont_valides(self):
        bash = shutil.which("bash")
        if not bash:
            self.skipTest("bash absent")
        for nom, script in (("REPARATION", R.REPARATION), ("RELEVE", R.RELEVE)):
            with tempfile.NamedTemporaryFile("wb", suffix=".sh", delete=False) as f:
                f.write(script.replace("\r\n", "\n").encode("utf-8"))
            try:
                p = subprocess.run([bash, "-n", f.name], capture_output=True)
                self.assertEqual(p.returncode, 0, "%s : %s" % (nom, p.stderr.decode("utf-8", "replace")[:200]))
            finally:
                os.unlink(f.name)

    def test_aucun_retour_chariot_dans_les_scripts(self):
        self.assertNotIn("\r", R.REPARATION)
        self.assertNotIn("\r", R.RELEVE)


class SuppressionTests(unittest.TestCase):
    """Suppression du WAL mis de cote : garde-fous de chemin (purs) et du script distant."""

    def test_chemin_autorise(self):
        ok, _ = R.chemin_supprimable("/var/lib/prometheus/wal-corrompu-20261112-090152")
        self.assertTrue(ok)
        self.assertEqual(R.WAL_DEPLACE_DEFAUT, "/var/lib/prometheus/wal-corrompu-20261112-090152")

    def test_chemins_refuses(self):
        for chemin in ("/var/lib/prometheus/metrics2/wal",                           # le WAL ACTIF
                       "/var/lib/prometheus/metrics2",
                       "/var/lib/prometheus",
                       "/var/lib/prometheus/",
                       "/var/lib/prometheus/wal-corrompu-20261112-090152/",         # barre finale
                       "/var/lib/prometheus/wal-corrompu-20261112-090152/00001973",   # un fichier a l'interieur
                       "/var/lib/prometheus/wal-corrompu-2026-090152",
                       "/var/lib/prometheus/wal-corrompu-20261112-090152-x",
                       "/var/lib/prometheus/../wal-corrompu-20261112-090152",
                       "/var/lib/prometheus/metrics2/../wal-corrompu-20261112-090152",
                       "/tmp/wal-corrompu-20261112-090152",
                       "/var/lib/prometheus/autre-20261112-090152",
                       "wal-corrompu-20261112-090152", "", None, 12,
                       "/var/lib/prometheus/wal-corrompu-20261112-090152\x00/etc"):
            self.assertFalse(R.chemin_supprimable(chemin)[0], repr(chemin))

    def test_le_script_distant_est_du_bash_valide_et_sans_retour_chariot(self):
        self.assertNotIn("\r", R.SUPPRESSION)
        bash = shutil.which("bash")
        if not bash:
            self.skipTest("bash absent")
        with tempfile.NamedTemporaryFile("wb", suffix=".sh", delete=False) as f:
            f.write(R.SUPPRESSION.encode("utf-8"))
        try:
            p = subprocess.run([bash, "-n", f.name], capture_output=True)
            self.assertEqual(p.returncode, 0, p.stderr.decode("utf-8", "replace")[:200])
        finally:
            os.unlink(f.name)

    def test_une_seule_suppression_et_elle_est_gardee(self):
        occurrences = re.findall(r"\brm\b[^\n]*", R.SUPPRESSION)
        self.assertEqual(occurrences, ['rm -rf --one-file-system -- "$P"'])
        for garde in ('case "$P" in', '[ -L "$P" ]', 'readlink -f', 'systemctl is-active prometheus', 'lsof', '--apply'):
            self.assertIn(garde, R.SUPPRESSION)
        # la suppression n'arrive qu'APRES toutes les gardes et APRES le test du mode simulation
        self.assertLess(R.SUPPRESSION.index("lsof"), R.SUPPRESSION.index("rm -rf"))
        self.assertLess(R.SUPPRESSION.index('"$MODE" != "--apply"'), R.SUPPRESSION.index("rm -rf"))

    def test_refus_cote_python_avant_tout_envoi(self):
        appels = []
        with mock.patch.object(R, "ssh", lambda *a, **k: appels.append(a) or (0, "", "")):
            code = R.principal(["--supprimer-wal-deplace", "--chemin", "/var/lib/prometheus/metrics2/wal", "--apply"])
        self.assertEqual(code, 4)
        self.assertEqual(appels, [])                                    # rien n'est envoye a .160

    def test_la_simulation_n_envoie_pas_apply(self):
        recu = []
        with mock.patch.object(R, "ssh", lambda cmd, entree=None, delai=0: recu.append(cmd) or (0, "SIMULATION", "")):
            R.principal(["--supprimer-wal-deplace"])
            R.principal(["--supprimer-wal-deplace", "--apply"])
        self.assertNotIn("--apply", recu[0])
        self.assertTrue(recu[1].endswith("--apply"))


class GardeFousTests(unittest.TestCase):
    def test_aucune_suppression_dans_la_reparation(self):
        for motif in (r"\brm\b", r"\brmdir\b", r"\bunlink\b", r"\bshred\b", r"\btruncate\b", r">\s*/var/lib", r"\bfind\b.*-delete"):
            self.assertIsNone(re.search(motif, R.REPARATION), "motif de suppression interdit : %s" % motif)

    def test_la_reparation_deplace_le_wal_entier_et_prevoit_le_retour_arriere(self):
        self.assertIn("mv {}/wal".format(R.DONNEES), R.REPARATION)
        self.assertIn("RETOUR ARRIERE", R.REPARATION)
        self.assertIn("systemctl stop prometheus", R.REPARATION)
        self.assertIn("systemctl start prometheus", R.REPARATION)
        self.assertNotIn("/wal/0", R.REPARATION)                        # jamais de segment isole

    def test_le_releve_est_en_lecture_seule(self):
        for motif in (r"\bmv\b", r"\brm\b", r"systemctl (stop|start|restart)", r">\s*/(?!dev/null)"):
            self.assertIsNone(re.search(motif, R.RELEVE), motif)


if __name__ == "__main__":
    unittest.main()
