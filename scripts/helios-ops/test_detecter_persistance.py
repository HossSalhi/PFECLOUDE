# -*- coding: utf-8 -*-
"""DETECTOR-PRIORITY-03 (12 nov. 2026, DEPLOYE sur .160 a 09:43) - une anomalie PERSISTANTE passe « de fond » (generalise PRIORITY-01 et -02).

Le detecteur HELIOS n'elit QU'UNE anomalie par cycle : la 1re dans l'ordre des regles. Une condition qui dure (disque de
supervision a 80 %, VM eteinte, noeud cordonne, memoire haute...) occupait le verdict en permanence et rendait inatteignables
toutes les regles de rang inferieur : un test de charge CPU (rang 11) restait sans incident. Ces tests figent la regle
generale (anciennete >= 10 min sans interruption de plus de 5 min => « de fond »), prouvent qu'une anomalie NOUVELLE garde sa
priorite, que le format de sortie ne change pas, et que toute erreur d'etat retombe sur l'election des versions precedentes.

Lancer : python -I test_detecter_persistance.py -v                    (depuis scripts/helios-ops)
Tester un autre fichier :  HELIOS_DETECTER_PATH=<chemin> python -I test_detecter_persistance.py

Le detecteur est charge par chemin ; http / prom / loki / horloge sont remplaces ; l'etat va dans un dossier temporaire :
aucun reseau, aucune VM, aucun secret, aucune ecriture hors du dossier temporaire.
"""
import contextlib
import importlib.util
import io
import json
import os
import random
import shutil
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
CANDIDAT = os.path.join(HERE, "detecter.candidate.py")
# DEPLOYE sur .160 le 12 nov. 2026 a 09:43 (sha256 147082bd4cea...) : la cible par defaut est la BASELINE (octet pour octet le
# fichier de production). Le candidat reste la source de deploy-detecteur-160.sh pour le PROCHAIN lot.
BASELINE = os.path.join(os.path.abspath(os.path.join(HERE, "..", "..")), "infra", "helios-sot", "baseline", "detecteur", "detecter.py")
CIBLE = os.environ.get("HELIOS_DETECTER_PATH") or BASELINE

IP = {"k8s-master": "192.168.148.153", "k8s-worker2": "192.168.148.155", "k8s-worker1": "192.168.148.158",
      "monitoring": "192.168.148.160", "iacamunda": "192.168.148.161"}
HOTES = sorted(IP)
NAMESPACES = ["pfe", "default", "kube-system", "monitoring", "calico-system"]
T0 = 1_800_000_000.0

ORDRE_ATTENDU = [
    "ETCD_NO_LEADER", "ETCD_SLOW_FSYNC", "ETCD_LEADER_FLAPPING", "NODE_NOT_READY", "NODE_DISK_PRESSURE",
    "NODE_PID_PRESSURE", "NODE_NETWORK_UNAVAILABLE", "NODE_CORDONED", "NODE_DISK_FULL", "NODE_HIGH_MEMORY",
    "NODE_HIGH_CPU", "NODE_TIME_DRIFT", "NODE_INODES_FULL", "NODE_FD_EXHAUSTED", "NODE_OOM_KILL", "NODE_CPU_STEAL",
    "NODE_DISK_SLOW", "NODE_LOAD_SATURATION", "NODE_NETWORK_ERRORS", "PVC_NOT_BOUND", "POD_IMAGE_PULL_BACKOFF",
    "POD_CRASH_LOOP", "POD_CONFIG_ERROR", "MEMORY_STRESS", "POD_INIT_CONTAINER_FAILURE",
    "POD_PENDING_INSUFFICIENT_RESOURCES", "SERVICE_UNAVAILABLE", "BRIDGE_DOWN", "TARGET_DOWN",
    "POD_NOT_READY_PERSISTENT", "DNS_FAILURE", "DATABASE_CONNECTIVITY", "NETWORK_PARTITION", "HIGH_ERROR_RATE",
    "CPU_STRESS", "DEPLOYMENT_UNAVAILABLE",
]


