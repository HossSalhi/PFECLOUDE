# -*- coding: utf-8 -*-
"""LOT 09.9 — tests du HELPER privilégié candidat (helios_host_ops_helper.candidate.py).
Lancer : python -m unittest test_host_ops_helper -v   (depuis scripts/helios-ops)
Exécuteur SIMULÉ (aucune commande réelle, aucun root, aucune VM). Le helper est chargé par importlib
(nom de fichier à points) ; aucun effet de bord à l'import."""
import os
import ast
import importlib.util
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
HELPER_FILE = os.path.join(HERE, "helios_host_ops_helper.candidate.py")

_spec = importlib.util.spec_from_file_location("helios_host_ops_helper_candidate", HELPER_FILE)
helper = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(helper)


class FakeRunner:
    """Exécuteur simulé : enregistre (binary, args) et renvoie des codes retour programmés (jamais d'exécution)."""
    def __init__(self, rcs=None, default=0, raise_exc=None):
        self.calls = []
        self.rcs = list(rcs or [])
        self.default = default
        self.raise_exc = raise_exc

    def __call__(self, binary, args):
        self.calls.append((binary, list(args)))
        if self.raise_exc is not None:
            raise self.raise_exc
        return self.rcs.pop(0) if self.rcs else self.default


class HelperTest(unittest.TestCase):

    def test_1_cinq_ids_reconnus(self):
        # les 5 identifiants restent reconnus (ensemble fermé)
        for op in ("restart-kubelet", "restart-containerd", "sync-time", "image-gc", "reboot"):
            self.assertIn(op, helper.OPERATIONS)
        # exécution générique (exécuteur simulé) pour les ops à mécanisme DIRECT ;
        # `sync-time` est désormais GATÉ par disponibilité (LOT 09.16) : il ne peut pas être exercé
        # avec un simple runner (il interroge la disponibilité réelle), il est couvert exhaustivement
        # par SyncTimeFallbackTest ci-dessous.
        for op in ("restart-kubelet", "restart-containerd", "image-gc", "reboot"):
            fr = FakeRunner()
            self.assertEqual(0, helper.main([op], runner=fr))
            self.assertTrue(fr.calls)                       # a bien appelé l'exécuteur

    def test_2_id_inconnu(self):
        self.assertEqual(2, helper.main(["bogus-op"], runner=FakeRunner()))

    def test_3_fragments_shell_refuses(self):
        for bad in ["restart-kubelet; rm -rf /", "a && b", "a || b", "a | b", "$(id)", "`id`", "reboot&&reboot"]:
            self.assertEqual(2, helper.main([bad], runner=FakeRunner()))

    def test_4_arguments_supplementaires_refuses(self):
        self.assertEqual(2, helper.main(["restart-kubelet", "extra"], runner=FakeRunner()))
        self.assertEqual(2, helper.main(["image-gc", "nginx:latest"], runner=FakeRunner()))

    def test_5_chaine_vide(self):
        self.assertEqual(2, helper.main([""], runner=FakeRunner()))
        self.assertEqual(2, helper.main([], runner=FakeRunner()))

    def test_6_caracteres_controle(self):
        self.assertEqual(2, helper.main(["restart-kubelet\n"], runner=FakeRunner()))
        self.assertEqual(2, helper.main(["restart-kubelet\x00"], runner=FakeRunner()))

    def test_7_codes_retour(self):
        self.assertEqual(0, helper.main(["restart-kubelet"], runner=FakeRunner(default=0)))
        self.assertEqual(5, helper.main(["restart-kubelet"], runner=FakeRunner(rcs=[5])))

    def test_8_9_exceptions_executeur(self):
        # exception générique de lancement -> code interne (4), jamais de propagation
        self.assertEqual(4, helper.main(["restart-kubelet"], runner=FakeRunner(raise_exc=RuntimeError("boom"))))

    def test_10_pas_de_shell_dans_le_code(self):
        with open(HELPER_FILE, encoding="utf-8") as f:
            src = f.read()
        rr = [ast.get_source_segment(src, n) for n in ast.walk(ast.parse(src))
              if isinstance(n, ast.FunctionDef) and n.name == "_real_run"][0]
        self.assertIn("shell=False", rr)
        self.assertNotIn("shell=True", rr)
        self.assertNotIn("bash -c", rr)
        self.assertNotIn("sh -c", rr)
        self.assertNotIn("eval(", src)

    def test_11_chemins_absolus_et_resolution_fail_closed(self):
        for cands in helper.BIN_CANDIDATES.values():
            for p in cands:
                self.assertTrue(p.startswith("/"), f"chemin non absolu: {p}")
        with self.assertRaises(helper.HelperError):   # binaire inconnu -> aucune résolution -> fail-closed
            helper._resolve_abs("binaire-inexistant")

    def test_12_sequence_restart_containerd(self):
        fr = FakeRunner(rcs=[0, 0])     # containerd OK -> kubelet
        self.assertEqual(0, helper.run_op("restart-containerd", fr))
        self.assertEqual([("systemctl", ["restart", "containerd"]), ("systemctl", ["restart", "kubelet"])], fr.calls)
        fr2 = FakeRunner(rcs=[1])       # containerd KO -> PAS de kubelet (&&)
        self.assertEqual(1, helper.run_op("restart-containerd", fr2))
        self.assertEqual([("systemctl", ["restart", "containerd"])], fr2.calls)

    def test_13_ordre_repli_sync_time(self):
        # LOT 09.16/09.18 : `sync-time` GATÉ par disponibilité ET soumis à une POSTCONDITION confirmée.
        # Déterministe : disponibilité, lecture d'état (sync_confirmed) et attente (sleep) INJECTÉES.
        allav = (lambda n: True); noop = (lambda _s: None); yes = (lambda: True)
        fr = FakeRunner(rcs=[1, 1, 1])  # tout dispo mais tout échoue -> 3 tentatives dans l'ordre -> échec explicite
        with self.assertRaises(helper.HelperError):
            helper._op_sync_time(fr, bin_available=allav, unit_available=allav, sync_confirmed=yes, sleep=noop)
        self.assertEqual([("chronyc", ["makestep"]),
                          ("systemctl", ["restart", "chronyd"]),
                          ("systemctl", ["restart", "systemd-timesyncd"])], fr.calls)
        fr2 = FakeRunner(rcs=[0])       # chronyc dispo + OK + postcondition confirmée -> succès, stop
        self.assertEqual(0, helper._op_sync_time(fr2, bin_available=allav, unit_available=allav,
                                                 sync_confirmed=yes, sleep=noop))
        self.assertEqual([("chronyc", ["makestep"])], fr2.calls)

    def test_14_image_gc_fixe(self):
        fr = FakeRunner()
        self.assertEqual(0, helper.run_op("image-gc", fr))
        self.assertEqual([("crictl", ["rmi", "--prune"])], fr.calls)   # aucun argument supplémentaire

    def test_15_reboot_construction_fixe_sans_execution(self):
        fr = FakeRunner()
        self.assertEqual(0, helper.run_op("reboot", fr))
        self.assertEqual([("systemctl", ["reboot"])], fr.calls)        # exécuteur simulé : rien n'est exécuté


