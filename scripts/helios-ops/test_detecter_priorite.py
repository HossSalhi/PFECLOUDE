# -*- coding: utf-8 -*-
"""DETECTOR-PRIORITY-02 (11 nov. 2026) - election du detecteur HELIOS
(DEPLOYE sur .160 le 11 nov. 2026 a 22:21:35 : /opt/helios/detecter.py ; baseline infra/helios-sot/baseline/detecteur/
detecter.py ; source du deploiement : scripts/helios-ops/detecter.candidate.py).

Le detecteur n'elit QU'UNE anomalie par cycle : le 1er match dans l'ordre statique des regles. Le disque racine de la VM
monitoring (.160, 19 Go) tient autour de 80 % : NODE_DISK_FULL (rang 9) prenait le verdict en permanence et masquait
NODE_HIGH_CPU (rang 11) et les 26 autres regles de rang 10 a 36 (le test de charge CPU du 11 nov. 2026 « ne faisait rien »).
Ces tests figent la correction ET son perimetre, et prouvent qu'ailleurs rien ne change.

Lancer : python -I test_detecter_priorite.py -v                      (depuis scripts/helios-ops)
Tester un autre fichier (avant deploiement) :  HELIOS_DETECTER_PATH=<chemin> python -I test_detecter_priorite.py

Le detecteur est charge par chemin ; http / prom / loki sont remplaces par des series factices : aucun reseau, aucune VM,
aucun secret, aucune ecriture.
"""
import contextlib
import importlib.util
import io
import json
import os
import random
import re
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
# Correctif DEPLOYE sur .160 le 11 nov. 2026 a 22:21:35 (sha256 2d742d8028e9...) : la cible par defaut est la BASELINE
# (infra/helios-sot/baseline/detecteur/detecter.py, octet pour octet le fichier de production). Le CANDIDAT
# (scripts/helios-ops/detecter.candidate.py) reste la source de deploy-detecteur-160.sh ; HELIOS_DETECTER_PATH permet
# de tester n'importe quel autre fichier AVANT un nouveau deploiement.
CANDIDAT = os.path.join(HERE, "detecter.candidate.py")
BASELINE = os.path.join(REPO, "infra", "helios-sot", "baseline", "detecteur", "detecter.py")
DEFAUT = BASELINE
CIBLE = os.environ.get("HELIOS_DETECTER_PATH") or DEFAUT
BRIDGE = os.path.join(REPO, "infra", "helios-sot", "bridge", "bridge.py")

IP = {"k8s-master": "192.168.148.153", "k8s-worker2": "192.168.148.155", "k8s-worker1": "192.168.148.158",
      "monitoring": "192.168.148.160", "iacamunda": "192.168.148.161"}
HOTES = sorted(IP)
NAMESPACES = ["pfe", "default", "kube-system", "monitoring", "calico-system"]

# Ordre = priorite. Toute modification volontaire de cette liste doit etre decidee ET refletee ici.
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


