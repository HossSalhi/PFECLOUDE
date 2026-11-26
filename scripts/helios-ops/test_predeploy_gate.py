# -*- coding: utf-8 -*-
"""LOT 08.1 — PREDEPLOY-SECURITY-GATE-01 : tests de la porte de securite (statique + unitaire + AST).
Lancer : python -m unittest test_predeploy_gate -v   (depuis scripts/helios-ops)
Le watchdog candidat n'est JAMAIS importe ni demarre. Aucun reseau, aucune VM, aucun SSH reel."""
import os
import ast
import hmac
import unittest
import helios_diag_profiles as d

HERE = os.path.dirname(os.path.abspath(__file__))
CANDIDATE = os.path.join(HERE, "helios-node-autoheal.candidate.py")


def _restart_refuse(recu, jeton_attendu):
    """Reproduction FIDELE du predicat de refus /restart du candidat (fail-closed).
    Refuse si aucun jeton configure OU si le jeton recu ne correspond pas."""
    return (not jeton_attendu) or (not hmac.compare_digest(recu, jeton_attendu))


class PredeployGateTest(unittest.TestCase):

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

    # ---------- /restart fail-closed ----------
    def test_restart_predicat_fail_closed_unitaire(self):
        # jeton non configure -> refus quel que soit le jeton recu
        self.assertTrue(_restart_refuse("", ""))
        self.assertTrue(_restart_refuse("peu-importe", ""))
        # jeton configure : refus si absent/incorrect, accepte si correct
        self.assertTrue(_restart_refuse("", "SECRET"))
        self.assertTrue(_restart_refuse("FAUX", "SECRET"))
        self.assertFalse(_restart_refuse("SECRET", "SECRET"))

    def test_restart_candidat_est_fail_closed(self):
        do_post = self._func("do_POST")
        seg = ast.get_source_segment(self.src, do_post)
        # le candidat refuse si JETON_ATTENDU vide (fail-closed)
        self.assertIn("not JETON_ATTENDU or not hmac.compare_digest(recu, JETON_ATTENDU)", seg)
        # l'ancien garde fail-open (`if JETON_ATTENDU:` encadrant la verification) a disparu
        self.assertNotIn("if JETON_ATTENDU:\n                recu", seg)

    # ---------- protections reseau/idempotence maintenues ----------
    def test_cooldown_maintenu(self):
        self.assertIn("ALREADY_EXECUTED", self.src)
        self.assertIn("COOLDOWN", self.src)

    def test_bind_restreint_maintenu(self):
        self.assertIn('BIND_ADDR = os.environ.get("HELIOS_WATCHDOG_BIND", "192.168.148.1")', self.src)
        self.assertIn("HTTPServer((BIND_ADDR, HTTP_PORT)", self.src)
        self.assertNotIn('("0.0.0.0", HTTP_PORT)', self.src)

    # ---------- pas de commande libre / allowlist ----------
    def test_pas_de_commande_libre(self):
        # profils de diagnostic fermes (module) + whitelist /host-exec figee
        self.assertIsNone(d.profile_commands("rm -rf /"))
        self.assertIn('"restart-kubelet": "systemctl restart kubelet"', self.src)
        self.assertIn("handle_host_diag", self.src)   # diag passe par la decision testee (pas de cmd libre)

    def test_allowlist_maintenue(self):
        self.assertTrue(d.is_allowed("k8s-worker1", "diag-cpu", {"k8s-worker1"}))
        self.assertFalse(d.is_allowed("intrus", "diag-cpu", {"k8s-worker1"}))
        self.assertIn("DIAG_NODES = set(NODE_IPS)", self.src)

    # ---------- SSH : clé-seule sur diag, host-key verifiee sur host-exec ----------
    def test_host_diag_sans_repli_mot_de_passe(self):
        seg = ast.get_source_segment(self.src, self._func("diag_exec"))
        self.assertNotIn("password", seg)
        self.assertNotIn("SSH_PASS", seg)
        self.assertIn("run_diagnostic", seg)

    def test_host_exec_durci_host_key(self):
        # LOT 09.3.1 : host_exec delegue au transport durci ; la verif d'identite (known_hosts+RejectPolicy)
        # et le sudo-via-stdin sont dans helios_host_exec (teste a part). host_exec ne doit rien faire inline.
        seg = ast.get_source_segment(self.src, self._func("host_exec"))
        self.assertNotIn("paramiko.AutoAddPolicy()", seg)
        self.assertIn("run_host_op", seg)             # delegation au transport durci
        self.assertNotIn("password", seg)             # aucun secret ni commande inline

    # ---------- secrets ----------
    def test_secrets_absents_sans_substitut_dangereux(self):
        self.assertNotIn("Helios2026", self.src)
        self.assertNotIn('"kube", "kube"', self.src)
        self.assertIn('HELIOS_PASS = os.environ.get("HELIOS_SUPERADMIN_PASS", "")', self.src)
        self.assertIn('SSH_PASS = os.environ.get("HELIOS_SSH_PASS", "")', self.src)

    def test_aucun_secret_journalise(self):
        secret_names = ("SSH_PASS", "HELIOS_PASS", "JETON_ATTENDU")
        offenders = []
        for n in ast.walk(self.tree):
            if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                    and isinstance(n.func.value, ast.Name) and n.func.value.id == "log"):
                seg = ast.get_source_segment(self.src, n) or ""
                for s in secret_names:
                    if s in seg:
                        offenders.append((n.func.attr, s, seg[:60]))
        self.assertEqual([], offenders, f"secret potentiel dans un log: {offenders}")

    # ---------- diagnostics preserves + pas de regression recovery ----------
    def test_diagnostics_preserves(self):
        self.assertIn("import helios_diag_profiles as diagprof", self.src)
        self.assertIn("import helios_diag_ssh as diagssh", self.src)
        self.assertIn('self.path.startswith("/host-diag/")', self.src)

    def test_recovery_sans_regression(self):
        power = self._func("power_on")
        self.assertIsNotNone(power)
        self.assertIn('"start"', ast.get_source_segment(self.src, power))  # vmrun start conserve
        do_post = ast.get_source_segment(self.src, self._func("do_POST"))
        self.assertIn("ok = power_on(node)", do_post)                      # /restart declenche toujours le power-on
        self.assertIn('self.path.startswith("/restart/")', do_post)
        self.assertIn('self.path.startswith("/host-exec/")', do_post)


if __name__ == "__main__":
    unittest.main()
