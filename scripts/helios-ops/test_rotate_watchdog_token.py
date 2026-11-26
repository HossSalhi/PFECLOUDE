# -*- coding: utf-8 -*-
"""Tests de infra/helios-sot/tools/rotate_watchdog_token.py avec un FAUX kubectl et un fichier de jeton temporaire.

Aucun vrai secret, aucun vrai cluster : le fichier hote est un fichier temporaire, le « secret » vit dans un JSON temporaire.
Verifie surtout que le jeton (ancien ou nouveau) n'est JAMAIS imprime et que l'ancien jeton est restaure en cas d'echec.

Lancer : python -m unittest test_rotate_watchdog_token -v      (depuis scripts/helios-ops)
"""
import base64
import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
SCRIPT = os.path.join(REPO, "infra", "helios-sot", "tools", "rotate_watchdog_token.py")

FAKE_KUBECTL = r'''
import base64, json, os, sys
state_path = os.environ["FAKE_KUBECTL_STATE"]
args = sys.argv[1:]
if args[:1] == ["-n"]:
    args = args[2:]
with open(state_path, encoding="utf-8") as f:
    state = json.load(f)
log = state.setdefault("calls", [])
log.append(args[0] + " " + (args[1] if len(args) > 1 else ""))
def save():
    with open(state_path, "w", encoding="utf-8") as f:
        json.dump(state, f)
cmd = args[0]
if cmd == "get":
    save()
    sys.stdout.write(base64.b64encode(state["secret"].encode()).decode())
elif cmd == "patch":
    if state.get("fail_patch") and not state.get("patch_failed_once"):
        state["patch_failed_once"] = True
        save()
        sys.stderr.write("forbidden")
        sys.exit(1)
    patch = json.loads(args[args.index("-p") + 1])
    state["secret"] = patch["stringData"]["REMEDIATION_MASTER_WATCHDOG_TOKEN"]
    save()
elif cmd == "rollout":
    save()
    print("deployment successfully rolled out")
else:
    save()
'''


class RotationTest(unittest.TestCase):
    OLD = "a" * 40

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="helios-rot-test-")
        self.token_file = os.path.join(self.tmp, "token")
        self.state = os.path.join(self.tmp, "state.json")
        self.fake = os.path.join(self.tmp, "fake_kubectl.py")
        with open(self.fake, "w", encoding="utf-8") as f:
            f.write(FAKE_KUBECTL)
        with open(self.token_file, "w", encoding="utf-8", newline="") as f:
            f.write(self.OLD)
        self.write_state(secret=self.OLD)

    def write_state(self, **kw):
        with open(self.state, "w", encoding="utf-8") as f:
            json.dump(kw, f)

    def read_state(self):
        with open(self.state, encoding="utf-8") as f:
            return json.load(f)

    def run_script(self, *args):
        env = dict(os.environ)
        env["HELIOS_WATCHDOG_TOKEN_FILE"] = self.token_file
        env["HELIOS_KUBECTL"] = json.dumps([sys.executable, self.fake])
        env["FAKE_KUBECTL_STATE"] = self.state
        r = subprocess.run([sys.executable, "-I", SCRIPT, *args], capture_output=True, text=True, env=env, timeout=60)
        return r.returncode, r.stdout + r.stderr

    def token(self):
        with open(self.token_file, encoding="utf-8") as f:
            return f.read()

    def test_dry_run_ne_lit_ni_n_ecrit_aucun_secret(self):
        rc, out = self.run_script()
        self.assertEqual(0, rc)
        self.assertIn("DRY-RUN", out)
        self.assertEqual(self.OLD, self.token())
        self.assertNotIn("calls", self.read_state(), "dry-run : kubectl n'est jamais appele")

    def test_rotation_remplace_le_jeton_dans_le_fichier_et_le_secret_sans_jamais_l_imprimer(self):
        rc, out = self.run_script("--apply")
        self.assertEqual(0, rc, out)
        new = self.token()
        self.assertEqual(40, len(new))
        self.assertNotEqual(self.OLD, new)
        self.assertEqual(new, self.read_state()["secret"], "fichier hote == secret du backend")
        self.assertNotIn(new, out)
        self.assertNotIn(self.OLD, out)
        self.assertIn("identiques=True", out)
        self.assertIn("different de l'ancien=True", out)
        backups = [n for n in os.listdir(self.tmp) if n.startswith("token.bak-")]
        self.assertEqual(1, len(backups), "ancien fichier sauvegarde dans le meme dossier")
        with open(os.path.join(self.tmp, backups[0]), encoding="utf-8") as f:
            self.assertEqual(self.OLD, f.read())
        self.assertIn("rollout restart deployment/backend", out, "rappel : les pods lisent le jeton au demarrage")

    def test_restart_backend_relance_le_deploiement_et_attend(self):
        rc, out = self.run_script("--apply", "--restart-backend")
        self.assertEqual(0, rc, out)
        calls = self.read_state()["calls"]
        self.assertTrue(any(c.startswith("rollout restart") for c in calls), calls)
        self.assertTrue(any(c.startswith("rollout status") for c in calls), calls)

    def test_echec_du_patch_restaure_l_ancien_jeton_partout(self):
        self.write_state(secret=self.OLD, fail_patch=True)
        rc, out = self.run_script("--apply")
        self.assertEqual(1, rc, out)
        self.assertEqual(self.OLD, self.token(), "fichier hote restaure")
        self.assertEqual(self.OLD, self.read_state()["secret"], "secret inchange / restaure")
        self.assertIn("ancien jeton restaure", out)
        self.assertNotIn(self.OLD, out)

    def test_la_source_n_imprime_jamais_de_valeur_de_jeton(self):
        with open(SCRIPT, encoding="utf-8") as f:
            src = f.read()
        for ligne in src.splitlines():
            if "print(" in ligne:
                self.assertNotRegex(ligne, r"\b(new|old_file|old_secret|after_file|after_secret)\s*[,)]", ligne)


if __name__ == "__main__":
    unittest.main()
