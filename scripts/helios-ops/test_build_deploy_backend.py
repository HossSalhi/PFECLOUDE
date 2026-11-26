# -*- coding: utf-8 -*-
"""Tests de la verification de scripts/helios-ops/build-deploy-backend.sh avec de FAUX kubectl / curl / ssh / scp / sleep / mvnw.

Rien n'est construit, rien n'est deploye, aucun cluster n'est contacte : HOME est un dossier temporaire (le vrai
~/.docker/config.json n'est jamais touche) et BACKEND_DIR un faux projet. On verifie surtout :
  - le FAUX ECHEC du 6 nov. 2026 (NodePort externe en erreur alors que les 2 pods sont prets et sains) n'entraine plus de retour arriere ;
  - une vraie panne (sante interne en erreur, rollout bloque) declenche le retour arriere APRES la capture des preuves.

Lancer : python -m unittest test_build_deploy_backend -v      (depuis scripts/helios-ops ; Git Bash requis)
"""
import json
import os
import shutil
import subprocess
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "build-deploy-backend.sh")
BASH = shutil.which("bash")

FAKE_KUBECTL = r'''#!/usr/bin/env bash
S="$FAKE_SCENARIO"
ST="$FAKE_STATE"
args="$*"
echo "kubectl $args" >> "$FAKE_LOG"
get() { python -c "import json,os; d=json.load(open(os.environ['FAKE_SCENARIO'])); print(d.get('$1',''))"; }
case "$args" in
  *"get deploy backend"*"containers[0].image"*)  cat "$ST" 2>/dev/null || echo "pfe-backend:old" ;;
  *"set image"*)
      img="${args#*backend=}"; img="${img%% *}"; echo "$img" > "$ST"; echo "deployment.apps/backend image updated" ;;
  *"get deploy backend"*"readyReplicas"*)        get ready ;;
  *"get deploy backend"*"updatedReplicas"*)      get updated ;;
  *"get deploy backend"*"availableReplicas"*)    get available ;;
  *"get pods"*"go-template"*)                    img="$(cat "$ST" 2>/dev/null || echo pfe-backend:old)"; printf '%s backend-a\n%s backend-b\n' "$img" "$img" ;;
  *"get pods"*"jsonpath"*)                       printf 'backend-a\nbackend-b\n' ;;
  *"exec"*"backend-a"*)                          get health_a ;;
  *"exec"*"backend-b"*)                          get health_b ;;
  *"rollout status"*)                            echo "deployment \"backend\" successfully rolled out" ;;
  *"logs"*)                                      echo "journal factice du pod" ;;
  *)                                             echo "(sortie factice) $args" ;;
esac
'''


