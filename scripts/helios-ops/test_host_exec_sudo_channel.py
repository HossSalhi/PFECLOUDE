# -*- coding: utf-8 -*-
"""LOT 09.6 — HOST-EXEC-KEY-ONLY-NOPASSWD-ALIGNMENT-01 : tests du transport host_exec (Paramiko SIMULE).
Lancer : python -m unittest test_host_exec_sudo_channel -v   (depuis scripts/helios-ops)
AUCUNE connexion SSH reelle : paramiko injecte (faux). Le candidat n'est PAS importe. Aucune VM/reseau.
Verifie : AUTH par CLE UNIQUEMENT (aucun connect(password=...), aucun repli), sudo NON INTERACTIF SANS mot de
passe (sudo -n ; aucune ecriture stdin), known_hosts+RejectPolicy, whitelist HOST_OPS, erreurs sans secret."""
import os
import ast
import socket
import types
import unittest
import helios_host_exec as hx

HERE = os.path.dirname(os.path.abspath(__file__))
CANDIDATE = os.path.join(HERE, "helios-node-autoheal.candidate.py")
KNOWN = os.path.abspath(__file__)          # fichier existant non vide = fausse ancre known_hosts "presente"
HOST_OPS = {
    "restart-kubelet": "systemctl restart kubelet",
    "restart-containerd": "systemctl restart containerd && systemctl restart kubelet",  # commande composee
}


class _Log:
    def __init__(self):
        self.msgs = []
    def warning(self, m):
        self.msgs.append(m)
    def info(self, m):
        self.msgs.append(m)


def make_fake(connect_raise=None, key_raise=False, exit_status=0, out=b"ok", errb=b"", exec_raise=None):
    rec = {"exec_cmds": [], "stdin_writes": [], "connect_kwargs": [], "connect_count": 0,
           "policy": None, "loaded": [], "get_pty": None, "closed": False}

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

    exc = {"auth": AuthenticationException, "badkey": BadHostKeyException, "ssh": SSHException}

    class _Chan:
        def recv_exit_status(self):
            return exit_status

    class _Stdin:
        def write(self, d):
            rec["stdin_writes"].append(d)   # ne doit JAMAIS etre appele (cle seule, sudo -n)
        def flush(self):
            pass

    class _Out:
        def __init__(self, data):
            self._d = data
            self.channel = _Chan()
        def read(self):
            return self._d

    class SSHClient:
        def load_system_host_keys(self, p):
            rec["loaded"].append(p)
        def set_missing_host_key_policy(self, pol):
            rec["policy"] = type(pol).__name__
        def connect(self, ip, **kw):
            rec["connect_count"] += 1
            rec["connect_kwargs"].append(kw)
            if connect_raise:
                raise exc[connect_raise]("echec connexion simulee")
        def exec_command(self, cmd, timeout=None, get_pty=False):
            rec["exec_cmds"].append(cmd)
            rec["get_pty"] = get_pty
            if exec_raise is not None:
                raise exec_raise
            return (_Stdin(), _Out(out), _Out(errb))
        def close(self):
            rec["closed"] = True

    class Ed25519Key:
        @staticmethod
        def from_private_key_file(p):
            if key_raise:
                raise OSError("cle simulee illisible")
            return object()

    fake = types.SimpleNamespace(
        SSHException=SSHException, AuthenticationException=AuthenticationException,
        BadHostKeyException=BadHostKeyException, RejectPolicy=RejectPolicy, AutoAddPolicy=AutoAddPolicy,
        SSHClient=SSHClient, Ed25519Key=Ed25519Key)
    return fake, rec


def _run(fake, op="restart-kubelet", known=KNOWN):
    return hx.run_host_op("k8s-worker1", op, ip="192.168.148.158", ssh_user="kube",
                          ssh_key_path=r"C:\fake\id", known_hosts_path=known, host_ops=HOST_OPS,
                          paramiko_mod=fake, logger=None)


class BuildInvocationTest(unittest.TestCase):
    def test_commande_sudo_non_interactive_sans_secret(self):
        inv = hx.build_sudo_invocation("systemctl restart kubelet")
        self.assertIn("sudo -n -p ''", inv)        # non interactif, invite neutralisee
        self.assertIn("bash -c", inv)
        self.assertNotIn("-S", inv)                # plus de lecture mot de passe sur stdin
        self.assertNotIn("echo", inv)
        self.assertNotIn("password", inv)


