# -*- coding: utf-8 -*-
"""VMDOWN-POLICY-20261105 - la « pause » HA-01 est un DRAPEAU DE MAINTENANCE de la VM visee.

Lancer : python -m unittest test_helios_pause_maintenance -v      (depuis infra/ha01/ops)

Hors cluster : le script PowerShell est execute avec un dossier temporaire (-Dir) et des ports locaux libres ;
il ne touche NI C:\\Users\\houss\\helios-maintenance, NI le vrai agent :9999, NI aucune VM.
"""
import os
import socket
import subprocess
import tempfile
import unittest
from unittest import mock

import p4_vmstop as P

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "helios-pause.ps1")


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def run(action, vm=None, directory=None, legacy_port=None):
    cmd = ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", SCRIPT,
           "-Action", action, "-Dir", directory, "-LegacyPort", str(legacy_port or free_port()),
           "-WatchdogPort", str(free_port())]
    if vm is not None:
        cmd += ["-Vm", vm]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=90)
    return r.returncode, (r.stdout + r.stderr).strip()


class PauseMaintenanceTest(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="helios-pause-test-")
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))

    def flag(self, name):
        return os.path.join(self.tmp, name + ".flag")

    def test_pause_pose_le_drapeau_de_la_vm_visee_et_resume_le_retire(self):
        rc, out = run("pause", "162", self.tmp)
        self.assertEqual(0, rc, out)
        self.assertIn("PAUSE effective", out)
        self.assertTrue(os.path.isfile(self.flag("k8s-lb2")))
        for autre in ("k8s-worker1", "k8s-worker2", "k8s-monitoring", "k8s-master"):
            self.assertFalse(os.path.exists(self.flag(autre)), "seule la VM visee est en maintenance")
        rc, out = run("resume", "162", self.tmp)
        self.assertEqual(0, rc, out)
        self.assertFalse(os.path.exists(self.flag("k8s-lb2")))

    def test_suffixes_et_noms_exacts_sont_acceptes(self):
        for vm, name in {"160": "k8s-monitoring", "153": "k8s-master", "155": "k8s-worker2", "158": "k8s-worker1",
                         "k8s-lb2": "k8s-lb2"}.items():
            rc, out = run("pause", vm, self.tmp)
            self.assertEqual(0, rc, "%s -> %s" % (vm, out))
            self.assertTrue(os.path.isfile(self.flag(name)), vm)

    def test_noms_inconnus_alias_et_casse_sont_refuses_sans_rien_ecrire(self):
        for vm in ("lb2", "monitoring", "K8S-LB2", "999", "k8s-aiCamunda", "161", "../x"):
            rc, out = run("pause", vm, self.tmp)
            self.assertNotEqual(0, rc, vm)
            self.assertEqual([], os.listdir(self.tmp), "aucun drapeau pour %r" % vm)

    def test_vm_obligatoire_pour_pause_et_resume(self):
        self.assertNotEqual(0, run("pause", None, self.tmp)[0])
        self.assertNotEqual(0, run("resume", None, self.tmp)[0])
        self.assertEqual([], os.listdir(self.tmp))

    def test_pause_refusee_tant_que_l_agent_legacy_repond(self):
        with socket.socket() as srv:                       # simule l'agent :9999 encore actif
            srv.bind(("127.0.0.1", 0))
            srv.listen(5)
            rc, out = run("pause", "162", self.tmp, legacy_port=srv.getsockname()[1])
        self.assertNotEqual(0, rc, out)
        self.assertIn("agent legacy", out)
        self.assertFalse(os.path.exists(self.flag("k8s-lb2")), "aucun drapeau : le test d'arret ne doit pas avoir lieu")

    def test_status_liste_les_cinq_vm_et_signale_l_agent_legacy(self):
        run("pause", "158", self.tmp)
        rc, out = run("status", None, self.tmp)
        self.assertEqual(0, rc, out)
        for name in ("k8s-worker2", "k8s-worker1", "k8s-lb2", "k8s-monitoring", "k8s-master"):
            self.assertIn(name, out)
        self.assertRegex(out, r"k8s-worker1\s+MAINTENANCE")
        self.assertRegex(out, r"k8s-lb2\s+normal")
        self.assertIn("ne repond pas (attendu)", out)
        with socket.socket() as srv:
            srv.bind(("127.0.0.1", 0))
            srv.listen(5)
            rc, out = run("status", None, self.tmp, legacy_port=srv.getsockname()[1])
        self.assertEqual(0, rc)
        self.assertIn("a retirer avant tout test", out)


class HarnessWiringTest(unittest.TestCase):
    """p4_vmstop.helios() transmet la VM visee au script de pause (sinon la maintenance ne viserait personne)."""

    def test_helios_transmet_action_et_vm(self):
        completed = subprocess.CompletedProcess([], 0, stdout="ok", stderr="")
        with mock.patch.object(P.subprocess, "run", return_value=completed) as m:
            P.helios("pause", "162")
            P.helios("status")
        first, second = m.call_args_list[0].args[0], m.call_args_list[1].args[0]
        self.assertEqual(["-Action", "pause", "-Vm", "162"], first[-4:])
        self.assertEqual(["-Action", "status"], second[-2:], "status sans -Vm reste possible")

    def test_tous_les_appels_pause_resume_status_du_harnais_passent_la_vm(self):
        with open(os.path.join(HERE, "p4_vmstop.py"), encoding="utf-8") as f:
            src = f.read()
        import re
        appels = re.findall(r'helios\("(?:status|pause|resume)"[^)]*\)', src)
        self.assertGreaterEqual(len(appels), 6)
        for a in appels:
            self.assertIn(", vm)", a, a)
        self.assertNotIn(":9999 (état avant pause", src)
        self.assertNotIn("agent hôte rétabli", src)


if __name__ == "__main__":
    unittest.main()
