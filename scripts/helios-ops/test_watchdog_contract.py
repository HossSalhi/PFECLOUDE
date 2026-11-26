# -*- coding: utf-8 -*-
"""VMDOWN-POLICY-20261105 - le watchdog REEL produit exactement le contrat golden (infra/helios-sot/contract/).

Lancer : python -m unittest test_watchdog_contract -v      (depuis scripts/helios-ops)

Si ce test echoue apres un changement VOLONTAIRE du watchdog : regenerer avec
`python gen_watchdog_golden.py --write`, relire le diff, puis adapter le client Java (WatchdogClientTest rejoue les
memes fichiers). Un ecart involontaire est une regression du contrat.
"""
import os
import unittest

import gen_watchdog_golden as gen
import test_watchdog_vm_policy as base


class ContractTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.built = gen.build_all()

    def test_chaque_scenario_correspond_exactement_au_fichier_golden(self):
        for name, golden in sorted(self.built.items()):
            path = os.path.join(gen.GOLDEN_DIR, name + ".json")
            self.assertTrue(os.path.isfile(path), "fichier golden manquant: " + name)
            with open(path, encoding="utf-8") as f:
                self.assertEqual(f.read(), gen.dump(golden), "ecart de contrat: " + name)

    def test_aucun_fichier_golden_orphelin(self):
        on_disk = {n[:-5] for n in os.listdir(gen.GOLDEN_DIR) if n.endswith(".json")}
        self.assertEqual(set(), on_disk - set(self.built))

    def test_aucun_secret_ni_chemin_local_dans_les_golden(self):
        for name in os.listdir(gen.GOLDEN_DIR):
            with open(os.path.join(gen.GOLDEN_DIR, name), encoding="utf-8") as f:
                text = f.read()
            self.assertNotIn(base.TOKEN, text, name)
            self.assertNotIn("mauvais-jeton", text, name)
            self.assertNotIn("helios-wd-test-", text, name)  # dossier temporaire des tests
            self.assertNotIn("C:\\\\Users", text, name)

    def test_les_scenarios_couvrent_tous_les_statuts_du_contrat(self):
        statuses = {(g["response"]["status"], g["response"]["body"].get("status")) for g in self.built.values()}
        for expected in [(200, "STARTED"), (202, "STARTING"), (200, "ALREADY_RUNNING"), (200, "ALREADY_STARTING"),
                         (200, "ALREADY_EXECUTED"), (403, "DETECTION_ONLY"), (403, "NOT_AUTHORIZED"),
                         (404, "UNKNOWN_VM"), (400, "INVALID_VM_NAME"), (400, "INCIDENT_REQUIRED"),
                         (401, "REJECTED"), (403, "CALLER_NOT_ALLOWED"), (409, "INCIDENT_CONFLICT"),
                         (423, "MAINTENANCE"), (500, "FAILED"), (503, "AUTH_NOT_CONFIGURED"),
                         (503, "BACKOFF"), (503, "COOLDOWN_ACTIVE"), (503, "HOST_RAM_LOW"),
                         (503, "LOCK_BLOCKED"), (503, "STATE_UNKNOWN")]:
            self.assertIn(expected, statuses)


if __name__ == "__main__":
    unittest.main()
