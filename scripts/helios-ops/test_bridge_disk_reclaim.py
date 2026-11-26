# -*- coding: utf-8 -*-
"""DISK-E2E-158-01 - tests de la capacite HOST_RECLAIM_DISK du Bridge CIBLE (infra/helios-sot/bridge/bridge.py).

Lancer : python -B -m unittest test_bridge_disk_reclaim -v          (depuis scripts/helios-ops)

Le VRAI code du Bridge est charge et execute ; seul `executer` (le point de passage UNIQUE vers SSH) est remplace par un faux
noeud en memoire. Ce faux noeud n'accepte que les QUATRE commandes que le Bridge est autorise a emettre pour cette action et
leve une AssertionError sur toute autre : une commande inattendue (joker, -r, find -delete, systemctl...) fait donc echouer le test.
Aucun reseau, aucun SSH, aucune VM, aucun fichier cree.

Preuves apportees (numerotation de la demande) :
  3. le Bridge ne supprime QUE les fichiers exacts du scenario (jeton), jamais un homonyme, un etranger, un autre jeton ;
  4. la verification confirme <= 77 % par une MESURE faite APRES la liberation, et jamais autrement ;
  5. une seconde execution ne supprime rien (idempotence), meme en concurrence, et ne peut pas viser un autre jeton ;
  7. un incident DISK etranger (aucun fichier du scenario) n'est pas touche : rien n'est supprime, aucun succes n'est declare.
"""
import ast
import importlib.util
import json
import os
import shlex
import sys
import threading
import time
import unittest
import urllib.request
from http.server import HTTPServer
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
CIBLE = os.path.join(REPO, "infra", "helios-sot", "bridge", "bridge.py")

spec = importlib.util.spec_from_file_location("bridge_cible_disque", CIBLE)
B = importlib.util.module_from_spec(spec)
sys.modules["bridge_cible_disque"] = B
spec.loader.exec_module(B)

GIO = 1024 ** 3
DOSSIER = "/var/lib/helios-diskfill"
JETON_A = "0123456789abcdef0123456789abcdef"
JETON_B = "fedcba9876543210fedcba9876543210"
CMD_STAT_DIR = "sudo -n /usr/bin/stat -c '%F|%U|%a' " + DOSSIER
CMD_FIND = "sudo -n /usr/bin/find " + DOSSIER + " -maxdepth 1 -mindepth 1 -printf '%y|%u|%m|%n|%s|%f\\n'"
CMD_STAT_FS = "sudo -n /usr/bin/stat -f -c '%S|%b|%a' /"
PREFIXE_RM = "sudo -n /usr/bin/rm -f -- "


def nom(jeton, i):
    return "helios-diskfill-%s-%03d.bin" % (jeton, i)


def demande(**kw):
    d = {"action": "HOST_RECLAIM_DISK", "type": "NODE_DISK_FULL", "ns": "k8s-nodes", "cible": "k8s-worker1",
         "noeud": "k8s-worker1", "scenario": "DISK_E2E_158", "incident_id": "4242"}
    d.update(kw)
    return d