class SyncTimeFallbackTest(unittest.TestCase):
    """LOT 09.16/09.18 — `sync-time` gaté par DISPONIBILITÉ (chronyc->chronyd->systemd-timesyncd) ET soumis
    à une POSTCONDITION de synchro confirmée. Tout est SIMULÉ et DÉTERMINISTE : run, disponibilité, lecture
    d'état (sync_confirmed) et attente (sleep) sont INJECTÉS ; aucune commande/horloge/service réel touché."""

    NOOP_SLEEP = staticmethod(lambda _s: None)

    @staticmethod
    def _av(bins=(), units=()):
        bins, units = set(bins), set(units)
        return (lambda n: n in bins), (lambda n: n in units)

    def _call(self, fr, bins=(), units=(), confirmed=True, sleep=None):
        b, u = self._av(bins, units)
        sc = confirmed if callable(confirmed) else (lambda: confirmed)
        return helper._op_sync_time(fr, bin_available=b, unit_available=u,
                                    sync_confirmed=sc, sleep=sleep or self.NOOP_SLEEP)

    # --- sélection / repli (dépendances injectées) ---
    def test_s1_chronyc_dispo_succes_confirme(self):
        fr = FakeRunner(rcs=[0])
        self.assertEqual(0, self._call(fr, bins=["chronyc"], units=["chronyd", "systemd-timesyncd"], confirmed=True))
        self.assertEqual([("chronyc", ["makestep"])], fr.calls)

    def test_s2_chronyc_absent_chronyd_dispo(self):
        fr = FakeRunner(rcs=[0])
        self.assertEqual(0, self._call(fr, units=["chronyd", "systemd-timesyncd"], confirmed=True))
        self.assertEqual([("systemctl", ["restart", "chronyd"])], fr.calls)     # chronyc JAMAIS appelé

    def test_s3_timesyncd_seul_dispo(self):
        fr = FakeRunner(rcs=[0])
        self.assertEqual(0, self._call(fr, units=["systemd-timesyncd"], confirmed=True))
        self.assertEqual([("systemctl", ["restart", "systemd-timesyncd"])], fr.calls)

    def test_s4_aucun_mecanisme_echec_explicite(self):
        fr = FakeRunner()
        with self.assertRaises(helper.HelperError):
            self._call(fr, bins=[], units=[])
        self.assertEqual([], fr.calls)                           # rien n'est lancé

    def test_s5_commande_echoue_sans_repli_echec(self):
        fr = FakeRunner(rcs=[7])
        with self.assertRaises(helper.HelperError):              # jamais de faux succès
            self._call(fr, bins=["chronyc"], units=[], confirmed=True)
        self.assertEqual([("chronyc", ["makestep"])], fr.calls)

    def test_s5b_echec_puis_repli_confirme(self):
        fr = FakeRunner(rcs=[1, 0])
        self.assertEqual(0, self._call(fr, bins=["chronyc"], units=["chronyd", "systemd-timesyncd"], confirmed=True))
        self.assertEqual([("chronyc", ["makestep"]), ("systemctl", ["restart", "chronyd"])], fr.calls)

    def test_s6_chronyd_indispo_saute_vers_timesyncd(self):
        fr = FakeRunner(rcs=[0])
        self.assertEqual(0, self._call(fr, units=["systemd-timesyncd"], confirmed=True))
        self.assertEqual([("systemctl", ["restart", "systemd-timesyncd"])], fr.calls)

    def test_s7_execution_leve_remonte(self):
        fr = FakeRunner(raise_exc=helper.HelperError("perm"))
        with self.assertRaises(helper.HelperError):
            self._call(fr, bins=["chronyc"], units=[], confirmed=True)

    def test_s8_tous_echouent_pas_de_faux_succes(self):
        fr = FakeRunner(rcs=[1, 2, 3])
        with self.assertRaises(helper.HelperError):
            self._call(fr, bins=["chronyc"], units=["chronyd", "systemd-timesyncd"], confirmed=True)
        self.assertEqual(3, len(fr.calls))

    # --- POSTCONDITION explicite (LOT 09.18) ---
    def test_p1_rc0_postcondition_positive_succes(self):
        fr = FakeRunner(rcs=[0])
        self.assertEqual(0, self._call(fr, units=["systemd-timesyncd"], confirmed=True))

    def test_p2_rc0_mais_non_synchronise_echec(self):
        # ESSENTIEL : restart rc0 mais NTPSynchronized=no -> PAS un succès
        fr = FakeRunner(rcs=[0])
        with self.assertRaises(helper.HelperError):
            self._call(fr, units=["systemd-timesyncd"], confirmed=False)
        self.assertEqual([("systemctl", ["restart", "systemd-timesyncd"])], fr.calls)   # commande bien lancée

    def test_p3_rc0_mais_verif_indisponible_echec(self):
        def raises():
            raise RuntimeError("controle de synchro indisponible")
        fr = FakeRunner(rcs=[0])
        with self.assertRaises(helper.HelperError):              # indisponibilité JAMAIS convertie en succès
            self._call(fr, units=["systemd-timesyncd"], confirmed=raises)

    def test_p4_parse_rc_non_nul_est_faux(self):
        self.assertFalse(helper._parse_ntp_synchronized(1, "NTPSynchronized=yes\n"))

    def test_p5_parse_sorties_mal_formees_fausses(self):
        for rc, out in [(0, ""), (0, "garbage\n"), (0, "NTPSynchronized=no\n"),
                        (0, "NTPSynchronized=maybe"), (0, "ntpsynchronized=yes")]:
            self.assertFalse(helper._parse_ntp_synchronized(rc, out), (rc, out))
        self.assertTrue(helper._parse_ntp_synchronized(0, "Foo=1\nNTPSynchronized=yes\n"))

    def test_p6_non_synchro_puis_confirme_dans_la_limite(self):
        seq = iter([False, False, True]); calls = {"sleep": 0}
        def confirmed():
            return next(seq)
        def sleep(_s):
            calls["sleep"] += 1
        fr = FakeRunner(rcs=[0])
        self.assertEqual(0, self._call(fr, units=["systemd-timesyncd"], confirmed=confirmed, sleep=sleep))
        self.assertEqual(2, calls["sleep"])     # a attendu 2 fois avant la 3e confirmation

    def test_p7_toujours_non_synchro_apres_limite_echec(self):
        calls = {"sleep": 0}
        def sleep(_s):
            calls["sleep"] += 1
        fr = FakeRunner(rcs=[0])
        with self.assertRaises(helper.HelperError):
            self._call(fr, units=["systemd-timesyncd"], confirmed=False, sleep=sleep)
        self.assertEqual(helper.SYNC_CONFIRM_ATTEMPTS - 1, calls["sleep"])   # attente BORNÉE

    # --- déterminisme / sécurité ---
    def test_d1_aucun_service_reel_touche(self):
        # garde-fou : avec dépendances injectées, le chemin d'exécution réel _real_run n'est JAMAIS appelé
        import unittest.mock as mock
        with mock.patch.object(helper, "_real_run", side_effect=AssertionError("_real_run appele en test!")):
            fr = FakeRunner(rcs=[0])
            self.assertEqual(0, self._call(fr, units=["systemd-timesyncd"], confirmed=True))

    def test_d2_pas_de_shell_arbitraire_dans_le_chemin(self):
        with open(HELPER_FILE, encoding="utf-8") as f:
            src = f.read()
        names = ("_op_sync_time", "_unit_available", "_bin_available", "_sync_confirmed",
                 "_parse_ntp_synchronized", "_await_sync", "_default_sleep", "_real_run")
        segs = [ast.get_source_segment(src, n) for n in ast.walk(ast.parse(src))
                if isinstance(n, ast.FunctionDef) and n.name in names]
        joined = "\n".join(s for s in segs if s)
        for bad in ("shell=True", "bash -c", "sh -c", "os.system", "eval(", "subprocess.getoutput"):
            self.assertNotIn(bad, joined)
        self.assertIn("shell=False", joined)   # exécution directe préservée


