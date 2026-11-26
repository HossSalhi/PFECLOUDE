# -*- coding: utf-8 -*-
"""Tests de l'outil d'observation du mode ombre (fonctions pures : aucun réseau, aucun kubectl, aucun PowerShell).

Lancer :  python -I -m unittest scripts/helios-ops/test_shadow_observe.py -v
"""
import contextlib
import copy
import importlib.util
import io
import json
import os
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.abspath(os.path.join(HERE, "..", "..", "infra", "helios-sot", "tools", "shadow_observe.py"))
spec = importlib.util.spec_from_file_location("shadow_observe", TOOL)
so = importlib.util.module_from_spec(spec)
spec.loader.exec_module(so)


def good(ts="2026-11-06T09:00:00+01:00", count=100.0, total=50.0):
    return {
        "ts": ts, "leader": "backend-a-1",
        "config": {"HELIOS_VMPOWER_ENABLED": "true", "HELIOS_VMPOWER_DRYRUN": "true", "HELIOS_VMPOWER_ACTIVE_VMS": ""},
        "pods": [{"name": "backend-a-1", "node": "k8s-worker2", "restarts": 0, "log_error": 0, "cycle_count": count,
                  "cycle_sum": total},
                 {"name": "backend-b-2", "node": "k8s-worker1", "restarts": 0, "log_error": 0, "cycle_count": count,
                  "cycle_sum": 1.0}],
        "db": {"restart_vm_depuis_ombre": 0, "incidents_vm_depuis_ombre": 0, "incidents_vm_ouverts": 0,
               "actions_vm_bloquees": 0, "lignes_mode_ombre": 0, "lignes_non_autorise": 0},
        "vms": {"k8s-worker1": {"mode": "AUTO", "etat": "RUNNING"}, "k8s-worker2": {"mode": "AUTO", "etat": "RUNNING"},
                "k8s-lb2": {"mode": "AUTO", "etat": "RUNNING"}, "k8s-monitoring": {"mode": "DETECT_ONLY", "etat": "RUNNING"},
                "k8s-master": {"mode": "AUTO", "etat": "RUNNING"}},      # .153 AUTO depuis la consigne du 17 nov. 2026
        "hote": {"ram_dispo_mo": 2000, "disque_c_libre_pct": 7.1, "ecouteur_9099": 1, "ecouteur_9999": 0,
                 "post_restart_dans_journal": 0},
        "bridge": {"sha": so.TARGET_BRIDGE_SHA12, "jeton": "0", "actif": "active"},
        "erreurs": [],
    }


def good_armed():
    r = good()
    r["observation"] = {"mode": "armee", "depuis": "2026-11-06 18:00:00"}
    r["config"] = {"HELIOS_VMPOWER_ENABLED": "true", "HELIOS_VMPOWER_DRYRUN": "false",
                   "HELIOS_VMPOWER_ACTIVE_VMS": "k8s-worker1,k8s-worker2,k8s-lb2,k8s-master"}
    r["db"]["restart_vm_interdits"] = 0
    r["hote"]["vmrun_start_depuis_fenetre"] = 0
    return r


