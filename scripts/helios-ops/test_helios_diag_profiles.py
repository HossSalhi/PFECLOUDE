# -*- coding: utf-8 -*-
"""LOT WATCHDOG-READONLY-DIAGNOSTICS-01 (+ SECURITY-FIX-01) — tests unitaires PURS
(sans réseau, sans VM, sans SSH, sans serveur HTTP).
Lancer : python -m unittest test_helios_diag_profiles -v   (depuis scripts/helios-ops)
Aucune commande distante ne peut être déclenchée par ces tests."""
import unittest
import helios_diag_profiles as d


def _executeur_interdit(node, profile):
    """Faux exécuteur : DOIT échouer bruyamment s'il est atteint par une requête refusée."""
    raise AssertionError(f"executeur distant atteint a tort pour {node}/{profile}")


def _executeur_ok(node, profile):
    return True, {"node": node, "profile": profile, "proof": "faux-sans-VM"}


def _executeur_ko(node, profile):
    return False, "echec simule (sans VM)"


class DiagProfilesTest(unittest.TestCase):

    def test_profils_fermes(self):
        self.assertIsNotNone(d.profile_commands("diag-cpu"))
        self.assertIn("uptime", d.profile_commands("diag-cpu"))
        self.assertIn("df -hT /", d.profile_commands("diag-disk"))
        # deux echantillons /proc/stat pour le CPU
        self.assertEqual(2, d.profile_commands("diag-cpu").count("cat /proc/stat"))
        # profil inconnu => None (jamais une commande libre)
        self.assertIsNone(d.profile_commands("rm -rf /"))
        self.assertIsNone(d.profile_commands("diag-arbitraire"))

    def test_cible_hors_allowlist_refusee(self):
        allow = {"k8s-worker1", "k8s-worker2"}
        self.assertTrue(d.is_allowed("k8s-worker1", "diag-cpu", allow))
        self.assertFalse(d.is_allowed("k8s-master-evil", "diag-cpu", allow))
        self.assertFalse(d.is_allowed("k8s-worker1", "op-inexistante", allow))
        self.assertFalse(d.is_allowed(None, "diag-cpu", allow))

    def test_auth_fail_closed(self):
        self.assertTrue(d.auth_ok("secret", "secret"))
        self.assertFalse(d.auth_ok("secret", "autre"))
        self.assertFalse(d.auth_ok("secret", ""))     # aucun jeton attendu => refus
        self.assertFalse(d.auth_ok("", "secret"))      # pas de jeton fourni => refus
        self.assertFalse(d.auth_ok(None, None))

    def test_troncature_sortie(self):
        self.assertIsNone(d.truncate(None))
        self.assertEqual("abc", d.truncate("abc", 10))
        long = "x" * 10000
        t = d.truncate(long, 4096)
        self.assertTrue(t.endswith("...[tronque]"))
        self.assertLess(len(t), len(long))

    def test_build_evidence_marque_non_collecte(self):
        raw = [{"command": "df -hT /", "exit_code": 0, "stdout": "/dev/sda1 ext4 50G 40G 8G 84% /", "stderr": ""}]
        ev = d.build_evidence("k8s-worker1", "diag-disk", "BEFORE", raw)
        by = {o["command"]: o for o in ev["outputs"]}
        self.assertTrue(by["df -hT /"]["available"])          # fournie
        self.assertFalse(by["lsblk"]["available"])             # absente => NON COLLECTÉ
        self.assertIsNone(by["lsblk"]["stdout"])               # aucune valeur inventée

    def test_authorize_diag_ordre_et_gates(self):
        allow = {"k8s-worker1", "k8s-worker2"}
        tok = "jeton-correct"
        # chemin invalide -> 400
        self.assertEqual(400, d.authorize_diag("/host-diag/x", "", tok, allow)[0])
        self.assertEqual(400, d.authorize_diag("/host-exec/k8s-worker1/diag-cpu", tok, tok, allow)[0])
        # mauvais jeton sur une cible VALIDE -> 401 (auth AVANT cible)
        self.assertEqual(401, d.authorize_diag("/host-diag/k8s-worker1/diag-cpu", "faux", tok, allow)[0])
        # aucun jeton attendu configuré -> 401 (fail-closed), meme avec cible valide
        self.assertEqual(401, d.authorize_diag("/host-diag/k8s-worker1/diag-cpu", "x", "", allow)[0])
        # mauvais jeton sur une cible INVALIDE -> 401 aussi (on ne divulgue pas la validité de la cible)
        self.assertEqual(401, d.authorize_diag("/host-diag/noeud-inconnu/diag-cpu", "faux", tok, allow)[0])
        # bon jeton + cible hors allowlist -> 404
        self.assertEqual(404, d.authorize_diag("/host-diag/noeud-inconnu/diag-cpu", tok, tok, allow)[0])
        # bon jeton + profil inconnu -> 404 (jamais de commande libre)
        self.assertEqual(404, d.authorize_diag("/host-diag/k8s-worker1/rm-rf", tok, tok, allow)[0])
        # bon jeton + cible + profil valides -> 200 (SEUL cas ou diag_exec serait atteint)
        code, node, profile = d.authorize_diag("/host-diag/k8s-worker1/diag-disk", tok, tok, allow)
        self.assertEqual((200, "k8s-worker1", "diag-disk"), (code, node, profile))

    def test_handle_host_diag_refus_natteint_jamais_executeur(self):
        # LOT 07.1 : teste le RACCORDEMENT route->autorisation->exécuteur, pas seulement authorize_diag.
        # _executeur_interdit lève si atteint -> prouve qu'AUCUNE requête refusée ne le déclenche.
        allow = {"k8s-worker1"}
        tok = "jeton"
        self.assertEqual(503, d.handle_host_diag(
            "/host-diag/k8s-worker1/diag-cpu", "x", tok, allow, _executeur_interdit, diag_available=False)[0])
        self.assertEqual(400, d.handle_host_diag(
            "/host-diag/x", "x", tok, allow, _executeur_interdit)[0])
        self.assertEqual(401, d.handle_host_diag(
            "/host-diag/k8s-worker1/diag-cpu", "faux", tok, allow, _executeur_interdit)[0])
        self.assertEqual(401, d.handle_host_diag(   # aucun jeton configuré -> refus systématique
            "/host-diag/k8s-worker1/diag-cpu", "x", "", allow, _executeur_interdit)[0])
        self.assertEqual(404, d.handle_host_diag(   # cible interdite
            "/host-diag/inconnu/diag-cpu", tok, tok, allow, _executeur_interdit)[0])
        self.assertEqual(404, d.handle_host_diag(   # profil inconnu (jamais de commande libre)
            "/host-diag/k8s-worker1/rm-rf", tok, tok, allow, _executeur_interdit)[0])
        # (si l'une de ces décisions avait appelé l'exécuteur, AssertionError aurait fait échouer le test)

    def test_handle_host_diag_200_appelle_executeur(self):
        allow = {"k8s-worker1"}
        tok = "jeton"
        code, body = d.handle_host_diag("/host-diag/k8s-worker1/diag-disk", tok, tok, allow, _executeur_ok)
        self.assertEqual(200, code)
        self.assertTrue(body["ok"])
        self.assertEqual("k8s-worker1", body["node"])
        self.assertEqual("diag-disk", body["profile"])
        # exécuteur renvoyant False -> 500, erreur remontée, pas d'evidence
        code, body = d.handle_host_diag("/host-diag/k8s-worker1/diag-disk", tok, tok, allow, _executeur_ko)
        self.assertEqual(500, code)
        self.assertFalse(body["ok"])
        self.assertIsNone(body["evidence"])
        self.assertIsNotNone(body["error"])

    def test_ssh_identity_source_fail_closed(self):
        # pas d'ancre de confiance -> identité non vérifiable -> refus (fail-closed)
        self.assertFalse(d.ssh_identity_source("")[0])
        self.assertFalse(d.ssh_identity_source(None)[0])
        self.assertFalse(d.ssh_identity_source(r"C:\chemin\inexistant\known_hosts")[0])
        # ancre existante et non vide -> ok (on réutilise le fichier du module comme fichier présent)
        ok, detail = d.ssh_identity_source(d.__file__)
        self.assertTrue(ok)
        self.assertEqual(d.__file__, detail)

    def test_compute_cpu_util(self):
        before = "cpu  100 0 0 700 0 0 0 0 0 0\n"
        after = "cpu  400 0 0 800 0 0 0 0 0 0\n"   # dTotal=400, dIdle=100 -> util=(400-100)/400=75%
        self.assertEqual(75.0, d.compute_cpu_util(before, after))
        # cas realiste non trivial : dTotal=300, dIdle=100 -> 66.7%
        self.assertEqual(66.7, d.compute_cpu_util(
            "cpu  100 0 100 800 0 0 0 0 0 0\n", "cpu  200 0 200 900 0 0 0 0 0 0\n"))
        self.assertIsNone(d.compute_cpu_util("garbage", "garbage"))
        self.assertIsNone(d.compute_cpu_util(before, before))  # dTotal=0 -> None


if __name__ == "__main__":
    unittest.main()