class SyncConfirmReaderTest(unittest.TestCase):
    """LOT 09.18 (revue) — le chemin RÉEL de lecture de la postcondition : `_sync_confirmed` ->
    `_real_run("timedatectl", …, capture=True)` -> `_parse_ntp_synchronized`. `_real_run` est monkeypatché
    (aucun `timedatectl` réel n'est lancé). Vérifie que `timedatectl` est bien AUTORISÉ (BIN_CANDIDATES)."""

    def test_r1_timedatectl_autorise_chemin_absolu(self):
        self.assertIn("timedatectl", helper.BIN_CANDIDATES)             # sinon _resolve_abs lèverait
        for p in helper.BIN_CANDIDATES["timedatectl"]:
            self.assertTrue(p.startswith("/"), f"chemin non absolu: {p}")

    def test_r2_sync_confirmed_lit_yes_no_invalide_rc(self):
        import unittest.mock as mock
        cases = [((0, "NTPSynchronized=yes\n"), True),      # yes -> confirmé
                 ((0, "NTPSynchronized=no\n"), False),      # no -> non confirmé
                 ((0, "garbage\n"), False),                 # sortie invalide -> non confirmé
                 ((0, ""), False),                          # sortie vide -> non confirmé
                 ((1, "NTPSynchronized=yes\n"), False)]     # code retour non nul -> non confirmé
        for ret, expected in cases:
            with mock.patch.object(helper, "_real_run", return_value=ret) as mr:
                self.assertEqual(expected, helper._sync_confirmed(), ret)
                args, kwargs = mr.call_args
                self.assertEqual("timedatectl", args[0])                       # bon binaire
                self.assertEqual(["show", "-p", "NTPSynchronized"], args[1])   # arguments fixes
                self.assertTrue(kwargs.get("capture"))                         # lecture (capture=True)

    def test_r3_sync_confirmed_resolution_echoue_est_false(self):
        import unittest.mock as mock
        with mock.patch.object(helper, "_real_run", side_effect=helper.HelperError("non resolu")):
            self.assertFalse(helper._sync_confirmed())     # indisponibilité JAMAIS convertie en succès


if __name__ == "__main__":
    unittest.main()