class ArmedVerdictTest(unittest.TestCase):
    """Observation de la configuration FINALE (rallumage réel actif pour .155 .158 .162) pendant 24 h, sans arrêt de VM."""

    def test_releve_nominal_arme_est_ok(self):
        self.assertEqual(so.verdict(good_armed()), ("OK", [], []))

    def test_une_action_restart_vm_dans_la_fenetre_est_un_fail_a_analyser(self):
        r = good_armed()
        r["db"]["restart_vm_depuis_ombre"] = 1
        v, fails, _ = so.verdict(r)
        self.assertEqual(v, "FAIL")
        self.assertTrue(any("ANALYSE REQUISE" in f for f in fails), fails)

    def test_un_vmrun_start_dans_la_fenetre_est_un_fail(self):
        r = good_armed()
        r["hote"]["vmrun_start_depuis_fenetre"] = 1
        self.assertEqual(so.verdict(r)[0], "FAIL")

    def test_une_action_vers_160_est_un_fail_interdit(self):
        r = good_armed()
        r["db"]["restart_vm_interdits"] = 1
        v, fails, _ = so.verdict(r)
        self.assertEqual(v, "FAIL")
        self.assertTrue(any("INTERDIT" in f for f in fails))

    def test_configuration_d_armement_non_conforme_est_un_fail(self):
        for cfg in ({"HELIOS_VMPOWER_ACTIVE_VMS": "k8s-worker1,k8s-worker2,k8s-lb2,k8s-master,k8s-monitoring"},   # .160 armée : interdit
                    {"HELIOS_VMPOWER_ACTIVE_VMS": "k8s-worker1,k8s-lb2,k8s-master"},                     # .155 oubliée
                    {"HELIOS_VMPOWER_ACTIVE_VMS": "k8s-worker1,k8s-worker2,k8s-lb2"},                    # .153 oubliée (palier 3 non fait)
                    {"HELIOS_VMPOWER_DRYRUN": "true"}, {"HELIOS_VMPOWER_ENABLED": "false"}):
            r = good_armed()
            r["config"].update(cfg)
            v, fails, _ = so.verdict(r)
            self.assertEqual(v, "FAIL", cfg)
            self.assertTrue(any("armement NON conforme" in f for f in fails), cfg)

    def test_160_doit_etre_detection_seule_cote_watchdog_et_153_est_une_cible_auto(self):
        r = good_armed()
        r["vms"]["k8s-monitoring"]["mode"] = "AUTO"       # watchdog pas redéployé : la détection seule n'est pas appliquée
        self.assertEqual(so.verdict(r)[0], "FAIL")
        r = good_armed()
        r["vms"]["k8s-master"]["mode"] = "DENY_RESTART"   # watchdog DÉPLOYÉ pas encore à jour (avant le palier 3) : signalé
        v, _, warns = so.verdict(r)
        self.assertEqual(v, "WARN")
        self.assertTrue(any("k8s-master" in w and "AUTO" in w for w in warns), warns)

    def test_journal_du_watchdog_illisible_est_un_warn_jamais_un_zero_silencieux(self):
        r = good_armed()
        r["hote"]["vmrun_start_depuis_fenetre"] = None
        v, _, warns = so.verdict(r)
        self.assertEqual(v, "WARN")
        self.assertTrue(any("journal du watchdog illisible" in w for w in warns))

    def test_ecouteur_9999_ou_bridge_non_conforme_restent_des_fail(self):
        r = good_armed()
        r["hote"]["ecouteur_9999"] = 1
        self.assertEqual(so.verdict(r)[0], "FAIL")
        r = good_armed()
        r["bridge"]["jeton"] = "1"
        self.assertEqual(so.verdict(r)[0], "FAIL")

    def test_panne_reelle_pendant_l_observation_est_signalee_comme_evenement(self):
        r = good_armed()
        r["vms"]["k8s-worker2"]["etat"] = "POWERED_OFF"
        evs = so.events(None, r)
        self.assertTrue(any("panne réelle" in e for e in evs), evs)