def charger(etat_dir):
    spec = importlib.util.spec_from_file_location("detecter_v3_test", CIBLE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.ETAT_DIR = etat_dir
    return mod


def metric(mode, nom, ns="pfe"):
    if mode == "hote":
        return {"instance": IP.get(nom, nom) + ":9100"}
    if mode == "noeud":
        return {"node": nom}
    if mode == "pod":
        return {"namespace": ns, "pod": nom + "-5d8f7c9b6-x2k4q"}
    if mode == "deploiement":
        return {"namespace": ns, "deployment": nom}
    if mode == "endpoint":
        return {"namespace": ns, "endpoint": nom}
    if mode == "app":
        return {"namespace": ns, "app": nom}
    if mode == "hpa":
        return {"namespace": ns, "horizontalpodautoscaler": nom}
    if mode == "pvc":
        return {"namespace": ns, "persistentvolumeclaim": nom}
    if mode == "fichier":
        return {"filename": "/var/log/containers/%s-5d8f7c9b6-x2k4q_%s_ctr-0123456789ab.log" % (nom, ns)}
    raise AssertionError("mode d'extraction non gere par le test : %s" % mode)


def serie(mode, nom, valeur="1", ns="pfe"):
    return {"metric": metric(mode, nom, ns), "value": [0, str(valeur)]}


class Banc:
    """Un detecteur charge, une horloge pilotable et un dossier d'etat temporaire."""

    def __init__(self, etat_dir=None, desactive=False):
        self.cree = etat_dir is None and not desactive
        self.dir = "" if desactive else (etat_dir or tempfile.mkdtemp(prefix="helios-etat-"))
        self.mod = charger(self.dir)
        self.t = T0
        self.mod.horloge = lambda: self.t
        par_requete = {}
        for typ, requete, _s, _m in self.mod.REGLES:
            assert requete not in par_requete, "deux regles partagent la meme requete"
            par_requete[requete] = typ
        self.par_requete = par_requete

    def nettoyer(self):
        if self.cree:
            shutil.rmtree(self.dir, ignore_errors=True)

    def lancer(self, matches, avance=0.0, argv=None):
        self.t += avance

        def faux(requete):
            if requete == "up":
                return [{"metric": {}, "value": [0, "1"]}]
            return list(matches.get(self.par_requete.get(requete), []))

        self.mod.http = lambda url, delai=12: {"database": "ok", "version": "test"}
        self.mod.prom = faux
        self.mod.loki = faux
        ancien = sys.argv
        sys.argv = ["detecter.py"] + list(argv or [])
        try:
            sortie = io.StringIO()
            with contextlib.redirect_stdout(sortie):
                self.mod.main()
        finally:
            sys.argv = ancien
        texte = sortie.getvalue()
        return texte if argv else json.loads(texte)


class BancTestCase(unittest.TestCase):
    def banc(self, **kw):
        b = Banc(**kw)
        self.addCleanup(b.nettoyer)
        return b


# ------------------------------------------------------------------------------------------------ fonction pure
class MajPersistanceTests(unittest.TestCase):
    def setUp(self):
        self.mod = charger("")

    def test_premiere_vue_anciennete_zero(self):
        etat, ages = self.mod.maj_persistance({}, ["A|x"], 1000.0)
        self.assertEqual(ages, {"A|x": 0.0})
        self.assertEqual(etat, {"A|x": {"first": 1000.0, "last": 1000.0}})

    def test_continuite_conserve_le_debut(self):
        etat, _ = self.mod.maj_persistance({}, ["A|x"], 1000.0)
        etat, ages = self.mod.maj_persistance(etat, ["A|x"], 1060.0)
        self.assertEqual(ages["A|x"], 60.0)
        self.assertEqual(etat["A|x"]["first"], 1000.0)

    def test_interruption_courte_ne_remet_pas_a_zero(self):
        etat, _ = self.mod.maj_persistance({}, ["A|x"], 1000.0)
        etat, _ = self.mod.maj_persistance(etat, [], 1100.0)            # absent 100 s
        self.assertIn("A|x", etat)                                      # encore garde
        etat, ages = self.mod.maj_persistance(etat, ["A|x"], 1250.0)    # de retour apres 250 s
        self.assertEqual(ages["A|x"], 250.0)

    def test_longue_absence_remet_a_zero(self):
        etat, _ = self.mod.maj_persistance({}, ["A|x"], 1000.0)
        etat, ages = self.mod.maj_persistance(etat, ["A|x"], 1000.0 + self.mod.INTERRUPTION_MAX_S + 1)
        self.assertEqual(ages["A|x"], 0.0)

    def test_couple_disparu_est_oublie(self):
        etat, _ = self.mod.maj_persistance({}, ["A|x"], 1000.0)
        etat, _ = self.mod.maj_persistance(etat, [], 1000.0 + self.mod.INTERRUPTION_MAX_S + 1)
        self.assertNotIn("A|x", etat)

    def test_horloge_revenue_en_arriere(self):
        etat = {"A|x": {"first": 500.0, "last": 2000.0}}
        _, ages = self.mod.maj_persistance(etat, ["A|x"], 1000.0)
        self.assertEqual(ages["A|x"], 0.0)

    def test_entrees_corrompues_sans_exception(self):
        etat = {"A|x": "bizarre", "B|y": {"first": "abc"}, "C|z": None, "D|w": {"first": 1, "last": "x"}}
        nouveau, ages = self.mod.maj_persistance(etat, ["A|x", "B|y", "C|z", "D|w"], 1000.0)
        self.assertEqual(set(ages), {"A|x", "B|y", "C|z", "D|w"})
        self.assertTrue(all(v == 0.0 for v in ages.values()))

    def test_etat_non_dictionnaire(self):
        for etat in (None, [], "texte", 12):
            nouveau, ages = self.mod.maj_persistance(etat, ["A|x"], 1000.0)
            self.assertEqual(ages, {"A|x": 0.0})


# ------------------------------------------------------------------------------------------------ entrees / sorties
class EtatEntreesSortiesTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="helios-etat-")
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.mod = charger(self.dir)

    def test_aller_retour(self):
        etat = {"A|x": {"first": 1.0, "last": 2.0}}
        self.mod.ecrire_etat(etat)
        self.assertEqual(self.mod.lire_etat(), etat)

    def test_fichier_corrompu(self):
        with open(self.mod._etat_chemin(), "w", encoding="utf-8") as f:
            f.write("{pas du json")
        self.assertEqual(self.mod.lire_etat(), {})

    def test_json_qui_n_est_pas_un_dictionnaire(self):
        with open(self.mod._etat_chemin(), "w", encoding="utf-8") as f:
            f.write("[1, 2, 3]")
        self.assertEqual(self.mod.lire_etat(), {})

    def test_dossier_inexistant_sans_exception(self):
        self.mod.ETAT_DIR = os.path.join(self.dir, "n-existe-pas", "du-tout")
        self.mod.ecrire_etat({"A|x": {"first": 1.0, "last": 2.0}})
        self.assertEqual(self.mod.lire_etat(), {})

    def test_etat_desactive_n_ecrit_rien(self):
        self.mod.ETAT_DIR = ""
        self.mod.ecrire_etat({"A|x": {"first": 1.0, "last": 2.0}})
        self.assertEqual(os.listdir(self.dir), [])
        self.assertEqual(self.mod.lire_etat(), {})

    def test_ecriture_atomique_sans_fichier_temporaire_residuel(self):
        for i in range(5):
            self.mod.ecrire_etat({"A|x": {"first": float(i), "last": float(i + 1)}})
        self.assertEqual([n for n in os.listdir(self.dir) if n.endswith(".tmp")], [])
        self.assertEqual(len(os.listdir(self.dir)), 1)

    def test_un_fichier_par_utilisateur(self):
        with mock.patch.object(self.mod.os, "getuid", create=True, return_value=1234):
            self.assertTrue(self.mod._etat_chemin().endswith("helios-detecteur-etat-1234.json"))
        with mock.patch.object(self.mod.os, "getuid", create=True, return_value=0):
            self.assertTrue(self.mod._etat_chemin().endswith("helios-detecteur-etat-0.json"))


