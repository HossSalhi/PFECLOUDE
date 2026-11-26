# -*- coding: utf-8 -*-
"""Tests du statut AUTO effectif (fonctions pures). Lancer : python -I scripts/helios-ops/test_auto_status.py"""
import importlib.util
import os
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.abspath(os.path.join(HERE, "..", "..", "infra", "helios-sot", "tools", "auto_status.py"))
spec = importlib.util.spec_from_file_location("auto_status", TOOL)
st = importlib.util.module_from_spec(spec)
spec.loader.exec_module(st)

POL = {n: {"mode": "AUTO", "restart_route": "CAMUNDA", "notifications": "EMAIL_AND_UI"}
       for n in ("k8s-worker1", "k8s-worker2", "k8s-lb2", "k8s-master")}      # .153 AUTO depuis la consigne du 17 nov. 2026
POL["k8s-monitoring"] = {"mode": "DETECT_ONLY", "restart_route": "NONE", "notifications": "UI_ONLY"}


def states(**over):
    s = {n: {"mode": "AUTO", "etat": "RUNNING", "cooldown_s": 0} for n in ("k8s-worker1", "k8s-worker2", "k8s-lb2", "k8s-master")}
    s["k8s-monitoring"] = {"mode": "DETECT_ONLY", "etat": "RUNNING", "cooldown_s": 0}
    for k, v in over.items():
        s[k].update(v)
    return s


CFG_OMBRE = {"HELIOS_VMPOWER_ENABLED": "true", "HELIOS_VMPOWER_DRYRUN": "true", "HELIOS_VMPOWER_ACTIVE_VMS": ""}
CFG_158 = {"HELIOS_VMPOWER_ENABLED": "true", "HELIOS_VMPOWER_DRYRUN": "false", "HELIOS_VMPOWER_ACTIVE_VMS": "k8s-worker1"}


def by(rows):
    return {r["vm"]: r for r in rows}


