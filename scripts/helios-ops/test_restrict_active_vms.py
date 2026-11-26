# -*- coding: utf-8 -*-
"""Tests du désarmement ciblé (restrict_active_vms.py) avec un FAUX kubectl : aucun cluster n'est touché.
Lancer : python -I scripts/helios-ops/test_restrict_active_vms.py"""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
TOOL = os.path.join(REPO, "infra", "helios-sot", "tools", "restrict_active_vms.py")
spec = importlib.util.spec_from_file_location("restrict_active_vms", TOOL)
ra = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ra)

FAKE = r'''
import json, os, sys
st_path = os.environ["FAKE_STATE"]
st = json.load(open(st_path, encoding="utf-8"))
a = sys.argv[1:]
def save():
    json.dump(st, open(st_path, "w", encoding="utf-8"))
st["calls"].append(" ".join(a))
if a[:2] == ["get", "configmap"]:
    print(json.dumps({"metadata": {"name": "backend-config", "resourceVersion": "1"}, "data": st["cm"]}))
elif a[:2] == ["get", "deployment"]:
    print(json.dumps({"spec": {"replicas": 2, "strategy": {"type": "RollingUpdate", "rollingUpdate": {"maxSurge": 0, "maxUnavailable": 1}}}}))
elif a[0] == "patch":
    patch = json.loads(a[a.index("-p") + 1])
    if not st.get("patch_is_noop"):
        st["cm"].update(patch["data"])
elif a[:2] == ["rollout", "restart"]:
    st["pod_env"] = dict(st["cm"])          # les pods relisent la ConfigMap au (re)démarrage
    st["restarted"] = True
elif a[:2] == ["rollout", "status"]:
    print("deployment \"backend\" successfully rolled out")
elif a[:2] == ["get", "pods"]:
    print("backend-aaa\nbackend-bbb")
elif a[0] == "exec":
    print(st["pod_env"][a[-1]])
else:
    sys.stderr.write("commande inattendue: " + " ".join(a)); sys.exit(2)
save()
'''

CM = {"HELIOS_VMPOWER_ENABLED": "true", "HELIOS_VMPOWER_DRYRUN": "false",
      "HELIOS_VMPOWER_ACTIVE_VMS": "k8s-worker1,k8s-worker2,k8s-lb2,k8s-monitoring", "AUTRE_CLE": "x"}


class Env:
    def __init__(self, cm=None, **extra):
        self.tmp = tempfile.mkdtemp(prefix="restrict-test-")
        self.state = os.path.join(self.tmp, "state.json")
        self.fake = os.path.join(self.tmp, "fake_kubectl.py")
        with open(self.fake, "w", encoding="utf-8") as fh:
            fh.write(FAKE)
        cm = dict(cm or CM)
        with open(self.state, "w", encoding="utf-8") as fh:
            json.dump({"cm": cm, "pod_env": dict(cm), "calls": [], **extra}, fh)
        self.env = dict(os.environ, FAKE_STATE=self.state, KUBECTL_CMD=json.dumps([sys.executable, self.fake]))
        self.backups = os.path.join(self.tmp, "bk")

    def run(self, *args):
        p = subprocess.run([sys.executable, "-I", TOOL, *args, "--backup-dir", self.backups], capture_output=True, text=True,
                           encoding="utf-8", env=self.env, timeout=60)
        return p.returncode, p.stdout + p.stderr

    def st(self):
        with open(self.state, encoding="utf-8") as fh:
            return json.load(fh)


def writes(calls):
    return [c for c in calls if c.startswith(("patch", "rollout restart"))]


class PlanTest(unittest.TestCase):
    def test_retire_un_nom_et_conserve_l_ordre(self):
        self.assertEqual(ra.plan("k8s-worker1,k8s-worker2,k8s-lb2,k8s-monitoring", ["k8s-monitoring"]), ("k8s-worker1,k8s-worker2,k8s-lb2", []))
        self.assertEqual(ra.plan("k8s-worker1, k8s-worker2 ,k8s-lb2", ["k8s-worker2"]), ("k8s-worker1,k8s-lb2", []))

    def test_n_ajoute_jamais_un_nom(self):
        new, _ = ra.plan("k8s-worker1", ["k8s-lb2"])
        self.assertEqual(new, "k8s-worker1")
        new, _ = ra.plan("", ["k8s-monitoring"], allow_empty=True)
        self.assertEqual(new, "")

    def test_nom_hors_table_ou_alias_refuse(self):
        for bad in ("monitoring", "lb2", "K8S-MONITORING", "192.168.148.160", "k8s-aiCamunda"):
            _, errors = ra.plan("k8s-worker1,k8s-monitoring", [bad])
            self.assertTrue(any("hors de la table fermée" in e for e in errors), bad)

    def test_resultat_vide_refuse_sauf_option(self):
        _, errors = ra.plan("k8s-monitoring", ["k8s-monitoring"])
        self.assertTrue(any("VIDE" in e for e in errors))
        self.assertEqual(ra.plan("k8s-monitoring", ["k8s-monitoring"], allow_empty=True), ("", []))