def charger():
    spec = importlib.util.spec_from_file_location("detecter_sot_test", CIBLE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    if hasattr(mod, "ETAT_DIR"):
        mod.ETAT_DIR = ""          # versions >= PRIORITY-03 : pas d'etat (test hermetique, election purement statique)
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


def lancer(mod, matches):
    """Execute main() du detecteur avec des series factices. matches : {type de regle: [serie, ...]}."""
    par_requete = {}
    for typ, requete, _source, _mode in mod.REGLES:
        assert requete not in par_requete, "deux regles partagent la meme requete : %s / %s" % (par_requete[requete], typ)
        par_requete[requete] = typ

    def faux(requete):
        if requete == "up":
            return [{"metric": {}, "value": [0, "1"]}]
        return list(matches.get(par_requete.get(requete), []))

    mod.http = lambda url, delai=12: {"database": "ok", "version": "test"}
    mod.prom = faux
    mod.loki = faux
    sortie = io.StringIO()
    with contextlib.redirect_stdout(sortie):
        mod.main()
    return json.loads(sortie.getvalue())


def historique(mod, matches):
    """Election d'AVANT DETECTOR-PRIORITY-02 (reference) : 1er match hors REGLES_DE_FOND, sinon le 1er.
    Retourne (type, cible, ns) ou None."""
    trouves = []
    for typ, _requete, _source, mode in mod.REGLES:
        series = matches.get(typ) or []
        if not series:
            continue
        choisie = None
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
        trouves.append((typ, info))
    if not trouves:
        return None
    typ, info = next((m for m in trouves if m[0] not in mod.REGLES_DE_FOND), trouves[0])
    return typ, info.get("cible"), info.get("ns")


def est_disque_monitoring_de_fond(mod, typ, s):
    return (typ == "NODE_DISK_FULL" and mod.extraire("hote", s)["cible"] == "monitoring"
            and float(s["value"][1]) < 90.0)


def attendu_nouveau(mod, matches):
    """Semantique voulue, exprimee avec l'election historique : on retire la serie de fond (NODE_DISK_FULL de
    monitoring sous 90 %) ; s'il ne reste rien de prioritaire, elle reprend son rang."""
    sans_fond = {}
    for typ, series in matches.items():
        gardees = [s for s in series if not est_disque_monitoring_de_fond(mod, typ, s)]
        if gardees:
            sans_fond[typ] = gardees
    r = historique(mod, sans_fond)
    if r is None or r[0] in mod.REGLES_DE_FOND:
        return historique(mod, matches)
    return r


class ElectionTests(unittest.TestCase):
    def setUp(self):
        self.mod = charger()

    def lancer(self, matches):
        return lancer(self.mod, matches)

    def test_disque_monitoring_ne_masque_plus_le_cpu(self):
        """Le cas du 11 nov. 2026 : disque .160 a 81,1 % + CPU .158 a 96,2 %."""
        r = self.lancer({"NODE_DISK_FULL": [serie("hote", "monitoring", 81.1)],
                         "NODE_HIGH_CPU": [serie("hote", "k8s-worker1", 96.2)]})
        self.assertEqual((r["type"], r["cible"], r["ns"]), ("NODE_HIGH_CPU", "k8s-worker1", "k8s-nodes"))
        self.assertEqual(r["matches"], ["NODE_DISK_FULL", "NODE_HIGH_CPU"])
        self.assertEqual(r["nbAnomalies"], 1)
        self.assertEqual(r["severite"], "warning")
        self.assertTrue(r["preuve"].startswith("NODE_HIGH_CPU = 96.2"), r["preuve"])

    def test_disque_monitoring_ne_masque_aucune_des_regles_de_rang_10_a_36(self):
        """Prouve le perimetre du defaut : 26 autres regles etaient masquees, pas seulement le CPU."""
        for typ, _requete, _source, mode in self.mod.REGLES[self.mod.REGLES.index(
                next(r for r in self.mod.REGLES if r[0] == "NODE_DISK_FULL")) + 1:]:
            if typ in self.mod.REGLES_DE_FOND:
                continue
            r = self.lancer({"NODE_DISK_FULL": [serie("hote", "monitoring", 81.1)], typ: [serie(mode, "cible-test")]})
            self.assertEqual(r["type"], typ, "la regle %s est encore masquee par NODE_DISK_FULL de monitoring" % typ)

    def test_disque_monitoring_seul_reste_elu(self):
        r = self.lancer({"NODE_DISK_FULL": [serie("hote", "monitoring", 81.1)]})
        self.assertEqual((r["type"], r["cible"], r["severite"]), ("NODE_DISK_FULL", "monitoring", "critical"))
        self.assertEqual(r["matches"], ["NODE_DISK_FULL"])
        self.assertEqual(r["nbAnomalies"], 1)

    def test_urgence_disque_monitoring_garde_la_priorite(self):
        autre = {"NODE_HIGH_CPU": [serie("hote", "k8s-worker2", 97)]}
        for valeur, attendu in ((80.0, "NODE_HIGH_CPU"), (89.99, "NODE_HIGH_CPU"), (90.0, "NODE_DISK_FULL"),
                                (97.5, "NODE_DISK_FULL")):
            r = self.lancer(dict(autre, NODE_DISK_FULL=[serie("hote", "monitoring", valeur)]))
            self.assertEqual(r["type"], attendu, "disque monitoring a %s %%" % valeur)

    def test_disque_des_autres_vm_garde_sa_priorite(self):
        for vm in ("k8s-worker1", "k8s-worker2", "k8s-master", "iacamunda"):
            cpu = "k8s-worker2" if vm != "k8s-worker2" else "k8s-worker1"
            r = self.lancer({"NODE_DISK_FULL": [serie("hote", vm, 82.0)], "NODE_HIGH_CPU": [serie("hote", cpu, 97)]})
            self.assertEqual((r["type"], r["cible"]), ("NODE_DISK_FULL", vm), "disque de %s" % vm)

    def test_serie_worker_preferee_a_celle_de_monitoring(self):
        for ordre in ((("monitoring", 81.1), ("k8s-worker1", 82.0)), (("k8s-worker1", 82.0), ("monitoring", 81.1))):
            r = self.lancer({"NODE_DISK_FULL": [serie("hote", n, v) for n, v in ordre],
                             "NODE_HIGH_CPU": [serie("hote", "k8s-worker2", 97)]})
            self.assertEqual((r["type"], r["cible"]), ("NODE_DISK_FULL", "k8s-worker1"), str(ordre))

    def test_regles_de_rang_superieur_inchangees(self):
        r = self.lancer({"NODE_NOT_READY": [serie("noeud", "k8s-worker1")],
                         "NODE_DISK_FULL": [serie("hote", "monitoring", 81.1)]})
        self.assertEqual(r["type"], "NODE_NOT_READY")
        r = self.lancer({"ETCD_SLOW_FSYNC": [serie("hote", "k8s-master", 0.9)],
                         "NODE_DISK_FULL": [serie("hote", "monitoring", 95)]})
        self.assertEqual(r["type"], "ETCD_SLOW_FSYNC")

    def test_regle_de_fond_d_origine_inchangee(self):
        r = self.lancer({"POD_NOT_READY_PERSISTENT": [serie("pod", "postgres-4")]})
        self.assertEqual(r["type"], "POD_NOT_READY_PERSISTENT")
        r = self.lancer({"POD_NOT_READY_PERSISTENT": [serie("pod", "postgres-4")],
                         "NETWORK_PARTITION": [serie("fichier", "backend")]})
        self.assertEqual(r["type"], "NETWORK_PARTITION")

    def test_deux_regles_de_fond_seules_le_premier_match_l_emporte(self):
        r = self.lancer({"NODE_DISK_FULL": [serie("hote", "monitoring", 81.1)],
                         "POD_NOT_READY_PERSISTENT": [serie("pod", "postgres-4")]})
        self.assertEqual((r["type"], r["cible"]), ("NODE_DISK_FULL", "monitoring"))
        self.assertEqual(r["matches"], ["NODE_DISK_FULL", "POD_NOT_READY_PERSISTENT"])

    def test_aucun_match(self):
        r = self.lancer({})
        self.assertEqual((r["type"], r["nbAnomalies"], r["matches"], r["severite"]), ("NO_INCIDENT", 0, [], "none"))

    def test_schema_de_sortie_inchange(self):
        """Camunda et alarme_exporter.py lisent cette sortie JSON : aucune cle ajoutee ni retiree."""
        r = self.lancer({"NODE_HIGH_CPU": [serie("hote", "k8s-worker1", 96)]})
        self.assertEqual(set(r), {"type", "ns", "cible", "noeud", "nbAnomalies", "monitoringFiable", "preuve",
                                  "severite", "grafana", "matches", "nbErreursLogs"})

    def test_defaut_d_origine_documente(self):
        """L'election HISTORIQUE (reference du test) masquait bien le CPU : c'est le defaut corrige."""
        matches = {"NODE_DISK_FULL": [serie("hote", "monitoring", 81.1)],
                   "NODE_HIGH_CPU": [serie("hote", "k8s-worker1", 96.2)]}
        self.assertEqual(historique(self.mod, matches)[:2], ("NODE_DISK_FULL", "monitoring"))


class PerimetreTests(unittest.TestCase):
    def setUp(self):
        self.mod = charger()

    def test_perimetre_fige(self):
        """NE PAS ETENDRE sans justification : toute extension doit modifier ce test en connaissance de cause."""
        self.assertEqual(self.mod.REGLES_DE_FOND, {"POD_NOT_READY_PERSISTENT"})
        self.assertEqual(self.mod.CIBLES_DE_FOND, {("NODE_DISK_FULL", "monitoring")})
        self.assertEqual(self.mod.SEUIL_URGENCE_FOND, 90.0)

    def test_ordre_des_regles_inchange(self):
        self.assertEqual([r[0] for r in self.mod.REGLES], ORDRE_ATTENDU)

    def test_seuil_80_80_avec_le_bridge(self):
        """Contrat 80/80 : le seuil de la regle de detection et celui de la verification du Bridge restent egaux."""
        requete = {r[0]: r[1] for r in self.mod.REGLES}["NODE_DISK_FULL"]
        self.assertRegex(requete, r">\s*80\s*$")
        with open(BRIDGE, encoding="utf-8") as f:
            src = f.read()
        m = re.search(r'"NODE_DISK_FULL": \("prom",(.*?)"NODE_INODES_FULL"', src, re.S)
        self.assertIsNotNone(m, "bloc de verification NODE_DISK_FULL introuvable dans bridge.py")
        self.assertRegex(m.group(1), r">\s*80'")

    def test_est_de_fond_mesure_illisible(self):
        """Mesure illisible : comportement historique (le match garde sa priorite), jamais d'exception."""
        info = {"cible": "monitoring"}
        for s in ({}, {"value": []}, {"value": [0, "abc"]}, {"value": [0, None]}):
            self.assertFalse(self.mod.est_de_fond("NODE_DISK_FULL", info, s), str(s))

    def test_est_de_fond_ne_concerne_que_la_cible_prevue(self):
        s = serie("hote", "monitoring", 81.1)
        self.assertTrue(self.mod.est_de_fond("NODE_DISK_FULL", {"cible": "monitoring"}, s))
        self.assertFalse(self.mod.est_de_fond("NODE_DISK_FULL", {"cible": "k8s-worker1"}, s))
        self.assertFalse(self.mod.est_de_fond("NODE_HIGH_CPU", {"cible": "monitoring"}, s))
        self.assertTrue(self.mod.est_de_fond("POD_NOT_READY_PERSISTENT", {"cible": "x"}, s))


class NonRegressionTests(unittest.TestCase):
    """Propriete : en dehors du seul cas vise (NODE_DISK_FULL de monitoring sous 90 %), l'election est IDENTIQUE a
    l'election historique ; dans ce cas, elle est celle de la semantique voulue. 500 scenarios aleatoires reproductibles."""

    def scenario(self, rnd, mod, avec_disque_monitoring):
        regles = [(r[0], r[2], r[3]) for r in mod.REGLES]
        matches = {}
        for typ, _source, mode in rnd.sample(regles, rnd.randint(1, 7)):
            series = []
            for _ in range(rnd.randint(1, 3)):
                if mode in ("hote", "noeud"):
                    noms = [h for h in HOTES if not (typ == "NODE_DISK_FULL" and h == "monitoring")]
                    nom = rnd.choice(noms)
                else:
                    nom = rnd.choice(["backend", "frontend", "postgres-4", "helios-latency"])
                valeur = round(rnd.uniform(80.5, 99.0), 2) if typ == "NODE_DISK_FULL" else "1"
                series.append(serie(mode, nom, valeur, rnd.choice(NAMESPACES)))
            matches[typ] = series
        if avec_disque_monitoring:
            disque = matches.setdefault("NODE_DISK_FULL", [])
            disque.insert(rnd.randint(0, len(disque)), serie("hote", "monitoring", round(rnd.uniform(80.1, 99.9), 2)))
        return matches

    def test_hors_cas_vise_identique_a_l_election_historique(self):
        mod = charger()
        rnd = random.Random(20261111)
        for i in range(500):
            matches = self.scenario(rnd, mod, avec_disque_monitoring=False)
            r = lancer(mod, matches)
            attendu = historique(mod, matches)
            self.assertEqual((r["type"], r["cible"], r["ns"]), attendu, "scenario %d : %s" % (i, matches))

    def test_cas_vise_conforme_a_la_semantique_voulue(self):
        mod = charger()
        rnd = random.Random(20261112)
        for i in range(500):
            matches = self.scenario(rnd, mod, avec_disque_monitoring=True)
            r = lancer(mod, matches)
            attendu = attendu_nouveau(mod, matches)
            self.assertEqual((r["type"], r["cible"], r["ns"]), attendu, "scenario %d : %s" % (i, matches))


if __name__ == "__main__":
    unittest.main()