# ------------------------------------------------------------------------------------------------ election
class ElectionPersistanteTests(BancTestCase):
    def test_anomalie_nouvelle_de_rang_eleve_garde_sa_priorite(self):
        b = self.banc()
        r = b.lancer({"NODE_NOT_READY": [serie("noeud", "k8s-worker1")],
                      "NODE_HIGH_CPU": [serie("hote", "k8s-worker2", 96.5)]})
        self.assertEqual((r["type"], r["cible"]), ("NODE_NOT_READY", "k8s-worker1"))

    def sonde(self, b, nr, cible_cpu, avance=0.0):
        """Fait apparaitre une anomalie CPU NOUVELLE (cible jamais vue) a cote de `nr` et dit qui est elu."""
        return b.lancer(dict(nr, NODE_HIGH_CPU=[serie("hote", cible_cpu, 96.5)]), avance=avance)["type"]

    def test_la_meme_anomalie_persistante_laisse_passer_le_cpu(self):
        """Le scenario reel : une anomalie de rang eleve dure deja ; le test de charge produit une anomalie CPU
        NOUVELLE apres ~8 min. Tant que l'ancienne a moins de 10 min elle passe devant ; ensuite, le CPU."""
        b = self.banc()
        nr = {"NODE_NOT_READY": [serie("noeud", "k8s-worker1")]}
        cpu = {"NODE_HIGH_CPU": [serie("hote", "k8s-worker2", 96.5)]}
        for i in range(8):                                  # 8 min : NODE_NOT_READY seule
            b.lancer(nr, avance=60.0 if i else 0.0)
        for i in range(8, 14):                              # puis les deux
            r = b.lancer(dict(nr, **cpu), avance=60.0)
            age_nr = i * 60
            attendu = "NODE_HIGH_CPU" if age_nr >= b.mod.DUREE_PERSISTANTE_S else "NODE_NOT_READY"
            self.assertEqual(r["type"], attendu, "NODE_NOT_READY present depuis %d s" % age_nr)
            self.assertEqual(r["matches"], ["NODE_NOT_READY", "NODE_HIGH_CPU"])

    def test_cas_du_11_nov_disque_monitoring_persistant_et_cpu(self):
        b = self.banc()
        b.mod.CIBLES_DE_FOND = set()            # prouve la regle GENERALE, sans l'exception statique de PRIORITY-02
        disque = {"NODE_DISK_FULL": [serie("hote", "monitoring", 81.1)]}
        for _ in range(11):
            r = b.lancer(disque, avance=60.0)
        self.assertEqual(r["type"], "NODE_DISK_FULL")                  # seul : toujours elu
        r = b.lancer(dict(disque, NODE_HIGH_CPU=[serie("hote", "k8s-worker1", 96.2)]), avance=60.0)
        self.assertEqual((r["type"], r["cible"]), ("NODE_HIGH_CPU", "k8s-worker1"))

    def test_memoire_haute_persistante_ne_masque_plus_le_cpu(self):
        b = self.banc()
        memoire = {"NODE_HIGH_MEMORY": [serie("hote", "iacamunda", 93.5)]}
        for _ in range(11):
            b.lancer(memoire, avance=60.0)
        r = b.lancer(dict(memoire, NODE_HIGH_CPU=[serie("hote", "k8s-master", 97.0)]), avance=60.0)
        self.assertEqual((r["type"], r["cible"]), ("NODE_HIGH_CPU", "k8s-master"))

    def test_noeud_cordonne_oublie_ne_masque_plus_rien(self):
        b = self.banc()
        cordon = {"NODE_CORDONED": [serie("noeud", "k8s-worker2")]}
        for _ in range(11):
            b.lancer(cordon, avance=60.0)
        r = b.lancer(dict(cordon, NODE_HIGH_CPU=[serie("hote", "k8s-worker1", 97.0)]), avance=60.0)
        self.assertEqual(r["type"], "NODE_HIGH_CPU")

    def test_persistante_seule_reste_elue(self):
        b = self.banc()
        seule = {"NODE_NOT_READY": [serie("noeud", "k8s-worker1")]}
        for _ in range(15):
            r = b.lancer(seule, avance=60.0)
        self.assertEqual((r["type"], r["cible"], r["nbAnomalies"]), ("NODE_NOT_READY", "k8s-worker1", 1))

    def test_interruption_courte_ne_remet_pas_a_zero_l_election(self):
        b = self.banc()
        nr = {"NODE_NOT_READY": [serie("noeud", "k8s-worker1")]}
        b.lancer(nr)                                  # t0 : debut de la presence
        b.lancer({}, avance=100.0)                    # absent a t0+100
        b.lancer({}, avance=100.0)                    # absent a t0+200
        self.assertEqual(self.sonde(b, nr, "k8s-master", avance=50.0), "NODE_NOT_READY")   # t0+250 : anciennete 250
        for _ in range(5):
            b.lancer(nr, avance=60.0)                 # jusqu'a t0+550
        self.assertEqual(self.sonde(b, nr, "k8s-worker2"), "NODE_NOT_READY")               # anciennete 550 : recente
        b.lancer(nr, avance=60.0)                     # t0+610
        self.assertEqual(self.sonde(b, nr, "iacamunda"), "NODE_HIGH_CPU")                  # anciennete 610 : de fond

    def test_longue_interruption_remet_a_zero_l_election(self):
        b = self.banc()
        nr = {"NODE_NOT_READY": [serie("noeud", "k8s-worker1")]}
        b.lancer(nr)                                  # t0
        b.lancer(nr, avance=100.0)                    # t0+100
        for _ in range(4):
            b.lancer({}, avance=100.0)                # absent de t0+200 a t0+500
        self.assertEqual(self.sonde(b, nr, "k8s-master", avance=100.0), "NODE_NOT_READY")  # t0+600 : repart de zero
        self.assertEqual(self.sonde(b, nr, "k8s-worker2", avance=200.0), "NODE_NOT_READY")  # t0+800 : anciennete 200
        for _ in range(7):
            b.lancer(nr, avance=60.0)                 # jusqu'a t0+1220 : anciennete 620
        self.assertEqual(self.sonde(b, nr, "iacamunda"), "NODE_HIGH_CPU")                  # de fond

    def test_toutes_de_fond_le_premier_match_l_emporte(self):
        b = self.banc()
        a = {"NODE_NOT_READY": [serie("noeud", "k8s-worker1")], "NODE_CORDONED": [serie("noeud", "k8s-worker2")]}
        for _ in range(12):
            r = b.lancer(a, avance=60.0)
        self.assertEqual((r["type"], r["cible"]), ("NODE_NOT_READY", "k8s-worker1"))
        self.assertEqual(r["matches"], ["NODE_NOT_READY", "NODE_CORDONED"])

    def test_plus_recent_d_abord_quand_tout_est_de_fond(self):
        """Une vieille anomalie de rang eleve ne doit pas affamer une anomalie plus recente, meme devenue de fond."""
        b = self.banc()
        nr = {"NODE_NOT_READY": [serie("noeud", "k8s-worker1")]}
        cpu = {"NODE_HIGH_CPU": [serie("hote", "k8s-worker2", 96.5)]}
        for i in range(60):                                 # 1 h : NODE_NOT_READY seule
            b.lancer(nr, avance=60.0 if i else 0.0)
        for _ in range(12):                                 # le CPU arrive et dure 12 min : tous deux de fond
            r = b.lancer(dict(nr, **cpu), avance=60.0)
        self.assertEqual((r["type"], r["cible"]), ("NODE_HIGH_CPU", "k8s-worker2"))     # le plus recent, pas le rang

    def test_debut_simultane_departage_par_le_rang(self):
        """Limite assumee : deux anomalies apparues au meme instant et durant plus de 10 min restent departagees par
        l'ordre des regles (comportement historique)."""
        b = self.banc()
        both = {"NODE_NOT_READY": [serie("noeud", "k8s-worker1")], "NODE_HIGH_CPU": [serie("hote", "k8s-worker2", 96.5)]}
        for i in range(14):
            r = b.lancer(both, avance=60.0 if i else 0.0)
        self.assertEqual(r["type"], "NODE_NOT_READY")

    def test_serie_fraiche_preferee_a_la_persistante_dans_une_meme_regle(self):
        b = self.banc()
        b.mod.CIBLES_DE_FOND = set()
        ancienne = [serie("hote", "k8s-worker1", 82.0)]
        for _ in range(11):
            b.lancer({"NODE_DISK_FULL": ancienne}, avance=60.0)
        r = b.lancer({"NODE_DISK_FULL": ancienne + [serie("hote", "k8s-worker2", 83.0)],
                      "NODE_HIGH_CPU": [serie("hote", "k8s-master", 97.0)]}, avance=60.0)
        self.assertEqual((r["type"], r["cible"]), ("NODE_DISK_FULL", "k8s-worker2"))   # la nouvelle, pas l'ancienne

    def test_urgence_disque_monitoring_garde_sa_priorite_tant_qu_elle_est_recente(self):
        b = self.banc()
        urgence = {"NODE_DISK_FULL": [serie("hote", "monitoring", 95.0)]}
        cpu = {"NODE_HIGH_CPU": [serie("hote", "k8s-worker2", 97.0)]}
        for i in range(8):
            b.lancer(urgence, avance=60.0 if i else 0.0)               # 8 min : disque en urgence, seul
        r = b.lancer(dict(urgence, **cpu), avance=60.0)                # t0+480 : le CPU arrive
        self.assertEqual(r["type"], "NODE_DISK_FULL")                  # PRIORITY-02 : >= 90 % = urgence, et recente
        b.lancer(dict(urgence, **cpu), avance=60.0)                    # t0+540
        r = b.lancer(dict(urgence, **cpu), avance=60.0)                # t0+600 : anciennete 600
        self.assertEqual(r["type"], "NODE_HIGH_CPU")                   # PRIORITY-03 : au-dela de 10 min, de fond

    def test_deux_anomalies_nouvelles_gardent_l_ordre_statique(self):
        b = self.banc()
        r = b.lancer({"NODE_HIGH_MEMORY": [serie("hote", "iacamunda", 93.0)],
                      "NODE_HIGH_CPU": [serie("hote", "k8s-master", 97.0)]})
        self.assertEqual(r["type"], "NODE_HIGH_MEMORY")

    def test_une_nouvelle_anomalie_apparait_pendant_qu_une_ancienne_est_de_fond(self):
        b = self.banc()
        ancienne = {"NODE_HIGH_MEMORY": [serie("hote", "iacamunda", 93.0)]}
        for _ in range(11):
            b.lancer(ancienne, avance=60.0)
        # une anomalie NOUVELLE de rang eleve (NODE_NOT_READY, rang 4) doit passer devant tout
        r = b.lancer(dict(ancienne, NODE_NOT_READY=[serie("noeud", "k8s-worker1")],
                          NODE_HIGH_CPU=[serie("hote", "k8s-master", 97.0)]), avance=60.0)
        self.assertEqual((r["type"], r["cible"]), ("NODE_NOT_READY", "k8s-worker1"))


