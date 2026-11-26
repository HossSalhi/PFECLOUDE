# -*- coding: utf-8 -*-
"""VMDOWN-POLICY-20261105 - le Bridge CIBLE (infra/helios-sot/bridge/bridge.py) n'a AUCUNE capacite de mise sous
tension et ne bruite plus les notifications de la famille VM.

Lancer : python -m unittest test_bridge_no_poweron -v      (depuis scripts/helios-ops)

Le Bridge n'est JAMAIS importe ni demarre : verification par AST + execution isolee des seules fonctions pures
de filtrage. Aucun reseau, aucune VM.
"""
import ast
import os
import re
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
SOT = os.path.join(REPO, "infra", "helios-sot")
TARGET = os.path.join(SOT, "bridge", "bridge.py")
BASELINE_DIR = os.path.join(SOT, "baseline", "bridge")
GENERATOR = os.path.join(SOT, "tools", "make_target_bridge.py")


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


class BridgeSansMiseSousTensionTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.src = read(TARGET)
        cls.tree = ast.parse(cls.src)

    def _func(self, name):
        for n in ast.walk(self.tree):
            if isinstance(n, ast.FunctionDef) and n.name == name:
                return n
        return None

    def _string_constants(self):
        return [n.value for n in ast.walk(self.tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)]

    # ---- aucune capacite de power-on ----------------------------------------------------------
    def test_la_fonction_rallumer_n_existe_plus(self):
        self.assertIsNone(self._func("rallumer"))

    def test_aucune_variable_ni_constante_du_watchdog(self):
        names = {n.id for n in ast.walk(self.tree) if isinstance(n, ast.Name)}
        self.assertNotIn("JETON_WATCHDOG", names)
        self.assertNotIn("WATCHDOG", names)
        for s in self._string_constants():
            self.assertNotIn(":9099", s)
            self.assertNotIn("/restart/", s)
            self.assertNotIn("vmrun", s.lower())
            self.assertNotEqual("HELIOS_WATCHDOG_TOKEN", s)
            self.assertNotEqual("HELIOS_WATCHDOG", s)
            self.assertNotEqual("__watchdog__", s)

    def test_aucun_appel_http_sortant_vers_l_hyperviseur(self):
        # le Bridge peut utiliser urllib pour d'autres usages, mais jamais avec une URL contenant /restart
        for n in ast.walk(self.tree):
            if isinstance(n, ast.Call) and getattr(n.func, "attr", "") in ("urlopen", "Request"):
                text = ast.get_source_segment(self.src, n) or ""
                self.assertNotIn("restart", text.lower(), text[:120])

    def test_catalogue_refuse_les_trois_noms_de_power_on(self):
        cat = ast.get_source_segment(self.src, self._func("catalogue"))
        self.assertIn('"__poweron_interdit__"', cat)
        self.assertNotIn("__watchdog__", cat)
        i = cat.index('"RESTART_VM", "RESTART_NODE_VM", "VM_DOWN"')
        self.assertIn('"__poweron_interdit__"', cat[i:i + 200])

    def test_node_not_ready_n_est_plus_mappe_a_un_redemarrage(self):
        mapping = None
        for n in ast.walk(self.tree):
            if isinstance(n, ast.Assign) and any(getattr(t, "id", "") == "ACTION_PAR_TYPE_MESURE" for t in n.targets):
                mapping = {k.value: v.value for k, v in zip(n.value.keys, n.value.values)
                           if isinstance(k, ast.Constant) and isinstance(v, ast.Constant)}
        self.assertIsNotNone(mapping)
        self.assertEqual("ESCALATE_NODE_NOT_READY", mapping["NODE_NOT_READY"])
        for typ, action in mapping.items():
            self.assertNotIn("RESTART_NODE_VM", action, typ)
            self.assertNotIn("POWER", action.upper(), typ)

    def test_le_handler_action_refuse_sans_commande_ni_reseau(self):
        i = self.src.index('if h == "__poweron_interdit__":')
        bloc = self.src[i:i + 900]
        self.assertIn("POWER_ON_INTERDIT", bloc)
        self.assertIn("return self.repondre(200", bloc)
        self.assertLess(i, self.src.index("executer_kubectl(cmd)", i), "le refus precede toute execution")

    # ---- filtre /notifier -----------------------------------------------------------------------
    def _filtre(self):
        wanted = {"_RE_SAFE_STOP_VM", "_CIBLES_VM_POLITIQUE", "_RE_MONITORING_NON_FIABLE", "notification_supprimee"}
        nodes = []
        for n in self.tree.body:
            names = set()
            if isinstance(n, ast.FunctionDef):
                names = {n.name}
            elif isinstance(n, ast.Assign):
                names = {t.id for t in n.targets if isinstance(t, ast.Name)}
            if names & wanted:
                nodes.append(n)
        module = ast.Module(body=nodes, type_ignores=[])
        ns = {"re": re}
        exec(compile(module, "<filtre>", "exec"), ns)
        return ns["notification_supprimee"]

    def test_safe_stop_des_types_vm_sur_les_vm_de_la_politique_est_supprime(self):
        f = self._filtre()
        for typ, cible in [("NODE_NOT_READY", "k8s-worker2"), ("NODE_NOT_READY", "k8s-worker1"),
                           ("NODE_NOT_READY", "k8s-master"), ("TARGET_DOWN", "lb2"), ("TARGET_DOWN", "monitoring"),
                           ("VM_DOWN", "k8s-lb2"), ("VM_UNREACHABLE", "k8s-monitoring")]:
            motif = f("[HELIOS] SAFE_STOP %s sur %s" % (typ, cible), "corps")
            self.assertEqual("SAFE_STOP_VM:" + typ, motif, (typ, cible))

    def test_les_autres_notifications_ne_sont_pas_touchees(self):
        f = self._filtre()
        for sujet in ["[HELIOS] SAFE_STOP POD_NOT_READY_PERSISTENT sur ingress-ngin",
                      "[HELIOS] SAFE_STOP POD_CRASH_LOOP sur speaker-stdmk",
                      "[HELIOS] SAFE_STOP NETWORK_PARTITION sur backend",
                      "[HELIOS] SAFE_STOP NODE_TIME_DRIFT sur k8s-master",
                      "[HELIOS] SAFE_STOP NODE_LOAD_SATURATION sur k8s-master",
                      "[HELIOS] SAFE_STOP NO_INCIDENT sur aucun",
                      "[HELIOS] Echec de remediation - POD_CRASH_LOOP sur pfe/backend",
                      # un TARGET_DOWN d'une cible hors politique reste notifie
                      "[HELIOS] SAFE_STOP TARGET_DOWN sur iacamunda"]:
            self.assertIsNone(f(sujet, "corps ordinaire"), sujet)

    def test_monitoring_non_fiable_est_supprime_ou_qu_il_apparaisse(self):
        f = self._filtre()
        self.assertEqual("MONITORING_NON_FIABLE",
                         f("[HELIOS] SAFE_STOP NO_INCIDENT sur aucun", "motif=MONITORING_NON_FIABLE"))
        self.assertEqual("MONITORING_NON_FIABLE", f("sujet", "etat: monitoringFiable=false"))
        self.assertEqual("MONITORING_NON_FIABLE", f("sujet", "monitoringFiable : False"))
        self.assertIsNone(f("[HELIOS] SAFE_STOP NO_INCIDENT sur aucun", "monitoringFiable=true"))

    def test_le_handler_notifier_filtre_avant_d_envoyer_et_repond_envoye_true(self):
        i = self.src.index('if self.path == "/notifier":')
        bloc = self.src[i:i + 1700]
        filtre = bloc.index("notification_supprimee(sujet, corps)")
        envoi = bloc.index("envoyer_notification(sujet, corps)")
        self.assertLess(filtre, envoi, "le filtre precede l'envoi")
        self.assertIn('"envoye": True, "voie": "SUPPRIMEE_DETECTION_SEULE"', bloc,
                      "reponse envoye=true pour ne declencher aucune reprise cote Camunda")

    # ---- derive de la baseline, sans autre modification -------------------------------------------
    def test_la_cible_est_exactement_la_baseline_transformee_par_le_generateur(self):
        if not os.path.isfile(os.path.join(BASELINE_DIR, "bridge.py")):
            self.skipTest("baseline de production absente (non versionnee : elle contient des identifiants herites)")
        with tempfile.TemporaryDirectory() as out:
            subprocess.run([sys.executable, GENERATOR, BASELINE_DIR, out], check=True, capture_output=True)
            self.assertEqual(read(os.path.join(out, "bridge.py")), self.src,
                             "le Bridge cible a derive : regenerer avec tools/make_target_bridge.py")
            self.assertEqual(read(os.path.join(out, "telemetrie.py")),
                             read(os.path.join(SOT, "bridge", "telemetrie.py")))

    def test_aucun_identifiant_en_dur_dans_la_cible(self):
        # litteral « secret » = affectation d'un mot de passe/jeton a une chaine SANS espace (les concatenations de
        # commandes comme TOKEN=" + info[...] ne sont pas des secrets)
        self.assertIsNone(re.search(
            r"(?i)\b(pass|password|passwd|secret|token)\w*\s*=\s*[\"'][A-Za-z0-9_\-/+=!@#$%^&*]{8,}[\"']", self.src))


