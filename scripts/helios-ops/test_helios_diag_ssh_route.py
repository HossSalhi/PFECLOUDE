# -*- coding: utf-8 -*-
"""LOT WATCHDOG-DIAG-KEYONLY-ROUTE-AUDIT-01 — tests transport SSH (doubles) + dispatch route (AST).
Lancer : python -m unittest test_helios_diag_ssh_route -v   (depuis scripts/helios-ops)
AUCUN appel SSH réel, AUCUN réseau, AUCUNE VM, AUCUN serveur HTTP démarré ni watchdog importé."""
import os
import ast
import unittest
import helios_diag_profiles as d
import helios_diag_ssh as ssh

HERE = os.path.dirname(os.path.abspath(__file__))
WATCHDOG = os.path.join(HERE, "helios-node-autoheal.py")
KNOWN = os.path.abspath(__file__)   # fichier existant non vide -> fausse ancre known_hosts "présente"


def make_fake(connect_raise=None, key_raise=False):
    """Faux module paramiko injectable (aucun réseau). `connect_raise` ∈ {auth,badkey,ssh,other}."""
    rec = {"calls": [], "connect_count": 0, "connect_kwargs": []}

    class SSHException(Exception):
        pass

    class AuthenticationException(SSHException):
        pass

    class BadHostKeyException(SSHException):
        pass

    class RejectPolicy:
        pass

    class AutoAddPolicy:
        pass

    exc_map = {"auth": AuthenticationException, "badkey": BadHostKeyException,
               "ssh": SSHException, "other": RuntimeError}

    class _Chan:
        def recv_exit_status(self):
            return 0

    class _Stream:
        def __init__(self, data):
            self._d = data
            self.channel = _Chan()

        def read(self):
            return self._d

    class SSHClient:
        def load_system_host_keys(self, path):
            rec["calls"].append(("load_system_host_keys", path))

        def set_missing_host_key_policy(self, policy):
            rec["calls"].append(("set_policy", type(policy).__name__))

        def connect(self, ip, **kw):
            rec["calls"].append(("connect", ip))
            rec["connect_count"] += 1
            rec["connect_kwargs"].append(kw)
            if connect_raise:
                raise exc_map[connect_raise]("echec simule")

        def exec_command(self, cmd, timeout=None):
            rec["calls"].append(("exec_command", cmd))
            return (None, _Stream(b"sortie-simulee"), _Stream(b""))

        def close(self):
            rec["calls"].append(("close", None))

    class Ed25519Key:
        @staticmethod
        def from_private_key_file(path):
            rec["calls"].append(("load_key", path))
            if key_raise:
                raise FileNotFoundError("cle simulee absente")
            return object()

    class _Fake:
        pass

    fake = _Fake()
    fake.SSHException = SSHException
    fake.AuthenticationException = AuthenticationException
    fake.BadHostKeyException = BadHostKeyException
    fake.RejectPolicy = RejectPolicy
    fake.AutoAddPolicy = AutoAddPolicy
    fake.SSHClient = SSHClient
    fake.Ed25519Key = Ed25519Key
    return fake, rec


SECRETS_INTERDITS = ("kube", "password", "mot de passe", "id_ed25519", "private", "token", "jeton")


def _run(fake, known=KNOWN):
    return ssh.run_diagnostic(
        "k8s-worker1", "diag-cpu", ip="192.168.148.158", ssh_user="kube",
        ssh_key_path=r"C:\fake\id_test", known_hosts_path=known,
        profile_commands=d.profile_commands, build_evidence=d.build_evidence,
        ssh_identity_source=d.ssh_identity_source, cmd_timeout=5, paramiko_mod=fake, logger=None)


class KeyOnlyTransportTest(unittest.TestCase):

    def _msg_sans_secret(self, msg):
        low = (msg or "").lower()
        for s in SECRETS_INTERDITS:
            self.assertNotIn(s, low, f"secret potentiel '{s}' dans le message: {msg!r}")

    def test_autorise_une_seule_connexion_par_cle(self):
        fake, rec = make_fake()
        ok, ev = _run(fake)
        self.assertTrue(ok)
        self.assertEqual(1, rec["connect_count"])                        # EXACTEMENT une connexion
        kw = rec["connect_kwargs"][0]
        self.assertIn("pkey", kw)                                        # par clé
        self.assertNotIn("password", kw)                                 # JAMAIS de mot de passe
        names = [c[0] for c in rec["calls"]]
        self.assertIn("set_policy", names)
        self.assertEqual("RejectPolicy", [c for c in rec["calls"] if c[0] == "set_policy"][0][1])
        # known_hosts chargé AVANT la connexion
        self.assertLess(names.index("load_system_host_keys"), names.index("connect"))

    def test_echec_auth_aucun_repli_mot_de_passe(self):
        fake, rec = make_fake(connect_raise="auth")
        ok, msg = _run(fake)
        self.assertFalse(ok)
        self.assertEqual(1, rec["connect_count"])                        # UNE tentative, pas de 2e (repli)
        self.assertNotIn("password", str(rec["connect_kwargs"]))
        self._msg_sans_secret(msg)

    def test_hote_inconnu_fail_closed(self):
        fake, rec = make_fake(connect_raise="ssh")
        ok, msg = _run(fake)
        self.assertFalse(ok)
        self.assertEqual(1, rec["connect_count"])
        self._msg_sans_secret(msg)

    def test_mauvaise_cle_hote_rejetee(self):
        fake, rec = make_fake(connect_raise="badkey")
        ok, msg = _run(fake)
        self.assertFalse(ok)
        self.assertEqual(1, rec["connect_count"])
        self._msg_sans_secret(msg)

    def test_pas_d_ancre_refuse_avant_connexion(self):
        fake, rec = make_fake()
        ok, msg = _run(fake, known=r"C:\chemin\inexistant\known_hosts")
        self.assertFalse(ok)
        self.assertEqual(0, rec["connect_count"])                        # ZERO connexion
        self.assertNotIn("load_system_host_keys", [c[0] for c in rec["calls"]])
        self._msg_sans_secret(msg)

    def test_cle_absente_refuse_avant_connexion(self):
        fake, rec = make_fake(key_raise=True)
        ok, msg = _run(fake)
        self.assertFalse(ok)
        self.assertEqual(0, rec["connect_count"])
        self._msg_sans_secret(msg)