# ------------------------------------------------------------------------------------------------ non-regression
def historique(mod, matches):
    """Election de reference (PRIORITY-02, deployee) : 1er match hors « de fond » statique, sinon le 1er."""
    trouves = []
    for typ, _requete, _source, mode in mod.REGLES:
        series = matches.get(typ) or []
        if not series:
            continue
        choisie = None
        for s in series:
            info = mod.extraire(mode, s)
            if info.get("ns") not in mod.SYSTEME and not statique(mod, typ, info, s):
                choisie = (s, info)
                break
        if choisie is None:
            for s in series:
                info = mod.extraire(mode, s)
                if info.get("ns") not in mod.SYSTEME:
                    choisie = (s, info)
                    break
        if choisie is None:
            choisie = (series[0], mod.extraire(mode, series[0]))
        s, info = choisie
        if not info.get("cible"):
            continue
        trouves.append((typ, info, s))
    if not trouves:
        return None
    typ, info, _s = next((m for m in trouves if not statique(mod, m[0], m[1], m[2])), trouves[0])
    return typ, info.get("cible"), info.get("ns")


def statique(mod, typ, info, s):
    if typ in mod.REGLES_DE_FOND:
        return True
    if (typ, info.get("cible")) in mod.CIBLES_DE_FOND:
        return float(s["value"][1]) < mod.SEUIL_URGENCE_FOND
    return False