class StatutTest(unittest.TestCase):
    def test_les_quatre_VM_sont_configurees_auto_et_monitoring_en_detection_seule(self):
        r = by(st.build(POL, states(), CFG_OMBRE, {}, {}))
        for vm in ("k8s-worker1", "k8s-worker2", "k8s-lb2", "k8s-master"):
            self.assertEqual(r[vm]["niveau"], 1, vm)
            self.assertIn("mode OMBRE", r[vm]["statut"])
        self.assertEqual(r["k8s-monitoring"]["niveau"], 0)
        self.assertIn("DÉTECTION SEULE", r["k8s-monitoring"]["statut"])
        self.assertNotIn("ANOMALIE", r["k8s-monitoring"]["statut"])

    def test_master_est_armee_puis_validee_comme_les_autres_cibles(self):
        cfg = dict(CFG_158, HELIOS_VMPOWER_ACTIVE_VMS="k8s-worker1,k8s-worker2,k8s-lb2,k8s-master")
        r = by(st.build(POL, states(), cfg, {}, {}))
        self.assertEqual(r["k8s-master"]["niveau"], 2)
        self.assertIn("ARMÉE", r["k8s-master"]["statut"])
        r = by(st.build(POL, states(), cfg, {"k8s-master": ("13999", "19:05:12")}, {}))
        self.assertEqual(r["k8s-master"]["niveau"], 3)
        self.assertIn("#13999", r["k8s-master"]["statut"])

    def test_master_auto_dans_le_depot_mais_watchdog_deploye_dit_deny_restart_n_est_pas_configuree(self):
        """État de PRODUCTION du 17 nov. avant le palier 3 : le dépôt dit AUTO, le watchdog en direct dit encore DENY_RESTART."""
        r = by(st.build(POL, states(**{"k8s-master": {"mode": "DENY_RESTART"}}), CFG_OMBRE, {}, {}))
        self.assertEqual(r["k8s-master"]["niveau"], 0)
        self.assertIn("NON CONFIGURÉE", r["k8s-master"]["statut"])

    def test_monitoring_dans_active_vms_est_une_anomalie_jamais_armee(self):
        cfg = dict(CFG_158, HELIOS_VMPOWER_ACTIVE_VMS="k8s-worker1,k8s-monitoring")
        r = by(st.build(POL, states(), cfg, {"k8s-monitoring": ("4895", "13:27:26")}, {}))
        self.assertEqual(r["k8s-monitoring"]["niveau"], -1)
        self.assertIn("ANOMALIE", r["k8s-monitoring"]["statut"])
        self.assertNotIn("ARMÉE (rallumage", r["k8s-monitoring"]["statut"])
        self.assertEqual(r["k8s-worker1"]["niveau"], 2)

    def test_monitoring_detection_seule_mais_watchdog_pas_redeploye_est_signale(self):
        r = by(st.build(POL, states(**{"k8s-monitoring": {"mode": "AUTO"}}), CFG_158, {}, {}))
        self.assertEqual(r["k8s-monitoring"]["niveau"], 0)
        self.assertIn("à redéployer", r["k8s-monitoring"]["statut"])
        self.assertIn("aucun rallumage demandé", r["k8s-monitoring"]["statut"])

    def test_armee_exige_enabled_dryrun_false_et_presence_dans_active_vms(self):
        r = by(st.build(POL, states(), CFG_158, {}, {}))
        self.assertEqual(r["k8s-worker1"]["niveau"], 2)
        self.assertEqual(r["k8s-worker2"]["niveau"], 1)          # AUTO mais pas dans ACTIVE_VMS : ombre
        cfg = dict(CFG_158, HELIOS_VMPOWER_DRYRUN="true")        # dry-run : personne n'est armée
        self.assertEqual(by(st.build(POL, states(), cfg, {}, {}))["k8s-worker1"]["niveau"], 1)
        cfg = dict(CFG_158, HELIOS_VMPOWER_ENABLED="false")
        self.assertEqual(by(st.build(POL, states(), cfg, {}, {}))["k8s-worker1"]["niveau"], 1)

    def test_validee_exige_un_arret_reel_ET_etre_armee(self):
        r = by(st.build(POL, states(), CFG_158, {"k8s-worker1": ("4888", "10:43:11")}, {}))
        self.assertEqual(r["k8s-worker1"]["niveau"], 3)
        self.assertIn("#4888", r["k8s-worker1"]["statut"])
        # validée autrefois mais plus armée : on ne l'affiche pas comme validée+armée
        r = by(st.build(POL, states(), CFG_OMBRE, {"k8s-worker1": ("4888", "10:43:11")}, {}))
        self.assertEqual(r["k8s-worker1"]["niveau"], 1)

    def test_une_vm_dont_le_watchdog_ou_la_politique_ne_dit_pas_auto_n_est_jamais_armee(self):
        r = by(st.build(POL, states(**{"k8s-worker2": {"mode": "DETECT_ONLY"}}), CFG_OMBRE, {}, {}))
        self.assertEqual(r["k8s-worker2"]["niveau"], 0)
        self.assertIn("NON CONFIGURÉE", r["k8s-worker2"]["statut"])
        pol = dict(POL, **{"k8s-lb2": {"mode": "DETECT_ONLY", "restart_route": "NONE", "notifications": "UI_ONLY"}})
        self.assertEqual(by(st.build(pol, states(), CFG_OMBRE, {}, {}))["k8s-lb2"]["niveau"], 0)

    def test_le_mode_deny_restart_reste_affiche_s_il_reapparait(self):
        """Aucune VM n'est DENY_RESTART depuis le 17 nov. 2026 ; le mode est CONSERVÉ : s'il revient, il est affiché (jamais armé)."""
        pol = dict(POL, **{"k8s-lb2": {"mode": "DENY_RESTART", "restart_route": "NONE", "notifications": "EMAIL_AND_UI"}})
        r = by(st.build(pol, states(**{"k8s-lb2": {"mode": "DENY_RESTART"}}), CFG_OMBRE, {}, {}))
        self.assertEqual(r["k8s-lb2"]["niveau"], 0)
        self.assertIn("INTERDITE", r["k8s-lb2"]["statut"])
        r = by(st.build(pol, states(), CFG_OMBRE, {}, {}))            # le watchdog en direct dit encore AUTO
        self.assertIn("ANOMALIE", r["k8s-lb2"]["statut"])

    def test_cooldowns_backend_5_min_et_watchdog_240_s_sont_distincts(self):
        # 18 nov. 2026 : cooldown backend 30 -> 5 min (« go cooldown 5 min ») ; le watchdog (240 s) reste INFÉRIEUR (invariant :
        # scripts/helios-ops/test_cooldown_coherence.py).
        self.assertEqual((st.BACKEND_COOLDOWN_MIN, st.WATCHDOG_COOLDOWN_S), (5, 240))
        r = by(st.build(POL, states(**{"k8s-worker1": {"cooldown_s": 100}}), CFG_158, {}, {"k8s-worker1": 120}))
        self.assertAlmostEqual(r["k8s-worker1"]["cooldown_backend_restant_min"], 3.0, places=1)    # 5 min - 2 min
        self.assertEqual(r["k8s-worker1"]["cooldown_watchdog_restant_s"], 100)
        r = by(st.build(POL, states(), CFG_158, {}, {"k8s-worker1": 600}))                       # 10 min : cooldown écoulé (c'était 20 min restantes à 30 min)
        self.assertEqual(r["k8s-worker1"]["cooldown_backend_restant_min"], 0.0)
        r = by(st.build(POL, states(), CFG_158, {}, {"k8s-worker1": 3600}))
        self.assertEqual(r["k8s-worker1"]["cooldown_backend_restant_min"], 0.0)
        self.assertIsNone(by(st.build(POL, states(), CFG_158, {}, {}))["k8s-worker1"]["cooldown_backend_restant_min"])

    def test_liste_active_vms_avec_espaces_et_vide(self):
        self.assertEqual(st.active_set({"HELIOS_VMPOWER_ACTIVE_VMS": " k8s-worker1 , k8s-lb2 "}), {"k8s-worker1", "k8s-lb2"})
        self.assertEqual(st.active_set({"HELIOS_VMPOWER_ACTIVE_VMS": ""}), set())
        self.assertEqual(st.active_set({}), set())


if __name__ == "__main__":
    unittest.main()