class NoeudFictif(object):
    """Noeud .158 en memoire : un volume racine, un repertoire exclusif, des entrees, et UNIQUEMENT quatre commandes admises."""
    BS = 4096

    def __init__(self, base_pct=70.0, dossier=True, total=40 * GIO):
        self.total_blocs = total // self.BS
        self.base_blocs = int(self.total_blocs * base_pct / 100.0)
        self.supplement = 0                       # blocs consommes AILLEURS (autre charge, apres coup)
        self.dossier = ("directory", "root", "700") if dossier else None
        self.entrees = {}
        self.log = []                             # toutes les commandes recues
        self.rm = []                              # listes de chemins des `rm`
        self.pannes = set()
        self.delai = 0.0                          # latence SSH simulee : expose les entrelacements sans verrou
        self.hors_dossier = {"/etc/passwd": 1789, "/var/log/syslog": 4096, "/var/lib/etcd/member": 777}

    # -- etat ----------------------------------------------------------------------------------------------------------
    def ajouter(self, nom_, octets, type_="f", proprio="root", liens=1):
        self.entrees[nom_] = {"type": type_, "proprio": proprio, "liens": liens, "taille": octets, "mode": "644"}

    def remplir(self, jeton, n, octets):
        for i in range(1, n + 1):
            self.ajouter(nom(jeton, i), octets)

    def dispo_blocs(self):
        utilises = self.base_blocs + self.supplement + sum(
            (e["taille"] + self.BS - 1) // self.BS for e in self.entrees.values() if e["type"] == "f")
        return self.total_blocs - utilises

    def usage(self):
        return (1.0 - float(self.dispo_blocs()) / self.total_blocs) * 100.0

    def noms(self):
        return sorted(self.entrees)

    def commandes_rm(self):
        return [c for c in self.log if c.startswith(PREFIXE_RM)]

    # -- le SEUL point d'entree : remplace bridge.executer ----------------------------------------------------------------
    def executer(self, noeud, commande):
        self.log.append(commande)
        assert noeud == "worker1", "le Bridge ne doit viser que worker1 (recu %r)" % (noeud,)
        if self.delai:
            time.sleep(self.delai)

        def r(out="", rc=0, err=""):
            return {"host": noeud, "ip": "192.168.148.158", "command": commande, "exitCode": rc, "stdout": out.strip()[:4000],
                    "stderr": err, "ok": rc == 0, "resume": (out or err or "")[:80]}

        if commande == CMD_STAT_DIR:
            if "stat_dir_ko" in self.pannes:
                return r(rc=255, err="ssh: connect to host: Connection refused")
            if self.dossier is None:
                return r(rc=1, err="stat: cannot statx '%s': No such file or directory" % DOSSIER)
            return r("|".join(self.dossier))
        if commande == CMD_FIND:
            if "find_ko" in self.pannes:
                return r(rc=1, err="find: erreur")
            if "relecture_ko" in self.pannes and self.rm:
                return r(rc=255, err="ssh: connexion perdue")
            lignes = ["%s|%s|%s|%d|%d|%s" % (e["type"], e["proprio"], e["mode"], e["liens"], e["taille"], n)
                      for n, e in sorted(self.entrees.items())]
            if "liste_geante" in self.pannes:
                lignes += ["f|root|644|1|1|x%05d.dat" % i for i in range(600)]
            if "ligne_illisible" in self.pannes:
                lignes.append("sortie|sans|structure")
            return r("\n".join(lignes))
        if commande == CMD_STAT_FS:
            if "statf_ko" in self.pannes or ("statf_ko_apres" in self.pannes and self.rm):
                return r(rc=255, err="ssh: connexion perdue")
            if "statf_illisible" in self.pannes:
                return r("n/a|n/a|n/a")
            return r("%d|%d|%d" % (self.BS, self.total_blocs, self.dispo_blocs()))
        if commande.startswith(PREFIXE_RM):
            argv = shlex.split(commande)
            assert argv[:5] == ["sudo", "-n", "/usr/bin/rm", "-f", "--"], "forme de rm inattendue : %r" % argv[:6]
            chemins = argv[5:]
            assert chemins, "rm sans chemin"
            for c in chemins:
                assert c.startswith(DOSSIER + "/"), "chemin hors du repertoire exclusif : %r" % c
                assert B.DISQUE_RE_FICHIER.fullmatch(c[len(DOSSIER) + 1:]), "chemin hors du motif EXACT : %r" % c
                assert not any(ch in c for ch in "*?[]{}~$`\\"), "metacaractere dans un chemin : %r" % c
            self.rm.append(list(chemins))
            if "rm_echoue" in self.pannes:
                return r(rc=1, err="rm: erreur")
            if "rm_sans_effet" in self.pannes:
                return r()                        # rc 0 mais RIEN supprime : seule la relecture le voit
            for c in chemins:
                self.entrees.pop(c.rsplit("/", 1)[1], None)
            return r()
        raise AssertionError("COMMANDE NON AUTORISEE emise par le Bridge : %r" % (commande,))


class BaseBridge(unittest.TestCase):
    def setUp(self):
        self.noeud = NoeudFictif()
        self.traces = []
        patches = [mock.patch.object(B, "executer", self.noeud.executer),
                   mock.patch.object(B, "tracer", lambda *p: self.traces.append(" ".join(str(x) for x in p))),
                   mock.patch.object(B, "_tele", None)]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        B._DISQUE_LIAISONS.clear()
        self.addCleanup(B._DISQUE_LIAISONS.clear)

    def appeler(self, **kw):
        return B.disque_reclamer("worker1", demande(**kw))

    def scenario_nominal(self, n=3, octets=int(1.5 * GIO)):
        """.158 a ~81 % : base 70 % + 3 x 1,5 Gio de fichiers du jeton A sur 40 Gio."""
        self.noeud.remplir(JETON_A, n, octets)
        return self.noeud


# ============================================================================================================== validation
class ValidationRequeteTests(BaseBridge):
    def test_une_requete_conforme_est_acceptee(self):
        self.assertEqual((True, None, ""), B.disque_valider_requete(demande(), "worker1"))

    def test_chaque_champ_hors_perimetre_est_refuse_sans_aucune_commande(self):
        cas = [({"action": "HOST_IMAGE_GC"}, "REJECTED_ACTION"), ({"action": ""}, "REJECTED_ACTION"),
               ({"cible": "k8s-worker2"}, "REJECTED_TARGET_NOT_ALLOWLISTED"), ({"cible": "k8s-master"}, "REJECTED_TARGET_NOT_ALLOWLISTED"),
               ({"type": "NODE_DISK_PRESSURE"}, "REJECTED_TYPE"), ({"type": "NODE_INODES_FULL"}, "REJECTED_TYPE"),
               ({"type": "NODE_HIGH_CPU"}, "REJECTED_TYPE"), ({"ns": "pfe"}, "REJECTED_NAMESPACE"),
               ({"scenario": "DISK_E2E_155"}, "REJECTED_SCENARIO"), ({"scenario": ""}, "REJECTED_SCENARIO"),
               ({"incident_id": "12; rm -rf /"}, "REJECTED_INCIDENT_ID"), ({"incident_id": "abc"}, "REJECTED_INCIDENT_ID")]
        for patch, rejet in cas:
            with self.subTest(patch=patch):
                self.noeud.log[:] = []
                res = self.appeler(**patch)
                self.assertEqual(rejet, res["rejet"])
                self.assertFalse(res["ok"])
                self.assertFalse(res["execute"])
                self.assertEqual(-1, res["exitCode"])
                self.assertEqual([], self.noeud.log, "aucune commande ne doit partir pour une requete refusee")

    def test_le_noeud_doit_etre_worker1_meme_si_la_cible_est_correcte(self):
        res = B.disque_reclamer("worker2", demande())
        self.assertEqual("REJECTED_TARGET_NOT_ALLOWLISTED", res["rejet"])
        self.assertEqual([], self.noeud.log)

    def test_le_type_est_lu_aussi_sous_l_alias_incident_type(self):
        d = demande()
        d["incident_type"], d["type"] = d.pop("type"), ""
        self.assertTrue(B.disque_valider_requete(d, "worker1")[0])


# ============================================================================================================== plan pur
class PlanPurTests(unittest.TestCase):
    def e(self, nom_, taille=GIO, type_="f", proprio="root", liens=1):
        return {"type": type_, "proprio": proprio, "mode": "644", "liens": liens, "taille": taille, "nom": nom_}

    def test_parse_find_extrait_les_six_champs_et_garde_le_nom_en_dernier(self):
        entrees, illisibles = B.disque_parse_find("f|root|644|1|1073741824|%s\nd|root|755|2|4096|sous|dossier" % nom(JETON_A, 1))
        self.assertEqual(0, illisibles - 0)
        self.assertEqual(2, len(entrees))
        self.assertEqual("sous|dossier", entrees[1]["nom"], "un nom contenant | reste entier (split a 5 coupures)")

    def test_parse_find_compte_les_lignes_illisibles(self):
        _, illisibles = B.disque_parse_find("f|root|644|x|12|nom\nsans structure\nf|root|644|1|12|")
        self.assertEqual(3, illisibles)

    def test_les_fichiers_du_jeton_sont_retenus_et_les_etrangers_comptes(self):
        plan = B.disque_plan([self.e(nom(JETON_A, 2)), self.e(nom(JETON_A, 1)), self.e("README"), self.e("autre.bin"),
                              self.e("sous", type_="d"), self.e("helios-diskfill-ABC-001.bin")])
        self.assertEqual("", plan["refus"])
        self.assertEqual(JETON_A, plan["jeton"])
        self.assertEqual([nom(JETON_A, 1), nom(JETON_A, 2)], plan["fichiers"])
        self.assertEqual(4, plan["etrangers"], "README, autre.bin, sous, nom de jeton invalide : comptes, jamais touches")

    def test_un_nom_avec_saut_de_ligne_final_n_est_pas_un_fichier_du_scenario(self):
        self.assertIsNone(B.DISQUE_RE_FICHIER.fullmatch(nom(JETON_A, 1) + "\n"))
        plan = B.disque_plan([self.e(nom(JETON_A, 1) + "\n")])
        self.assertEqual([], plan["fichiers"])
        self.assertEqual(1, plan["etrangers"])

    def test_deux_jetons_sont_refuses_et_rien_n_est_selectionne(self):
        plan = B.disque_plan([self.e(nom(JETON_A, 1)), self.e(nom(JETON_B, 1))])
        self.assertEqual("AMBIGUOUS_TOKENS", plan["rejet"])
        self.assertEqual([], plan["fichiers"])
        self.assertIsNone(plan["jeton"])

    def test_chaque_homonyme_non_conforme_refuse_tout_le_plan(self):
        for lib, entree in [("lien symbolique", self.e(nom(JETON_A, 2), type_="l")), ("repertoire", self.e(nom(JETON_A, 2), type_="d")),
                            ("autre proprietaire", self.e(nom(JETON_A, 2), proprio="kube")), ("lien physique", self.e(nom(JETON_A, 2), liens=2)),
                            ("taille nulle", self.e(nom(JETON_A, 2), taille=0)), ("index 000", self.e(nom(JETON_A, 0))),
                            ("index 017", self.e(nom(JETON_A, 17)))]:
            with self.subTest(cas=lib):
                plan = B.disque_plan([self.e(nom(JETON_A, 1)), entree])
                self.assertEqual("ANOMALOUS_ENTRY", plan["rejet"])
                self.assertEqual([], plan["fichiers"], "une anomalie annule TOUTE suppression")

    def test_une_taille_totale_excessive_est_refusee(self):
        plan = B.disque_plan([self.e(nom(JETON_A, i), taille=int(0.5 * GIO) * 3) for i in range(1, 6)])   # 5 x 1,5 Gio... = 7,5 Gio
        self.assertEqual("TOO_LARGE", plan["rejet"])

    def test_un_listing_illisible_refuse(self):
        self.assertEqual("UNREADABLE_LISTING", B.disque_plan([self.e(nom(JETON_A, 1))], illisibles=1)["rejet"])

    def test_repertoire_vide_ou_sans_fichier_du_scenario_donne_un_plan_vide_sans_refus(self):
        for entrees in ([], [self.e("README")]):
            plan = B.disque_plan(entrees)
            self.assertEqual(("", None, []), (plan["refus"], plan["jeton"], plan["fichiers"]))


# ============================================================================================ preuve 3 : nettoyage exclusif
class NettoyageExclusifTests(BaseBridge):
    def test_seuls_les_fichiers_du_jeton_sont_supprimes_les_etrangers_restent(self):
        n = self.scenario_nominal()
        n.ajouter("README", 120)
        n.ajouter("autre.bin", GIO // 4)
        n.ajouter("sous", 4096, type_="d")
        n.ajouter("helios-diskfill-NOTAHEX-001.bin", 1000)
        n.ajouter(nom(JETON_A, 1)[:-4] + ".bak", 1000)
        avant_etrangers = {k: dict(v) for k, v in n.entrees.items() if not B.DISQUE_RE_FICHIER.fullmatch(k)}
        res = self.appeler()
        self.assertTrue(res["ok"], res)
        self.assertEqual(3, res["supprimes"])
        self.assertEqual(sorted(avant_etrangers), n.noms(), "restent EXACTEMENT les entrees etrangeres")
        self.assertEqual(avant_etrangers, n.entrees, "les etrangers ne sont ni modifies ni deplaces")
        self.assertEqual(5, res["etrangers"])
        self.assertEqual(1, len(n.rm), "une seule suppression, groupee")
        self.assertEqual([DOSSIER + "/" + nom(JETON_A, i) for i in (1, 2, 3)], n.rm[0], "chemins EXACTS, tries, sans joker")

    def test_les_fichiers_hors_du_repertoire_ne_sont_jamais_references(self):
        self.scenario_nominal()
        self.appeler()
        for commande in self.noeud.log:
            for hors in self.noeud.hors_dossier:
                self.assertNotIn(hors, commande)
        self.assertEqual(3, len(self.noeud.rm[0]))

    def test_aucune_commande_hors_des_quatre_formes_autorisees_et_aucune_n_est_interdite_par_le_bridge(self):
        self.scenario_nominal()
        self.appeler()
        for commande in self.noeud.log:
            self.assertIn(commande, (CMD_STAT_DIR, CMD_FIND, CMD_STAT_FS) if not commande.startswith(PREFIXE_RM) else (commande,))
            self.assertIsNone(B.commande_interdite(commande), "la garde air-gap du Bridge refuserait : %s" % commande)

    def test_ordre_des_operations_lecture_puis_suppression_puis_relecture_puis_mesure(self):
        self.scenario_nominal()
        self.appeler()
        formes = [("rm" if c.startswith(PREFIXE_RM) else c) for c in self.noeud.log]
        self.assertEqual([CMD_STAT_DIR, CMD_FIND, CMD_STAT_FS, "rm", CMD_STAT_DIR, CMD_FIND, CMD_STAT_FS], formes,
                         "la MESURE de recuperation est la DERNIERE operation, apres la suppression et la relecture")

    def test_un_repertoire_non_conforme_refuse_avant_toute_suppression(self):
        for lu in (("symbolic link", "root", "777"), ("directory", "kube", "700"), ("directory", "root", "755"), ("regular file", "root", "600")):
            with self.subTest(lu=lu):
                n = self.noeud.__class__()
                n.dossier = lu
                n.remplir(JETON_A, 2, GIO)
                with mock.patch.object(B, "executer", n.executer):
                    res = B.disque_reclamer("worker1", demande())
                self.assertEqual("UNSAFE_DIRECTORY", res["rejet"])
                self.assertEqual([], n.rm)
                self.assertEqual(2, len(n.entrees))

    def test_deux_jetons_ou_un_homonyme_suspect_ne_suppriment_rien(self):
        n = self.scenario_nominal()
        n.remplir(JETON_B, 2, GIO)
        res = self.appeler()
        self.assertEqual("AMBIGUOUS_TOKENS", res["rejet"])
        self.assertEqual([], n.rm)
        n.entrees = {}
        n.remplir(JETON_A, 2, GIO)
        n.ajouter(nom(JETON_A, 3), 100, type_="l")
        res = self.appeler()
        self.assertEqual("ANOMALOUS_ENTRY", res["rejet"])
        self.assertEqual([], n.rm)
        self.assertEqual(3, len(n.entrees))

    def test_une_liste_geante_ou_illisible_refuse(self):
        self.scenario_nominal()
        self.noeud.pannes.add("liste_geante")
        self.assertEqual("LISTING_TRUNCATED", self.appeler()["rejet"])
        self.noeud.pannes = {"ligne_illisible"}
        self.assertEqual("UNREADABLE_LISTING", self.appeler()["rejet"])
        self.assertEqual([], self.noeud.rm)


# ================================================================================================= preuve 4 : mesure <= 77 %
class VerificationMesureTests(BaseBridge):
    def test_succes_seulement_si_l_occupation_mesuree_apres_liberation_est_au_plus_77(self):
        n = self.scenario_nominal()
        self.assertGreater(n.usage(), 80.0)
        res = self.appeler()
        self.assertTrue(res["ok"] and res["recupere"])
        self.assertLessEqual(res["usage_apres_pct"], 77.0)
        self.assertGreater(res["usage_avant_pct"], 80.0)
        self.assertEqual(77.0, res["seuil_recuperation_pct"])
        self.assertEqual(round(n.usage(), 2), res["usage_apres_pct"], "la valeur rendue est la mesure reelle du noeud")
        self.assertEqual(0, res["exitCode"])
        self.assertTrue(res["execute"])

    def test_fichiers_supprimes_mais_occupation_toujours_au_dessus_de_77_n_est_pas_un_succes(self):
        n = self.scenario_nominal()
        n.supplement = int(0.09 * n.total_blocs)             # une AUTRE charge a pris 9 points : 70 + 9 = 79 % apres liberation
        res = self.appeler()
        self.assertFalse(res["ok"], "jamais un succes declare pour satisfaire le test")
        self.assertEqual("NOT_RECOVERED", res["rejet"])
        self.assertFalse(res["recupere"])
        self.assertGreater(res["usage_apres_pct"], 77.0)
        self.assertEqual(3, res["supprimes"], "les fichiers du scenario ont bien ete supprimes")
        self.assertEqual(-1, res["exitCode"])

    def test_la_frontiere_est_inclusive_a_77_et_exclusive_au_dessus(self):
        for pct, attendu in ((76.99, True), (77.0, True), (77.2, False), (80.5, False)):
            with self.subTest(pct=pct):
                n = NoeudFictif(base_pct=pct)
                n.remplir(JETON_A, 1, GIO)
                with mock.patch.object(B, "executer", n.executer):
                    res = B.disque_reclamer("worker1", demande(incident_id=""))
                self.assertEqual(attendu, res["ok"], (pct, res.get("usage_apres_pct")))

    def test_la_frontiere_exacte_77_00_est_un_succes_et_77_01_ne_l_est_pas(self):
        """Volume de 10 000 000 blocs : 7 700 000 utilises = EXACTEMENT 77,00 % (verifie en flottant), 7 701 000 = 77,01 %."""
        for base_pct, attendu in ((77.0, True), (77.01, False)):
            with self.subTest(base_pct=base_pct):
                n = NoeudFictif(base_pct=base_pct, total=10000000 * NoeudFictif.BS)
                n.remplir(JETON_A, 1, GIO)
                with mock.patch.object(B, "executer", n.executer):
                    res = B.disque_reclamer("worker1", demande(incident_id=""))
                self.assertEqual(attendu, res["ok"], res)
                self.assertEqual(base_pct, res["usage_apres_pct"])
                self.assertEqual(attendu, res["recupere"])

    def test_mesure_impossible_apres_suppression_ne_donne_jamais_un_succes(self):
        self.scenario_nominal()
        self.noeud.pannes.add("statf_ko_apres")
        res = self.appeler()
        self.assertFalse(res["ok"])
        self.assertEqual("MEASURE_FAILED", res["rejet"])
        self.assertEqual(3, res["supprimes"])

    def test_mesure_impossible_ou_illisible_avant_toute_suppression_refuse(self):
        for panne in ("statf_ko", "statf_illisible"):
            with self.subTest(panne=panne):
                n = NoeudFictif()
                n.remplir(JETON_A, 2, GIO)
                n.pannes.add(panne)
                with mock.patch.object(B, "executer", n.executer):
                    res = B.disque_reclamer("worker1", demande())
                self.assertEqual("MEASURE_FAILED", res["rejet"])
                self.assertEqual([], n.rm)

    def test_rm_qui_rend_zero_sans_rien_supprimer_n_est_pas_un_succes(self):
        n = self.scenario_nominal()
        n.pannes.add("rm_sans_effet")
        res = self.appeler()
        self.assertFalse(res["ok"])
        self.assertEqual("DELETION_INCOMPLETE", res["rejet"])
        self.assertEqual(3, len(res["restants"]))
        self.assertEqual(0, res["supprimes"])

    def test_rm_qui_echoue_ou_relecture_perdue_n_est_pas_un_succes(self):
        n = self.scenario_nominal()
        n.pannes.add("rm_echoue")
        self.assertEqual("DELETION_INCOMPLETE", self.appeler()["rejet"])
        n = NoeudFictif()
        n.remplir(JETON_A, 3, int(1.5 * GIO))
        n.pannes.add("relecture_ko")
        with mock.patch.object(B, "executer", n.executer):
            res = B.disque_reclamer("worker1", demande())
        self.assertFalse(res["ok"])
        self.assertEqual("DIRECTORY_UNREADABLE", res["rejet"])

    def test_la_reponse_est_serialisable_en_json_et_porte_les_champs_lus_par_le_backend(self):
        self.scenario_nominal()
        res = self.appeler()
        corps = json.loads(json.dumps(res))
        for champ in ("ok", "execute", "exitCode", "stdout", "stderr"):
            self.assertIn(champ, corps)
        self.assertIsInstance(corps["ok"], bool)
        self.assertIsInstance(corps["execute"], bool)
        self.assertIsInstance(corps["exitCode"], int)


# ===================================================================================== preuve 5 : idempotence, pas de double suppression
class IdempotenceTests(BaseBridge):
    def test_la_seconde_execution_ne_supprime_rien_et_reste_un_succes_si_l_occupation_est_recuperee(self):
        n = self.scenario_nominal()
        premiere = self.appeler()
        seconde = self.appeler()
        self.assertTrue(premiere["ok"] and not premiere["noop"])
        self.assertEqual(3, premiere["supprimes"])
        self.assertTrue(seconde["ok"] and seconde["noop"])
        self.assertEqual(0, seconde["supprimes"])
        self.assertEqual(0, seconde["octets_liberes"])
        self.assertEqual(1, len(n.rm), "UN SEUL rm sur les deux executions : aucune double suppression")
        self.assertEqual(premiere["usage_apres_pct"], seconde["usage_apres_pct"])

    def test_cinq_executions_successives_ne_produisent_qu_une_suppression(self):
        n = self.scenario_nominal()
        for _ in range(5):
            self.assertTrue(self.appeler()["ok"])
        self.assertEqual(1, len(n.rm))

    def test_deux_appels_simultanes_ne_suppriment_qu_une_fois(self):
        n = self.scenario_nominal()
        n.delai = 0.02          # sans le verrou du Bridge, les deux appels liraient les 3 fichiers puis lanceraient deux rm
        resultats, barriere = [], threading.Barrier(2)

        def tache():
            barriere.wait()
            resultats.append(self.appeler())

        fils = [threading.Thread(target=tache) for _ in range(2)]
        [f.start() for f in fils]
        [f.join(10) for f in fils]
        self.assertEqual(2, len(resultats))
        self.assertTrue(all(r["ok"] for r in resultats))
        self.assertEqual(1, len(n.rm), "le verrou serialise : un seul rm")
        self.assertEqual(sorted([0, 3]), sorted(r["supprimes"] for r in resultats))

    def test_un_doublon_tardif_ne_peut_pas_faire_supprimer_les_fichiers_d_un_nouveau_jeton(self):
        n = self.scenario_nominal()
        self.assertTrue(self.appeler(incident_id="777")["ok"])         # incident 777 lie au jeton A, nettoye
        n.remplir(JETON_B, 2, int(2.2 * GIO))                          # NOUVEAU lancement (jeton B), occupation > 80 %
        res = self.appeler(incident_id="777")                          # doublon tardif de l'incident 777
        self.assertEqual("TOKEN_CHANGED", res["rejet"])
        self.assertFalse(res["ok"])
        self.assertEqual([nom(JETON_B, 1), nom(JETON_B, 2)], n.noms(), "les fichiers du nouveau jeton sont INTACTS")
        self.assertEqual(1, len(n.rm))

    def test_un_nouvel_incident_peut_nettoyer_un_nouveau_jeton(self):
        n = self.scenario_nominal()
        self.assertTrue(self.appeler(incident_id="777")["ok"])
        n.remplir(JETON_B, 2, int(2.2 * GIO))
        res = self.appeler(incident_id="778")
        self.assertTrue(res["ok"], res)
        self.assertEqual([], n.noms())
        self.assertEqual(2, len(n.rm))

    def test_les_liaisons_sont_bornees(self):
        self.scenario_nominal()
        for i in range(B.DISQUE_LIAISONS_MAX + 20):
            B._disque_lier(str(i), JETON_A)
        self.assertLessEqual(len(B._DISQUE_LIAISONS), B.DISQUE_LIAISONS_MAX)


# ============================================================ preuve 7 : un incident DISK etranger (sans fichiers du scenario) n'est pas touche
class IncidentEtrangerTests(BaseBridge):
    def test_disque_plein_sans_aucun_fichier_du_scenario_rien_n_est_touche_et_pas_de_succes(self):
        n = NoeudFictif(base_pct=84.0)                              # un VRAI incident : 84 % d'occupation, aucun fichier du scenario
        n.ajouter("README", 100)
        n.ajouter("journal.bin", GIO)
        avant = {k: dict(v) for k, v in n.entrees.items()}
        with mock.patch.object(B, "executer", n.executer):
            res = B.disque_reclamer("worker1", demande())
        self.assertFalse(res["ok"])
        self.assertFalse(res["execute"])
        self.assertEqual("NO_SCENARIO_FILES", res["rejet"])
        self.assertEqual([], n.rm, "aucune suppression")
        self.assertEqual(avant, n.entrees)
        self.assertEqual(2, res["etrangers"])
        self.assertIn("RIEN n a ete touche", res["stderr"])

    def test_repertoire_absent_et_occupation_normale_est_un_noop_reussi(self):
        n = NoeudFictif(base_pct=60.0, dossier=False)
        with mock.patch.object(B, "executer", n.executer):
            res = B.disque_reclamer("worker1", demande())
        self.assertTrue(res["ok"] and res["noop"])
        self.assertEqual([], n.rm)

    def test_repertoire_absent_et_disque_plein_n_est_pas_un_succes(self):
        n = NoeudFictif(base_pct=88.0, dossier=False)
        with mock.patch.object(B, "executer", n.executer):
            res = B.disque_reclamer("worker1", demande())
        self.assertFalse(res["ok"])
        self.assertEqual("NO_SCENARIO_FILES", res["rejet"])

    def test_erreur_ssh_sur_le_repertoire_refuse_sans_rien_supprimer(self):
        n = self.scenario_nominal()
        n.pannes.add("stat_dir_ko")
        res = self.appeler()
        self.assertEqual("DIRECTORY_UNREADABLE", res["rejet"])
        self.assertEqual([], n.rm)


class RecommendOnlyTests(BaseBridge):
    def test_validation_seule_ne_supprime_rien(self):
        n = self.scenario_nominal()
        res = self.appeler(recommend_only="true")
        self.assertTrue(res["ok"])
        self.assertFalse(res["execute"])
        self.assertEqual("ACCEPTED", res["validation"])
        self.assertEqual("RECOMMEND_ONLY", res["execution_mode"])
        self.assertEqual(3, res["fichiers"])
        self.assertEqual([], n.rm)
        self.assertEqual(3, len(n.entrees))


# ====================================================================================== aiguillage reel du handler HTTP (/action)
class ServeurBridge(object):
    def __enter__(self):
        self.srv = HTTPServer(("127.0.0.1", 0), B.Handler)
        self.th = threading.Thread(target=self.srv.serve_forever, daemon=True)
        self.th.start()
        return self

    def __exit__(self, *a):
        self.srv.shutdown()
        self.srv.server_close()
        self.th.join(5)

    def post(self, chemin, corps):
        # Boucle locale UNIQUEMENT, jamais de proxy (ProxyHandler({})) : ce serveur n ecoute que sur 127.0.0.1, port ephemere.
        ouvreur = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        req = urllib.request.Request("http://127.0.0.1:%d%s" % (self.srv.server_address[1], chemin),
                                     data=json.dumps(corps).encode("utf-8"), headers={"Content-Type": "application/json"})
        with ouvreur.open(req, timeout=15) as r:
            return json.loads(r.read().decode("utf-8"))


class HandlerHttpTests(BaseBridge):
    def setUp(self):
        super().setUp()
        for p in (mock.patch.object(B, "JETON", ""), mock.patch.object(B, "noeud_kubectl", lambda force=False: "master")):
            p.start()
            self.addCleanup(p.stop)

    def test_la_demande_explicite_n_est_pas_ecrasee_par_le_catalogue_et_atteint_la_capacite_disque(self):
        n = self.scenario_nominal()
        with ServeurBridge() as s:
            res = s.post("/action", demande())
        self.assertEqual("HOST_RECLAIM_DISK", res["action"], "NON substituee par HOST_IMAGE_GC (ACTION_PAR_TYPE_MESURE)")
        self.assertTrue(res["ok"], res)
        self.assertTrue(res["execute"])
        self.assertEqual(3, res["supprimes"])
        self.assertEqual([], n.noms())
        self.assertFalse(any("vacuum" in c for c in n.log), "le nettoyage generique de HOST_IMAGE_GC n'est JAMAIS emis")

    def test_une_cible_hors_allowlist_est_refusee_au_niveau_http_sans_commande(self):
        n = self.scenario_nominal()
        with ServeurBridge() as s:
            res = s.post("/action", demande(cible="k8s-worker2", noeud="k8s-worker2"))
        self.assertFalse(res["ok"])
        self.assertEqual("REJECTED_TARGET_NOT_ALLOWLISTED", res["rejet"])
        self.assertEqual([], n.log)

    def test_le_chemin_historique_host_image_gc_reste_inchange_et_ne_vise_pas_le_repertoire_du_scenario(self):
        commandes = []

        def faux_executer(noeud, commande):
            commandes.append((noeud, commande))
            return {"host": noeud, "ip": "x", "command": commande, "exitCode": 0, "stdout": "ok", "stderr": "", "ok": True, "resume": "ok"}

        with mock.patch.object(B, "executer", faux_executer), ServeurBridge() as s:
            res = s.post("/action", {"action": "HOST_IMAGE_GC", "type": "NODE_DISK_FULL", "ns": "k8s-nodes", "cible": "k8s-worker1",
                                     "noeud": "k8s-worker1"})
        self.assertEqual("HOST_IMAGE_GC", res["action"])
        self.assertEqual(1, len(commandes))
        self.assertIn("journalctl --vacuum-size=200M", commandes[0][1])
        self.assertNotIn("helios-diskfill", commandes[0][1])

    def test_la_capacite_est_annoncee_par_actions_et_absente_du_catalogue_historique(self):
        src = open(CIBLE, encoding="utf-8").read()
        self.assertIn("_NOMS += list(ACTIONS_DISQUE)", src)
        self.assertEqual((None, None), B.catalogue("HOST_RECLAIM_DISK", "k8s-nodes", "k8s-worker1", "worker1"),
                         "le catalogue historique ne sait PAS executer HOST_RECLAIM_DISK : seule la voie explicite le fait")
        self.assertEqual("REMEDIATION", B.semantique_action("HOST_RECLAIM_DISK"))
        self.assertEqual([], self.noeud.log, "catalogue() n'a emis aucune commande")


# ============================================================================================ perimetre : rien d'autre ne change
class PerimetreTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.src = open(CIBLE, encoding="utf-8").read()
        cls.tree = ast.parse(cls.src)

    def setUp(self):
        # catalogue() appelle noeud_kubectl(), qui sonde les noeuds en SSH : ni l un ni l autre ne doivent pouvoir partir d ici.
        def ssh_interdit(noeud, commande):
            raise AssertionError("SSH interdit dans ce test : %s %r" % (noeud, commande))

        for p in (mock.patch.object(B, "noeud_kubectl", lambda force=False: "master"),
                  mock.patch.object(B, "executer", ssh_interdit)):
            p.start()
            self.addCleanup(p.stop)

    def test_les_tables_cpu_et_vm_et_la_politique_par_type_sont_inchangees(self):
        self.assertEqual("HOST_IMAGE_GC", B.ACTION_PAR_TYPE_MESURE["NODE_DISK_FULL"])
        self.assertEqual("ESCALATE_NODE_DISK_PRESSURE", B.ACTION_PAR_TYPE_MESURE["NODE_DISK_PRESSURE"])
        self.assertEqual("ESCALATE_HOST_CPU_SATURATION", B.ACTION_PAR_TYPE_MESURE["NODE_HIGH_CPU"])
        self.assertEqual("ESCALATE_NODE_NOT_READY", B.ACTION_PAR_TYPE_MESURE["NODE_NOT_READY"])
        self.assertEqual("HOST_CLEAN_INODES", B.ACTION_PAR_TYPE_MESURE["NODE_INODES_FULL"])
        self.assertNotIn("HOST_RECLAIM_DISK", B.ACTION_PAR_TYPE_MESURE.values(),
                         "le Bridge ne CHOISIT toujours pas cette action : aucune regle type -> HOST_RECLAIM_DISK")
        self.assertEqual(("HOST_SERVICE_CPU_QUOTA", "HOST_SERVICE_CPU_QUOTA_REMOVE", "HOST_SERVICE_CPU_QUOTA_STATUS"), B.ACTIONS_QUOTA)
        self.assertEqual(("STOP_CPU_LOAD_SERVICE", "START_CPU_LOAD_SERVICE", "CPU_LOAD_SERVICE_STATUS"), B.ACTIONS_CPULOAD)
        self.assertEqual("__poweron_interdit__", B.catalogue("RESTART_NODE_VM", "", "", "worker1")[0])

    def test_les_seules_commandes_de_la_capacite_sont_stat_find_rm_et_aucune_autre(self):
        autorises = {"/usr/bin/stat", "/usr/bin/find", "/usr/bin/rm"}
        trouves, interdits = set(), ("rm -rf", "rm -r ", " -delete", "-exec", "*", "systemctl", "kubectl", "crictl", "vmrun",
                                     "mkfs", "truncate", " dd ", "chmod", "chown", "mv ", ">/", "| sh", "bash -c")
        for n in ast.walk(self.tree):
            if isinstance(n, ast.FunctionDef) and (n.name.startswith("disque_") or n.name.startswith("_disque_")):
                for c in ast.walk(n):
                    if isinstance(c, ast.Constant) and isinstance(c.value, str) and "sudo" in c.value:
                        for mot in c.value.split():
                            if mot.startswith("/usr/bin/"):
                                trouves.add(mot)
                        for motif in interdits:
                            self.assertNotIn(motif, c.value, "commande de la capacite DISK : motif interdit %r dans %r" % (motif, c.value))
        self.assertEqual(autorises, trouves)

    def test_aucune_fonction_existante_du_bridge_n_a_ete_retiree(self):
        noms = {n.name for n in ast.walk(self.tree) if isinstance(n, ast.FunctionDef)}
        for attendu in ("catalogue", "quota_appliquer", "quota_retirer", "quota_valider", "cpuload_cible", "verifier_incident",
                        "valider_action_explicite", "preparer_rollback", "notification_supprimee", "executer"):
            self.assertIn(attendu, noms)
        self.assertNotIn("rallumer", noms, "la politique VM_DOWN : le Bridge n'a toujours AUCUNE mise sous tension")

    def test_les_marqueurs_de_la_modification_sont_presents_et_localises(self):
        self.assertEqual(5, self.src.count("DISK-E2E-158-01"), "bloc + exemption + aiguillage + methode(doc) + annonce /actions")

    def test_la_capacite_ne_depend_d_aucun_secret_ni_d_une_variable_d_environnement_nouvelle(self):
        debut = self.src.index("# DISK-E2E-158-01 - CAPACITE D EXECUTION")
        fin = self.src.index("# ACTION-CAPABILITY-EXTENSION-01 (2026-09-30)")
        bloc = self.src[debut:fin]
        self.assertNotIn("os.environ", bloc)
        self.assertNotIn("urllib", bloc)
        self.assertNotIn("subprocess", bloc)


if __name__ == "__main__":
    unittest.main(verbosity=2)