class NonRegressionTests(BancTestCase):
    def scenario(self, rnd, mod):
        regles = [(r[0], r[2], r[3]) for r in mod.REGLES]
        matches = {}
        for typ, _source, mode in rnd.sample(regles, rnd.randint(1, 7)):
            series = []
            for _ in range(rnd.randint(1, 3)):
                nom = rnd.choice(HOTES) if mode in ("hote", "noeud") else rnd.choice(
                    ["backend", "frontend", "postgres-4", "helios-latency"])
                valeur = round(rnd.uniform(80.5, 99.0), 2) if typ == "NODE_DISK_FULL" else "1"
                series.append(serie(mode, nom, valeur, rnd.choice(NAMESPACES)))
            matches[typ] = series
        return matches

    def test_premier_cycle_identique_a_l_election_deployee(self):
        """Etat vide (premier cycle) : election IDENTIQUE a celle de PRIORITY-02 deployee. 500 scenarios."""
        rnd = random.Random(20261112)
        b = self.banc()
        for i in range(500):
            chemin = b.mod._etat_chemin()
            if os.path.exists(chemin):
                os.remove(chemin)                  # premier cycle : etat vide
            matches = self.scenario(rnd, b.mod)
            r = b.lancer(matches)
            self.assertEqual((r["type"], r["cible"], r["ns"]), historique(b.mod, matches) or ("NO_INCIDENT", "aucun", "aucun"),
                             "scenario %d : %s" % (i, matches))

    def test_etat_desactive_identique_a_l_election_deployee_meme_apres_longtemps(self):
        """ETAT_DIR vide : jamais de demotion, meme apres 40 min de presence continue (comportement des versions precedentes)."""
        rnd = random.Random(20261113)
        for i in range(100):
            b = self.banc(desactive=True)
            matches = self.scenario(rnd, b.mod)
            attendu = historique(b.mod, matches) or ("NO_INCIDENT", "aucun", "aucun")
            for _ in range(5):
                r = b.lancer(matches, avance=600.0)
                self.assertEqual((r["type"], r["cible"], r["ns"]), attendu, "scenario %d" % i)

    def test_schema_de_sortie_inchange(self):
        """Camunda, le Bridge et alarme_exporter.py lisent ce JSON : aucune cle ajoutee ni retiree."""
        b = self.banc()
        r = b.lancer({"NODE_HIGH_CPU": [serie("hote", "k8s-worker1", 96)]})
        self.assertEqual(set(r), {"type", "ns", "cible", "noeud", "nbAnomalies", "monitoringFiable", "preuve",
                                  "severite", "grafana", "matches", "nbErreursLogs"})

    def test_aucun_match(self):
        b = self.banc()
        r = b.lancer({})
        self.assertEqual((r["type"], r["nbAnomalies"], r["matches"], r["severite"]), ("NO_INCIDENT", 0, [], "none"))


