# -*- coding: utf-8 -*-
"""Tests de la réconciliation de politique (fonctions pures + lecteurs de fichiers du dépôt). Lancer : python -I scripts/helios-ops/test_policy_reconcile.py"""
import importlib.util
import os
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
TOOL = os.path.join(REPO, "infra", "helios-sot", "tools", "policy_reconcile.py")
spec = importlib.util.spec_from_file_location("policy_reconcile", TOOL)
pr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pr)

FINAL = dict(pr.EXPECTED)


def coherent():
    return {
        "yaml": dict(FINAL), "java": dict(FINAL), "wd_repo": dict(FINAL), "wd_deployed": dict(FINAL), "wd_live": dict(FINAL),
        "active_vms": set(pr.ARMED), "enabled": "true", "dryrun": "false",
        "backend_markers": {"policy_new": True, "watch_new": True, "master_auto": True},
        "apply_allowlist": set(pr.ARMED), "preflight_targets": set(pr.ARMED),
        "bridge": {"sha": pr.so.TARGET_BRIDGE_SHA12, "jeton": "0", "actif": "active"},
        "host": {"ecouteur_9099": 1, "ecouteur_9999": 0,
                 "taches": {"HELIOS-Watchdog": "Running", "HELIOS-Watchdog-HTTP": "Disabled", "HELIOS-NodeAutoheal": "Disabled"}},
        "pods_ready": 2, "image_digest": "d" * 64, "image_digest_expected": "d" * 64,
    }


class DeviationsTest(unittest.TestCase):
    def test_etat_definitif_est_coherent(self):
        self.assertEqual(pr.deviations(coherent()), [])

    def test_etat_reel_du_6_nov_apres_midi_est_detecte(self):
        f = coherent()
        f["wd_deployed"]["k8s-monitoring"] = "AUTO"
        f["wd_live"]["k8s-monitoring"] = "AUTO"
        f["active_vms"].add("k8s-monitoring")
        f["backend_markers"] = {"policy_new": False, "watch_new": False, "master_auto": False}
        d = pr.deviations(f)
        self.assertTrue(any("watchdog (script déployé)" in x and "k8s-monitoring" in x for x in d), d)
        self.assertTrue(any("watchdog (en direct)" in x for x in d), d)
        self.assertTrue(any("ARMÉE : INTERDIT" in x for x in d), d)
        self.assertTrue(any("VmPowerPolicy ancienne" in x for x in d), d)
        self.assertTrue(any("sans « .153 AUTO »" in x for x in d), d)
        self.assertEqual(len(d), 6)   # l'empreinte du contenu est conforme dans ce scénario (fixture)

    def test_etat_de_production_du_17_nov_avant_deploiement_153_est_detecte(self):
        """Dépôt = .153 AUTO ; production = watchdog DENY_RESTART, backend ancien, .153 non armée : TOUS ces écarts sont signalés."""
        f = coherent()
        f["wd_deployed"]["k8s-master"] = "DENY_RESTART"
        f["wd_live"]["k8s-master"] = "DENY_RESTART"
        f["active_vms"].discard("k8s-master")
        f["backend_markers"]["master_auto"] = False
        d = pr.deviations(f)
        self.assertTrue(any("watchdog (script déployé) : k8s-master = DENY_RESTART" in x for x in d), d)
        self.assertTrue(any("watchdog (en direct) : k8s-master = DENY_RESTART" in x for x in d), d)
        self.assertTrue(any("manquant ['k8s-master']" in x for x in d), d)
        self.assertTrue(any("sans « .153 AUTO »" in x for x in d), d)
        self.assertEqual(len(d), 4)

    def test_chaque_couche_est_verifiee_pour_chaque_vm(self):
        for key, _ in pr.LAYERS:
            for vm in pr.NAMES:
                f = coherent()
                f[key][vm] = "AUTO" if FINAL[vm] != "AUTO" else "DETECT_ONLY"
                self.assertEqual(len(pr.deviations(f)), 1, (key, vm))

    def test_couche_absente_ou_non_etablie_est_un_ecart_fail_closed(self):
        f = coherent()
        f["wd_live"] = {}
        self.assertEqual(len(pr.deviations(f)), 5)
        f = coherent()
        f["active_vms"] = None
        self.assertTrue(any("illisible" in x for x in pr.deviations(f)))
        f = coherent()
        f["backend_markers"] = None
        self.assertTrue(any("illisible" in x for x in pr.deviations(f)))

    def test_armement_exact_quatre_vm_jamais_160(self):
        f = coherent()
        f["active_vms"].add("k8s-monitoring")
        self.assertTrue(any("INTERDIT" in x for x in pr.deviations(f)), "k8s-monitoring")
        for manque in ("k8s-worker2", "k8s-master"):      # chacune des QUATRE VM AUTO doit être armée
            f = coherent()
            f["active_vms"].discard(manque)
            self.assertTrue(any("manquant" in x and manque in x for x in pr.deviations(f)), manque)
        f = coherent()
        f["dryrun"] = "true"
        self.assertTrue(any("armement" in x for x in pr.deviations(f)))

    def test_outils_hote_et_bridge(self):
        f = coherent()
        f["apply_allowlist"].add("k8s-monitoring")
        f["preflight_targets"] = {"k8s-worker1"}
        f["host"]["ecouteur_9999"] = 1
        f["host"]["taches"]["HELIOS-Watchdog-HTTP"] = "Ready"
        f["bridge"]["jeton"] = "1"
        f["pods_ready"] = 1
        self.assertEqual(len(pr.deviations(f)), 6)


