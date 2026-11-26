# -*- coding: utf-8 -*-
"""LOT 09.3 — WATCHDOG-CREDENTIAL-PROVISIONING-01 : tests de l'outil de provisionnement.
Lancer : python -m unittest test_provisioning_tool -v   (depuis scripts/helios-ops)
Le VRAI Credential Manager n'est JAMAIS sollicite (writer/prober injectes). Le candidat n'est pas importe.
Aucun reseau, aucune VM, aucune ecriture de credential."""
import os
import ast
import unittest
import provision_helios_credentials as prov
import helios_credstore as cs

HERE = os.path.dirname(os.path.abspath(__file__))
CANDIDATE = os.path.join(HERE, "helios-node-autoheal.candidate.py")
TOOL = os.path.join(HERE, "provision_helios_credentials.py")

SECRET = "FICTIF-TEST-SECRET-123"   # valeur FICTIVE ; jamais un vrai secret


class _Log:
    def __init__(self):
        self.msgs = []
    def warning(self, m):
        self.msgs.append(("W", m))
    def info(self, m):
        self.msgs.append(("I", m))


def seq(values):
    it = iter(values)
    return lambda prompt: next(it)


class ProvisioningTest(unittest.TestCase):

    def test_cibles_fixes_depuis_le_fournisseur(self):
        self.assertIs(prov.CRED_TARGETS, cs.CRED_TARGETS)          # source unique (meme objet)
        self.assertEqual({"superadmin_pass", "ssh_pass"}, set(prov.CRED_TARGETS))

    def test_compte_inattendu_refuse_sans_ecriture(self):
        w = []
        code = prov.provision("ssh_pass", seq([SECRET, SECRET]), lambda t, b: w.append((t, b)),
                              lambda t: False, current_user="intrus")
        self.assertEqual("WRONG_ACCOUNT", code)
        self.assertEqual([], w)

    def test_anti_ecrasement_par_defaut(self):
        w = []
        code = prov.provision("ssh_pass", seq([SECRET, SECRET]), lambda t, b: w.append((t, b)),
                              lambda t: True, current_user="houss")   # existe deja
        self.assertEqual("EXISTS", code)
        self.assertEqual([], w)                                        # NON ecrase

    def test_saisies_discordantes_annule(self):
        w = []
        code = prov.provision("ssh_pass", seq([SECRET, "AUTRE"]), lambda t, b: w.append((t, b)),
                              lambda t: False, current_user="houss")
        self.assertEqual("MISMATCH", code)
        self.assertEqual([], w)

    def test_saisie_vide_annule(self):
        w = []
        code = prov.provision("ssh_pass", seq(["", ""]), lambda t, b: w.append((t, b)),
                              lambda t: False, current_user="houss")
        self.assertEqual("EMPTY", code)
        self.assertEqual([], w)

    def test_probe_error_annule(self):
        def boom(t):
            raise OSError("echec probe")
        w = []
        code = prov.provision("ssh_pass", seq([SECRET, SECRET]), lambda t, b: w.append((t, b)),
                              boom, current_user="houss")
        self.assertEqual("PROBE_ERROR", code)
        self.assertEqual([], w)

    def test_nom_logique_inconnu(self):
        w = []
        code = prov.provision("inexistant", seq([SECRET, SECRET]), lambda t, b: w.append((t, b)),
                              lambda t: False, current_user="houss")
        self.assertEqual("UNKNOWN_NAME", code)
        self.assertEqual([], w)

    def test_ecriture_nominale_utf16le(self):
        w = []
        lg = _Log()
        code = prov.provision("ssh_pass", seq([SECRET, SECRET]), lambda t, b: w.append((t, b)),
                              lambda t: False, current_user="houss", logger=lg)
        self.assertEqual("WRITTEN", code)
        self.assertEqual(1, len(w))
        target, blob = w[0]
        self.assertEqual("HELIOS/watchdog/ssh", target)
        self.assertEqual(SECRET.encode("utf-16-le"), blob)             # UTF-16LE (coherent lecteur)
        for _, m in lg.msgs:
            self.assertNotIn(SECRET, m)

    def test_remplacement_explicite_autorise(self):
        w = []
        code = prov.provision("ssh_pass", seq([SECRET, SECRET]), lambda t, b: w.append((t, b)),
                              lambda t: True, current_user="houss", allow_replace=True)  # existe + explicite
        self.assertEqual("WRITTEN", code)
        self.assertEqual(1, len(w))

    def test_secret_trop_long_annule(self):
        big = "x" * (5 * 512)   # 2560 chars -> 5120 octets UTF-16LE > limite
        w = []
        code = prov.provision("ssh_pass", seq([big, big]), lambda t, b: w.append((t, b)),
                              lambda t: False, current_user="houss")
        self.assertEqual("TOO_LARGE", code)
        self.assertEqual([], w)

    def test_aucune_fuite_de_secret_dans_les_logs(self):
        lg = _Log()
        prov.provision("ssh_pass", seq([SECRET, SECRET]), lambda t, b: None, lambda t: False,
                       current_user="houss", logger=lg)                          # ecriture OK

        def boom(t, b):
            raise RuntimeError(SECRET)   # writer qui PORTE le secret dans l'exception

        prov.provision("ssh_pass", seq([SECRET, SECRET]), boom, lambda t: False,
                       current_user="houss", logger=lg)                          # WRITE_ERROR
        for _, m in lg.msgs:
            self.assertNotIn(SECRET, m)

    def test_credential_absent_verify(self):
        self.assertEqual("ABSENT", prov.verify_target("HELIOS/watchdog/ssh", lambda t: "ABSENT"))
        self.assertEqual("PRESENT", prov.verify_target("HELIOS/watchdog/ssh", lambda t: "PRESENT"))
        self.assertEqual("READ_ERROR", prov.verify_target("HELIOS/watchdog/ssh", lambda t: "READ_ERROR"))

    def test_aucune_dependance_externe(self):
        with open(TOOL, encoding="utf-8") as f:
            tree = ast.parse(f.read())
        imported = set()
        for n in ast.walk(tree):
            if isinstance(n, ast.Import):
                for a in n.names:
                    imported.add(a.name)
            elif isinstance(n, ast.ImportFrom):
                imported.add(n.module)
        allowed = {"sys", "helios_credstore", "ctypes", "getpass"}
        self.assertTrue(imported <= allowed, f"imports inattendus: {imported - allowed}")

    def test_pas_de_secret_en_ligne_de_commande(self):
        with open(TOOL, encoding="utf-8") as f:
            src = f.read()
        self.assertIn("getpass", src)          # saisie masquee
        self.assertNotIn("argparse", src)      # pas de parseur d'arguments (donc pas de secret en option)

    def test_coherence_noms_fournisseur_candidat(self):
        # LOT 09.6 : host_exec passe en cle seule -> le candidat ne consomme PLUS `ssh_pass` ; seul
        # `superadmin_pass` est lu (login master). La cible `ssh_pass` reste definie (geree par l'outil de
        # provisionnement/rotation), desormais VESTIGIALE cote watchdog.
        with open(CANDIDATE, encoding="utf-8") as f:
            csrc = f.read()
        self.assertIn('get_secret("superadmin_pass"', csrc)
        self.assertNotIn('get_secret("ssh_pass"', csrc)             # plus consomme par le watchdog (cle seule)
        self.assertEqual(set(cs.CRED_TARGETS), {"superadmin_pass", "ssh_pass"})

    # ---- LOT 09.4 : rotation ciblée (--only) ----
    def test_only_selectionne_un_seul_credential(self):
        self.assertEqual(["ssh_pass"], prov.selected_names(["--only", "ssh_pass"]))
        self.assertEqual(["superadmin_pass"], prov.selected_names(["--only", "superadmin_pass"]))

    def test_sans_only_tous_les_credentials(self):
        self.assertEqual(set(prov.CRED_TARGETS), set(prov.selected_names([])))

    def test_only_nom_inconnu_ou_vide_refuse(self):
        self.assertEqual([], prov.selected_names(["--only", "inexistant"]))
        self.assertEqual([], prov.selected_names(["--only"]))   # sans valeur

    def test_rotation_ciblee_ne_touche_que_ssh(self):
        # simule la boucle de main() avec --only ssh_pass --replace : seul ssh_pass est ecrit
        written = []
        for name in prov.selected_names(["--only", "ssh_pass", "--replace"]):
            prov.provision(name, seq([SECRET, SECRET]), lambda t, b: written.append(t),
                           lambda t: True, current_user="houss", allow_replace=True)
        self.assertEqual(["HELIOS/watchdog/ssh"], written)       # superadmin NON modifie

    def test_rotation_ciblee_sans_replace_ne_ecrase_pas(self):
        written = []
        for name in prov.selected_names(["--only", "ssh_pass"]):   # pas de --replace
            prov.provision(name, seq([SECRET, SECRET]), lambda t, b: written.append(t),
                           lambda t: True, current_user="houss")    # ssh existe deja
        self.assertEqual([], written)                              # EXISTS -> non ecrase


if __name__ == "__main__":
    unittest.main()