class RobustesseTests(BancTestCase):
    def matches(self):
        return {"NODE_DISK_FULL": [serie("hote", "monitoring", 81.1)],
                "NODE_HIGH_CPU": [serie("hote", "k8s-worker1", 96.2)]}

    def attendu_v2(self, b):
        return ("NODE_HIGH_CPU", "k8s-worker1")      # PRIORITY-02 : le disque de monitoring (81 %) est « de fond »

    def test_erreur_de_lecture_de_l_etat(self):
        b = self.banc()
        def boom():
            raise RuntimeError("lecture impossible")
        b.mod.lire_etat = boom
        r = b.lancer(self.matches())
        self.assertEqual((r["type"], r["cible"]), self.attendu_v2(b))

    def test_erreur_d_ecriture_de_l_etat(self):
        b = self.banc()
        def boom(_etat):
            raise OSError("disque plein")
        b.mod.ecrire_etat = boom
        r = b.lancer(self.matches())
        self.assertEqual((r["type"], r["cible"]), self.attendu_v2(b))

    def test_erreur_dans_le_calcul_de_persistance(self):
        b = self.banc()
        def boom(*_a, **_k):
            raise ValueError("calcul impossible")
        b.mod.maj_persistance = boom
        r = b.lancer(self.matches())
        self.assertEqual((r["type"], r["cible"]), self.attendu_v2(b))

    def test_etat_corrompu_sur_disque(self):
        b = self.banc()
        with open(b.mod._etat_chemin(), "w", encoding="utf-8") as f:
            f.write("\x00\x01 pas du json")
        r = b.lancer(self.matches())
        self.assertEqual((r["type"], r["cible"]), self.attendu_v2(b))
        self.assertIsInstance(b.mod.lire_etat(), dict)                  # et le fichier est reecrit proprement

    def test_deux_appelants_concurrents_sur_le_meme_etat(self):
        a = self.banc()
        b = self.banc(etat_dir=a.dir)
        nr = {"NODE_NOT_READY": [serie("noeud", "k8s-worker1")]}
        cpu = {"NODE_HIGH_CPU": [serie("hote", "k8s-worker2", 96.5)]}
        for i in range(14):
            for banc in (a, b):
                banc.t = T0 + 60.0 * i
                r = banc.lancer(nr if i < 8 else dict(nr, **cpu))
        self.assertEqual(r["type"], "NODE_HIGH_CPU")                    # de fond apres 10 min, pour les deux
        self.assertIsInstance(a.mod.lire_etat(), dict)