class EmpreinteDImageTest(unittest.TestCase):
    def test_contenu_different_de_l_empreinte_verifiee_est_un_ecart(self):
        f = coherent()
        f["image_digest"] = "e" * 64
        d = pr.deviations(f)
        self.assertEqual(len(d), 1)
        self.assertIn("n'est pas celui qui a été contrôlé", d[0])

    def test_empreinte_non_etablie_est_un_ecart_fail_closed(self):
        f = coherent()
        f["image_digest"] = None
        self.assertTrue(any("non établie" in x for x in pr.deviations(f)))

    def test_le_message_d_ecart_nomme_l_etiquette_deployee(self):
        f = coherent()
        f["image_tag"], f["image_digest"] = "0.2.165-disk-e2e", "e" * 64
        d = pr.deviations(f)
        self.assertEqual(len(d), 1)
        self.assertIn("0.2.165-disk-e2e", d[0])

    def test_aucune_empreinte_attendue_pour_l_image_deployee_est_un_ecart_fail_closed(self):
        """Avant 17 nov. un fichier d'empreinte illisible ne produisait AUCUN écart (fail-open) : désormais il est signalé."""
        f = coherent()
        f["image_tag"], f["image_digest_expected"] = "0.9.9-sans-fichier", None
        d = pr.deviations(f)
        self.assertEqual(len(d), 1, d)
        self.assertIn("aucune empreinte vérifiée", d[0])
        self.assertIn("IMAGE-0.9.9-sans-fichier.sha256", d[0])
        self.assertIn("--write-expected 0.9.9-sans-fichier", d[0])

    def test_une_empreinte_attendue_par_image_et_lisible(self):
        self.assertTrue(pr.image_digest_path("0.2.165-disk-e2e").endswith("IMAGE-0.2.165-disk-e2e.sha256"))
        # image EN PRODUCTION au 17 nov. 2026 : empreinte du build du 14 nov. = empreinte du code en marche (contrôlé en lecture seule)
        d = pr.expected_image_digest("0.2.165-disk-e2e")
        self.assertIsNotNone(d)
        self.assertRegex(d, r"^[0-9a-f]{64}$")
        # l'image précédente reste lisible (historique) ; une étiquette inconnue ou vide n'a PAS d'empreinte
        self.assertRegex(pr.expected_image_digest("0.2.164-vm3auto") or "", r"^[0-9a-f]{64}$")
        self.assertIsNone(pr.expected_image_digest("0.9.9-inexistante"))
        self.assertIsNone(pr.expected_image_digest(None))
        self.assertIsNone(pr.expected_image_digest(""))
        self.assertIsNone(pr.expected_image_digest("0.2.165-disk-e2e", path=os.path.join(REPO, "inexistant.sha256")))

    def test_le_fichier_ecrit_par_image_digest_est_lu_par_policy_reconcile(self):
        """Contrat entre les deux outils : `--write-expected` produit exactement ce que `expected_image_digest` sait relire."""
        import tempfile
        spec2 = importlib.util.spec_from_file_location("image_digest", os.path.join(REPO, "infra", "helios-sot", "tools", "image_digest.py"))
        idg = importlib.util.module_from_spec(spec2)
        spec2.loader.exec_module(idg)
        with tempfile.TemporaryDirectory() as repo:
            ok, path, msg = idg.write_expected_file("0.2.166-master153", "ab" * 32, 305, "x", repo=repo, date="2026-11-17")
            self.assertTrue(ok, msg)
            self.assertEqual(pr.expected_image_digest("0.2.166-master153", path=path), "ab" * 32)
            self.assertIsNone(pr.expected_image_digest("0.2.165-disk-e2e", path=path), "une autre étiquette ne lit pas cette ligne")


class LecteursDuDepotTest(unittest.TestCase):
    """Les lecteurs lisent les VRAIS fichiers du dépôt : le dépôt doit déjà dire la politique définitive."""

    def test_le_depot_dit_la_politique_definitive(self):
        j = pr.java_modes(os.path.join(REPO, "backend", "backend", "src", "main", "java", "com", "orange", "pfe", "backend", "service",
                                       "vmpower", "VmPowerPolicy.java"))
        w = pr.watchdog_modes(os.path.join(REPO, "infra", "helios-sot", "watchdog", "helios-vm-watchdog.py"))
        y = {n: e.get("mode") for n, e in pr.n3.policy_entries().items()}
        self.assertEqual((j, w, y), (FINAL, FINAL, FINAL))

    def test_le_script_d_activation_n_accepte_que_les_quatre_cibles(self):
        self.assertEqual(pr.apply_allowlist(os.path.join(REPO, "infra", "helios-sot", "tools", "apply_vm4_auto.sh")), set(pr.ARMED))

    def test_les_marqueurs_de_version_existent_dans_les_sources_du_depot(self):
        base = os.path.join(REPO, "backend", "backend", "src", "main", "java", "com", "orange", "pfe", "backend", "service", "vmpower")
        with open(os.path.join(base, "VmPowerPolicy.java"), encoding="utf-8") as fh:
            policy = fh.read()
        with open(os.path.join(base, "VmPowerWatch.java"), encoding="utf-8") as fh:
            watch = fh.read()
        self.assertIn(pr.POLICY_MARKER.decode(), policy)
        self.assertIn(pr.WATCH_MARKER.decode(), watch)
        self.assertIn('POLICY_REVISION = "%s"' % pr.MASTER_MARKER.decode(), policy, "révision de la table = marqueur de l'outil")


if __name__ == "__main__":
    unittest.main()