class KeyOnlyChannelTest(unittest.TestCase):

    def test_auth_par_cle_une_seule_connexion_sans_password(self):
        fake, rec = make_fake()
        ok, res = _run(fake)
        self.assertTrue(ok)
        self.assertEqual(1, rec["connect_count"])                 # une seule connexion
        kw = rec["connect_kwargs"][0]
        self.assertIn("pkey", kw)                                  # par cle
        self.assertNotIn("password", kw)                           # JAMAIS connect(password=...)
        self.assertEqual("RejectPolicy", rec["policy"])
        self.assertEqual([KNOWN], rec["loaded"])
        self.assertIs(False, rec["get_pty"])                       # pas de pseudo-terminal

    def test_commande_sudo_n_sans_mot_de_passe(self):
        fake, rec = make_fake()
        _run(fake)
        self.assertEqual(1, len(rec["exec_cmds"]))
        self.assertIn("sudo -n -p ''", rec["exec_cmds"][0])        # sudo non interactif
        self.assertNotIn("password", rec["exec_cmds"][0])

    def test_aucune_ecriture_stdin(self):
        fake, rec = make_fake()
        _run(fake)
        self.assertEqual([], rec["stdin_writes"])                  # aucun mot de passe (ni rien) sur stdin

    def test_echec_cle_aucun_repli_mot_de_passe(self):
        fake, rec = make_fake(connect_raise="auth")
        lg = _Log()
        ok, res = hx.run_host_op("k8s-worker1", "restart-kubelet", ip="192.168.148.158", ssh_user="kube",
                                 ssh_key_path=r"C:\fake\id", known_hosts_path=KNOWN, host_ops=HOST_OPS,
                                 paramiko_mod=fake, logger=lg)
        self.assertFalse(ok)
        self.assertEqual("authentification SSH par cle refusee", res)
        self.assertEqual(1, rec["connect_count"])                  # UNE tentative, pas de repli
        self.assertNotIn("password", str(rec["connect_kwargs"]))
        self.assertEqual([], rec["exec_cmds"])                     # rien d'execute

    def test_cle_absente_refuse_avant_connexion(self):
        fake, rec = make_fake(key_raise=True)
        ok, res = _run(fake)
        self.assertFalse(ok)
        self.assertIn("cle SSH introuvable", res)
        self.assertEqual(0, rec["connect_count"])                  # aucune connexion

    def test_known_hosts_absent_fail_closed(self):
        fake, rec = make_fake()
        ok, res = _run(fake, known=r"C:\inexistant\known_hosts")
        self.assertFalse(ok)
        self.assertIn("known_hosts absent", res)
        self.assertEqual(0, rec["connect_count"])

    def test_identite_hote_inconnue_rejetee(self):
        fake, rec = make_fake(connect_raise="badkey")
        ok, res = _run(fake)
        self.assertFalse(ok)
        self.assertIn("identite d'hote SSH invalide", res)
        self.assertEqual([], rec["exec_cmds"])

    def test_hote_inconnu_sshexception(self):
        fake, rec = make_fake(connect_raise="ssh")
        ok, res = _run(fake)
        self.assertFalse(ok)
        self.assertIn("hote SSH inconnu", res)

    def test_echec_sudo_code_retour(self):
        fake, rec = make_fake(exit_status=1, out=b"", errb=b"sudo: a password is required")
        ok, res = _run(fake)
        self.assertFalse(ok)                                       # rc != 0 -> echec explicite (fail-closed)

    def test_timeout_gere_sans_secret(self):
        fake, rec = make_fake(exec_raise=socket.timeout("timeout simule"))
        lg = _Log()
        ok, res = hx.run_host_op("k8s-worker1", "restart-kubelet", ip="192.168.148.158", ssh_user="kube",
                                 ssh_key_path=r"C:\fake\id", known_hosts_path=KNOWN, host_ops=HOST_OPS,
                                 paramiko_mod=fake, logger=lg)
        self.assertFalse(ok)
        self.assertEqual("echec host-exec", res)

    def test_whitelist_preservee(self):
        fake, rec = make_fake()
        ok, res = _run(fake, op="rm-rf-tout")                      # hors whitelist
        self.assertFalse(ok)
        self.assertIn("inconnu", res)
        self.assertEqual([], rec["exec_cmds"])

    def test_commande_composee_quotee(self):
        fake, rec = make_fake()
        _run(fake, op="restart-containerd")                        # contient &&
        self.assertIn("systemctl restart containerd && systemctl restart kubelet", rec["exec_cmds"][0])
        self.assertIn("sudo -n -p ''", rec["exec_cmds"][0])

    def test_paramiko_absent(self):
        ok, res = hx.run_host_op("k8s-worker1", "restart-kubelet", ip="192.168.148.158", ssh_user="kube",
                                 ssh_key_path=r"C:\fake\id", known_hosts_path=KNOWN, host_ops=HOST_OPS,
                                 paramiko_mod=None)                 # aucun SSH reel possible
        self.assertFalse(ok)
        self.assertIn("paramiko indisponible", res)


class CandidateDelegationTest(unittest.TestCase):
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

    def test_host_exec_delegue_keyonly(self):
        seg = ast.get_source_segment(self.src, self._func("host_exec"))
        self.assertIn("run_host_op", seg)
        self.assertNotIn("_ssh_pass", seg)             # plus de credential SSH
        self.assertNotIn("password", seg)
        self.assertNotIn("echo", seg)
        self.assertNotIn("exec_command", seg)

    def test_module_keyonly_sudo_n(self):
        with open(os.path.join(HERE, "helios_host_exec.py"), encoding="utf-8") as f:
            s = f.read()
        tree = ast.parse(s)
        bld = rho = None
        for n in ast.walk(tree):
            if isinstance(n, ast.FunctionDef) and n.name == "build_sudo_invocation":
                bld = ast.get_source_segment(s, n)
            if isinstance(n, ast.FunctionDef) and n.name == "run_host_op":
                rho = ast.get_source_segment(s, n)
        self.assertIn("sudo -n -p ''", bld)
        self.assertNotIn("-S", bld)                    # plus de stdin password
        self.assertNotIn("password", rho)              # aucun mot de passe
        self.assertNotIn("stdin.write", rho)           # aucune ecriture stdin
        self.assertNotIn("ssh_pass", rho)              # plus de credential SSH


if __name__ == "__main__":
    unittest.main()