class CompteurDemarragesTest(unittest.TestCase):
    def test_compte_seulement_les_vmrun_start_apres_le_debut_de_la_fenetre(self):
        import json as _json
        import tempfile as _tmp
        lignes = [
            {"event": "READY", "ts": "2026-11-06T14:00:00.000Z"},
            {"event": "VMRUN_START", "vm": "k8s-worker2", "ts": "2026-11-06T12:40:30.238Z"},   # avant la fenêtre
            {"event": "VMRUN_START", "vm": "k8s-worker1", "ts": "2026-11-06T17:30:00.100Z"},   # dans la fenêtre
            {"decision": "REJECTED", "http": 401, "path": "/restart/k8s-lb2", "ts": "2026-11-06T17:31:00.000Z"},  # refus : pas un démarrage
            {"event": "START_DONE", "vm": "k8s-worker1", "ts": "2026-11-06T17:30:00.500Z"},
        ]
        with _tmp.NamedTemporaryFile("w", suffix=".log", delete=False, encoding="utf-8") as fh:
            fh.write("\n".join(_json.dumps(x) for x in lignes) + "\nligne illisible\n")
            path = fh.name
        try:
            # fenêtre locale : on la fixe en UTC+0 pour un résultat indépendant du fuseau de la machine de test
            import datetime as _dt
            since_local = (_dt.datetime(2026, 11, 6, 15, 0, 0, tzinfo=_dt.timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S"))
            self.assertEqual(so.count_watchdog_starts(since_local, path), 1)
        finally:
            os.unlink(path)

    def test_journal_absent_donne_none_pas_zero(self):
        self.assertIsNone(so.count_watchdog_starts("2026-11-06 10:00:00", r"C:\chemin\inexistant.log"))


class VerdictTest(unittest.TestCase):
    def test_releve_nominal_est_ok(self):
        v, fails, warns = so.verdict(good())
        self.assertEqual((v, fails, warns), ("OK", [], []))

    def test_une_action_restart_vm_depuis_le_mode_ombre_est_un_fail(self):
        r = good()
        r["db"]["restart_vm_depuis_ombre"] = 1
        self.assertEqual(so.verdict(r)[0], "FAIL")

    def test_un_post_restart_recu_par_le_watchdog_est_un_fail(self):
        r = good()
        r["hote"]["post_restart_dans_journal"] = 1
        v, fails, _ = so.verdict(r)
        self.assertEqual(v, "FAIL")
        self.assertTrue(any("POST /restart" in f for f in fails))

    def test_action_restart_vm_bloquee_est_un_fail(self):
        r = good()
        r["db"]["actions_vm_bloquees"] = 1
        self.assertEqual(so.verdict(r)[0], "FAIL")

    def test_master_pas_auto_cote_watchdog_est_un_warn_en_mode_ombre(self):
        """.153 est une cible AUTO (17 nov. 2026) : un watchdog qui la dit encore DENY_RESTART est signalé, pas bloquant en ombre."""
        r = good()
        r["vms"]["k8s-master"]["mode"] = "DENY_RESTART"
        v, fails, warns = so.verdict(r)
        self.assertEqual(v, "WARN")
        self.assertEqual(fails, [])
        self.assertTrue(any("k8s-master" in w and "AUTO" in w for w in warns), warns)

    def test_cible_qui_nest_plus_auto_est_un_warn(self):
        r = good()
        r["vms"]["k8s-lb2"]["mode"] = "DETECT_ONLY"
        self.assertEqual(so.verdict(r)[0], "WARN")

    def test_monitoring_encore_auto_cote_watchdog_est_un_warn_a_corriger_par_redeploiement(self):
        r = good()
        r["vms"]["k8s-monitoring"]["mode"] = "AUTO"       # watchdog de l'hôte pas encore redéployé
        v, _, warns = so.verdict(r)
        self.assertEqual(v, "WARN")
        self.assertTrue(any("k8s-monitoring" in w and "DETECT_ONLY" in w for w in warns), warns)

    def test_watchdog_qui_necoute_plus_est_un_fail(self):
        r = good()
        r["hote"]["ecouteur_9099"] = 0
        self.assertEqual(so.verdict(r)[0], "FAIL")

    def test_ancien_agent_9999_de_retour_est_un_fail(self):
        r = good()
        r["hote"]["ecouteur_9999"] = 1
        self.assertEqual(so.verdict(r)[0], "FAIL")

    def test_bridge_avec_jeton_ou_mauvaise_version_est_un_fail(self):
        r = good()
        r["bridge"]["jeton"] = "1"
        self.assertEqual(so.verdict(r)[0], "FAIL")
        r = good()
        r["bridge"]["sha"] = "000000000000"
        self.assertEqual(so.verdict(r)[0], "FAIL")

    def test_configuration_sortie_du_mode_ombre_est_signalee(self):
        r = good()
        r["config"]["HELIOS_VMPOWER_DRYRUN"] = "false"
        v, _, warns = so.verdict(r)
        self.assertEqual(v, "WARN")
        self.assertTrue(any("mode ombre" in w for w in warns))
        r = good()
        r["config"]["HELIOS_VMPOWER_ACTIVE_VMS"] = "k8s-worker1"
        self.assertEqual(so.verdict(r)[0], "WARN")

    def test_ram_proche_de_la_garde_du_watchdog_est_un_warn(self):
        r = good()
        r["hote"]["ram_dispo_mo"] = so.RAM_GUARD_MB + 100
        self.assertEqual(so.verdict(r)[0], "WARN")

    def test_pod_redemarre_ou_ligne_error_sont_des_warn(self):
        r = good()
        r["pods"][0]["restarts"] = 2
        r["pods"][1]["log_error"] = 3
        v, _, warns = so.verdict(r)
        self.assertEqual(v, "WARN")
        self.assertEqual(len(warns), 2)

    def test_incident_de_perimetre_vm_est_un_warn_a_expliquer(self):
        r = good()
        r["db"]["incidents_vm_depuis_ombre"] = 1
        self.assertEqual(so.verdict(r)[0], "WARN")


class EventsTest(unittest.TestCase):
    def test_rien_a_signaler(self):
        self.assertEqual(so.events(good(), good(ts="2026-11-06T09:15:00+01:00")), [])

    def test_une_vm_qui_nest_plus_running_arrete_lobservation(self):
        r = good()
        r["vms"]["k8s-worker1"]["etat"] = "POWERED_OFF"
        evs = so.events(good(), r)
        self.assertTrue(any("k8s-worker1" in e and "POWERED_OFF" in e for e in evs))

    def test_nouvelle_ligne_mode_ombre_est_un_evenement(self):
        r = good()
        r["db"]["lignes_mode_ombre"] = 1
        self.assertTrue(any("MODE OMBRE" in e for e in so.events(good(), r)))

    def test_changement_de_leader_et_redemarrage_de_pod(self):
        r = good()
        r["leader"] = "backend-b-2"
        r["pods"][0]["restarts"] = 1
        evs = so.events(good(), r)
        self.assertTrue(any("changement de leader" in e for e in evs))
        self.assertTrue(any("a redémarré" in e for e in evs))

    def test_un_fail_est_toujours_un_evenement(self):
        r = good()
        r["verdict"] = "FAIL"
        r["fails"] = ["x"]
        self.assertTrue(so.events(None, r))

    def test_un_releve_invalide_ne_declenche_rien(self):
        r = good()
        r["vms"]["k8s-worker1"]["etat"] = "UNKNOWN"
        r["invalide"] = "sonde cassée"
        self.assertEqual(so.events(good(), r), [])


class CadenceTest(unittest.TestCase):
    def test_periode_du_leader_et_du_non_leader(self):
        a = good(ts="2026-11-06T09:00:00+01:00", count=100.0, total=50.0)
        b = good(ts="2026-11-06T09:15:00+01:00", count=156.0, total=50.0 + 56 * 0.6)
        c = so.cadence(a, b)
        self.assertEqual(c["backend-a-1"]["cycles"], 56)
        self.assertAlmostEqual(c["backend-a-1"]["periode_s"], 900 / 56, places=2)
        self.assertAlmostEqual(c["backend-a-1"]["duree_moy_s"], 0.6, places=2)

    def test_sans_releve_precedent_ou_compteur_qui_recule_rien(self):
        self.assertEqual(so.cadence(None, good()), {})
        a = good(count=200.0)
        b = good(ts="2026-11-06T09:15:00+01:00", count=5.0)  # pod redémarré : compteur remis à zéro
        self.assertEqual(so.cadence(a, b), {})


class RobustesseEtRapportTest(unittest.TestCase):
    def test_un_releve_qui_plante_devient_un_releve_invalide_et_ne_tue_pas_la_boucle(self):
        original = so.snapshot
        try:
            so.snapshot = lambda prev: (_ for _ in ()).throw(RuntimeError("kubectl absent"))
            r = so.safe_snapshot(None)
        finally:
            so.snapshot = original
        self.assertEqual(r["verdict"], "WARN")
        self.assertIn("kubectl absent", r["invalide"])

    def test_le_rapport_ecarte_les_releves_invalides_et_conclut(self):
        old_dir, old_file = so.OUT_DIR, so.OUT_FILE
        with tempfile.TemporaryDirectory() as d:
            so.OUT_DIR, so.OUT_FILE = d, os.path.join(d, "o.jsonl")
            try:
                bad = copy.deepcopy(good(ts="2026-11-06T08:00:00+01:00"))
                bad["verdict"], bad["fails"], bad["warns"] = "FAIL", ["faux"], []
                bad["invalide"] = "sonde v1"
                ok1 = good(ts="2026-11-06T09:00:00+01:00")
                ok2 = good(ts="2026-11-06T09:15:00+01:00", count=156.0, total=83.6)
                for r in (ok1, ok2):
                    r["verdict"], r["fails"], r["warns"] = so.verdict(r)
                ok2["cadence"] = so.cadence(ok1, ok2)
                for r in (bad, ok1, ok2):
                    so.save(r)
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf):
                    so.report()
                out = buf.getvalue()
            finally:
                so.OUT_DIR, so.OUT_FILE = old_dir, old_file
        self.assertIn("2 relevé(s) valide(s)", out)
        self.assertIn("écarté", out)
        self.assertIn("mode ombre respecté", out)
        self.assertNotIn("✗", out)
        # 56 cycles en 900 s => 900 / 56 = 16,07 s (15 s de délai fixe + durée du travail), pas une valeur arrondie à 15
        self.assertIn("une exécution toutes les 16.07 s", out)


if __name__ == "__main__":
    unittest.main()
