# -*- coding: utf-8 -*-
"""LOT 09.2 — WATCHDOG-SECURE-CREDENTIAL-PROVIDER-01 : tests du fournisseur + integration candidat.
Lancer : python -m unittest test_credential_provider -v   (depuis scripts/helios-ops)
Le vrai Credential Manager n'est JAMAIS sollicite (reader injecte). Le watchdog candidat n'est PAS importe
ni demarre (verification AST). Aucun reseau, aucune VM, aucun SSH reel."""
import os
import ast
import unittest
import helios_credstore as cs

HERE = os.path.dirname(os.path.abspath(__file__))
CANDIDATE = os.path.join(HERE, "helios-node-autoheal.candidate.py")

SECRET = "VALEUR-FACTICE-DE-TEST"   # valeur FICTIVE ; jamais un vrai secret


class FakeLogger:
    def __init__(self):
        self.msgs = []
    def warning(self, m):
        self.msgs.append(m)
    def info(self, m):
        self.msgs.append(m)
    def error(self, m):
        self.msgs.append(m)


class CredStoreTest(unittest.TestCase):

    def test_cibles_fixes_documentees(self):
        self.assertEqual("HELIOS/watchdog/superadmin", cs.CRED_TARGETS["superadmin_pass"])
        self.assertEqual("HELIOS/watchdog/ssh", cs.CRED_TARGETS["ssh_pass"])

    def test_present(self):
        self.assertEqual(SECRET, cs.get_secret("ssh_pass", reader=lambda t: SECRET))

    def test_reader_recoit_la_cible_fixe(self):
        calls = []
        cs.get_secret("ssh_pass", reader=lambda t: calls.append(t) or SECRET)
        self.assertEqual(["HELIOS/watchdog/ssh"], calls)   # jamais d'acces reel, cible correcte

    def test_absent_fail_closed(self):
        self.assertIsNone(cs.get_secret("ssh_pass", reader=lambda t: None))
        self.assertIsNone(cs.get_secret("ssh_pass", reader=lambda t: ""))   # vide => indisponible

    def test_erreur_lecture_fail_closed(self):
        def boom(t):
            raise OSError("acces refuse")
        lg = FakeLogger()
        self.assertIsNone(cs.get_secret("ssh_pass", reader=boom, logger=lg))
        self.assertTrue(any("illisible" in m for m in lg.msgs))

    def test_nom_logique_inconnu(self):
        self.assertIsNone(cs.get_secret("inexistant", reader=lambda t: SECRET))

    def test_aucune_fuite_de_secret_dans_les_logs(self):
        lg = FakeLogger()
        cs.get_secret("ssh_pass", reader=lambda t: None, logger=lg)        # absent
        cs.get_secret("inexistant", reader=lambda t: SECRET, logger=lg)    # inconnu

        def boom(t):
            raise RuntimeError(SECRET)   # meme si l'exception PORTE le secret...

        cs.get_secret("ssh_pass", reader=boom, logger=lg)                  # illisible
        for m in lg.msgs:
            self.assertNotIn(SECRET, m)  # ...il ne doit JAMAIS apparaitre dans un journal

    def test_pas_de_valeur_en_dur_dans_le_module(self):
        with open(os.path.join(HERE, "helios_credstore.py"), encoding="utf-8") as f:
            src = f.read()
        for s in ("Helios2026", '"kube"', "password ="):
            self.assertNotIn(s, src)


class CandidateIntegrationTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        with open(CANDIDATE, encoding="utf-8") as f:
            cls.src = f.read()
        cls.tree = ast.parse(cls.src)

    def _func(self, name):
        for n in ast.walk(self.tree):
            if isinstance(n, ast.FunctionDef) and n.name == name:
                return n
        return None

    def test_importe_credstore_tolerant(self):
        self.assertIn("import helios_credstore as credstore", self.src)

    def test_helpers_fail_closed_presents(self):
        # LOT 09.6 : _ssh_pass supprime (host_exec par cle) ; _superadmin_pass subsiste (login master)
        seg = ast.get_source_segment(self.src, self._func("_superadmin_pass"))
        self.assertIn("credstore.get_secret", seg)
        self.assertIn('return ""', seg)                 # aucun substitut en dur (fail-closed)
        self.assertIsNone(self._func("_ssh_pass"))      # _ssh_pass n'existe plus

    def test_login_utilise_provider_et_echoue_explicitement(self):
        seg = ast.get_source_segment(self.src, self._func("_helios_login"))
        self.assertIn("_superadmin_pass()", seg)
        self.assertIn("return None, None", seg)         # echec explicite si secret absent
        self.assertIn('"password": pw', seg)            # plus de global secret direct dans le body

    def test_host_exec_keyonly_sans_credential_ssh(self):
        # LOT 09.6 : host_exec n'utilise PLUS le credential SSH (cle seule, sudo -n)
        seg = ast.get_source_segment(self.src, self._func("host_exec"))
        self.assertIn("run_host_op", seg)               # delegation au transport durci
        self.assertNotIn("_ssh_pass", seg)              # plus d'usage du credential HELIOS/watchdog/ssh
        self.assertNotIn("password", seg)

    def test_host_diag_reste_par_cle_sans_secret_mdp(self):
        seg = ast.get_source_segment(self.src, self._func("diag_exec"))
        self.assertNotIn("_ssh_pass", seg)              # /host-diag independant du secret mot de passe
        self.assertNotIn("password", seg)
        self.assertIn("run_diagnostic", seg)

    def test_routes_et_protections_preservees(self):
        self.assertIn('self.path.startswith("/restart/")', self.src)
        self.assertIn('self.path.startswith("/host-exec/")', self.src)
        self.assertIn('self.path.startswith("/host-diag/")', self.src)
        self.assertIn("not JETON_ATTENDU or not hmac.compare_digest(recu, JETON_ATTENDU)", self.src)  # /restart fail-closed
        self.assertIn("HTTPServer((BIND_ADDR, HTTP_PORT)", self.src)                                   # bind restreint
        self.assertIn("ALREADY_EXECUTED", self.src)                                                    # cooldown

    def test_aucun_secret_journalise(self):
        # aucun appel log.* ne reference une variable de secret
        offenders = []
        for n in ast.walk(self.tree):
            if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                    and isinstance(n.func.value, ast.Name) and n.func.value.id == "log"):
                seg = ast.get_source_segment(self.src, n) or ""
                for s in ("SSH_PASS", "HELIOS_PASS", "JETON_ATTENDU", "ssh_pass", "{pw"):
                    if s in seg:
                        offenders.append((s, seg[:50]))
        self.assertEqual([], offenders, f"secret potentiel dans un log: {offenders}")


if __name__ == "__main__":
    unittest.main()
