# -*- coding: utf-8 -*-
"""LOT 09.9 — tests du TRANSPORT CANDIDAT redesign (helios_host_exec_candidate.py).
Lancer : python -m unittest test_host_exec_candidate -v   (depuis scripts/helios-ops)
Paramiko SIMULÉ (aucune connexion réelle). Vérifie : op-id fermé, PLUS de bash -c / sudo -S / mot de passe,
clé seule + known_hosts/RejectPolicy, get_pty=False, refus avant connexion, erreurs sans secret."""
import os
import types
import unittest
import helios_host_exec_candidate as tx

HERE = os.path.dirname(os.path.abspath(__file__))
KNOWN = os.path.abspath(__file__)                       # fausse ancre known_hosts "présente"
HELPER = "/usr/local/sbin/helios-host-ops"
ALLOWED = {"restart-kubelet", "restart-containerd", "sync-time", "image-gc", "reboot"}


def make_fake(connect_raise=None, key_raise=False, exit_status=0, out=b"ok", errb=b""):
    rec = {"exec_cmds": [], "connect_kwargs": [], "connect_count": 0, "policy": None,
           "loaded": [], "get_pty": None}

    class SSHException(Exception): pass
    class AuthenticationException(SSHException): pass
    class BadHostKeyException(SSHException): pass
    class RejectPolicy: pass
    class AutoAddPolicy: pass
    exc = {"auth": AuthenticationException, "badkey": BadHostKeyException, "ssh": SSHException}

    class _Chan:
        def recv_exit_status(self): return exit_status

    class _Out:
        def __init__(self, d): self._d = d; self.channel = _Chan()
        def read(self): return self._d

    class SSHClient:
        def load_system_host_keys(self, p): rec["loaded"].append(p)
        def set_missing_host_key_policy(self, pol): rec["policy"] = type(pol).__name__
        def connect(self, ip, **kw):
            rec["connect_count"] += 1; rec["connect_kwargs"].append(kw)
            if connect_raise: raise exc[connect_raise]("echec")
        def exec_command(self, cmd, timeout=None, get_pty=False):
            rec["exec_cmds"].append(cmd); rec["get_pty"] = get_pty
            return (None, _Out(out), _Out(errb))
        def close(self): pass

    class Ed25519Key:
        @staticmethod
        def from_private_key_file(p):
            if key_raise: raise OSError("cle KO")
            return object()

    fake = types.SimpleNamespace(
        SSHException=SSHException, AuthenticationException=AuthenticationException,
        BadHostKeyException=BadHostKeyException, RejectPolicy=RejectPolicy, AutoAddPolicy=AutoAddPolicy,
        SSHClient=SSHClient, Ed25519Key=Ed25519Key)
    return fake, rec


def _run(fake, op="restart-kubelet", known=KNOWN):
    return tx.run_host_op("k8s-worker1", op, ip="192.168.148.158", ssh_user="kube",
                          ssh_key_path=r"C:\fake\id", known_hosts_path=known, allowed_ops=ALLOWED,
                          helper_path=HELPER, paramiko_mod=fake, logger=None)


class BuildInvocationTest(unittest.TestCase):
    def test_build_helper_sans_shell(self):
        inv = tx.build_helper_invocation(HELPER, "restart-kubelet", ALLOWED)
        self.assertEqual("sudo -n -p '' /usr/local/sbin/helios-host-ops restart-kubelet", inv)
        self.assertNotIn("bash -c", inv)
        self.assertNotIn("-S", inv)
    def test_build_rejette_op_hors_ensemble(self):
        with self.assertRaises(ValueError):
            tx.build_helper_invocation(HELPER, "restart-kubelet; id", ALLOWED)


class TransportTest(unittest.TestCase):

    def test_1_cinq_ops_vers_helper(self):
        for op in ALLOWED:
            fake, rec = make_fake()
            ok, res = _run(fake, op=op)
            self.assertTrue(ok)
            self.assertEqual(f"sudo -n -p '' /usr/local/sbin/helios-host-ops {op}", rec["exec_cmds"][0])

    def test_2_id_inconnu_refuse_avant_connexion(self):
        fake, rec = make_fake()
        ok, res = _run(fake, op="op-inconnue")
        self.assertFalse(ok)
        self.assertIn("non autorisee", res)
        self.assertEqual(0, rec["connect_count"])          # AUCUNE connexion
        self.assertEqual([], rec["exec_cmds"])

    def test_3_action_forgee_jamais_incorporee(self):
        fake, rec = make_fake()
        ok, res = _run(fake, op="reboot; rm -rf /")        # valeur forgée
        self.assertFalse(ok)
        self.assertEqual(0, rec["connect_count"])
        self.assertEqual([], rec["exec_cmds"])             # jamais transmise

    def test_4_5_pas_de_bash_c_ni_sudo_S(self):
        fake, rec = make_fake()
        _run(fake)
        self.assertNotIn("bash -c", rec["exec_cmds"][0])
        self.assertNotIn("-S", rec["exec_cmds"][0])
        self.assertNotIn("echo", rec["exec_cmds"][0])

    def test_6_aucun_mot_de_passe_ssh(self):
        fake, rec = make_fake()
        _run(fake)
        self.assertIn("pkey", rec["connect_kwargs"][0])
        self.assertNotIn("password", rec["connect_kwargs"][0])

    def test_7_8_hostkey_verifiee_sans_pty(self):
        fake, rec = make_fake()
        _run(fake)
        self.assertEqual("RejectPolicy", rec["policy"])
        self.assertEqual([KNOWN], rec["loaded"])
        self.assertIs(False, rec["get_pty"])

    def test_9_erreurs_sans_secret(self):
        for sel, needle in [("auth", "authentification SSH par cle"), ("badkey", "identite d'hote"),
                            ("ssh", "hote SSH inconnu")]:
            fake, rec = make_fake(connect_raise=sel)
            ok, res = _run(fake)
            self.assertFalse(ok)
            self.assertIn(needle, res)
            self.assertEqual([], rec["exec_cmds"])
        fake, rec = make_fake(exit_status=1)               # helper rc != 0 -> echec explicite
        ok, res = _run(fake)
        self.assertFalse(ok)

    def test_known_hosts_absent_fail_closed(self):
        fake, rec = make_fake()
        ok, res = _run(fake, known=r"C:\inexistant\known_hosts")
        self.assertFalse(ok)
        self.assertIn("known_hosts absent", res)
        self.assertEqual(0, rec["connect_count"])

    def test_cle_absente(self):
        fake, rec = make_fake(key_raise=True)
        ok, res = _run(fake)
        self.assertFalse(ok)
        self.assertIn("cle SSH introuvable", res)
        self.assertEqual(0, rec["connect_count"])

    def test_10_paramiko_absent_aucun_reseau(self):
        ok, res = tx.run_host_op("k8s-worker1", "reboot", ip="192.168.148.158", ssh_user="kube",
                                 ssh_key_path=r"C:\fake\id", known_hosts_path=KNOWN, allowed_ops=ALLOWED,
                                 helper_path=HELPER, paramiko_mod=None)
        self.assertFalse(ok)
        self.assertIn("paramiko indisponible", res)


if __name__ == "__main__":
    unittest.main()
