# -*- coding: utf-8 -*-
"""Tests du suivi en direct d'un essai N3 (aucun réseau : les sondes sont remplacées par des faux).
Lancer : python -I scripts/helios-ops/test_n3_watch.py

Écart corrigé le 17 nov. 2026 : pour .153 (kind « control-plane »), le suivi traitait la VM comme une VM d'infrastructure (état du nœud
« sans objet », toujours Ready) et n'aurait donc JAMAIS vu le nœud Kubernetes passer NotReady puis Ready pendant l'essai réel."""
import contextlib
import importlib.util
import io
import os
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.abspath(os.path.join(HERE, "..", "..", "infra", "helios-sot", "tools", "n3_watch.py"))
spec = importlib.util.spec_from_file_location("n3_watch", TOOL)
nw = importlib.util.module_from_spec(spec)
spec.loader.exec_module(nw)


class SuiviDuNoeudTest(unittest.TestCase):
    def suivre(self, vm, node_ready):
        """Exécute `run()` quelques ms avec des sondes factices ; retourne (watch, appels)."""
        w = nw.Watch(vm, 0.001)                      # échéance ≈ 60 ms
        appels = []

        def noeud():
            appels.append("node")
            w.node_ready = node_ready

        w.live_pod = lambda: None
        w.watchdog = lambda pod: None
        w.node = noeud
        w.database = lambda: appels.append("db")
        w.watchdog_log = lambda: None
        w.backend_logs = lambda: None
        vrai_sleep = nw.time.sleep
        nw.time.sleep = lambda s: vrai_sleep(0.01)    # pas de pause de 10 s en test (nw.time EST le module time : ne pas se rappeler soi-même)
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                w.run()
        finally:
            nw.time.sleep = vrai_sleep
        return w, appels

    def test_le_plan_de_controle_est_suivi_comme_un_noeud_kubernetes(self):
        w, appels = self.suivre("k8s-master", node_ready=False)
        self.assertIn("node", appels)
        self.assertFalse(w.node_ready, "un nœud NotReady ne doit PAS être considéré comme prêt")

    def test_un_worker_est_toujours_suivi(self):
        w, appels = self.suivre("k8s-worker1", node_ready=True)
        self.assertIn("node", appels)

    def test_une_vm_d_infrastructure_n_a_pas_de_noeud(self):
        w, appels = self.suivre("k8s-lb2", node_ready=False)
        self.assertNotIn("node", appels)
        self.assertTrue(w.node_ready, "sans objet : ne bloque pas la fin du suivi")

    def test_les_types_de_cibles_suivis_sont_ceux_du_preflight(self):
        self.assertEqual(nw.n3.NODE_KINDS, ("worker", "control-plane"))
        self.assertEqual(nw.n3.TARGETS["k8s-master"]["kind"], "control-plane")
        self.assertEqual(nw.n3.TARGETS["k8s-lb2"]["kind"], "infra")

    def test_un_retour_a_une_valeur_deja_vue_est_re_emis(self):
        """Écart du 18 nov. 2026 (essai réel de .153) : « Ready=True » -> « Ready=Unknown » -> « Ready=True » n'émettait que DEUX lignes
        (le retour à Ready était dédupliqué) : le résumé affichait « nœud Ready : non observé » alors que le nœud était bien revenu."""
        w = nw.Watch("k8s-master", 1)
        with contextlib.redirect_stdout(io.StringIO()):
            w.change("noeud", "k8s-master Ready=True")
            w.change("noeud", "k8s-master Ready=Unknown")
            w.change("noeud", "k8s-master Ready=True")
            w.change("noeud", "k8s-master Ready=True")          # pas de changement : rien
        self.assertEqual([m for _, s, m in w.events if s == "noeud"],
                         ["k8s-master Ready=True", "k8s-master Ready=Unknown", "k8s-master Ready=True"])

    def test_les_lignes_de_journal_identiques_restent_dedupliquees(self):
        w = nw.Watch("k8s-master", 1)
        with contextlib.redirect_stdout(io.StringIO()):
            w.emit("log abcde", "VmPowerWatch LAUNCHED:k8s-master")
            w.emit("log abcde", "VmPowerWatch LAUNCHED:k8s-master")      # fenêtres de journaux qui se chevauchent
        self.assertEqual(len(w.events), 1)

    def test_le_resume_voit_le_retour_a_ready(self):
        w = nw.Watch("k8s-master", 1)
        w.state, w.node_ready, w.incident_status = "RUNNING", True, "RESOLVED"
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            w.change("noeud", "k8s-master Ready=True")
            w.change("noeud", "k8s-master Ready=Unknown")
            w.change("noeud", "k8s-master Ready=True")
            w.summary()
        self.assertNotIn("nœud Ready              — (non observé)", buf.getvalue())

    def test_une_vm_non_eligible_est_refusee(self):
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(nw.main(["--vm", "k8s-monitoring"]), 2)
        self.assertIn("détection seule", out.getvalue())


if __name__ == "__main__":
    unittest.main()
