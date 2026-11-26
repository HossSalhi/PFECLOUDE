# -*- coding: utf-8 -*-
"""LOT 08 — WATCHDOG-SECURE-RECONCILIATION-01 : tests du CANDIDAT (statique + AST).
Lancer : python -m unittest test_candidate_reconciliation -v   (depuis scripts/helios-ops)
Le watchdog candidat n'est JAMAIS importé ni démarré : vérification par lecture de source + AST.
Aucun réseau, aucune VM, aucun serveur HTTP, aucune commande distante."""
import os
import ast
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
CANDIDATE = os.path.join(HERE, "helios-node-autoheal.candidate.py")


class CandidateReconciliationTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        with open(CANDIDATE, encoding="utf-8") as f:
            cls.src = f.read()
        cls.tree = ast.parse(cls.src)   # prouve la syntaxe/compilation (sans exécuter)

    def _func(self, name):
        for n in ast.walk(self.tree):
            if isinstance(n, ast.FunctionDef) and n.name == name:
                return n
        return None

    # ---- protections de la référence déployée préservées ----
    def test_restart_authentification_preservee(self):
        self.assertIn("JETON_ATTENDU = _f.read().strip()", self.src)   # jeton lu depuis fichier
        self.assertIn("hmac.compare_digest(recu, JETON_ATTENDU)", self.src)  # /restart authentifié

    def test_bind_reseau_preserve(self):
        self.assertIn('BIND_ADDR = os.environ.get("HELIOS_WATCHDOG_BIND", "192.168.148.1")', self.src)
        self.assertIn("HTTPServer((BIND_ADDR, HTTP_PORT)", self.src)
        self.assertNotIn('("0.0.0.0", HTTP_PORT)', self.src)           # pas de bind effectif 0.0.0.0

    def test_cooldown_idempotence_preserve(self):
        self.assertIn("ALREADY_EXECUTED", self.src)
        self.assertIn("COOLDOWN", self.src)

    def test_routes_historiques_presentes(self):
        self.assertIn('self.path.startswith("/restart/")', self.src)
        self.assertIn('self.path.startswith("/host-exec/")', self.src)
        self.assertIsNotNone(self._func("host_exec"))
        self.assertIsNotNone(self._func("power_on"))

    # ---- aucun secret copié ----
    def test_aucun_secret_litteral(self):
        self.assertNotIn("Helios2026", self.src)
        self.assertNotIn('"kube", "kube"', self.src)
        self.assertNotIn('password="kube"', self.src)
        self.assertIn('HELIOS_PASS = os.environ.get("HELIOS_SUPERADMIN_PASS", "")', self.src)
        self.assertIn('SSH_PASS = os.environ.get("HELIOS_SSH_PASS", "")', self.src)

    # ---- diagnostics intégrés et correctement dispatchés ----
    def test_reutilise_modules_testes(self):
        self.assertIn("import helios_diag_profiles as diagprof", self.src)
        self.assertIn("import helios_diag_ssh as diagssh", self.src)

    def test_host_diag_dispatch_unique_sans_appel_direct(self):
        do_post = self._func("do_POST")
        self.assertIsNotNone(do_post)
        handle = direct = as_arg = 0
        for n in ast.walk(do_post):
            if isinstance(n, ast.Call):
                f = n.func
                if isinstance(f, ast.Attribute) and f.attr == "handle_host_diag":
                    handle += 1
                    for a in n.args:
                        if isinstance(a, ast.Name) and a.id == "diag_exec":
                            as_arg += 1
                if isinstance(f, ast.Name) and f.id == "diag_exec":
                    direct += 1
                if isinstance(f, ast.Attribute) and f.attr == "diag_exec":
                    direct += 1
        self.assertEqual(1, handle)      # une seule délégation
        self.assertEqual(0, direct)      # diag_exec jamais appelé directement
        self.assertEqual(1, as_arg)      # passé comme exécuteur injecté

    def test_host_diag_utilise_meme_jeton_que_restart(self):
        do_post = self._func("do_POST")
        found = False
        for n in ast.walk(do_post):
            if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                    and n.func.attr == "handle_host_diag"):
                # args : (path, header_token, expected_token, allowed_nodes, executor)
                self.assertGreaterEqual(len(n.args), 5)
                exp = n.args[2]
                self.assertTrue(isinstance(exp, ast.Name) and exp.id == "JETON_ATTENDU",
                                "le jeton attendu de /host-diag doit etre JETON_ATTENDU (meme source que /restart)")
                found = True
        self.assertTrue(found)

    def test_diag_exec_keyonly_delegue(self):
        diag = self._func("diag_exec")
        self.assertIsNotNone(diag)
        seg = ast.get_source_segment(self.src, diag)
        self.assertIn("run_diagnostic", seg)
        self.assertNotIn("password", seg)
        self.assertNotIn("SSH_PASS", seg)
        self.assertNotIn("AutoAddPolicy", seg)
        self.assertNotIn(".connect(", seg)          # tout le transport est dans helios_diag_ssh
        calls = sum(1 for n in ast.walk(diag) if isinstance(n, ast.Call)
                    and isinstance(n.func, ast.Attribute) and n.func.attr == "run_diagnostic")
        self.assertEqual(1, calls)

    def test_host_exec_delegue_transport_durci(self):
        # LOT 09.6 : host_exec delegue au transport PAR CLE ; plus de SSH/sudo inline, plus de credential ssh.
        seg = ast.get_source_segment(self.src, self._func("host_exec"))
        self.assertIn("run_host_op", seg)                   # delegation
        self.assertNotIn("_ssh_pass", seg)                  # plus d'usage du credential HELIOS/watchdog/ssh
        self.assertNotIn("paramiko.AutoAddPolicy()", seg)   # aucune politique permissive
        self.assertNotIn("password", seg)                   # aucun secret ni commande inline


if __name__ == "__main__":
    unittest.main()