LEGACY_DEPLOY = os.path.join(REPO, "camunda-direct", "bridge", "deploy_bridge.py")
CHECK_SH = os.path.join(SOT, "tools", "check_bridge_no_poweron.sh")


class AncienneCopieNonDeployableTest(unittest.TestCase):
    """camunda-direct/bridge/bridge.py est l'ANCIEN Bridge (rallumer() -> watchdog :9099, mot de passe en clair) : il ne doit
    pas pouvoir etre deploye (defaut constate le 6 nov. 2026 : deploy_bridge.py l'aurait installe sur .161)."""

    def test_l_installateur_historique_s_arrete_avant_toute_connexion(self):
        p = subprocess.run([sys.executable, "-I", LEGACY_DEPLOY], capture_output=True, text=True, timeout=30)
        self.assertEqual(3, p.returncode, p.stderr)
        self.assertIn("OBSOLETE", p.stderr)
        self.assertNotIn("ModuleNotFoundError", p.stderr)     # paramiko n'est meme pas importe

    def test_la_garde_precede_tout_import_et_toute_connexion(self):
        src = read(LEGACY_DEPLOY)
        garde = src.index("raise SystemExit(3)")
        for motif in ("import paramiko", "SSHClient(", ".connect(", "sftp.put(", "open_sftp("):
            for m in re.finditer(re.escape(motif), src):
                self.assertGreater(m.start(), garde, motif)

    def test_aucun_autre_script_ne_deploie_l_ancien_bridge(self):
        suspects = []
        for racine, dossiers, fichiers in os.walk(REPO):
            dossiers[:] = [d for d in dossiers if d not in (".git", "node_modules", "target", "results", "__pycache__", ".angular")]
            for f in fichiers:
                if not f.endswith((".py", ".sh", ".ps1")) or f in ("deploy_bridge.py", "test_bridge_no_poweron.py"):
                    continue
                chemin = os.path.join(racine, f)
                try:
                    texte = read(chemin)
                except (OSError, UnicodeDecodeError):
                    continue
                if re.search(r"camunda-direct[/\\]+bridge[/\\]+bridge\.py", texte) and re.search(r"\b(scp|sftp|put\()", texte):
                    suspects.append(os.path.relpath(chemin, REPO))
        self.assertEqual([], suspects)

    def test_le_controle_ne_source_pas_bridge_env_et_cache_le_jeton(self):
        sh = read(CHECK_SH)
        self.assertNotRegex(sh, r"(?m)^\s*\.\s+/etc/helios/bridge\.env")      # plus de « source » du fichier de secrets
        self.assertNotIn("set -a", sh)
        self.assertIn("curl -s -m 10 -K -", sh)                                # jeton par l'entree standard de curl
        self.assertNotRegex(sh, r'-H\s+"X-Helios-Token:\s*\$')                 # jamais en argument de ligne de commande
        self.assertIn("--static-only", sh)                                     # mode strictement lecture seule disponible
        self.assertIn("CHECK_BRIDGE_NO_POWERON", sh)                           # le trafic de test est etiquete


if __name__ == "__main__":
    unittest.main()