class DiagnosticTests(BancTestCase):
    def test_mode_diag_rapport_lisible_et_pas_de_json(self):
        b = self.banc()
        nr = {"NODE_NOT_READY": [serie("noeud", "k8s-worker1")]}
        matches = dict(nr, NODE_HIGH_CPU=[serie("hote", "k8s-worker2", 96.5)])
        for i in range(8):
            b.lancer(nr, avance=60.0 if i else 0.0)
        for _ in range(3):
            b.lancer(matches, avance=60.0)
        texte = b.lancer(matches, avance=60.0, argv=["--diag"])
        self.assertIn("DIAGNOSTIC DU DETECTEUR", texte)
        self.assertIn("NODE_NOT_READY", texte)
        self.assertIn("DE FOND", texte)
        self.assertIn("ELU", texte)
        self.assertIn("NODE_HIGH_CPU", texte)
        self.assertIn("Verdict envoye a Camunda : type=NODE_HIGH_CPU cible=k8s-worker2", texte)
        self.assertNotIn('"type"', texte)

    def test_sans_argument_le_resultat_reste_du_json(self):
        b = self.banc()
        r = b.lancer({"NODE_HIGH_CPU": [serie("hote", "k8s-worker1", 96)]})
        self.assertEqual(r["type"], "NODE_HIGH_CPU")

    def test_duree_lisible(self):
        m = charger("")
        self.assertEqual(m.duree_lisible(45), "45 s")
        self.assertEqual(m.duree_lisible(600), "10 min")
        self.assertEqual(m.duree_lisible(3 * 3600 + 12 * 60), "3 h 12 min")