class DeployScriptTest(unittest.TestCase):

    def setUp(self):
        if not BASH:
            self.skipTest("bash introuvable")
        self.tmp = tempfile.mkdtemp(prefix="helios-deploy-test-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.bin = os.path.join(self.tmp, "bin")
        self.home = os.path.join(self.tmp, "home")
        self.backend = os.path.join(self.tmp, "backend")
        for d in (self.bin, self.home, self.backend):
            os.makedirs(d)
        self.log = os.path.join(self.tmp, "calls.log")
        self.state = os.path.join(self.tmp, "image.txt")
        self.scenario = os.path.join(self.tmp, "scenario.json")
        self.diag_root = os.path.join(self.tmp, "diag")
        self.fake("kubectl", FAKE_KUBECTL)
        self.fake("curl", '#!/usr/bin/env bash\necho "curl $*" >> "$FAKE_LOG"\nprintf "%s" "${FAKE_NODEPORT_CODE:-403}"\n')
        self.fake("sleep", '#!/usr/bin/env bash\nexit 0\n')
        self.fake("scp", '#!/usr/bin/env bash\necho "scp $*" >> "$FAKE_LOG"\n')
        self.fake("ssh", '#!/usr/bin/env bash\necho "ssh $*" >> "$FAKE_LOG"\necho "unpacking docker.io/library/pfe-backend:test (sha256:00)...done"\n')
        with open(os.path.join(self.backend, "mvnw"), "w", newline="\n") as f:
            f.write('#!/usr/bin/env bash\nmkdir -p target && echo tar > target/jib-image.tar\necho "mvnw $*" >> "$FAKE_LOG"\n')
        # faux ~/.docker/config.json avec credsStore : doit etre mis de cote PUIS restaure dans HOME (jamais le vrai)
        os.makedirs(os.path.join(self.home, ".docker"))
        with open(os.path.join(self.home, ".docker", "config.json"), "w") as f:
            f.write('{"credsStore": "desktop"}')

    def fake(self, name, body):
        path = os.path.join(self.bin, name)
        with open(path, "w", newline="\n") as f:
            f.write(body)
        os.chmod(path, 0o755)

    def run_script(self, scenario, nodeport="403"):
        with open(self.scenario, "w") as f:
            json.dump(scenario, f)
        env = dict(os.environ)
        env.update({
            "PATH": self.bin + os.pathsep + env.get("PATH", ""),
            "HOME": self.home, "SUDO_PASS": "x", "TAG": "9.9.9-test", "BACKEND_DIR": self.backend,
            "FAKE_SCENARIO": self.scenario, "FAKE_STATE": self.state, "FAKE_LOG": self.log,
            "FAKE_NODEPORT_CODE": nodeport, "DIAG_DIR": self.diag_root,
        })
        r = subprocess.run([BASH, SCRIPT], capture_output=True, text=True, env=env, timeout=180, encoding="utf-8", errors="replace")
        calls = ""
        if os.path.exists(self.log):
            with open(self.log, encoding="utf-8") as f:
                calls = f.read()
        return r.returncode, r.stdout + r.stderr, calls

    OK = {"ready": "2", "updated": "2", "available": "2", "health_a": "200", "health_b": "200"}

    def test_succes_sans_retour_arriere(self):
        rc, out, calls = self.run_script(dict(self.OK))
        self.assertEqual(0, rc, out)
        self.assertIn("Déploiement pfe-backend:9.9.9-test OK", out)
        self.assertEqual(1, calls.count("set image"), "une seule bascule : aucun retour arriere")
        self.assertFalse(os.path.exists(self.diag_root), "pas de diagnostic en cas de succes")

    def test_faux_echec_du_nodeport_externe_ne_provoque_plus_de_retour_arriere(self):
        # Cas du 6 nov. 2026 : 2 pods prets et sains, mais la requete externe de 5 s sur le NodePort est en erreur (000).
        rc, out, calls = self.run_script(dict(self.OK), nodeport="000")
        self.assertEqual(0, rc, out)
        self.assertIn("NodePort externe", out)
        self.assertIn("informatif", out)
        self.assertEqual(1, calls.count("set image"))

    def test_sante_interne_en_erreur_capture_les_preuves_puis_revient_en_arriere(self):
        sc = dict(self.OK)
        sc["health_b"] = "503"
        rc, out, calls = self.run_script(sc)
        self.assertEqual(1, rc, out)
        self.assertIn("santé interne de backend-b", out)
        self.assertEqual(2, calls.count("set image"), "bascule puis retour arriere")
        self.assertIn("backend=pfe-backend:old", calls.splitlines()[-4:] and calls)
        # preuves capturees AVANT le retour arriere
        diags = os.listdir(self.diag_root)
        self.assertEqual(1, len(diags))
        files = os.listdir(os.path.join(self.diag_root, diags[0]))
        self.assertIn("etat.txt", files)
        self.assertIn("backend-a.log", files)
        self.assertIn("backend-b.log", files)
        first_logs = calls.index("logs")
        last_set_image = calls.rindex("set image")
        self.assertLess(first_logs, last_set_image, "les journaux sont lus AVANT le retour arriere")

    def test_rollout_bloque_declenche_le_retour_arriere_avec_le_motif(self):
        sc = dict(self.OK)
        sc["ready"] = "1"
        rc, out, calls = self.run_script(sc)
        self.assertEqual(1, rc, out)
        self.assertIn("rollout non terminé en 4 min", out)
        self.assertEqual(2, calls.count("set image"))

    def test_le_vrai_fichier_docker_de_l_utilisateur_n_est_jamais_touche_et_le_faux_est_restaure(self):
        rc, out, _ = self.run_script(dict(self.OK))
        self.assertEqual(0, rc, out)
        cfg = os.path.join(self.home, ".docker", "config.json")
        self.assertTrue(os.path.isfile(cfg), "config.json restaure apres le build")
        self.assertFalse(os.path.exists(cfg + ".helios.bak"))


if __name__ == "__main__":
    unittest.main()