class DispatchToTransportTest(unittest.TestCase):
    """Chaîne RÉELLE handle_host_diag -> diag_exec(run_diagnostic) avec seul paramiko simulé."""

    def _executor(self, fake):
        def _exec(node, profile):
            return ssh.run_diagnostic(
                node, profile, ip="192.168.148.158", ssh_user="kube", ssh_key_path=r"C:\fake\id_test",
                known_hosts_path=KNOWN, profile_commands=d.profile_commands,
                build_evidence=d.build_evidence, ssh_identity_source=d.ssh_identity_source,
                cmd_timeout=5, paramiko_mod=fake, logger=None)
        return _exec

    def test_requete_autorisee_transport_une_fois(self):
        fake, rec = make_fake()
        tok = "jeton"
        code, body = d.handle_host_diag("/host-diag/k8s-worker1/diag-cpu", tok, tok,
                                        {"k8s-worker1"}, self._executor(fake))
        self.assertEqual(200, code)
        self.assertEqual(1, rec["connect_count"])          # autorisée -> transport exactement 1x

    def test_requete_refusee_transport_zero(self):
        fake, rec = make_fake()
        tok = "jeton"
        refus = [("/host-diag/k8s-worker1/diag-cpu", "faux", tok),   # mauvais jeton
                 ("/host-diag/k8s-worker1/diag-cpu", "x", ""),       # aucun jeton configuré
                 ("/host-diag/inconnu/diag-cpu", tok, tok),          # cible interdite
                 ("/host-diag/k8s-worker1/rm-rf", tok, tok)]         # profil inconnu
        for path, hdr, exp in refus:
            code, _ = d.handle_host_diag(path, hdr, exp, {"k8s-worker1"}, self._executor(fake))
            self.assertIn(code, (401, 404))
        self.assertEqual(0, rec["connect_count"])          # refusée -> transport 0x


class RouteDispatchASTTest(unittest.TestCase):
    """Contrôle le dispatch RÉEL du handler par analyse AST, sans importer le watchdog."""

    @classmethod
    def setUpClass(cls):
        with open(WATCHDOG, encoding="utf-8") as f:
            cls.src = f.read()
        cls.tree = ast.parse(cls.src)

    def _func(self, name):
        for n in ast.walk(self.tree):
            if isinstance(n, ast.FunctionDef) and n.name == name:
                return n
        return None

    def test_do_post_delegue_une_fois_sans_appel_direct(self):
        do_post = self._func("do_POST")
        self.assertIsNotNone(do_post)
        handle_calls = diag_exec_direct = diag_exec_as_arg = 0
        for n in ast.walk(do_post):
            if isinstance(n, ast.Call):
                f = n.func
                if isinstance(f, ast.Attribute) and f.attr == "handle_host_diag":
                    handle_calls += 1
                    for a in n.args:
                        if isinstance(a, ast.Name) and a.id == "diag_exec":
                            diag_exec_as_arg += 1
                if isinstance(f, ast.Name) and f.id == "diag_exec":
                    diag_exec_direct += 1
                if isinstance(f, ast.Attribute) and f.attr == "diag_exec":
                    diag_exec_direct += 1
        self.assertEqual(1, handle_calls)      # une seule délégation
        self.assertEqual(0, diag_exec_direct)  # jamais appelé directement dans le handler
        self.assertEqual(1, diag_exec_as_arg)  # passé comme exécuteur injecté

    def test_diag_exec_delegue_run_diagnostic_sans_mot_de_passe(self):
        diag = self._func("diag_exec")
        seg = ast.get_source_segment(self.src, diag)
        self.assertIn("run_diagnostic", seg)
        self.assertNotIn("password", seg)
        self.assertNotIn("SSH_PASS", seg)
        self.assertNotIn("AutoAddPolicy", seg)
        calls = sum(1 for n in ast.walk(diag) if isinstance(n, ast.Call)
                    and isinstance(n.func, ast.Attribute) and n.func.attr == "run_diagnostic")
        self.assertEqual(1, calls)             # exactement une exécution autorisée possible

    def test_host_exec_historique_intact(self):
        host = self._func("host_exec")
        seg = ast.get_source_segment(self.src, host)
        self.assertIn("AutoAddPolicy", seg)    # route historique NON modifiée (documentée à part)
        self.assertIn("sudo -S bash -c", seg)

    def test_module_ssh_keyonly(self):
        with open(os.path.join(HERE, "helios_diag_ssh.py"), encoding="utf-8") as f:
            s = f.read()
        self.assertEqual(0, s.count("password="))   # aucun mot de passe
        self.assertNotIn("SSH_PASS", s)
        self.assertNotIn("AutoAddPolicy", s)
        self.assertEqual(1, s.count(".connect("))   # une seule invocation de connexion
        self.assertIn("RejectPolicy", s)
        self.assertIn("load_system_host_keys", s)


if __name__ == "__main__":
    unittest.main()