class CliTest(unittest.TestCase):
    def test_dry_run_n_ecrit_rien_et_montre_le_diff_exact(self):
        e = Env()
        rc, out = e.run("--remove", "k8s-monitoring")
        self.assertEqual(rc, 0, out)
        self.assertIn("LECTURE SEULE", out)
        self.assertIn('-  HELIOS_VMPOWER_ACTIVE_VMS: "k8s-worker1,k8s-worker2,k8s-lb2,k8s-monitoring"', out)
        self.assertIn('+  HELIOS_VMPOWER_ACTIVE_VMS: "k8s-worker1,k8s-worker2,k8s-lb2"', out)
        self.assertIn('HELIOS_VMPOWER_DRYRUN: "false"', out)           # contexte inchangé
        self.assertIn("maxSurge=0 maxUnavailable=1", out)
        self.assertIn("DRY-RUN terminé : rien n'a été écrit", out)
        self.assertIn("kubectl -n pfe patch configmap backend-config --type merge", out)   # retour arrière affiché
        self.assertEqual(writes(e.st()["calls"]), [])
        self.assertEqual(e.st()["cm"], CM)
        self.assertFalse(os.path.exists(e.backups))

    def test_apply_ne_change_qu_une_cle_sauvegarde_puis_redemarre_et_verifie_chaque_pod(self):
        e = Env()
        rc, out = e.run("--remove", "k8s-monitoring", "--apply")
        self.assertEqual(rc, 0, out)
        st = e.st()
        self.assertEqual(st["cm"], dict(CM, HELIOS_VMPOWER_ACTIVE_VMS="k8s-worker1,k8s-worker2,k8s-lb2"))   # UNE seule clé a changé
        calls = st["calls"]
        i_patch = next(i for i, c in enumerate(calls) if c.startswith("patch"))
        i_restart = next(i for i, c in enumerate(calls) if c.startswith("rollout restart"))
        self.assertLess(i_patch, i_restart)                                            # patch AVANT redémarrage
        self.assertEqual(len([c for c in calls if c.startswith("patch")]), 1)          # un seul correctif
        self.assertIn("backend-aaa : HELIOS_VMPOWER_ACTIVE_VMS = 'k8s-worker1,k8s-worker2,k8s-lb2'  ✓", out)
        self.assertIn("désarmement effectif dans tous les pods", out)
        files = os.listdir(e.backups)
        self.assertEqual(len(files), 1)
        with open(os.path.join(e.backups, files[0]), encoding="utf-8") as fh:
            bak = json.load(fh)
        self.assertEqual(bak["keys"]["HELIOS_VMPOWER_ACTIVE_VMS"], CM["HELIOS_VMPOWER_ACTIVE_VMS"])
        self.assertEqual(sorted(bak["keys"]), sorted(["HELIOS_VMPOWER_ENABLED", "HELIOS_VMPOWER_DRYRUN", "HELIOS_VMPOWER_ACTIVE_VMS"]))
        self.assertNotIn("AUTRE_CLE", json.dumps(bak))                                  # la sauvegarde ne contient que les 3 clés

    def test_ne_reactive_jamais_dryrun_ni_enabled(self):
        # configuration en MODE OMBRE (DRYRUN=true) : retirer un nom ne doit PAS la faire basculer en réel
        cm = dict(CM, HELIOS_VMPOWER_DRYRUN="true")
        e = Env(cm)
        rc, out = e.run("--remove", "k8s-monitoring", "--apply")
        self.assertEqual(rc, 0, out)
        after = e.st()["cm"]
        self.assertEqual(after["HELIOS_VMPOWER_DRYRUN"], "true")
        self.assertEqual(after["HELIOS_VMPOWER_ENABLED"], "true")

    def test_nom_inconnu_refuse_sans_rien_ecrire(self):
        e = Env()
        rc, out = e.run("--remove", "monitoring", "--apply")
        self.assertEqual(rc, 1)
        self.assertIn("REFUS", out)
        self.assertEqual(writes(e.st()["calls"]), [])

    def test_retrait_sans_effet_est_un_succes_sans_ecriture(self):
        e = Env(dict(CM, HELIOS_VMPOWER_ACTIVE_VMS="k8s-worker1,k8s-worker2,k8s-lb2"))
        rc, out = e.run("--remove", "k8s-monitoring", "--apply")
        self.assertEqual(rc, 0, out)
        self.assertIn("Rien à faire", out)
        self.assertEqual(writes(e.st()["calls"]), [])

    def test_resultat_vide_refuse_puis_accepte_avec_l_option(self):
        e = Env(dict(CM, HELIOS_VMPOWER_ACTIVE_VMS="k8s-monitoring"))
        rc, out = e.run("--remove", "k8s-monitoring", "--apply")
        self.assertEqual(rc, 1)
        self.assertEqual(writes(e.st()["calls"]), [])
        rc, out = e.run("--remove", "k8s-monitoring", "--apply", "--allow-empty")
        self.assertEqual(rc, 0, out)
        self.assertEqual(e.st()["cm"]["HELIOS_VMPOWER_ACTIVE_VMS"], "")

    def test_si_le_correctif_n_est_pas_relu_aucun_redemarrage(self):
        e = Env(patch_is_noop=True)
        rc, out = e.run("--remove", "k8s-monitoring", "--apply")
        self.assertEqual(rc, 1)
        self.assertIn("AUCUN redémarrage lancé", out)
        self.assertEqual([c for c in e.st()["calls"] if c.startswith("rollout restart")], [])


if __name__ == "__main__":
    unittest.main()
