# -*- coding: utf-8 -*-
"""Cohérence du COOLDOWN de redémarrage entre toutes les couches (18 nov. 2026 : 30 -> 5 min, « go cooldown 5 min » de l'utilisateur).
Lancer : python -I scripts/helios-ops/test_cooldown_coherence.py   (aucun réseau ; lit des fichiers du dépôt)

Invariants verrouillés :
  1. politique (YAML thresholds.restart_cooldown_minutes) == Java (VmPowerPolicy.RESTART_COOLDOWN) == auto_status == fenêtre du pré-vol ;
  2. le cooldown du BACKEND est >= celui du WATCHDOG (HELIOS_COOLDOWN_S, 240 s) : sinon le backend demanderait un démarrage que le watchdog
     refuserait (COOLDOWN_ACTIVE) ;
  3. la révision de politique (POLICY_REVISION Java) == marqueur attendu par policy_reconcile.py : une image à 30 min est détectée ;
  4. la fenêtre de recherche de la dernière action (VmPowerWatch) = max(cooldown, budget du rallumage direct) : jamais plus courte que le budget ;
  5. le texte de l'interface ne promet plus « 30 min » pour le cooldown de redémarrage d'une VM."""
import importlib.util
import os
import re
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
TOOLS = os.path.join(REPO, "infra", "helios-sot", "tools")
JAVA = os.path.join(REPO, "backend", "backend", "src", "main", "java", "com", "orange", "pfe", "backend", "service", "vmpower", "VmPowerPolicy.java")
WATCH_JAVA = os.path.join(os.path.dirname(JAVA), "VmPowerWatch.java")
WATCHDOG = os.path.join(REPO, "infra", "helios-sot", "watchdog", "helios-vm-watchdog.py")
FRONT = os.path.join(REPO, "frontend", "src", "app", "core", "config", "remediation-scenarios.ts")


def load(nom):
    spec = importlib.util.spec_from_file_location(nom, os.path.join(TOOLS, nom + ".py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


n3 = load("n3_preflight")
auto_status = load("auto_status")
reconcile = load("policy_reconcile")


def lire(chemin):
    with open(chemin, encoding="utf-8") as fh:
        return fh.read()


def cooldown_java_minutes():
    m = re.search(r"RESTART_COOLDOWN\s*=\s*Duration\.ofMinutes\((\d+)\)", lire(JAVA))
    assert m, "RESTART_COOLDOWN introuvable dans VmPowerPolicy.java"
    return int(m.group(1))


def cooldown_watchdog_secondes():
    m = re.search(r'_env_float\(env,\s*"HELIOS_COOLDOWN_S",\s*(\d+(?:\.\d+)?)\)', lire(WATCHDOG))
    assert m, "défaut de HELIOS_COOLDOWN_S introuvable dans le watchdog"
    return float(m.group(1))


class CooldownTest(unittest.TestCase):
    def test_la_valeur_demandee_par_l_utilisateur_est_5_minutes(self):
        self.assertEqual(n3.restart_cooldown_minutes(), 5)

    def test_yaml_java_auto_status_et_preflight_disent_la_meme_valeur(self):
        yaml_min = n3.restart_cooldown_minutes()
        self.assertEqual(cooldown_java_minutes(), yaml_min, "VmPowerPolicy.RESTART_COOLDOWN != YAML")
        self.assertEqual(auto_status.BACKEND_COOLDOWN_MIN, yaml_min, "auto_status.BACKEND_COOLDOWN_MIN != YAML")

    def test_le_cooldown_du_backend_n_est_jamais_plus_court_que_celui_du_watchdog(self):
        backend_s = n3.restart_cooldown_minutes() * 60
        self.assertGreaterEqual(backend_s, cooldown_watchdog_secondes(),
                                "cooldown backend < cooldown watchdog : le backend demanderait un démarrage refusé (COOLDOWN_ACTIVE)")
        self.assertEqual(auto_status.WATCHDOG_COOLDOWN_S, cooldown_watchdog_secondes(), "auto_status.WATCHDOG_COOLDOWN_S != watchdog")

    def test_la_revision_de_politique_du_java_est_le_marqueur_attendu_par_policy_reconcile(self):
        m = re.search(r'POLICY_REVISION\s*=\s*"([^"]+)"', lire(JAVA))
        self.assertTrue(m, "POLICY_REVISION introuvable")
        self.assertEqual(m.group(1).encode(), reconcile.MASTER_MARKER)
        self.assertNotEqual(reconcile.MASTER_MARKER, b"2026-11-17-master-auto", "l'ancienne révision (cooldown 30 min) ne doit plus être acceptée")

    def test_le_test_java_de_la_table_attend_la_meme_revision_que_policy_reconcile(self):
        # VmPowerPolicyTest.table_exacte_5_cibles_dans_l_ordre_du_yaml fige POLICY_REVISION : une révision changée sans ce test casse le
        # build Maven ET le lanceur Java ; ce contrôle Python l'attrape sans attendre la porte mémoire (oubli constaté le 18 nov. 2026).
        test_java = os.path.join(REPO, "backend", "backend", "src", "test", "java", "com", "orange", "pfe", "backend", "service", "vmpower", "VmPowerPolicyTest.java")
        m = re.search(r'assertEquals\("([^"]+)",\s*VmPowerPolicy\.POLICY_REVISION', lire(test_java))
        self.assertTrue(m, "assertEquals(<révision>, VmPowerPolicy.POLICY_REVISION …) introuvable dans VmPowerPolicyTest")
        self.assertEqual(m.group(1).encode(), reconcile.MASTER_MARKER)

    def test_une_valeur_illisible_retombe_sur_la_plus_prudente(self):
        self.assertEqual(n3.restart_cooldown_minutes(os.path.join(REPO, "inexistant.yaml")), 30)

    def test_la_valeur_est_lue_meme_avec_commentaire_et_indentation(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "p.yaml")
            with open(p, "w", encoding="utf-8") as fh:
                fh.write("thresholds:\n  # commentaire\n    restart_cooldown_minutes: 12   # une valeur de test\n")
            self.assertEqual(n3.restart_cooldown_minutes(p), 12)

    def test_la_fenetre_de_recherche_de_la_derniere_action_n_est_jamais_plus_courte_que_le_budget_du_rallumage_direct(self):
        # VmPowerWatch.msSinceLastRestart interroge les actions RESTART_VM sur une fenêtre. Elle valait le cooldown seul (30 min > budget direct
        # de 6 min) ; à 5 min elle serait plus courte que le budget et la route DIRECTE (dormante) verrait « aucune action » trop tôt.
        texte = lire(WATCH_JAVA)
        self.assertRegex(texte, r"long windowMs = Math\.max\(VmPowerPolicy\.RESTART_COOLDOWN\.toMillis\(\),\s*Math\.max\(1, props\.getDirectVerifyBudgetMinutes\(\)\) \* 60_000L\);")
        self.assertRegex(texte, r"Instant\.ofEpochMilli\(nowMs - windowMs\)")
        self.assertNotRegex(texte, r"Instant\.ofEpochMilli\(nowMs - VmPowerPolicy\.RESTART_COOLDOWN\.toMillis\(\)\)")

    def test_l_interface_ne_promet_plus_30_min_pour_le_cooldown_de_redemarrage(self):
        texte = lire(FRONT)
        self.assertNotRegex(texte, r"cooldown 30 min")
        self.assertNotRegex(texte, r"hors cooldown \(30 min\)")


if __name__ == "__main__":
    unittest.main()