class PerimetreTests(unittest.TestCase):
    def setUp(self):
        self.mod = charger("")

    def test_durees_et_perimetre_figes(self):
        """NE PAS MODIFIER sans decision : toute evolution doit changer ce test en connaissance de cause."""
        self.assertEqual(self.mod.DUREE_PERSISTANTE_S, 600)
        self.assertEqual(self.mod.INTERRUPTION_MAX_S, 300)
        self.assertEqual(self.mod.REGLES_DE_FOND, {"POD_NOT_READY_PERSISTENT"})
        self.assertEqual(self.mod.CIBLES_DE_FOND, {("NODE_DISK_FULL", "monitoring")})
        self.assertEqual(self.mod.SEUIL_URGENCE_FOND, 90.0)

    def test_ordre_des_regles_inchange(self):
        self.assertEqual([r[0] for r in self.mod.REGLES], ORDRE_ATTENDU)

    def test_dossier_d_etat_par_defaut(self):
        """Sans variable d'environnement, l'etat va dans /var/tmp (fichier par utilisateur), jamais ailleurs."""
        spec = importlib.util.spec_from_file_location("detecter_v3_defaut", CIBLE)
        mod = importlib.util.module_from_spec(spec)
        with mock.patch.dict(os.environ):
            os.environ.pop("HELIOS_DETECTEUR_ETAT_DIR", None)
            spec.loader.exec_module(mod)
        self.assertEqual(mod.ETAT_DIR, "/var/tmp")


if __name__ == "__main__":
    unittest.main()
