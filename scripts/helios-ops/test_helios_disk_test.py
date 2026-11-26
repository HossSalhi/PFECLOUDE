# -*- coding: utf-8 -*-
"""Tests de l'outil de test DISK (scripts/helios-ops/helios_disk_test.py) : parametres, calculs, garde-fous, pre-vol (pur),
allocation / suivi / expiration / arret d'urgence / erreurs sur un NOEUD SIMULE, jeu ferme de commandes du noeud (emulateur),
ligne de commande et absence d'effets de bord.

AUCUN reseau, AUCUNE VM, AUCUN ssh, AUCUN subprocess, AUCUN sleep reel : chaque test echoue si l'outil tente d'y acceder.

Lancer : python -I -B test_helios_disk_test.py -v        (depuis scripts/helios-ops)
"""
import argparse
import ast
import contextlib
import copy
import hashlib
import importlib.util
import io
import json
import os
import re
import shlex
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import types
import unittest
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "helios_disk_test.py")
spec = importlib.util.spec_from_file_location("helios_disk_test_mod", SRC)
H = importlib.util.module_from_spec(spec)
sys.modules.setdefault("helios_disk_test", H)       # la porte E2E (chargee paresseusement) compose CE module, pas une 2e copie
spec.loader.exec_module(H)

MIO = H.MIO
GIO = H.GIO
JETON = "a" * 32
DOSSIER = H.DOSSIER
KUBELET = 'apiVersion: kubelet.config.k8s.io/v1beta1\nkind: KubeletConfiguration\nevictionHard:\n    imagefs.available: "15%"\n' \
          '    memory.available: "100Mi"\n    nodefs.available: "10%"\n    nodefs.inodesFree: "5%"\nevictionPressureTransitionPeriod: 0s\n'


def _interdit(*a, **k):
    raise AssertionError("ACCES REEL INTERDIT dans un test : %r" % (a[:1],))


class SansInfra(unittest.TestCase):
    """Fait echouer tout test dont le code tente un acces reseau, ssh, subprocess, sleep ou commande systeme."""

    def setUp(self):
        cibles = [(subprocess, "run"), (subprocess, "Popen"), (subprocess, "call"), (subprocess, "check_call"),
                  (subprocess, "check_output"), (urllib.request, "urlopen"), (socket, "create_connection"),
                  (socket.socket, "connect"), (time, "sleep"), (os, "system")]
        self._patchs = [mock.patch.object(o, n, _interdit) for o, n in cibles]
        for p in self._patchs:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in reversed(self._patchs)])


def fs155():
    return H.Fs(38121 * MIO, 11259 * MIO)


def fs_usage(pct, taille=38121 * MIO):
    return H.Fs(taille, int(round(taille * (1.0 - pct / 100.0))))


def monde(**kw):
    d = {"taille": 38121 * MIO, "dispo": 11259 * MIO}
    d.update(kw)
    return H.MondeSimule(**d)


def lancer(m=None, p=None, jeton=JETON, **kw):
    """Execute le test sur un noeud simule ; retourne (resultat, lignes de sortie, monde)."""
    m = m or monde()
    p = p or H.valider_parametres()
    lignes = []
    res = H.executer_test(H.NoeudSimule(m), H.SondeSimulee(m), H.HorlogeSimulee(m), p, jeton, lignes.append, **kw)
    return res, lignes, m


def ns(**kw):
    d = dict(sim_taille_mo=None, sim_usage=None, sim_primaire_cnpg=None, sim_ram_mo=None, sim_drapeau=None)
    d.update(kw)
    return argparse.Namespace(**d)


def faits(cle="158", **kw):
    f = copy.deepcopy(H.faits_simules(cle, ns())[1])
    f.update(kw)
    return f


def niveaux(rapport):
    return [x[0] for x in rapport["raisons"]]


def textes(rapport, niveau=None):
    return " || ".join(x[1] for x in rapport["raisons"] if niveau is None or x[0] == niveau)


LECTURES = ("stats", "ls", "inspect", "hostname")             # appels du noeud simule qui ne modifient RIEN


def ecritures(m):
    return [a for a in m.appels if a not in LECTURES]


# ------------------------------------------------------------------------------------------------ parametres et cible
class ParametresTests(SansInfra):
    def test_defauts_valides_par_l_exploitant(self):
        p = H.valider_parametres()
        self.assertEqual((p.cible, p.plafond, p.urgence, p.recuperation), (81.0, 82.0, 83.0, 77.0))
        self.assertEqual((p.bloc, p.observe, p.expiration), (512 * MIO, 720, 1020))

    def test_on_peut_resserrer_jamais_desserrer(self):
        ok = H.valider_parametres(cible=80.5, plafond=81.5, urgence=82.5, recuperation=75.0)
        self.assertEqual((ok.cible, ok.plafond, ok.urgence, ok.recuperation), (80.5, 81.5, 82.5, 75.0))
        for kw in ({"cible": 81.01}, {"plafond": 82.01}, {"urgence": 83.01}, {"recuperation": 77.01},
                   {"cible": 80.2}, {"cible": 81.0, "plafond": 81.0}, {"plafond": 82.0, "urgence": 82.0},
                   {"recuperation": 0.0}, {"recuperation": -5.0}, {"urgence": 85.0}):
            with self.subTest(kw=kw), self.assertRaises(H.RefusSecurite):
                H.valider_parametres(**kw)

    def test_valeurs_non_numeriques_ou_non_finies_refusees(self):
        for kw in ({"cible": float("nan")}, {"plafond": float("inf")}, {"urgence": "abc"}, {"recuperation": None},
                   {"bloc": "x"}, {"observe_s": None}):
            with self.subTest(kw=kw), self.assertRaises(H.RefusSecurite):
                H.valider_parametres(**kw)

    def test_bornes_bloc_observation_expiration(self):
        for kw in ({"bloc": 1 * MIO}, {"bloc": 2 * GIO}, {"observe_s": 10}, {"observe_s": 5000},
                   {"observe_s": 600, "expiration_s": 650}, {"observe_s": 600, "expiration_s": 4000}):
            with self.subTest(kw=kw), self.assertRaises(H.RefusSecurite):
                H.valider_parametres(**kw)
        self.assertEqual(H.valider_parametres(observe_s=60).expiration, 360)

    def test_jeton_de_confirmation_exact(self):
        self.assertEqual(H.jeton_confirmation("155", H.valider_parametres()), "DISK-155-81-82-83")
        self.assertEqual(H.jeton_confirmation("158", H.valider_parametres(cible=80.5)), "DISK-158-80.5-82-83")

    def test_cible_explicite_obligatoire(self):
        for v in (None, "", "   "):
            with self.subTest(v=v), self.assertRaises(H.RefusSecurite) as c:
                H.resoudre_cible(v)
            self.assertIn("EXPLICITE", str(c.exception))

    def test_seules_155_et_158_sont_autorisees(self):
        for v in ("155", ".155", "k8s-worker2", "192.168.148.155"):
            self.assertEqual(H.resoudre_cible(v), "155")
        for v in ("158", ".158", "k8s-worker1", "192.168.148.158"):
            self.assertEqual(H.resoudre_cible(v), "158")
        for v in ("153", ".160", "161", "162", "192.168.148.153", "192.168.148.160"):
            with self.subTest(v=v), self.assertRaises(H.RefusSecurite) as c:
                H.resoudre_cible(v)
            self.assertIn("HORS PERIMETRE", str(c.exception))
        for v in ("k8s-master", "999", "all", "155,158", "155 158"):
            with self.subTest(v=v), self.assertRaises(H.RefusSecurite):
                H.resoudre_cible(v)


# ------------------------------------------------------------------------------------------------ calculs et garde-fous
class CalculTests(SansInfra):
    def test_formule_identique_a_la_regle_du_detecteur(self):
        self.assertAlmostEqual(fs155().usage, 70.465, places=2)            # releve reel du 13 nov. : 70,47 %
        self.assertAlmostEqual(H.usage_pct(100, 25), 75.0)
        self.assertAlmostEqual(H.usage_pct(100, 0), 100.0)
        self.assertAlmostEqual(H.usage_pct(100, 100), 0.0)

    def test_mesures_incoherentes_refusees(self):
        for t, d in ((0, 0), (-1, 5), (100, 101), (100, -1), (100.0, 50), (100, 50.5), (None, 1)):
            with self.subTest(t=t, d=d), self.assertRaises(H.ErreurNoeud):
                H.usage_pct(t, d)

    def test_octets_pour_81_sur_le_noeud_mesure(self):
        besoin = H.octets_pour(fs155(), 81.0)
        self.assertTrue(4016 <= besoin / MIO <= 4017, besoin / MIO)           # +4 016 Mo mesures a l'etape 1
        self.assertTrue(3634 <= H.octets_pour(fs155(), 80.0) / MIO <= 3636)    # 80 % : 3 634,8 Mo (le 80 % du detecteur est STRICT)
        self.assertTrue(4397 <= H.octets_pour(fs155(), 82.0) / MIO <= 4398)
        self.assertTrue(4778 <= H.octets_pour(fs155(), 83.0) / MIO <= 4779)
        self.assertEqual(H.octets_pour(fs155(), 70.0), 0)
        self.assertEqual(H.octets_pour(fs_usage(90), 81.0), 0)

    def test_arrondi_mio(self):
        self.assertEqual((H.arrondi_mio(1), H.arrondi_mio(MIO), H.arrondi_mio(MIO + 1)), (MIO, MIO, 2 * MIO))

    def test_plan_nominal_155(self):
        p = H.valider_parametres()
        blocs = H.planifier(fs155(), p)
        self.assertEqual((len(blocs), blocs[0], max(blocs) <= p.bloc), (8, 512 * MIO, True))
        final = H.Fs(fs155().taille, fs155().dispo - sum(blocs)).usage
        self.assertTrue(81.0 <= final <= 81.01, final)

    def test_plan_refuse_une_base_hors_limites(self):
        p = H.valider_parametres()
        for u in (77.5, 79.9, 80.5, 82.5, 84.0, 95.0):
            with self.subTest(u=u), self.assertRaises(H.RefusSecurite):
                H.planifier(fs_usage(u), p)

    def test_plan_refuse_un_remplissage_trop_gros(self):
        with self.assertRaises(H.RefusSecurite) as c:
            H.planifier(fs_usage(50.0, 100 * GIO), H.valider_parametres())
        self.assertIn("limite", str(c.exception))

    def test_le_plan_ne_depasse_jamais_le_plafond(self):
        p = H.valider_parametres()
        vus = refus = 0
        for taille_go in (10, 20, 38, 60, 100):
            for u in range(30, 78, 3):
                fs = fs_usage(float(u), taille_go * GIO)
                try:
                    blocs = H.planifier(fs, p)
                except H.RefusSecurite:
                    refus += 1
                    continue
                vus += 1
                cumul = 0
                for b in blocs:
                    cumul += b
                    self.assertLessEqual(H.Fs(fs.taille, fs.dispo - cumul).usage, p.plafond)
                self.assertLessEqual(len(blocs), H.MAX_FICHIERS)
                self.assertLessEqual(cumul, H.MAX_TOTAL)
        self.assertGreater(vus, 20)
        self.assertGreater(refus, 5)

    def test_garde_avant_chaque_allocation(self):
        p = H.valider_parametres()
        self.assertEqual(H.garde_allocation(fs155(), 512 * MIO, p), (True, "ok"))
        cas = [(fs_usage(80.9), 512 * MIO, "plafond"),          # 80,9 + 1,34 = 82,24 > 82
               (fs_usage(83.2), 64 * MIO, "urgence"),
               (fs_usage(81.2), 64 * MIO, "cible"),
               (fs155(), 0, "hors limites"), (fs155(), -MIO, "hors limites"), (fs155(), 513 * MIO, "hors limites"),
               (fs155(), 1.5 * MIO, "hors limites"), (fs155(), None, "hors limites")]
        for fs, octets, mot in cas:
            with self.subTest(usage=fs.usage, octets=octets):
                ok, raison = H.garde_allocation(fs, octets, p)
                self.assertFalse(ok)
                self.assertIn(mot, raison)

    def test_la_garde_refuse_un_bloc_plus_grand_que_l_espace(self):
        fs = H.Fs(10 * GIO, 100 * MIO)
        ok, raison = H.garde_allocation(fs, 200 * MIO, H.valider_parametres())
        self.assertFalse(ok)

    def test_noms_de_fichiers_stricts(self):
        self.assertEqual(H.nom_fichier(JETON, 1), "helios-diskfill-%s-001.bin" % JETON)
        self.assertEqual(len(H.noms_prevus(JETON)), H.MAX_FICHIERS)
        for j, i in (("A" * 32, 1), ("a" * 31, 1), ("g" * 32, 1), (JETON, 0), (JETON, 17), (JETON, "1"), (None, 1)):
            with self.subTest(j=j, i=i), self.assertRaises(H.RefusSecurite):
                H.nom_fichier(j, i)
        self.assertEqual(H.chemin_de("helios-diskfill-%s-003.bin" % JETON), DOSSIER + "/helios-diskfill-%s-003.bin" % JETON)
        for n in ("../etc/passwd", "*", "helios-diskfill-*.bin", "x.bin", "/tmp/x", "helios-diskfill-%s-001.bin " % JETON,
                  "helios-diskfill-%s-001.bin/../x" % JETON, "HELIOS-diskfill-%s-001.bin" % JETON, "", None, 5):
            with self.subTest(n=n), self.assertRaises(H.RefusSecurite):
                H.chemin_de(n)


# ------------------------------------------------------------------------------------------------ parsing
class ParsingTests(SansInfra):
    def test_stat_f(self):
        fs = H.parse_stat_f("4096 9960448 2742000 3335207\n")
        self.assertEqual((fs.taille, fs.dispo), (4096 * 9960448, 4096 * 2742000))
        for s in ("", None, "1 2 3", "a b c d", "4096 100 50 40", "4096 100 50 101", "3 100 50 60", "4096 0 0 0", "4096 -1 1 1"):
            with self.subTest(s=s), self.assertRaises(H.ErreurNoeud):
                H.parse_stat_f(s)

    def test_kubelet_defauts_du_cluster(self):
        k = H.parse_kubelet(KUBELET)
        self.assertEqual((k["imagefs"], k["nodefs"], k["gc_haut"], k["gc_bas"]), (15.0, 10.0, 85.0, 80.0))
        self.assertEqual(H.seuil_kubelet(k), 85.0)

    def test_kubelet_cas_particuliers(self):
        self.assertEqual(H.seuil_kubelet(H.parse_kubelet("kind: KubeletConfiguration\n")), 85.0)          # tout par defaut
        self.assertEqual(H.seuil_kubelet(H.parse_kubelet(KUBELET + "imageGCHighThresholdPercent: 70\n")), 70.0)
        souple = KUBELET + 'evictionSoft:\n    imagefs.available: "30%"\n'
        self.assertEqual(H.seuil_kubelet(H.parse_kubelet(souple)), 70.0)
        self.assertIsNone(H.seuil_kubelet(H.parse_kubelet(KUBELET.replace('"10%"', '"5Gi"'))))              # quantite : inconnue
        self.assertIsNone(H.seuil_kubelet(H.parse_kubelet(KUBELET + "imageGCHighThresholdPercent: abc\n")))
        for t in ("", None, "pas du yaml", "apiVersion: v1\nkind: Pod\n"):
            self.assertIsNone(H.parse_kubelet(t))
        self.assertIsNone(H.seuil_kubelet(None))

    def test_valeurs_prometheus(self):
        ok = {"status": "success", "data": {"result": [{"value": [0, "70.4"]}, {"value": [0, "1"]}]}}
        self.assertEqual(H.valeurs_prom(ok), [70.4, 1.0])
        for o in (None, {}, {"status": "error"}, {"status": "success", "data": {}}, {"status": "success", "data": {"result": [{"value": [0]}]}},
                  {"status": "success", "data": {"result": [{"value": [0, "abc"]}]}}):
            self.assertEqual(H.valeurs_prom(o), [])

    def test_noeuds_kubectl(self):
        s = "k8s-master Ready control-plane 100d v1\nk8s-worker1 NotReady <none> 100d v1\nk8s-worker2 Ready <none> 100d v1\n"
        self.assertEqual(H.parse_noeuds(s, "k8s-worker2"), (2, 3, True))
        self.assertEqual(H.parse_noeuds(s, "k8s-worker1"), (2, 3, False))
        self.assertEqual(H.parse_noeuds("", "k8s-worker1"), (0, 0, False))
        self.assertEqual(H.parse_noeuds(None, "x"), (0, 0, False))


# ------------------------------------------------------------------------------------------------ fichiers de l'hote (vmdk, gels)
DESCRIPTEUR = ('# Disk DescriptorFile\nversion=1\nCID=6e6cfb56\nparentCID=ffffffff\ncreateType="twoGbMaxExtentSparse"\n\n'
               '# Extent description\n%s\n\n# The Disk Data Base\n#DDB\nddb.adapterType = "lsilogic"\n')


def fabriquer_vm(base, nom, create="twoGbMaxExtentSparse", parent=None, snapshots=None, delta=False, extents=3, manquant=False, vmx=1,
                 journal=None):
    d = os.path.join(base, nom)
    os.makedirs(d, exist_ok=True)
    if journal is not None:                                # journal VMware sans aucun gel (sinon : illisible => pre-vol ROUGE)
        with open(os.path.join(d, "vmware.log"), "w", encoding="utf-8") as fh:
            fh.write(journal)
    lignes, tailles = [], [4096, 8192, 2048]
    for i in range(extents):
        f = "disk-s%03d.vmdk" % (i + 1)
        lignes.append('RW 8388608 SPARSE "%s"' % f)
        if not (manquant and i == 0):
            with open(os.path.join(d, f), "wb") as fh:
                fh.write(b"x" * tailles[i % 3])
    txt = DESCRIPTEUR % "\n".join(lignes)
    txt = txt.replace("twoGbMaxExtentSparse", create)
    if parent:
        txt = txt.replace("parentCID=ffffffff", "parentCID=1234abcd") + 'parentFileNameHint="%s"\n' % parent
    with open(os.path.join(d, "disk.vmdk"), "w", encoding="latin-1") as fh:
        fh.write(txt)
    for k in range(vmx):
        with open(os.path.join(d, "vm%s.vmx" % ("" if k == 0 else k)), "w", encoding="utf-8") as fh:
            fh.write('scsi0:0.fileName = "disk.vmdk"\n')
    if snapshots is not None:
        with open(os.path.join(d, "vm.vmsd"), "w", encoding="utf-8") as fh:
            fh.write('snapshot.numSnapshots = "%d"\n' % snapshots)
    if delta:
        with open(os.path.join(d, "disk-000001.vmdk"), "wb") as fh:
            fh.write(b"d")
    return d


class HoteTests(SansInfra):
    def setUp(self):
        super().setUp()
        self.tmp = tempfile.mkdtemp(prefix="helios_disk_test_")
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_vmdk_croissant_autonome_sans_snapshot(self):
        v = H.analyser_vmdk(fabriquer_vm(self.tmp, "vm"))
        self.assertEqual((v["type"], v["extents"], v["croissant"], v["prealloue"], v["parent"], v["snapshots"]),
                         ("twoGbMaxExtentSparse", 3, True, False, False, 0))
        self.assertEqual(v["capacite"], 3 * 8388608 * 512)
        self.assertEqual(v["alloue"], 4096 + 8192 + 2048)
        self.assertEqual(v["reste"], v["capacite"] - v["alloue"])

    def test_vmdk_cas_a_risque(self):
        self.assertTrue(H.analyser_vmdk(fabriquer_vm(self.tmp, "a", parent="base.vmdk"))["parent"])
        self.assertEqual(H.analyser_vmdk(fabriquer_vm(self.tmp, "b", snapshots=2))["snapshots"], 2)
        self.assertEqual(H.analyser_vmdk(fabriquer_vm(self.tmp, "c", delta=True))["snapshots"], 1)
        f = H.analyser_vmdk(fabriquer_vm(self.tmp, "d", create="monolithicFlat"))
        self.assertTrue(f["prealloue"] and not f["croissant"])

    def test_vmdk_illisible_donne_none(self):
        self.assertIsNone(H.analyser_vmdk(os.path.join(self.tmp, "inexistant")))
        self.assertIsNone(H.analyser_vmdk(fabriquer_vm(self.tmp, "e", manquant=True)))
        self.assertIsNone(H.analyser_vmdk(fabriquer_vm(self.tmp, "f", vmx=2)))
        os.makedirs(os.path.join(self.tmp, "g"))
        self.assertIsNone(H.analyser_vmdk(os.path.join(self.tmp, "g")))

    def ecrire_log(self, vm, lignes, nom="vmware.log"):
        d = os.path.join(self.tmp, vm)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, nom), "w", encoding="utf-8") as fh:
            fh.write("\n".join(lignes) + "\n")

    @staticmethod
    def ligne_gel(t, secondes):
        return "%s.123Z In(05) vmx Guest: *** WARNING: GuestInfo collection interval longer than expected; actual=%d sec, expected=30 sec. ***" % (
            t.strftime("%Y-%m-%dT%H:%M:%S"), secondes)

    def test_gels_vmware(self):
        now = datetime(2026, 11, 13, 12, 0, 0, tzinfo=timezone.utc)
        self.ecrire_log("vmA", [self.ligne_gel(now - timedelta(minutes=30), 120), self.ligne_gel(now - timedelta(minutes=20), 59),
                                self.ligne_gel(now - timedelta(days=3), 300), "ligne quelconque"])
        self.ecrire_log("vmB", [self.ligne_gel(now - timedelta(hours=5), 250)], "vmware-0.log")          # journal d'une session precedente
        g = H.gels_vmware(self.tmp, now)
        self.assertEqual(sorted(g), [(1800, 120, "vmA"), (18000, 250, "vmB")])
        self.assertEqual(H.gels_vmware(self.tmp, now, fenetre_s=600), [])

    def test_gels_vmware_jamais_vide_par_defaut(self):
        now = datetime(2026, 11, 13, 12, 0, 0, tzinfo=timezone.utc)
        self.assertIsNone(H.gels_vmware(self.tmp, now))                                   # aucun journal : pas un feu vert
        self.assertIsNone(H.gels_vmware(os.path.join(self.tmp, "absent"), now))


# ------------------------------------------------------------------------------------------------ pre-vol (pur)
class EvaluationTests(SansInfra):
    def test_158_orange_tant_que_la_remediation_n_est_pas_activee(self):
        r = H.evaluer(faits("158"), "158", H.valider_parametres())
        self.assertEqual(r["verdict"], "ORANGE")
        self.assertIn("NON activee", textes(r, "ORANGE"))
        self.assertEqual(H.evaluer(faits("158", drapeau="true"), "158", H.valider_parametres())["verdict"], "VERT")

    def test_primaire_cnpg_jamais_presume_accepte(self):
        p = H.valider_parametres()
        r = H.evaluer(faits("155"), "155", p)
        self.assertEqual(r["verdict"], "ROUGE")
        self.assertIn("PRIMAIRE CNPG", textes(r, "ROUGE"))
        self.assertIn("NON accepte", textes(r, "ROUGE"))
        r2 = H.evaluer(faits("155"), "155", p, ack_cnpg=True)
        self.assertEqual(r2["verdict"], "ORANGE")
        self.assertIn("accepte explicitement", textes(r2, "ORANGE"))
        self.assertNotIn("ROUGE", niveaux(r2))

    def test_l_acquittement_n_a_aucun_effet_sur_un_noeud_sans_primaire(self):
        a = H.evaluer(faits("158"), "158", H.valider_parametres(), ack_cnpg=True)
        b = H.evaluer(faits("158"), "158", H.valider_parametres())
        self.assertEqual(a["raisons"], b["raisons"])

    def test_tout_fait_absent_ou_illisible_est_rouge(self):
        p = H.valider_parametres()
        cas = {"noeud absent": {"noeud": None}, "noeud injoignable": {"noeud": {"joignable": False}},
               "prometheus": {"prom": None}, "cluster": {"cluster": None}, "noeud k8s": {"noeud_k8s": None},
               "cnpg": {"cnpg": None}, "incidents": {"incidents": None}, "hote": {"hote": None}}
        for nom, kw in cas.items():
            with self.subTest(cas=nom):
                self.assertEqual(H.evaluer(faits("158", drapeau="true", **kw), "158", p)["verdict"], "ROUGE")
        for cle, valeur in (("fs", None), ("fstype", None), ("fstype", "btrfs"), ("fallocate", None), ("fallocate", False),
                            ("dossier", "inconnu"), ("kubelet", None)):
            f = faits("158", drapeau="true")
            f["noeud"][cle] = valeur
            with self.subTest(noeud=cle, valeur=valeur):
                self.assertEqual(H.evaluer(f, "158", p)["verdict"], "ROUGE")
        for cle in ("ram_mo", "c_libre_go", "gels", "vmdk"):
            f = faits("158", drapeau="true")
            f["hote"][cle] = None
            with self.subTest(hote=cle):
                self.assertEqual(H.evaluer(f, "158", p)["verdict"], "ROUGE")
        f = faits("158", drapeau="true")
        f["cnpg"]["sain"] = None
        self.assertEqual(H.evaluer(f, "158", p)["verdict"], "ROUGE")

    def test_seuils_de_l_hote(self):
        p = H.valider_parametres()

        def v(**h):
            f = faits("158", drapeau="true")
            f["hote"].update(h)
            return H.evaluer(f, "158", p)["verdict"]
        self.assertEqual((v(ram_mo=1499), v(ram_mo=1500)), ("ROUGE", "VERT"))
        self.assertEqual((v(c_libre_go=24.9), v(c_libre_go=25.0)), ("ROUGE", "VERT"))
        self.assertEqual((v(gels=[(1800, 120, "vmA")]), v(gels=[(18000, 120, "vmA")]), v(gels=[])), ("ROUGE", "ORANGE", "VERT"))

    def test_base_hors_limites_et_kubelet(self):
        p = H.valider_parametres()
        # frontiere a 77 % : jamais teste a la valeur exacte (le bruit flottant d'une mesure en octets la rendrait arbitraire)
        for u, attendu in ((70.0, "VERT"), (76.99, "VERT"), (77.01, "ROUGE"), (77.5, "ROUGE"), (80.5, "ROUGE"), (86.0, "ROUGE")):
            f = faits("158", drapeau="true")
            f["noeud"]["fs"] = fs_usage(u)
            f["prom"]["usage"] = u
            with self.subTest(usage=u):
                self.assertEqual(H.evaluer(f, "158", p)["verdict"], attendu)
        f = faits("158", drapeau="true")
        f["noeud"]["kubelet"] = H.parse_kubelet(KUBELET + 'evictionSoft:\n    nodefs.available: "20%"\n')      # declenchement a 80 %
        r = H.evaluer(f, "158", p)
        self.assertEqual(r["verdict"], "ROUGE")
        self.assertIn("kubelet", textes(r, "ROUGE"))
        f["noeud"]["kubelet"] = H.parse_kubelet(KUBELET)
        self.assertIn("marge", textes(H.evaluer(f, "158", p), "INFO"))

    def test_base_au_dessus_de_la_recuperation_est_expliquee_par_le_pre_vol_lui_meme(self):
        # le pre-vol refuse DEJA (pas seulement planifier() appele a l'interieur) et dit pourquoi
        f = faits("158", drapeau="true")
        f["noeud"]["fs"] = fs_usage(77.5)
        f["prom"]["usage"] = 77.5
        r = H.evaluer(f, "158", H.valider_parametres())
        self.assertEqual(r["verdict"], "ROUGE")
        self.assertIn("la recuperation serait impossible", textes(r, "ROUGE"))
        self.assertEqual(r["plan"], None)                                # aucun plan de remplissage n'est meme calcule

    def test_etat_du_cluster_et_du_noeud(self):
        p = H.valider_parametres()
        for kw in ({"cluster": (2, 3)}, {"noeud_k8s": {"ready": False, "disk_pressure": False}},
                   {"noeud_k8s": {"ready": True, "disk_pressure": True}}, {"noeud_k8s": {"ready": True, "disk_pressure": None}}):
            with self.subTest(kw=kw):
                self.assertEqual(H.evaluer(faits("158", drapeau="true", **kw), "158", p)["verdict"], "ROUGE")

    def test_prometheus_serie_unique_et_coherente(self):
        p = H.valider_parametres()
        for prom in ({"series": 0, "usage": None, "incident_actif": 0}, {"series": 2, "usage": 70.0, "incident_actif": 0},
                     {"series": None, "usage": fs155().usage, "incident_actif": 0},      # comptage illisible mais valeur presente : refus
                     {"series": 1, "usage": fs155().usage, "incident_actif": None},      # etat des incidents illisible : refus
                     {"series": 1, "usage": 73.0, "incident_actif": 0}, {"series": 1, "usage": None, "incident_actif": 0},
                     {"series": 1, "usage": fs155().usage, "incident_actif": 1}):
            f = faits("155", drapeau="true", prom=prom)
            with self.subTest(prom=prom):
                self.assertEqual(H.evaluer(f, "155", p, ack_cnpg=True)["verdict"], "ROUGE")

    def test_incidents_actifs_et_notready_recents(self):
        p = H.valider_parametres()

        def v(**i):
            f = faits("158", drapeau="true", incidents=dict({"disque_actifs": [], "notready_ages_s": []}, **i))
            return H.evaluer(f, "158", p)["verdict"]
        self.assertEqual(v(), "VERT")
        self.assertEqual(v(disque_actifs=["123"]), "ROUGE")
        self.assertEqual((v(notready_ages_s=[600]), v(notready_ages_s=[3600]), v(notready_ages_s=[10000])), ("ROUGE", "ORANGE", "VERT"))

    def test_repertoire_de_test_deja_occupe(self):
        f = faits("158", drapeau="true")
        f["noeud"]["dossier"] = ["helios-diskfill-%s-001.bin" % JETON]
        r = H.evaluer(f, "158", H.valider_parametres())
        self.assertEqual(r["verdict"], "ROUGE")
        self.assertIn("aucun nettoyage automatique", textes(r, "ROUGE"))
        for d in (None, []):
            f["noeud"]["dossier"] = d
            self.assertEqual(H.evaluer(f, "158", H.valider_parametres())["verdict"], "VERT")

    def test_vmdk_a_risque_et_cas_neutres(self):
        p = H.valider_parametres()

        def v(**kw):
            f = faits("158", drapeau="true")
            f["hote"]["vmdk"].update(kw)
            return H.evaluer(f, "158", p)
        self.assertEqual(v(parent=True)["verdict"], "ROUGE")
        self.assertEqual(v(snapshots=1)["verdict"], "ROUGE")
        self.assertEqual(v(croissant=False, prealloue=True)["verdict"], "VERT")
        self.assertEqual(v(croissant=False, prealloue=False, type="inconnu")["verdict"], "ORANGE")
        self.assertIn("croissance attendue ~0", textes(v(), "INFO"))

    def test_evaluation_pure_et_sans_mutation(self):
        f = faits("155")
        avant = copy.deepcopy(f)
        r1 = H.evaluer(f, "155", H.valider_parametres())
        r2 = H.evaluer(f, "155", H.valider_parametres())
        self.assertEqual(f, avant)
        self.assertEqual(r1, r2)


# ------------------------------------------------------------------------------------------------ allocation sur noeud simule
class AllocationTests(SansInfra):
    def test_nominal_helios_resout(self):
        res, lignes, m = lancer(monde(helios="resolve"))
        self.assertEqual(res["verdict"], "OK", res)
        self.assertTrue(81.0 <= res["usage_atteint"] <= 81.01, res["usage_atteint"])
        self.assertLessEqual(m.usage_max, 82.0)
        self.assertEqual((res["liberee_par"], res["statut"], res["recupere"], res["liberation_ok"]), ("helios", "RESOLVED", True, True))
        self.assertTrue(100 <= res["detection_s"] <= 140, res["detection_s"])
        self.assertLess(res["usage_final"], 77.0)
        self.assertEqual((len(res["alloues"]), m.fichiers, m.dossier), (8, {}, None))             # tout retire, dossier supprime
        self.assertFalse(m.minuteurs[JETON][2])                                                    # minuteur desarme

    def test_ordre_des_operations_filets_avant_la_premiere_allocation(self):
        res, lignes, m = lancer(monde(helios="resolve"))
        ops = ecritures(m)
        self.assertEqual(ops[:3], ["mkdir", "systemd-run", "systemd-run-garde"])      # repertoire, minuteur, garde : AVANT tout fallocate
        self.assertEqual(ops.count("fallocate"), 8)
        self.assertLess(ops.index("systemd-run-garde"), ops.index("fallocate"))
        self.assertEqual(ops[-4:], ["rm", "systemctl-stop", "systemctl-stop-garde", "rmdir"])    # liberation verifiee PUIS desarmement PUIS rmdir

    def test_detection_seule_quand_la_remediation_est_absente(self):
        res, lignes, m = lancer(monde(helios="detect"))
        self.assertEqual(res["verdict"], "DETECTION_SEULE", res)
        self.assertIsNotNone(res["detection_s"])
        self.assertTrue(res["liberation_ok"] and res["recupere"])                                   # l'outil a libere
        self.assertEqual((m.fichiers, m.dossier), ({}, None))

    def test_aucune_detection(self):
        res, lignes, m = lancer(monde(helios="absent"))
        self.assertEqual(res["verdict"], "AUCUNE_DETECTION")
        self.assertTrue(res["liberation_ok"] and res["recupere"])
        self.assertEqual(m.fichiers, {})

    def test_arret_d_urgence_par_croissance_organique(self):
        res, lignes, m = lancer(monde(helios="absent", croissance_o_s=20 * MIO))
        self.assertEqual(res["verdict"], "URGENCE", res)
        self.assertTrue(res["liberation_ok"])
        self.assertEqual((m.fichiers, m.dossier), ({}, None))
        self.assertTrue(any("ARRET D'URGENCE" in l for l in lignes))
        self.assertLess(m.fs().usage, 77.0)

    def test_arret_d_urgence_pendant_l_allocation(self):
        # une ecriture EXTERNE de 6 Go apparait apres le 2e bloc : la mesure fraiche suivante franchit 83 % => arret net
        res, lignes, m = lancer(monde(helios="absent", pannes={"saut_apres": (2, 6 * GIO)}))
        self.assertEqual(res["verdict"], "URGENCE", res)
        self.assertEqual(len(res["alloues"]), 2)                      # aucune allocation supplementaire apres le saut
        self.assertEqual(m.fichiers, {})
        self.assertTrue(res["liberation_ok"])
        self.assertTrue(any("ARRET D'URGENCE" in l for l in lignes))
        i = max(k for k, a in enumerate(m.appels) if a == "fallocate")
        self.assertEqual(m.appels[i + 1:i + 3], ["stats", "rm"])      # urgence controlee AUSSITOT apres le bloc, sans attendre la mesure suivante

    def test_ecriture_externe_pendant_l_allocation_avance_la_cible_sans_depasser_le_plafond(self):
        # +2 Go externes apres le 2e bloc : l'outil mesure AVANT chaque bloc et s'arrete plus tot (4 blocs au lieu de 8)
        res, lignes, m = lancer(monde(helios="absent", pannes={"saut_apres": (2, 2 * GIO)}))
        self.assertEqual(res["verdict"], "AUCUNE_DETECTION", res)
        self.assertEqual(len(res["alloues"]), 4)
        self.assertTrue(81.0 <= res["usage_atteint"] <= 81.01, res["usage_atteint"])
        self.assertLessEqual(m.usage_max, 82.0)
        self.assertEqual((m.fichiers, m.dossier), ({}, None))

    def test_ecriture_externe_apres_le_dernier_bloc_au_dela_du_plafond(self):
        # +600 Mo externes juste apres le 8e (dernier) bloc : 82,6 % > plafond 82 % mais < urgence 83 % => arret AVANT tout suivi
        res, lignes, m = lancer(monde(helios="detect", pannes={"saut_apres": (8, 600 * MIO)}))
        self.assertEqual(res["verdict"], "URGENCE", res)
        self.assertEqual(len(res["alloues"]), 8)
        self.assertTrue(82.0 < res["usage_atteint"] < 83.0, res["usage_atteint"])
        self.assertIsNone(res["detection_s"])                          # aucun suivi : liberation immediate
        self.assertEqual((m.fichiers, m.dossier), ({}, None))
        self.assertTrue(any("plafond" in l for l in lignes))

    def test_le_plafond_n_est_jamais_depasse_sans_croissance_externe(self):
        for taille_go in (20, 38, 60):
            for u in (50, 60, 70, 75, 77):
                with self.subTest(taille_go=taille_go, usage=u):
                    fs = fs_usage(float(u), taille_go * GIO)
                    res, lignes, m = lancer(H.MondeSimule(fs.taille, fs.dispo, helios="resolve"))
                    self.assertIn(res["verdict"], ("OK", "REFUS"), res)
                    self.assertLessEqual(m.usage_max, 82.0)
                    self.assertEqual(m.fichiers, {})
                    if res["verdict"] == "REFUS":
                        self.assertEqual(ecritures(m), [])             # refus = AUCUNE ecriture

    def test_refus_base_trop_haute_aucune_ecriture(self):
        for u in (78.0, 80.5, 85.0, 90.0):
            fs = fs_usage(u)
            res, lignes, m = lancer(H.MondeSimule(fs.taille, fs.dispo))
            with self.subTest(usage=u):
                self.assertEqual(res["verdict"], "REFUS")
                self.assertEqual(ecritures(m), [])
                self.assertTrue(res["liberation_ok"])

    def test_residu_dans_le_repertoire_de_test_refus_sans_toucher_aux_fichiers(self):
        etranger = {"helios-diskfill-%s-001.bin" % ("b" * 32): 10 * MIO}
        res, lignes, m = lancer(monde(dossier=etranger))
        self.assertEqual(res["verdict"], "REFUS")
        self.assertEqual(ecritures(m), [])
        self.assertEqual(set(m.fichiers), set(etranger))              # le fichier d'un autre lancement n'est PAS supprime
        self.assertIsNotNone(m.dossier)

    def test_echec_d_armement_aucune_allocation(self):
        res, lignes, m = lancer(monde(pannes={"armer": True}))
        self.assertEqual(res["verdict"], "ERREUR")
        self.assertNotIn("fallocate", m.appels)
        self.assertEqual((m.fichiers, m.dossier), ({}, None))          # le repertoire cree par l'outil est retire (vide)

    def test_minuteur_inactif_aucune_allocation(self):
        res, lignes, m = lancer(monde(pannes={"expiration_inactive": True}))
        self.assertEqual(res["verdict"], "REFUS")
        self.assertIn("minuteur", res["raison"])
        self.assertNotIn("fallocate", m.appels)
        self.assertIn("systemctl-stop", m.appels)                      # armement tente => desarmement tente

    def test_echec_de_creation_du_repertoire(self):
        res, lignes, m = lancer(monde(pannes={"mkdir": True}))
        self.assertEqual(res["verdict"], "ERREUR")
        self.assertEqual(ecritures(m), ["mkdir"])                      # la SEULE tentative : ni minuteur, ni allocation, ni rmdir
        self.assertEqual((m.fichiers, m.dossier, m.minuteurs), ({}, None, {}))

    def test_echec_d_allocation_libere_et_arrete(self):
        res, lignes, m = lancer(monde(pannes={"allouer": True}))
        self.assertEqual(res["verdict"], "ERREUR")
        self.assertEqual((m.fichiers, m.dossier), ({}, None))
        self.assertTrue(res["liberation_ok"])

    def test_perte_des_mesures_pendant_l_allocation(self):
        res, lignes, m = lancer(monde(helios="absent", pannes={"stats_apres": 3}))
        self.assertEqual(res["verdict"], "ERREUR", res)
        self.assertEqual(m.fichiers, {})                               # ce qui avait ete cree est retire malgre l'erreur
        self.assertTrue(res["liberation_ok"])
        self.assertIsNone(res["usage_final"])

    def test_liberation_impossible_le_minuteur_reste_arme_puis_libere(self):
        m = monde(helios="absent", pannes={"liberer": 99})
        res, lignes, m = lancer(m)
        self.assertEqual(res["verdict"], "AUCUNE_DETECTION")
        self.assertFalse(res["liberation_ok"])
        self.assertTrue(any("LIBERATION IMPOSSIBLE" in l for l in lignes))
        self.assertTrue(m.minuteurs[JETON][2])                         # PAS desarme tant que la liberation n'est pas verifiee
        self.assertNotIn("systemctl-stop", m.appels)
        self.assertTrue(m.fichiers)
        m.avancer(H.valider_parametres().expiration)                   # le minuteur systemd fait le travail seul
        self.assertEqual(m.fichiers, {})
        self.assertIn("expiration", [e for _, e in m.journal])

    def test_une_liberation_non_verifiee_n_est_pas_comptee_comme_reussie(self):
        # rm rend 0 mais les fichiers sont toujours la : seule la relecture du repertoire le revele
        res, lignes, m = lancer(monde(helios="absent", pannes={"liberer_sans_effet": True}))
        self.assertFalse(res["liberation_ok"])
        self.assertTrue(any("LIBERATION IMPOSSIBLE" in l for l in lignes))
        self.assertTrue(m.minuteurs[JETON][2])                         # le minuteur reste arme
        self.assertNotIn("systemctl-stop", m.appels)
        self.assertNotIn("rmdir", m.appels)                            # le repertoire n'est pas supprime tant qu'il n'est pas vide
        self.assertTrue(m.fichiers)
        m.avancer(H.valider_parametres().expiration)
        self.assertEqual(m.fichiers, {})

    def test_liberation_apres_une_panne_passagere(self):
        res, lignes, m = lancer(monde(helios="absent", pannes={"liberer": 2}))
        self.assertTrue(res["liberation_ok"])                           # 3e tentative
        self.assertEqual(m.fichiers, {})
        self.assertEqual(sum(1 for a in m.appels if a == "rm"), 3)

    def test_expiration_si_l_outil_est_tue_sans_finally(self):
        m = monde(helios="absent")
        ops = H.NoeudSimule(m)
        ops.creer_dossier()
        ops.armer_expiration(JETON, H.noms_prevus(JETON), 1020)
        ops.allouer(H.nom_fichier(JETON, 1), 512 * MIO)
        ops.allouer(H.nom_fichier(JETON, 2), 512 * MIO)
        self.assertEqual(len(m.fichiers), 2)
        m.avancer(1019)
        self.assertEqual(len(m.fichiers), 2)
        m.avancer(2)
        self.assertEqual(m.fichiers, {})

    def test_ctrl_c_libere_quand_meme(self):
        class Interrompue(H.HorlogeSimulee):
            def dormir(self, s):
                raise KeyboardInterrupt()
        m = monde(helios="absent")
        with self.assertRaises(KeyboardInterrupt):
            H.executer_test(H.NoeudSimule(m), H.SondeSimulee(m), Interrompue(m), H.valider_parametres(), JETON, lambda s: None)
        self.assertEqual((m.fichiers, m.dossier), ({}, None))
        self.assertFalse(m.minuteurs[JETON][2])

    def test_noms_exacts_et_un_seul_repertoire(self):
        res, lignes, m = lancer(monde(helios="absent"))
        self.assertTrue(all(H.RE_FICHIER.match(n) for n in res["alloues"]))
        self.assertEqual(len(set(res["alloues"])), len(res["alloues"]))
        self.assertLessEqual(len(res["alloues"]), H.MAX_FICHIERS)

    def test_bilan_affichable_et_ascii(self):
        res, lignes, m = lancer(monde(helios="resolve"))
        sortie = []
        H.afficher_bilan(res, H.valider_parametres(), sortie.append)
        for l in lignes + sortie:
            l.encode("ascii")
        self.assertTrue(any(l.startswith("BILAN : OK") for l in sortie))

    def test_croissance_vmdk_signalee(self):
        avant = {"alloue": 1000 * MIO}
        res, _, _ = lancer(monde(helios="resolve"), vmdk_avant=avant, vmdk_apres=lambda: {"alloue": 1000 * MIO + 10 * MIO})
        self.assertEqual(res["croissance_vmdk"], 10 * MIO)
        sortie = []
        res2, _, _ = lancer(monde(helios="resolve"), vmdk_avant=avant, vmdk_apres=lambda: {"alloue": 1000 * MIO + 400 * MIO})
        H.afficher_bilan(res2, H.valider_parametres(), sortie.append)
        self.assertTrue(any("a examiner" in l for l in sortie))
        res3, _, _ = lancer(monde(helios="resolve"), vmdk_avant=avant, vmdk_apres=lambda: None)
        self.assertIsNone(res3["croissance_vmdk"])


# ------------------------------------------------------------------------------------------------ monde E2E (porte du scenario DISK_E2E_158)
E2E_IMAGE = "pfe-backend:0.2.165-disk-e2e"                      # image Backend « attendue » par les tests (aucune image reelle n'est lue)
IP_BRIDGE_SIM = "192.168.148.161"
BRIDGE_DEPLOYE_SIM = "/opt/helios-ssh-bridge/bridge.py"
URL_ACTIONS_SIM = "http://127.0.0.1:8099/actions"


def sha_bridge_sot():
    with open(os.path.join(HERE, "..", "..", "infra", "helios-sot", "bridge", "bridge.py"), "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def e2e_pret(**surcharges):
    """Monde E2E « tout est pret » de l'emulateur (ConfigMap, deploiement, pods, Bridge, noeud, base, hote). `surcharges` remplace des cles."""
    monde = {
        "configmap": {"HELIOS_BRIDGE_ENABLED": "true", "HOST_DISK_E2E_158_ENABLED": "true", "REMEDIATION_COOLDOWN_MINUTES": "2"},
        "cm_time": "2026-11-13T22:00:00Z", "cm_illisible": False,
        "deploy": {"replicas": 2, "ready": 2, "updated": 2, "available": 2, "generation": 7, "observed": 7, "image": E2E_IMAGE, "env": [],
                   "illisible": False},
        "pods": [{"image": E2E_IMAGE, "start": "2026-11-14T00:30:00Z", "phase": "Running"},
                 {"image": E2E_IMAGE, "start": "2026-11-14T00:31:00Z", "phase": "Running"}], "pods_illisibles": False,
        "bridge_actions": ["HOST_IMAGE_GC", "HOST_RECLAIM_DISK", "RESTART_POD"], "bridge_curl_rc": 0, "bridge_sha": sha_bridge_sot(),
        "sonde": "ok",          # reponse de la SONDE du Bridge (validation seule) : ok | rc | jeton | json | rejet | execute | dict de champs a surcharger
        "systemd_run_rc": 0, "awk_rc": 0, "unites": [],
        "sql_illisible": False, "ages": [], "ouverts": [],
        "pages": [120, 140, 130],
    }
    monde.update(surcharges)
    return monde


# ------------------------------------------------------------------------------------------------ emulateur du noeud, de kubectl et de Prometheus
class ExecEmule(object):
    """Faux executeur : emule le noeud (ssh), kubectl / psql (local) et Prometheus (http) a partir d'un MondeSimule. AUCUNE E/S.
    Enregistre TOUTES les commandes et signale toute violation du jeu ferme (verbe inconnu, chemin hors du repertoire de test, joker)."""
    VERBES = {"stat", "findmnt", "test", "ls", "cat", "mkdir", "rmdir", "fallocate", "rm", "systemd-run", "systemctl", "hostname"}
    FIXES = {"/", "/var/lib", "/usr/bin/fallocate", "/usr/bin/rm", "/usr/bin/awk", "/var/lib/kubelet/config.yaml"}
    NOMS = {"192.168.148.155": "k8s-worker2", "192.168.148.158": "k8s-worker1"}          # table d'identite de l'emulateur
    IPS = {"k8s-worker2": "192.168.148.155", "k8s-worker1": "192.168.148.158"}
    # Grammaire du programme de garde, ECRITE ICI (oracle independant : rien n'est importe du modele de l'outil).
    RE_GARDE = re.compile(
        r'BEGIN \{ for \(n = 0; n < (?P<boucles>[0-9]{1,4}); n\+\+\) \{ c = "df -P -k /"; c \| getline h; c \| getline d; close\(c\); '
        r'split\(d, f, " "\); if \(f\[2\] > 0 && \(f\[2\] - f\[4\]\) \* 10000 / f\[2\] >= (?P<seuil>[0-9]{4})\) \{ '
        r'system\("/usr/bin/rm -f -- (?P<chemins>/var/lib/helios-diskfill/helios-diskfill-[0-9a-f]{32}-[0-9]{3}\.bin'
        r'(?: /var/lib/helios-diskfill/helios-diskfill-[0-9a-f]{32}-[0-9]{3}\.bin){0,15})"\); exit 0 \} '
        r'system\("sleep 2"\) \} \}')

    def __init__(self, m, noeud="k8s-worker1", primaire_sur="k8s-worker2", flag=None, hostname=None, ip_k8s=None, nom_k8s=None,
                 prom_pannes=None, sonde_pannes=None, e2e=True):
        self.m, self.noeud, self.primaire_sur, self.flag = m, noeud, primaire_sur, flag      # flag = ancien drapeau HOST_DISK_REMEDIATION_ENABLED
        self.e2e = e2e_pret() if e2e is True else (e2e if isinstance(e2e, dict) else None)    # monde E2E : True = pret, dict = personnalise
        self.e2e_ssh, self.e2e_local = [], []                                              # commandes de LECTURE de la porte, enregistrees
        self.hostname, self.ip_k8s, self.nom_k8s = hostname, ip_k8s, nom_k8s            # surcharges : simulent une IP reattribuee
        self.prom_pannes = dict(prom_pannes or {})                                       # {'series'|'usage'|'incident': mode}
        self.sonde_pannes = sonde_pannes                                                 # liste de bool (echec par lecture) ou 'toujours'
        self.ssh_appels, self.local_appels, self.http_appels, self.sql = [], [], [], []
        self.violations, self.inconnues, self.gardes_armees = [], [], []
        self.ops = H.NoeudSimule(m)

    def _chemin(self, p):
        ok = p in self.FIXES or p == DOSSIER or (isinstance(p, str) and p.startswith(DOSSIER + "/") and bool(H.RE_FICHIER.match(p[len(DOSSIER) + 1:])))
        if not ok or any(c in p for c in "*?[]{}~") or ".." in p:
            self.violations.append(p)
        return p.rsplit("/", 1)[-1]

    # SONDE du Bridge : oracle INDEPENDANT du modele de l'outil. Motifs OBLIGATOIRES et INTERDITS du script bash envoye sur .161.
    SONDE_OBLIGATOIRES = ("sudo -n sed -n 's/^HELIOS_BRIDGE_TOKEN=//p' /etc/helios/bridge.env", "X-Helios-Token: %s", "-H @-",
                          '"recommend_only":true', '"action":"HOST_RECLAIM_DISK"', '"scenario":"DISK_E2E_158"', '"cible":"k8s-worker1"', '"noeud":"k8s-worker1"',
                          '"type":"NODE_DISK_FULL"', '"ns":"k8s-nodes"', "http://127.0.0.1:8099/action ")
    SONDE_INTERDITS = (r"\brm\b", r"\bsystemctl\b", r"\bfallocate\b", r"\bmkdir\b", r"\brmdir\b", r"\bkill\b", r"\bchmod\b", r"\bchown\b", r"\btee\b", r"\bdd\b",
                       r"\bmv\b", r"\bcp\b", r"\bscp\b", r"\bssh\b", r"\bkubectl\b", r"\bsystemd-run\b", r">>", r"[^|]>[^&]", r"echo\s+[\"']?\$T", r"set -x", r"curl -v",
                       r"--trace", r"-H\s+\"X-Helios-Token", r"-H\s+'X-Helios-Token", r"--header")

    def _sonde_bridge(self, a):
        """Emule le Bridge deploye : valide la commande de la sonde (script bash) puis rend la reponse de la VALIDATION SEULE d'apres le monde."""
        w = self.e2e
        script = a[2]
        for motif in self.SONDE_OBLIGATOIRES:
            if motif not in script:
                self.violations.append("sonde : motif obligatoire absent : %s" % motif)
        for motif in self.SONDE_INTERDITS:
            if re.search(motif, script):
                self.violations.append("sonde : motif interdit present : %s" % motif)
        if len(re.findall(r"\$T\b", script)) != 2:                                    # [ -n "$T" ] et le tube vers curl : jamais ailleurs
            self.violations.append("sonde : le jeton est utilise ailleurs que dans le test de presence et le tube")
        mode = None if w is None else w.get("sonde")
        if mode is None or mode == "rc":
            return H.Res(255, "", "ssh: connect to host: Connection refused")
        if mode == "jeton":
            return H.Res(0, '{"sonde_erreur":"jeton_illisible"}\n', "")
        if mode == "json":
            return H.Res(0, "<html>502 Bad Gateway</html>\n", "")
        reponse = {"action": "HOST_RECLAIM_DISK", "scenario": "DISK_E2E_158", "noeud": "worker1", "execute": False, "ok": True, "exitCode": 0,
                   "stdout": "validation seule : %d fichier(s) a supprimer (0 Mo), 0 entree(s) etrangere(s) intacte(s)" % len(self.m.fichiers),
                   "stderr": "", "seuil_recuperation_pct": 77.0, "validation": "ACCEPTED", "execution_mode": "RECOMMEND_ONLY", "jeton": None,
                   "fichiers": len(self.m.fichiers), "etrangers": 0, "usage_avant_pct": round(self.m.fs().usage, 2)}
        if mode == "rejet":
            reponse.update(ok=False, rejet="DIRECTORY_UNREADABLE", stderr="stat du repertoire impossible")
            del reponse["validation"], reponse["execution_mode"]
        elif mode == "execute":
            reponse.update(execute=True)
        elif isinstance(mode, dict):
            reponse.update(mode)
        return H.Res(0, json.dumps(reponse) + "\n", "")

    def _ssh_e2e(self, ip, a):
        """Commandes de LECTURE de la porte E2E (Bridge sur .161 ; systemd-run / awk / unites sur .158). None si ce n'en est pas une."""
        w = self.e2e
        if ip == IP_BRIDGE_SIM and len(a) == 3 and a[:2] == ["bash", "-c"]:
            return self._sonde_bridge(a)
        if ip == IP_BRIDGE_SIM and a[:1] == ["curl"]:
            if w is None or w["bridge_curl_rc"] != 0:
                return H.Res(7 if w is not None else 255, "", "curl: (7) Failed to connect")
            if a != ["curl", "-sS", "--max-time", "8", URL_ACTIONS_SIM]:
                return None
            return H.Res(0, json.dumps({"catalogue": w["bridge_actions"]}) if w["bridge_actions"] is not None else "pas du json", "")
        if ip == IP_BRIDGE_SIM and a[:1] == ["sha256sum"]:
            if w is None or w["bridge_sha"] is None or a != ["sha256sum", BRIDGE_DEPLOYE_SIM]:
                return H.Res(1, "", "sha256sum: erreur")
            return H.Res(0, "%s  %s\n" % (w["bridge_sha"], BRIDGE_DEPLOYE_SIM), "")
        if a[:2] == ["test", "-x"] and len(a) == 3 and a[2] in ("/usr/bin/systemd-run", "/usr/bin/awk"):
            cle = "systemd_run_rc" if a[2].endswith("systemd-run") else "awk_rc"
            return H.Res(255 if w is None else w[cle], "", "")
        if a[:2] == ["systemctl", "list-units"]:
            if a != ["systemctl", "list-units", "--all", "--no-legend", "--plain", "helios-diskfill-*"] or w is None or w["unites"] is None:
                return H.Res(1, "", "systemctl: erreur")
            return H.Res(0, "".join("%s loaded active running HELIOS test disque\n" % u for u in w["unites"]), "")
        return None

    def ssh(self, ip, argv, delai=45):
        argv = list(argv)
        self.ssh_appels.append((ip, argv))
        a = argv[2:] if argv[:2] == ["sudo", "-n"] else argv
        e2e = self._ssh_e2e(ip, a) if a else None
        if e2e is not None:
            self.e2e_ssh.append((ip, argv))
            return e2e
        if not a or a[0] not in self.VERBES:
            self.inconnues.append(argv)
            return H.Res(127, "", "commande inconnue")
        for t in a:
            if isinstance(t, str) and t.startswith("/"):
                self._chemin(t)
        v, m = a[0], self.m
        try:
            if v == "stat":
                if a[1] == "-f":
                    f = m.fs()
                    return H.Res(0, "4096 %d %d %d\n" % (f.taille // 4096, f.dispo // 4096, f.dispo // 4096), "")
                if a[1:4] == ["-c", "%F:%u:%g:%a:%d", "--"] and len(a) == 5:
                    info = self.ops.inspecter(a[4])
                    if info is None:
                        return H.Res(1, "", "stat: cannot statx '%s': No such file or directory" % a[4])
                    return H.Res(0, "%s:%d:%d:%s:%d\n" % (info["type"], info["uid"], info["gid"], info["mode"], info["dev"]), "")
            if v == "findmnt":
                if a[1] == "-no":
                    return H.Res(0, m.fstype + "\n", "")
                if a[1:5] == ["-n", "-o", "FSTYPE,TARGET", "-T"] and len(a) == 6:
                    info = self.ops.inspecter(a[5])
                    if info is None:
                        return H.Res(1, "", "findmnt: no such file or directory")
                    return H.Res(0, "%s %s\n" % (info["fstype"], info["montage"]), "")
            if v == "hostname":
                if m.pannes.get("hostname_illisible"):
                    return H.Res(1, "", "hostname: erreur")
                return H.Res(0, (self.hostname or self.NOMS.get(ip, "inconnu")) + "\n", "")
            if v == "cat":
                return H.Res(0, KUBELET, "")
            if v == "test":
                if a[:3] == ["test", "-x", "/usr/bin/fallocate"]:
                    return H.Res(1 if m.pannes.get("sans_fallocate") else 0, "", "")
                if a[:3] == ["test", "!", "-e"]:
                    return H.Res(1 if a[3].rsplit("/", 1)[-1] in m.fichiers else 0, "", "")
            if v == "ls":
                c = self.ops.contenu_dossier()
                if c is None:
                    return H.Res(2, "", "ls: cannot access '%s': No such file or directory" % DOSSIER)
                return H.Res(0, "".join(n + "\n" for n in c), "")
            if v == "mkdir":
                self.ops.creer_dossier()
                return H.Res(0, "", "")
            if v == "rmdir":
                self.ops.supprimer_dossier_vide()
                return H.Res(0, "", "")
            if v == "fallocate":
                self.ops.allouer(a[4].rsplit("/", 1)[-1], int(a[2]))
                return H.Res(0, "", "")
            if v == "rm":
                self.ops.liberer([t.rsplit("/", 1)[-1] for t in a[3:]])
                return H.Res(0, "", "")
            if v == "systemd-run":
                unite = [t for t in a if t.startswith("--unit=")][0].split("=", 1)[1]
                if "/usr/bin/awk" in a:                                         # GARDE cote noeud
                    i = a.index("/usr/bin/awk")
                    g = self.RE_GARDE.fullmatch(a[i + 1]) if len(a) == i + 2 else None
                    if not g or not unite.startswith(H.PREFIXE_UNITE_GARDE):
                        self.violations.append("programme ou unite de garde hors grammaire")
                        return H.Res(1, "", "programme refuse")
                    duree = int([t for t in a if t.startswith("--property=RuntimeMaxSec=")][0].split("=", 2)[2])
                    chemins = g.group("chemins").split(" ")
                    self.gardes_armees.append({"unite": unite, "seuil": int(g.group("seuil")), "boucles": int(g.group("boucles")),
                                               "chemins": chemins, "duree_max": duree})
                    self.ops.armer_garde(unite[len(H.PREFIXE_UNITE_GARDE):], [c.rsplit("/", 1)[-1] for c in chemins], int(g.group("seuil")), duree - 60)
                    return H.Res(0, "", "")
                delai_s = int([t for t in a if t.startswith("--on-active=")][0].split("=", 1)[1].rstrip("s"))
                chemins = a[a.index("--") + 1:]
                if a[a.index("--") - 2:a.index("--")] != ["/usr/bin/rm", "-f"]:
                    self.violations.append("commande du minuteur")
                self.ops.armer_expiration(unite[len(H.PREFIXE_UNITE):], [c.rsplit("/", 1)[-1] for c in chemins], delai_s)
                return H.Res(0, "", "")
            if v == "systemctl":
                unite = a[2]
                if unite.startswith(H.PREFIXE_UNITE) and unite.endswith(".timer"):
                    jeton = unite[len(H.PREFIXE_UNITE):-len(".timer")]
                    actif_fn, stop_fn = self.ops.expiration_active, self.ops.desarmer_expiration
                elif unite.startswith(H.PREFIXE_UNITE_GARDE) and unite.endswith(".service"):
                    jeton = unite[len(H.PREFIXE_UNITE_GARDE):-len(".service")]
                    actif_fn, stop_fn = self.ops.garde_active, self.ops.desarmer_garde
                else:
                    self.violations.append("unite systemctl hors du jeu ferme : %s" % unite)
                    return H.Res(1, "", "unite refusee")
                if a[1] == "is-active":
                    actif = actif_fn(jeton)
                    return H.Res(0 if actif else 3, "active\n" if actif else "inactive\n", "")
                if a[1] == "stop":
                    stop_fn(jeton)
                    return H.Res(0, "", "")
        except (H.ErreurNoeud, H.RefusSecurite) as e:
            return H.Res(1, "", str(e))
        self.inconnues.append(argv)
        return H.Res(127, "", "forme inconnue")

    def local(self, argv, delai=40, entree=None):
        argv = list(argv)
        self.local_appels.append((argv, entree))
        if argv[:3] == ["kubectl", "get", "nodes"]:
            return H.Res(0, "k8s-master Ready control-plane 1d v1\nk8s-worker1 Ready <none> 1d v1\nk8s-worker2 Ready <none> 1d v1\n", "")
        if argv[:3] == ["kubectl", "get", "node"] and len(argv) == 6 and argv[4:] == ["-o", "json"]:
            nom = self.nom_k8s or argv[3]
            ip = self.ip_k8s or self.IPS.get(argv[3], "10.0.0.1")
            return H.Res(0, json.dumps({"metadata": {"name": nom}, "status": {
                "conditions": [{"type": "Ready", "status": "True"}, {"type": "DiskPressure", "status": "False"}],
                "addresses": [{"type": "InternalIP", "address": ip}, {"type": "Hostname", "address": nom}]}}), "")
        if "cluster.postgresql.cnpg.io" in argv:
            return H.Res(0, json.dumps({"items": [{"status": {"currentPrimary": "postgres-8", "readyInstances": 2,
                                                             "phase": "Cluster in healthy state"}, "spec": {"instances": 2}}]}), "")
        if "pod" in argv and "postgres-8" in argv:
            return H.Res(0, self.primaire_sur, "")
        w = self.e2e
        if "configmap" in argv:
            if "--show-managed-fields" in argv:
                self.e2e_local.append(argv)
            data = dict(w["configmap"]) if w is not None else {}
            if self.flag is not None:
                data["HOST_DISK_REMEDIATION_ENABLED"] = self.flag
            meta = {} if w is None or w["cm_time"] is None else {"managedFields": [{"manager": "kubectl", "time": w["cm_time"]}]}
            if w is not None and w["cm_illisible"]:
                return H.Res(1, "", "Error from server")
            return H.Res(0, json.dumps({"data": data, "metadata": meta}), "")
        if argv[:5] == ["kubectl", "-n", "pfe", "get", "deployment"] and argv[5:] == ["backend", "-o", "json"]:
            self.e2e_local.append(argv)
            if w is None or w["deploy"]["illisible"]:
                return H.Res(1, "", "Error from server")
            d = w["deploy"]
            status = {"observedGeneration": d["observed"]}
            for cle, nom in (("replicas", "replicas"), ("ready", "readyReplicas"), ("updated", "updatedReplicas"), ("available", "availableReplicas")):
                if d[cle] is not None:
                    status[nom] = d[cle]
            return H.Res(0, json.dumps({"metadata": {"generation": d["generation"]}, "spec": {"replicas": d["replicas"], "template": {"spec": {
                "containers": [{"name": "backend", "image": d["image"], "env": d["env"]}]}}}, "status": status}), "")
        if argv[:5] == ["kubectl", "-n", "pfe", "get", "pods"] and argv[5:] == ["-l", "app=backend", "-o", "json"]:
            self.e2e_local.append(argv)
            if w is None or w["pods_illisibles"]:
                return H.Res(1, "", "Error from server")
            items = []
            for p in w["pods"]:
                meta = {"name": "backend-x"}
                if p.get("terminating"):
                    meta["deletionTimestamp"] = "2026-11-14T00:40:00Z"
                items.append({"metadata": meta, "status": {"phase": p["phase"], "startTime": p["start"]},
                              "spec": {"containers": [{"name": "backend", "image": p["image"]}]}})
            return H.Res(0, json.dumps({"items": items}), "")
        if argv[:1] == ["powershell"]:
            self.e2e_local.append(argv)
            if w is None or w["pages"] is None:
                return H.Res(1, "", "powershell: erreur")
            return H.Res(0, "".join("%s\n" % v for v in w["pages"]), "")
        if argv[:2] == ["kubectl", "exec"] and "psql" in argv:
            self.sql.append(entree or "")
            for instruction in [s.strip() for s in (entree or "").split(";") if s.strip()]:
                if not instruction.lower().startswith("select "):
                    self.violations.append("SQL non SELECT : %s" % instruction[:40])
            if "'AGE'" in (entree or ""):                                      # lecture SQL de la porte E2E (cooldown, incidents ouverts)
                self.e2e_local.append(argv)
                if w is None or w["sql_illisible"]:
                    return H.Res(1, "", "psql: connection failed")
                lignes = ["AGE|%d" % a for a in w["ages"]] + ["OUVERT|%d|%s|%s" % (i["id"], i["app"], i["statut"]) for i in w["ouverts"]]
                return H.Res(0, "".join(l + "\n" for l in lignes), "")
            if "'DISQUE'" in (entree or ""):
                return H.Res(0, "MAXID|123\n", "")
            if self.sonde_pannes == "toujours" or (self.sonde_pannes and self.sonde_pannes.pop(0)):      # sonde de l'incident en panne
                return H.Res(1, "", "psql: connection failed")
            i = self.m.incident
            return H.Res(0, ("%d|%s|%s\n" % (i["id"], i["statut"], self.m.action or "-")) if i else "", "")
        self.inconnues.append(argv)
        return H.Res(127, "", "")

    def http_json(self, url, delai=25):
        self.http_appels.append(url)
        q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)["query"][0]
        if q.startswith("count(node_filesystem_avail_bytes"):
            v, kind = "1", "series"
        elif q.startswith("(1-(node_filesystem_avail_bytes"):
            v, kind = "%.4f" % self.m.fs().usage, "usage"
        elif "helios_incident" in q:
            v, kind = "0", "incident"
        else:
            self.inconnues.append(url)
            return None
        mode = self.prom_pannes.get(kind)
        if mode == "none":
            return None                                                        # echec HTTP / JSON invalide
        if mode == "statut":
            return {"status": "error", "errorType": "bad_data"}
        if mode == "json":
            return {"status": "success"}                                       # corps sans data
        if mode == "valeur":
            return {"status": "success", "data": {"result": [{"value": [0, "abc"]}]}}
        return {"status": "success", "data": {"result": [{"value": [0, v]}]}}


class DepsFaux(object):
    def __init__(self, exe, horloge, ram=4000, c_libre=36.1, now=None):
        self.exe, self.h, self.ram, self.c, self.now = exe, horloge, ram, c_libre, now or datetime(2026, 11, 13, 12, 0, 0, tzinfo=timezone.utc)

    def executeur(self):
        return self.exe

    def horloge(self):
        return self.h

    def maintenant(self):
        return self.now

    def nouveau_jeton(self):
        return JETON

    def memoire_hote_mo(self):
        return self.ram

    def espace_libre_go(self, chemin):
        return self.c


class PiegeDeps(object):
    """Dependances dont TOUT acces est interdit : prouve qu'un refus ou la simulation n'en construit aucune."""

    def __getattr__(self, nom):
        raise AssertionError("dependance reelle utilisee : %s" % nom)


class NoeudSshTests(SansInfra):
    """Le noeud reel (NoeudSsh) sur l'emulateur : jeu ferme de commandes, noms exacts, aucun joker, aucune ecriture hors du repertoire."""

    def setUp(self):
        super().setUp()
        self.m = monde(helios="resolve")
        self.exe = ExecEmule(self.m)
        self.n = H.NoeudSsh(self.exe, "192.168.148.158")

    def test_lectures(self):
        self.assertAlmostEqual(self.n.stats().usage, 70.465, places=2)
        self.assertEqual((self.n.type_fs(), self.n.fallocate_present()), ("ext4", True))
        self.assertIsNone(self.n.contenu_dossier())
        self.assertEqual(H.seuil_kubelet(H.parse_kubelet(self.n.config_kubelet())), 85.0)
        argv = [a for _, a in self.exe.ssh_appels]
        self.assertEqual(argv[0], ["stat", "-f", "-c", "%S %b %a %f", "/"])                 # pas de sudo pour la mesure
        self.assertIn(["sudo", "-n", "ls", "-A", "--", DOSSIER], argv)
        self.assertIn(["sudo", "-n", "cat", "/var/lib/kubelet/config.yaml"], argv)

    def test_creation_allocation_liberation_arguments_exacts(self):
        self.n.creer_dossier()
        nom = H.nom_fichier(JETON, 1)
        self.n.allouer(nom, 512 * MIO)
        self.assertEqual(self.n.contenu_dossier(), [nom])
        self.n.liberer([nom])
        self.n.supprimer_dossier_vide()
        argv = [a for _, a in self.exe.ssh_appels]
        chemin = DOSSIER + "/" + nom
        self.assertIn(["sudo", "-n", "mkdir", "-m", "0700", "--", DOSSIER], argv)
        self.assertIn(["sudo", "-n", "test", "!", "-e", chemin], argv)
        self.assertIn(["sudo", "-n", "fallocate", "-l", str(512 * MIO), "--", chemin], argv)
        self.assertIn(["sudo", "-n", "rm", "-f", "--", chemin], argv)
        self.assertIn(["sudo", "-n", "rmdir", "--", DOSSIER], argv)
        self.assertEqual((self.exe.violations, self.exe.inconnues), ([], []))

    def test_noms_invalides_refuses_avant_tout_appel_ssh(self):
        for mauvais in ("../x", "*", "helios-diskfill-*.bin", "/tmp/x", "x.bin", "helios-diskfill-%s-001.bin/../a" % JETON):
            with self.subTest(nom=mauvais):
                with self.assertRaises(H.RefusSecurite):
                    self.n.liberer([mauvais])
                with self.assertRaises(H.RefusSecurite):
                    self.n.allouer(mauvais, MIO)
                with self.assertRaises(H.RefusSecurite):
                    self.n.armer_expiration(JETON, [mauvais], 600)
        self.assertEqual(self.exe.ssh_appels, [])

    def test_tailles_d_allocation_bornees(self):
        for octets in (0, -1, 1.5, "10", None, H.BLOC_MAX + 1):
            with self.subTest(octets=octets), self.assertRaises(H.RefusSecurite):
                self.n.allouer(H.nom_fichier(JETON, 1), octets)
        self.assertEqual(self.exe.ssh_appels, [])

    def test_allocation_refuse_un_fichier_existant(self):
        self.n.creer_dossier()
        nom = H.nom_fichier(JETON, 1)
        self.n.allouer(nom, MIO)
        with self.assertRaises(H.ErreurNoeud):
            self.n.allouer(nom, MIO)
        self.assertEqual(len([a for _, a in self.exe.ssh_appels if "fallocate" in a]), 1)       # le test "! -e" a bloque le 2e

    def test_minuteur_d_expiration(self):
        noms = H.noms_prevus(JETON)
        self.n.armer_expiration(JETON, noms, 1020)
        self.assertTrue(self.n.expiration_active(JETON))
        argv = [a for _, a in self.exe.ssh_appels if "systemd-run" in a][0]
        self.assertEqual(argv[:3], ["sudo", "-n", "systemd-run"])
        self.assertIn("--unit=helios-diskfill-expire-" + JETON, argv)
        self.assertIn("--on-active=1020s", argv)
        i = argv.index("--")
        self.assertEqual(argv[i - 2:i], ["/usr/bin/rm", "-f"])
        self.assertEqual(argv[i + 1:], [DOSSIER + "/" + n for n in noms])                      # 16 noms EXACTS, aucun joker
        self.n.desarmer_expiration(JETON)
        self.assertFalse(self.n.expiration_active(JETON))
        self.assertIn(["sudo", "-n", "systemctl", "stop", "helios-diskfill-expire-%s.timer" % JETON], [a for _, a in self.exe.ssh_appels])

    def test_minuteur_parametres_invalides(self):
        for jeton, noms, delai in (("zz", H.noms_prevus(JETON), 600), (JETON, [], 600), (JETON, H.noms_prevus(JETON), 59),
                                   (JETON, H.noms_prevus(JETON), 3601), (JETON, H.noms_prevus(JETON), "600")):
            with self.subTest(jeton=jeton, delai=delai), self.assertRaises(H.RefusSecurite):
                self.n.armer_expiration(jeton, noms, delai)
        self.assertEqual(self.exe.ssh_appels, [])

    def test_mesure_en_echec_ou_illisible(self):
        class Muet(object):
            def ssh(self, ip, argv, delai=45):
                return H.Res(255, "", "timeout")
        with self.assertRaises(H.ErreurNoeud):
            H.NoeudSsh(Muet(), "x").stats()
        with self.assertRaises(H.ErreurNoeud):
            H.NoeudSsh(Muet(), "x").contenu_dossier()
        self.assertFalse(H.NoeudSsh(Muet(), "x").fallocate_present())

        class Charabia(object):
            def ssh(self, ip, argv, delai=45):
                return H.Res(0, "n'importe quoi", "")
        with self.assertRaises(H.ErreurNoeud):
            H.NoeudSsh(Charabia(), "x").stats()

    def test_execution_complete_sur_l_emulateur_jeu_ferme(self):
        res = H.executer_test(self.n, H.SondeSimulee(self.m), H.HorlogeSimulee(self.m), H.valider_parametres(), JETON, lambda s: None)
        self.assertEqual(res["verdict"], "OK", res)
        self.assertEqual((self.exe.violations, self.exe.inconnues), ([], []))
        verbes = {(a[2:] if a[:2] == ["sudo", "-n"] else a)[0] for _, a in self.exe.ssh_appels}
        self.assertTrue(verbes <= ExecEmule.VERBES, verbes)
        for _, a in self.exe.ssh_appels:
            programme = a.index("/usr/bin/awk") + 1 if "/usr/bin/awk" in a else None        # SEUL argument autorise a contenir de la syntaxe
            for i, t in enumerate(a):
                if i != programme:
                    self.assertNotRegex(t, r"[*?\[\]{}`$;|&<>]|\.\.", a)
        self.assertEqual(len(self.exe.gardes_armees), 1)
        self.assertEqual((self.m.fichiers, self.m.dossier), ({}, None))

    def test_les_verbes_systemctl_se_limitent_a_is_active_et_stop(self):
        H.executer_test(self.n, H.SondeSimulee(self.m), H.HorlogeSimulee(self.m), H.valider_parametres(), JETON, lambda s: None)
        vus = {a[a.index("systemctl") + 1] for _, a in self.exe.ssh_appels if "systemctl" in a}
        self.assertEqual(vus, {"is-active", "stop"})


# ------------------------------------------------------------------------------------------------ executeur reel (sans reseau)
class ExecReelTests(SansInfra):
    def test_ssh_quote_les_arguments_et_n_execute_que_ssh(self):
        vus = []

        def faux_run(cmd, **kw):
            vus.append((cmd, kw))
            return subprocess.CompletedProcess(cmd, 0, "sortie", "")
        with mock.patch.object(H.subprocess, "run", faux_run):
            r = H.ExecReel().ssh("192.168.148.155", ["sudo", "-n", "rm", "-f", "--", "/var/lib/x; reboot", "a b"])
        self.assertEqual(r, H.Res(0, "sortie", ""))
        cmd = vus[0][0]
        self.assertEqual(cmd[:len(H.SSH)], H.SSH)
        self.assertEqual(cmd[len(H.SSH)], "kube@192.168.148.155")
        self.assertEqual(cmd[len(H.SSH) + 1], "sudo -n rm -f -- '/var/lib/x; reboot' 'a b'")        # injection neutralisee par le quoting
        self.assertEqual(len(cmd), len(H.SSH) + 2)
        self.assertTrue(vus[0][1]["capture_output"] and vus[0][1]["text"])

    def test_ssh_en_echec_donne_255_sans_lever(self):
        def boom(cmd, **kw):
            raise OSError("pas de ssh")
        with mock.patch.object(H.subprocess, "run", boom):
            self.assertEqual(H.ExecReel().ssh("x", ["true"]).rc, 255)
            self.assertEqual(H.ExecReel().local(["kubectl", "get", "nodes"]).rc, 255)

    def test_http_json(self):
        class Rep(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False
        with mock.patch.object(H.urllib.request, "urlopen", lambda url, timeout=0: Rep(b'{"status": "success"}')):
            self.assertEqual(H.ExecReel().http_json("http://x"), {"status": "success"})
        with mock.patch.object(H.urllib.request, "urlopen", lambda url, timeout=0: Rep(b"pas du json")):
            self.assertIsNone(H.ExecReel().http_json("http://x"))
        with mock.patch.object(H.urllib.request, "urlopen", _interdit):
            self.assertIsNone(H.ExecReel().http_json("http://x"))


# ------------------------------------------------------------------------------------------------ ligne de commande
class CliBase(SansInfra):
    """Base des tests en ligne de commande : dossier temporaire de VM (journaux VMware calmes), executeur emule, `deps`."""

    def setUp(self):
        super().setUp()
        self.tmp = tempfile.mkdtemp(prefix="helios_disk_test_")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        calme = "2026-11-13T10:00:00.000Z In(05) vmx Guest: tout va bien\n"
        fabriquer_vm(self.tmp, "k8s-worker1", journal=calme)
        fabriquer_vm(self.tmp, "k8s-worker2", journal=calme)

    def cli(self, argv, deps=None, image=True):
        """Lance H.principal. `image=True` : ajoute --image-backend aux lancements REELS de .158 (la porte E2E l'exige) ; `image=False` :
        n'ajoute rien (pour tester son absence)."""
        argv = list(argv)
        reel_158 = "--target" in argv and argv[argv.index("--target") + 1] == "158" and ("--go" in argv or "--preflight" in argv)
        if image and reel_158 and "--image-backend" not in argv:
            argv += ["--image-backend", E2E_IMAGE]
        lignes = []
        code = H.principal(argv, deps, lignes.append)
        return code, "\n".join(lignes)

    def deps(self, m, flag=None, primaire_sur="k8s-worker2", ram=4000, c=36.1, **exe):
        return DepsFaux(ExecEmule(m, primaire_sur=primaire_sur, flag=flag, **exe), H.HorlogeSimulee(m), ram, c)


class LigneDeCommandeTests(CliBase):
    def test_cible_obligatoire(self):
        code, sortie = self.cli([], PiegeDeps())
        self.assertEqual(code, 2)
        self.assertIn("EXPLICITE", sortie)

    def test_cibles_hors_perimetre_et_alias_inconnus(self):
        for t in ("153", "160", "161", "162", "k8s-master", "foo"):
            with self.subTest(t=t):
                self.assertEqual(self.cli(["--target", t], PiegeDeps())[0], 2)

    def test_seuils_desserres_refuses(self):
        for opt in (["--target-pct", "82"], ["--cap-pct", "90"], ["--emergency-pct", "85"], ["--recovery-pct", "80"], ["--chunk-mb", "4096"]):
            with self.subTest(opt=opt):
                self.assertEqual(self.cli(["--target", "158"] + opt, PiegeDeps())[0], 2)

    def test_defaut_simulation_158(self):
        code, sortie = self.cli(["--target", "158"], PiegeDeps())
        self.assertEqual(code, 0, sortie)
        self.assertIn("SIMULATION", sortie)
        self.assertIn("aucune commande reelle executee", sortie)
        self.assertIn("BILAN : OK", sortie)
        self.assertIn("--go --confirm DISK-158-81-82-83", sortie)
        self.assertNotIn("--accepte-risque-primaire-cnpg", sortie)

    def test_defaut_simulation_155_refuse_car_primaire_cnpg(self):
        code, sortie = self.cli(["--target", "155"], PiegeDeps())
        self.assertEqual(code, 1)
        self.assertIn("PRIMAIRE CNPG", sortie)
        self.assertIn("NON accepte", sortie)
        self.assertNotIn("BILAN", sortie)

    def test_simulation_155_avec_acquittement_explicite(self):
        code, sortie = self.cli(["--target", "155", "--accepte-risque-primaire-cnpg"], PiegeDeps())
        self.assertEqual(code, 0, sortie)
        self.assertIn("accepte explicitement", sortie)
        self.assertIn("--accepte-risque-primaire-cnpg", sortie.split("Lancement reel")[1])

    def test_simulation_des_scenarios_helios(self):
        for helios, verdict, code in (("resolve", "OK", 0), ("detect", "DETECTION_SEULE", 3), ("absent", "AUCUNE_DETECTION", 1)):
            c, s = self.cli(["--target", "158", "--sim-helios", helios], PiegeDeps())
            with self.subTest(helios=helios):
                self.assertEqual(c, code, s)
                self.assertIn("BILAN : %s" % verdict, s)

    def test_simulation_croissance_externe_declenche_l_urgence(self):
        c, s = self.cli(["--target", "158", "--sim-helios", "absent", "--sim-croissance-mo-s", "20"], PiegeDeps())
        self.assertEqual(c, 1)
        self.assertIn("BILAN : URGENCE", s)

    def test_simulation_pre_vol_rouge_ne_lance_rien(self):
        for opt, mot in ((["--sim-ram-mo", "1000"], "RAM de l'hote"), (["--sim-usage", "78"], "recuperation"), (["--sim-usage", "81"], "incident disque")):
            c, s = self.cli(["--target", "158"] + opt, PiegeDeps())
            with self.subTest(opt=opt):
                self.assertEqual(c, 1)
                self.assertIn(mot, s)
                self.assertIn("SIMULATION arretee", s)
                self.assertNotIn("BILAN", s)

    def test_sortie_de_simulation_ascii(self):
        for t in ("155", "158"):
            c, s = self.cli(["--target", t, "--accepte-risque-primaire-cnpg"], PiegeDeps())
            s.encode("ascii")

    def test_go_exige_la_confirmation_exacte(self):
        for argv in (["--target", "158", "--go"], ["--target", "158", "--go", "--confirm", "DISK-158"],
                     ["--target", "158", "--go", "--confirm", "DISK-155-81-82-83"], ["--target", "158", "--go", "--confirm", "disk-158-81-82-83"]):
            with self.subTest(argv=argv):
                c, s = self.cli(argv, PiegeDeps())
                self.assertEqual(c, 2)
                self.assertIn("--go exige --confirm DISK-158-81-82-83", s)

    def test_go_et_preflight_incompatibles(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            H.principal(["--target", "158", "--go", "--preflight"], PiegeDeps(), lambda s: None)

    def test_preflight_reel_ne_fait_que_lire(self):
        m = monde(dispo=12639 * MIO)
        d = self.deps(m)
        c, s = self.cli(["--target", "158", "--preflight", "--vm-base", self.tmp], d)
        self.assertEqual(c, 0, s)
        self.assertIn("PRE-VOL DU SCENARIO DISK_E2E_158", s)                  # .158 : la porte du scenario est appliquee au pre-vol reel
        self.assertIn("VERDICT : VERT", s)
        self.assertIn("RIEN n'a ete cree ni modifie", s)
        verbes = {(a[2:] if a[:2] == ["sudo", "-n"] else a)[0] for _, a in d.exe.ssh_appels}
        # lecture seule : aucun verbe d'ecriture ; les lectures de la porte (curl /actions, sha256sum, systemctl list-units) s'ajoutent
        self.assertTrue(verbes <= {"stat", "findmnt", "test", "ls", "cat", "hostname", "curl", "sha256sum", "systemctl", "bash"}, verbes)
        # `bash` = la SONDE du Bridge (validation seule) : une seule fois, uniquement vers .161, jamais vers un noeud Kubernetes
        self.assertEqual([ip for ip, a in d.exe.ssh_appels if a[:1] == ["bash"]], [IP_BRIDGE_SIM])
        self.assertEqual([a for _, a in d.exe.ssh_appels if a[:1] == ["test"] and a[:2] != ["test", "-x"]], [])
        self.assertEqual([a for _, a in d.exe.ssh_appels if any(w in a for w in ("systemd-run", "mkdir", "rm", "rmdir", "fallocate"))], [])
        self.assertEqual([a for _, a in d.exe.ssh_appels if a[:1] == ["systemctl"] and a[1:2] != ["list-units"]], [])     # ni is-active ni stop
        self.assertEqual({ip for ip, _ in d.exe.e2e_ssh}, {IP_BRIDGE_SIM, "192.168.148.158"})                          # Bridge (.161) ET noeud (.158)
        self.assertEqual(ecritures(m), [])
        self.assertEqual((d.exe.violations, d.exe.inconnues), ([], []))
        for argv, _ in d.exe.local_appels:
            if argv[:1] == ["powershell"]:                                                                              # lecture CIM des pages/s
                self.assertEqual(argv[:3], ["powershell", "-NoProfile", "-NonInteractive"], argv)
                continue
            self.assertTrue(argv[:2] in (["kubectl", "get"], ["kubectl", "-n"], ["kubectl", "exec"]), argv)
            self.assertNotIn(argv[1] if argv[1] != "-n" else argv[3], ("delete", "apply", "scale", "patch", "drain", "cordon", "edit"))

    def test_preflight_rouge_si_le_noeud_est_le_primaire_cnpg(self):
        m = monde()
        d = self.deps(m, primaire_sur="k8s-worker2")
        c, s = self.cli(["--target", "155", "--preflight", "--vm-base", self.tmp], d)
        self.assertEqual(c, 1)
        self.assertIn("PRIMAIRE CNPG", s)
        self.assertEqual(ecritures(m), [])

    def test_preflight_lit_les_drapeaux_du_backend(self):
        """Le pre-vol de .158 lit LES DEUX drapeaux : celui du scenario doit etre « true », l'ancien ne doit PAS l'etre."""
        pont = {"HELIOS_BRIDGE_ENABLED": "true", "REMEDIATION_COOLDOWN_MINUTES": "2"}
        cas = (({}, None, 0, "VERDICT : VERT"),
               ({}, "false", 0, "VERDICT : VERT"),
               ({}, "true", 1, "E2E_DRAPEAU_LEGACY"),
               ({"configmap": dict(pont)}, None, 1, "E2E_DRAPEAU_DISQUE"),
               ({"configmap": dict(pont, HOST_DISK_E2E_158_ENABLED="false")}, None, 1, "E2E_DRAPEAU_DISQUE"),
               ({"configmap": {"HOST_DISK_E2E_158_ENABLED": "true"}}, None, 1, "E2E_DRAPEAU_BRIDGE"))
        for monde_e2e, flag, code, attendu in cas:
            m = monde(dispo=12639 * MIO)
            c, s = self.cli(["--target", "158", "--preflight", "--vm-base", self.tmp], self.deps(m, flag=flag, e2e=e2e_pret(**monde_e2e)))
            with self.subTest(monde=monde_e2e, flag=flag):
                self.assertEqual(c, code, s)
                self.assertIn(attendu, s)

    def test_preflight_rouge_ram_ou_c_basse(self):
        for kw in ({"ram": 900}, {"c": 20.0}):
            c, s = self.cli(["--target", "158", "--preflight", "--vm-base", self.tmp], self.deps(monde(dispo=12639 * MIO), **kw))
            with self.subTest(kw=kw):
                self.assertEqual(c, 1)
                self.assertIn("VERDICT : ROUGE", s)

    def test_go_complet_sur_emulateur(self):
        m = monde(dispo=12639 * MIO, helios="resolve")
        d = self.deps(m)                                      # monde E2E pret (drapeau du scenario actif) : pre-vol VERT, aucun acquittement
        c, s = self.cli(["--target", "158", "--go", "--confirm", "DISK-158-81-82-83", "--vm-base", self.tmp], d)
        self.assertEqual(c, 0, s)
        self.assertIn("PRE-VOL DU SCENARIO DISK_E2E_158", s)
        self.assertIn("BILAN : OK", s)
        self.assertIn("croissance des extents VMDK sur C: : +0 Mo", s)
        self.assertEqual((m.fichiers, m.dossier), ({}, None))
        self.assertLessEqual(m.usage_max, 82.0)
        self.assertEqual((d.exe.violations, d.exe.inconnues), ([], []))
        self.assertEqual(len(d.exe.gardes_armees), 1)                                  # la garde cote noeud a bien ete armee

    def test_go_refuse_si_le_pre_vol_est_rouge(self):
        m = monde(dispo=12639 * MIO)
        d = self.deps(m, ram=500)
        c, s = self.cli(["--target", "158", "--go", "--confirm", "DISK-158-81-82-83", "--vm-base", self.tmp], d)
        self.assertEqual(c, 1)
        self.assertIn("REFUS : pre-vol ROUGE", s)
        self.assertEqual(ecritures(m), [])
        self.assertEqual([a for _, a in d.exe.ssh_appels if any(v in a for v in ("fallocate", "mkdir", "rm", "systemd-run"))], [])

    def test_go_sur_le_primaire_cnpg_exige_l_acquittement(self):
        m = monde(helios="resolve")
        d = self.deps(m, primaire_sur="k8s-worker2")
        c, s = self.cli(["--target", "155", "--go", "--confirm", "DISK-155-81-82-83", "--vm-base", self.tmp], d)
        self.assertEqual(c, 1)
        self.assertEqual(ecritures(m), [])
        m2 = monde(helios="resolve")
        d2 = self.deps(m2, primaire_sur="k8s-worker2", flag="true")
        c2, s2 = self.cli(["--target", "155", "--go", "--confirm", "DISK-155-81-82-83", "--accepte-risque-primaire-cnpg", "--vm-base", self.tmp], d2)
        self.assertEqual(c2, 0, s2)
        self.assertEqual((m2.fichiers, m2.dossier), ({}, None))

    def test_go_detection_seule_quand_helios_ne_remedie_pas_dans_le_monde_simule(self):
        """La porte est VERTE mais, dans le monde simule, HELIOS ne remedie pas : le bilan est DETECTION_SEULE et le noeud est laisse propre."""
        m = monde(dispo=12639 * MIO, helios="detect")
        c, s = self.cli(["--target", "158", "--go", "--confirm", "DISK-158-81-82-83", "--vm-base", self.tmp], self.deps(m))
        self.assertEqual(c, 3, s)
        self.assertIn("DETECTION_SEULE", s)
        self.assertEqual((m.fichiers, m.dossier), ({}, None))

    def test_158_n_a_aucun_lancement_en_detection_seule(self):
        for extra in (["--accepte-orange", "REMEDIATION_NON_ACTIVEE"], ["--accepte-risque-primaire-cnpg"]):
            for mode in (["--go", "--confirm", "DISK-158-81-82-83"], ["--preflight"], []):
                with self.subTest(extra=extra, mode=mode):
                    c, s = self.cli(["--target", "158"] + mode + extra, PiegeDeps())            # refus AVANT toute dependance reelle
                    self.assertEqual(c, 2, s)
                    self.assertIn("DISK_E2E_158", s)
                    self.assertIn("rien n'a ete lance", s)


# ================================================================================================ CORRECTIONS DE LA REVUE DE SECURITE
# Tests INDEPENDANTS : chacun reproduit un defaut releve par la revue (B1-B3, M2-M4) avec ses propres valeurs et son propre oracle.
RACINE = {"type": "directory", "uid": 0, "gid": 0, "mode": "755", "dev": 64512, "fstype": "ext4", "montage": "/"}
DOSSIER_OK = dict(RACINE, mode="700")
ANOMALIES_DOSSIER = (("type", "symbolic link", "n'est pas un repertoire"), ("uid", 1000, "proprietaire"), ("gid", 1000, "proprietaire"),
                     ("mode", "755", "permissions"), ("mode", "777", "permissions"), ("mode", "1700", "permissions"),
                     ("dev", 64513, "autre systeme de fichiers"), ("fstype", "tmpfs", "systeme de fichiers"),
                     ("fstype", None, "systeme de fichiers"), ("montage", "/var", "point de montage"))
FAUX_GEL = ("2026-11-13T07:00:00.123Z In(05) vmx Guest: *** WARNING: GuestInfo collection interval longer than expected; "
            "actual=120 sec, expected=30 sec. ***\n")        # 5 h avant l'horloge de DepsFaux (2026-11-13T12:00Z) : gel de 2 min


def faits_noeud(cle="158", drapeau="true", **noeud):
    """Faits de pre-vol simules ; `noeud` surcharge des cles de faits['noeud']."""
    f = faits(cle, drapeau=drapeau)
    f["noeud"].update(noeud)
    return f


# ------------------------------------------------------------------------------------------------ B1 : volume, effet, cumul
class ControleDuVolumeTests(SansInfra):
    """B1 : le repertoire de test est sur le volume MESURE, vrai repertoire root:root 0700 ; chaque allocation a un effet mesurable ;
    le plafond cumule de 6 Gio s'applique DANS la boucle."""

    def test_parse_stat_chemin(self):
        self.assertEqual(H.parse_stat_chemin("directory:0:0:700:64512\n"), {"type": "directory", "uid": 0, "gid": 0, "mode": "700", "dev": 64512})
        self.assertEqual(H.parse_stat_chemin("symbolic link:0:0:777:64512")["type"], "symbolic link")
        for t in ("", None, "directory:0:0:700", "directory:0:0:700:1:2", "directory:a:0:700:1", "directory:0:0:789:1",
                  "directory:0:0:700:-1", "DIRECTORY:0:0:700:1", "x:0:0:700:1", "directory:0:0:70:1"):
            with self.subTest(t=t), self.assertRaises(H.ErreurNoeud):
                H.parse_stat_chemin(t)

    def test_parse_findmnt(self):
        self.assertEqual(H.parse_findmnt("ext4 /\n"), {"fstype": "ext4", "montage": "/"})
        for t in ("", None, "ext4", "ext4 / plus", "ext4\n/\nxfs /var"):
            with self.subTest(t=t), self.assertRaises(H.ErreurNoeud):
                H.parse_findmnt(t)

    def test_anomalies_du_dossier_une_par_une(self):
        self.assertEqual(H.anomalies_dossier(dict(DOSSIER_OK), RACINE), [])
        for cle, valeur, mot in ANOMALIES_DOSSIER:
            with self.subTest(cle=cle, valeur=valeur):
                a = H.anomalies_dossier(dict(DOSSIER_OK, **{cle: valeur}), RACINE)
                self.assertEqual(len(a), 1, a)
                self.assertIn(mot, a[0])
        for illisible in (None, {}):
            self.assertEqual(len(H.anomalies_dossier(illisible, RACINE)), 1)
            self.assertEqual(len(H.anomalies_dossier(DOSSIER_OK, illisible)), 1)

    def test_anomalies_cumulees(self):
        a = H.anomalies_dossier(dict(DOSSIER_OK, type="symbolic link", uid=1, mode="777", dev=1, montage="/data"), RACINE)
        self.assertEqual(len(a), 5, a)

    def test_anomalies_du_parent(self):
        self.assertEqual(H.anomalies_parent(dict(RACINE), RACINE), [])
        for modif in ({"dev": 2}, {"type": "symbolic link"}, {"fstype": "tmpfs"}, {"montage": "/var"}):
            with self.subTest(modif=modif):
                self.assertEqual(len(H.anomalies_parent(dict(RACINE, **modif), RACINE)), 1)
        self.assertEqual(len(H.anomalies_parent(None, RACINE)), 1)

    def verdict(self, **noeud):
        return H.evaluer(faits_noeud(**noeud), "158", H.valider_parametres())

    def test_preflight_dossier_conforme_ou_absent_avec_parent_conforme_est_vert(self):
        for kw in (dict(dossier=[], dossier_info=dict(DOSSIER_OK)), dict(dossier=None, dossier_info=None, parent=dict(RACINE))):
            with self.subTest(kw=kw):
                r = self.verdict(**kw)
                self.assertEqual(r["verdict"], "VERT", r["raisons"])
                self.assertIn("meme volume que /", textes(r, "INFO"))

    def test_preflight_chaque_anomalie_du_dossier_existant_est_rouge(self):
        for cle, valeur, mot in ANOMALIES_DOSSIER:
            with self.subTest(cle=cle, valeur=valeur):
                r = self.verdict(dossier=[], dossier_info=dict(DOSSIER_OK, **{cle: valeur}))
                self.assertEqual(r["verdict"], "ROUGE")
                self.assertIn(mot, textes(r, "ROUGE"))
                self.assertIn("repertoire de test", textes(r, "ROUGE"))

    def test_preflight_dossier_absent_et_parent_non_conforme_est_rouge(self):
        for parent in (dict(RACINE, dev=2), dict(RACINE, type="symbolic link"), dict(RACINE, fstype="tmpfs"), dict(RACINE, montage="/var"), None):
            with self.subTest(parent=parent):
                r = self.verdict(dossier=None, dossier_info=None, parent=parent)
                self.assertEqual(r["verdict"], "ROUGE")
                self.assertIn("repertoire parent", textes(r, "ROUGE"))

    def test_preflight_faits_du_volume_illisibles_ou_absents_sont_rouges(self):
        self.assertEqual(self.verdict(racine=None)["verdict"], "ROUGE")
        self.assertEqual(self.verdict(dossier_info="inconnu")["verdict"], "ROUGE")
        for cle in ("racine", "dossier_info"):                          # cle absente du dictionnaire des faits : echec sur
            f = faits_noeud()
            del f["noeud"][cle]
            self.assertEqual(H.evaluer(f, "158", H.valider_parametres())["verdict"], "ROUGE", cle)

    def test_repertoire_cree_non_conforme_est_refuse_avant_toute_allocation_puis_retire(self):
        for panne in ("dossier_autre_fs", "dossier_lien", "dossier_proprio", "dossier_mode"):
            with self.subTest(panne=panne):
                res, lignes, m = lancer(monde(pannes={panne: True}))
                self.assertEqual(res["verdict"], "REFUS", res)
                self.assertIn("non conforme", res["raison"])
                self.assertEqual(ecritures(m), ["mkdir", "rmdir"])          # cree puis retire : ni minuteur, ni garde, ni fallocate
                self.assertEqual((m.fichiers, m.dossier, m.minuteurs, m.gardes), ({}, None, {}, {}))

    def test_repertoire_preexistant_non_conforme_est_refuse_sans_rien_toucher(self):
        for panne in ("dossier_autre_fs", "dossier_lien", "dossier_proprio", "dossier_mode"):
            with self.subTest(panne=panne):
                res, lignes, m = lancer(monde(dossier={}, pannes={panne: True}))
                self.assertEqual(res["verdict"], "REFUS", res)
                self.assertEqual(ecritures(m), [])
                self.assertEqual(m.dossier, {})                              # il existe toujours : l'outil n'y touche pas

    def test_parent_sur_un_autre_volume_est_refuse_avant_la_creation(self):
        res, lignes, m = lancer(monde(pannes={"parent_autre_fs": True}))
        self.assertEqual(res["verdict"], "REFUS", res)
        self.assertIn("repertoire parent", res["raison"])
        self.assertEqual(ecritures(m), [])

    def test_etat_illisible_du_volume_ou_du_dossier_est_une_erreur_sans_ecriture(self):
        for panne in ("racine_illisible", "dossier_illisible"):
            with self.subTest(panne=panne):
                res, lignes, m = lancer(monde(pannes={panne: True}))
                self.assertEqual(res["verdict"], "ERREUR", res)
                self.assertEqual(ecritures(m), [])

    def test_incoherence_entre_ls_et_stat_est_refusee(self):
        class Incoherent(H.NoeudSimule):
            def inspecter(self, chemin):
                if chemin == DOSSIER:
                    return dict(DOSSIER_OK)                                   # stat dit "existe" alors que ls dit "absent"
                return super().inspecter(chemin)
        m = monde()
        res = H.executer_test(Incoherent(m), H.SondeSimulee(m), H.HorlogeSimulee(m), H.valider_parametres(), JETON, lambda s: None)
        self.assertEqual(res["verdict"], "REFUS", res)
        self.assertIn("incoherent", res["raison"])
        self.assertEqual(ecritures(m), [])

    def test_allocation_sans_effet_mesure_s_arrete_des_le_premier_bloc(self):
        for ratio in (0.0, 0.5, 0.89):
            with self.subTest(ratio=ratio):
                res, lignes, m = lancer(monde(helios="absent", pannes={"effet_ratio": ratio}))
                self.assertEqual(res["verdict"], "REFUS", res)
                self.assertIn("sans effet mesure", res["raison"])
                self.assertEqual((len(res["alloues"]), res["total_alloue"]), (1, 512 * MIO))     # UN bloc, jamais 8 ni 16
                self.assertTrue(res["liberation_ok"])
                self.assertEqual((m.fichiers, m.dossier), ({}, None))

    def test_allocation_avec_un_effet_suffisant_est_acceptee(self):
        res, lignes, m = lancer(monde(helios="absent", pannes={"effet_ratio": 0.95}))
        self.assertEqual(res["verdict"], "AUCUNE_DETECTION", res)
        self.assertTrue(81.0 <= res["usage_atteint"] <= 82.0, res["usage_atteint"])
        self.assertLessEqual(res["total_alloue"], 6 * GIO)

    def test_plafond_cumule_de_la_garde_d_allocation(self):
        p = H.valider_parametres()
        fs = fs_usage(70.0)
        self.assertEqual(H.garde_allocation(fs, 512 * MIO, p, deja_alloue=H.MAX_TOTAL - 512 * MIO), (True, "ok"))
        ok, raison = H.garde_allocation(fs, 512 * MIO, p, deja_alloue=H.MAX_TOTAL - 511 * MIO)
        self.assertFalse(ok)
        self.assertIn("plafond cumule", raison)
        for mauvais in (-1, 1.5, "0", None):
            with self.subTest(deja=mauvais):
                self.assertFalse(H.garde_allocation(fs, 512 * MIO, p, deja_alloue=mauvais)[0])

    def test_plafond_cumule_applique_dans_la_boucle_apres_une_liberation_externe(self):
        # un tiers libere 4 Gio entre deux blocs : il faudrait allouer bien plus que le plan initial ; le cumul s'arrete a 6 Gio
        res, lignes, m = lancer(monde(helios="absent", pannes={"liberation_externe": (4, 4 * GIO)}))
        self.assertEqual(res["verdict"], "REFUS", res)
        self.assertIn("plafond cumule", res["raison"])
        self.assertEqual((len(res["alloues"]), res["total_alloue"]), (12, 6 * GIO))
        self.assertEqual((m.fichiers, m.dossier), ({}, None))


# ------------------------------------------------------------------------------------------------ B2 : identite du noeud
class IdentiteDuNoeudTests(CliBase):
    """B2 : le nom d'hote repondu en ssh, le nom du noeud Kubernetes et son InternalIP doivent correspondre a la cible (une IP
    reattribuee inverserait la porte CNPG)."""

    def test_parse_hostname_et_nom_court(self):
        self.assertEqual(H.parse_hostname("k8s-worker1\n"), "k8s-worker1")
        self.assertEqual(H.parse_hostname("K8S-Worker1.lan"), "k8s-worker1.lan")
        self.assertEqual(H.nom_court(" K8s-Worker1.Lan\n"), "k8s-worker1")
        for t in ("", "  ", None, "a b", "a\nb", "-x", "x-", "a;b", "$(id)", "a" * 300):
            with self.subTest(t=t), self.assertRaises(H.ErreurNoeud):
                H.parse_hostname(t)

    def test_les_trois_sources_doivent_concorder(self):
        p = H.valider_parametres()
        cas = (("k8s-worker1", ["192.168.148.158"], "k8s-worker1", "VERT"),
               ("k8s-worker1.lan", ["192.168.148.158"], "k8s-worker1", "VERT"),
               ("k8s-worker1", ["10.0.0.5", "192.168.148.158"], "k8s-worker1", "VERT"),
               ("k8s-worker2", ["192.168.148.158"], "k8s-worker1", "ROUGE"),
               ("k8s-worker1", ["192.168.148.155"], "k8s-worker1", "ROUGE"),
               ("k8s-worker1", ["192.168.148.155", "10.0.0.5"], "k8s-worker1", "ROUGE"),
               ("k8s-worker1", ["192.168.148.158"], "k8s-worker2", "ROUGE"),
               (None, ["192.168.148.158"], "k8s-worker1", "ROUGE"),
               ("k8s-worker1", None, None, "ROUGE"),
               ("k8s-worker1", [], "k8s-worker1", "ROUGE"))
        for hn, ips, nom_k8s, attendu in cas:
            with self.subTest(hostname=hn, ips=ips, nom_k8s=nom_k8s):
                f = faits_noeud("158", hostname=hn)
                f["noeud_k8s"].update(internal_ips=ips, nom_k8s=nom_k8s)
                r = H.evaluer(f, "158", p)
                self.assertEqual(r["verdict"], attendu, r["raisons"])
                if attendu == "VERT":
                    self.assertIn("identite verifiee", textes(r, "INFO"))
                else:
                    self.assertTrue(any(w in textes(r, "ROUGE") for w in ("MAUVAISE MACHINE", "illisible", "s'appelle")))

    def test_preflight_hostname_ssh_discordant_est_rouge_sans_ecriture(self):
        m = monde(dispo=12639 * MIO)
        c, s = self.cli(["--target", "158", "--preflight", "--vm-base", self.tmp], self.deps(m, hostname="k8s-worker2"))
        self.assertEqual(c, 1, s)
        self.assertIn("MAUVAISE MACHINE", s)
        self.assertEqual(ecritures(m), [])

    def test_preflight_internalip_kubernetes_discordante_est_rouge(self):
        m = monde(dispo=12639 * MIO)
        c, s = self.cli(["--target", "158", "--preflight", "--vm-base", self.tmp], self.deps(m, ip_k8s="192.168.148.155"))
        self.assertEqual(c, 1, s)
        self.assertIn("InternalIP", s)

    def test_preflight_hostname_illisible_est_rouge(self):
        m = monde(dispo=12639 * MIO, pannes={"hostname_illisible": True})
        c, s = self.cli(["--target", "158", "--preflight", "--vm-base", self.tmp], self.deps(m))
        self.assertEqual(c, 1, s)
        self.assertIn("nom d'hote repondu en ssh illisible", s)

    def test_preflight_noeud_kubernetes_illisible_est_rouge(self):
        class SansNoeud(ExecEmule):
            def local(self, argv, delai=40, entree=None):
                if argv[:3] == ["kubectl", "get", "node"]:
                    return H.Res(1, "", "Error from server")
                return super().local(argv, delai, entree)
        m = monde(dispo=12639 * MIO)
        d = DepsFaux(SansNoeud(m, primaire_sur="k8s-worker2"), H.HorlogeSimulee(m), 4000, 36.1)
        c, s = self.cli(["--target", "158", "--preflight", "--vm-base", self.tmp], d)
        self.assertEqual(c, 1, s)
        self.assertIn("InternalIP du noeud Kubernetes k8s-worker1 illisible", s)

    def test_go_refuse_sans_aucune_ecriture_si_l_identite_discorde(self):
        for exe in ({"hostname": "k8s-worker2"}, {"ip_k8s": "192.168.148.155"}):
            with self.subTest(exe=exe):
                m = monde(dispo=12639 * MIO, helios="resolve")
                d = self.deps(m, **exe)
                c, s = self.cli(["--target", "158", "--go", "--confirm", "DISK-158-81-82-83", "--vm-base", self.tmp], d)
                self.assertEqual(c, 1, s)
                self.assertIn("REFUS : pre-vol ROUGE", s)
                self.assertEqual(ecritures(m), [])
                self.assertEqual([a for _, a in d.exe.ssh_appels if any(v in a for v in ("fallocate", "mkdir", "rm", "systemd-run"))], [])

    def test_go_reverifie_l_identite_au_lancement_meme_si_le_pre_vol_etait_bon(self):
        class ReattribueEnCours(ExecEmule):
            """Le nom d'hote repondu change APRES le pre-vol (IP reattribuee entre le pre-vol et le lancement)."""
            n_hostname = 0

            def ssh(self, ip, argv, delai=45):
                a = argv[2:] if argv[:2] == ["sudo", "-n"] else argv
                if a[:1] == ["hostname"]:
                    self.n_hostname += 1
                    if self.n_hostname > 1:
                        self.ssh_appels.append((ip, list(argv)))
                        return H.Res(0, "k8s-worker2\n", "")
                return super().ssh(ip, argv, delai)
        m = monde(dispo=12639 * MIO, helios="resolve")
        d = DepsFaux(ReattribueEnCours(m, primaire_sur="k8s-worker2"), H.HorlogeSimulee(m), 4000, 36.1)
        c, s = self.cli(["--target", "158", "--go", "--confirm", "DISK-158-81-82-83", "--vm-base", self.tmp], d)
        self.assertEqual(d.exe.n_hostname, 2)                       # une fois au pre-vol (bon), une fois au lancement (mauvais)
        self.assertEqual(c, 1, s)
        self.assertIn("BILAN : REFUS", s)
        self.assertIn("mauvaise machine", s)
        self.assertEqual(ecritures(m), [])

    def test_executer_test_reverifie_le_nom_d_hote_avant_toute_ecriture(self):
        res, lignes, m = lancer(monde(hostname="k8s-worker2"), identite="k8s-worker1")
        self.assertEqual(res["verdict"], "REFUS", res)
        self.assertIn("mauvaise machine", res["raison"])
        self.assertEqual(ecritures(m), [])
        res, lignes, m = lancer(monde(pannes={"hostname_illisible": True}), identite="k8s-worker1")
        self.assertEqual(res["verdict"], "ERREUR", res)
        self.assertEqual(ecritures(m), [])
        res, lignes, m = lancer(monde(hostname="k8s-worker1.lan", helios="resolve"), identite="k8s-worker1")
        self.assertEqual(res["verdict"], "OK", res)

    def test_noeud_ssh_hostname_et_inspecter_commandes_exactes(self):
        exe = ExecEmule(monde())
        n = H.NoeudSsh(exe, "192.168.148.158")
        self.assertEqual(n.hostname(), "k8s-worker1")
        self.assertEqual(n.inspecter("/")["fstype"], "ext4")
        self.assertIsNone(n.inspecter(DOSSIER))
        argv = [a for _, a in exe.ssh_appels]
        self.assertEqual(argv[0], ["hostname"])
        self.assertIn(["stat", "-c", "%F:%u:%g:%a:%d", "--", "/"], argv)
        self.assertIn(["findmnt", "-n", "-o", "FSTYPE,TARGET", "-T", "/"], argv)
        avant = len(exe.ssh_appels)
        for mauvais in ("/etc", "/var/lib/../etc", DOSSIER + "/x", "*", "", None, "/var/lib/"):
            with self.subTest(chemin=mauvais), self.assertRaises(H.RefusSecurite):
                n.inspecter(mauvais)
        self.assertEqual(len(exe.ssh_appels), avant)                 # chemins refuses AVANT tout appel ssh

    def test_sorties_illisibles_du_noeud_sont_des_erreurs(self):
        class Charabia(object):
            def ssh(self, ip, argv, delai=45):
                return H.Res(0, "n'importe quoi", "")

        class Echec(object):
            def ssh(self, ip, argv, delai=45):
                return H.Res(255, "", "timeout")
        for exe in (Charabia(), Echec()):
            for appel in (lambda x: x.hostname(), lambda x: x.inspecter("/")):
                with self.assertRaises(H.ErreurNoeud):
                    appel(H.NoeudSsh(exe, "x"))

    def test_lire_noeud_k8s_illisible(self):
        class Exe(object):
            def __init__(self, res):
                self.res = res

            def local(self, argv, delai=40, entree=None):
                return self.res
        ok = json.dumps({"metadata": {"name": "k8s-worker1"}, "status": {"conditions": [{"type": "DiskPressure", "status": "False"}],
                                                                       "addresses": [{"type": "InternalIP", "address": "1.2.3.4"}]}})
        self.assertEqual(H.lire_noeud_k8s(Exe(H.Res(0, ok, "")), "k8s-worker1"),
                         {"disk_pressure": False, "internal_ips": ["1.2.3.4"], "nom_k8s": "k8s-worker1"})
        for res in (H.Res(1, "", "err"), H.Res(0, "pas du json", ""), H.Res(0, "{}", ""), H.Res(0, "[]", ""),
                    H.Res(0, json.dumps({"metadata": {}, "status": {}}), "")):
            with self.subTest(res=res):
                self.assertIsNone(H.lire_noeud_k8s(Exe(res), "k8s-worker1"))


# ------------------------------------------------------------------------------------------------ B3 : delai, expiration, attribution
class DelaiEtExpirationTests(SansInfra):
    """B3 : l'expiration se compte depuis l'instant REEL d'armement ; plus aucune allocation quand le delai restant ne suffit pas ;
    l'attribution de la liberation ne credite jamais HELIOS par defaut."""

    def test_l_expiration_doit_couvrir_budget_observation_et_marge(self):
        self.assertEqual(H.budget_allocation(H.valider_parametres(observe_s=600, expiration_s=840)), 120)
        self.assertEqual(H.budget_allocation(H.valider_parametres()), 180)
        for exp in (839, 800, 720, 650):
            with self.subTest(expiration=exp), self.assertRaises(H.RefusSecurite):
                H.valider_parametres(observe_s=600, expiration_s=exp)

    def test_ssh_lent_arrete_les_allocations_bien_avant_l_echeance_du_minuteur(self):
        # 12 s par appel : le minuteur est arme a t=84 (apres 7 appels) ; budget 180 s => limite t=264 ; un bloc = 3 appels = 36 s
        res, lignes, m = lancer(monde(helios="absent", pannes={"latence_ssh_s": 12}))
        self.assertEqual(res["verdict"], "REFUS", res)
        self.assertIn("delai insuffisant", res["raison"])
        self.assertEqual(len(res["alloues"]), 3)         # compte depuis l'ARMEMENT ; compte depuis le lancement il n'y en aurait qu'un
        self.assertNotIn("expiration", [e for _, e in m.journal])
        self.assertTrue(res["liberation_ok"])
        self.assertEqual((m.fichiers, m.dossier), ({}, None))

    def test_ssh_tres_lent_refuse_avant_la_premiere_allocation(self):
        res, lignes, m = lancer(monde(helios="absent", pannes={"latence_ssh_s": 40}))
        self.assertEqual(res["verdict"], "REFUS", res)
        self.assertIn("delai insuffisant", res["raison"])
        self.assertEqual(res["alloues"], [])
        self.assertNotIn("fallocate", m.appels)
        self.assertEqual((m.fichiers, m.dossier), ({}, None))

    def test_ssh_modere_ne_gene_pas_un_test_nominal(self):
        res, lignes, m = lancer(monde(helios="resolve", pannes={"latence_ssh_s": 5}))
        self.assertEqual(res["verdict"], "OK", res)
        self.assertEqual((res["liberee_par"], len(res["alloues"])), ("helios", 8))
        evenements = [e for _, e in m.journal]
        self.assertNotIn("expiration", evenements)
        self.assertNotIn("garde", evenements)

    def test_suivi_borne_par_l_echeance_la_plus_proche_du_minuteur(self):
        m = monde(helios="absent")
        t0 = m.t
        H.suivre(H.NoeudSimule(m), H.SondeSimulee(m), H.HorlogeSimulee(m), H.valider_parametres(), set(H.noms_prevus(JETON)),
                 lambda s: None, JETON, echeance_min=t0 + H.MARGE_FIN_S + 100)
        self.assertTrue(95 <= m.t - t0 <= 105, m.t - t0)          # 100 s au lieu de 720 s : jamais au-dela de (echeance - marge de fin)

    def test_fenetre_d_observation_insuffisante_est_refusee(self):
        m = monde(helios="absent")
        with self.assertRaises(H.RefusSecurite):
            H.suivre(H.NoeudSimule(m), H.SondeSimulee(m), H.HorlogeSimulee(m), H.valider_parametres(), set(), lambda s: None, JETON,
                     echeance_min=m.t + H.MARGE_FIN_S + 59)
        self.assertEqual(m.t, 0.0)                                  # refus immediat : l'horloge n'a pas avance

    def test_attribution_de_la_liberation_ne_credite_jamais_helios_par_defaut(self):
        m = monde()
        ops = H.NoeudSimule(m)
        noms = H.noms_prevus(JETON)
        ops.armer_expiration(JETON, noms, 1020)
        ops.armer_garde(JETON, noms, 8350, 1020)
        t = m.t

        def attribuer(maintenant, echeance):
            return H.attribuer_liberation(ops, JETON, maintenant, echeance)
        self.assertEqual(attribuer(t + 10, t + 1020), "helios")
        self.assertEqual(attribuer(t + 1020, t + 1020), "expiration")          # echeance atteinte : le minuteur ne peut plus etre exclu
        self.assertEqual(attribuer(t + 10, None), "indeterminee")               # echeance inconnue
        ops.desarmer_expiration(JETON)
        self.assertEqual(attribuer(t + 10, t + 1020), "expiration")             # le minuteur n'est plus actif : il a tire
        ops.desarmer_garde(JETON)
        self.assertEqual(attribuer(t + 10, t + 1020), "garde")                  # la garde n'est plus active : elle a tire (prioritaire)

        class Illisible(H.NoeudSimule):
            def garde_active(self, jeton):
                raise H.ErreurNoeud("ssh en echec")
        self.assertEqual(H.attribuer_liberation(Illisible(m), JETON, t + 10, t + 1020), "indeterminee")

    def test_un_minuteur_qui_tire_pendant_l_observation_donne_une_erreur_et_jamais_helios(self):
        # horloge du noeud en avance : le minuteur tire a t_arm + 120 s alors que l'outil observe encore
        res, lignes, m = lancer(monde(helios="absent", pannes={"minuteur_avance_s": 900}))
        self.assertEqual(res["liberee_par"], "expiration")
        self.assertEqual(res["verdict"], "ERREUR", res)
        self.assertIn("minuteur", res["raison"])
        self.assertIn("expiration", [e for _, e in m.journal])

    def test_horloge_reelle_est_monotone(self):
        with mock.patch.object(time, "monotonic", return_value=123.5), mock.patch.object(time, "time", _interdit):
            self.assertEqual(H.HorlogeReelle().now(), 123.5)


# ------------------------------------------------------------------------------------------------ M2 : acquittements ORANGE
class AcquittementsOrangeTests(CliBase):
    """M2 : un ORANGE non acquitte (code par code) refuse --go ; le risque CNPG n'a que son propre drapeau."""

    def orange(self, code):
        """(faits, cle, ack_cnpg) qui produisent exactement l'ORANGE `code`."""
        if code == "PRIMAIRE_CNPG_ACCEPTE":
            return faits("155", drapeau="true"), "155", True
        if code == "REMEDIATION_NON_ACTIVEE":
            return faits("158", drapeau="absent"), "158", False
        f = faits("158", drapeau="true")
        if code == "GEL_HOTE_48H":
            f["hote"]["gels"] = [(18000, 120, "vmA")]
        elif code == "NOTREADY_2H":
            f["incidents"]["notready_ages_s"] = [3600]
        elif code == "VMDK_INATTENDU":
            f["hote"]["vmdk"].update(croissant=False, prealloue=False, type="inconnu")
        return f, "158", False

    def go(self, m, extra=(), **deps):
        d = self.deps(m, **deps)
        c, s = self.cli(["--target", "158", "--go", "--confirm", "DISK-158-81-82-83", "--vm-base", self.tmp] + list(extra), d)
        return c, s, d

    def test_chaque_orange_a_un_code_connu_et_un_seul(self):
        p = H.valider_parametres()
        for code in H.CODES_ORANGE:
            if code.startswith("E2E_"):                      # ORANGE de la PORTE du scenario (pas du pre-vol de base) : test_helios_disk_e2e.py
                continue
            with self.subTest(code=code):
                f, cle, ack = self.orange(code)
                r = H.evaluer(f, cle, p, ack)
                self.assertEqual([x[3] for x in r["raisons"] if x[0] == "ORANGE"], [code], r["raisons"])
                self.assertEqual(r["verdict"], "ORANGE")

    def test_orange_non_acquittes_est_pur_et_code_par_code(self):
        p = H.valider_parametres()
        f, cle, ack = self.orange("REMEDIATION_NON_ACTIVEE")
        r = H.evaluer(f, cle, p)
        self.assertEqual([x[3] for x in H.orange_non_acquittes(r, set())], ["REMEDIATION_NON_ACTIVEE"])
        self.assertEqual(H.orange_non_acquittes(r, {"REMEDIATION_NON_ACTIVEE"}), [])
        self.assertEqual(len(H.orange_non_acquittes(r, {"GEL_HOTE_48H"})), 1)         # un autre code n'acquitte pas celui-ci
        self.assertEqual(len(H.orange_non_acquittes(r, None)), 1)
        f, cle, ack = self.orange("PRIMAIRE_CNPG_ACCEPTE")
        self.assertEqual(H.orange_non_acquittes(H.evaluer(f, cle, p, ack), set()), [])   # acquitte par son propre drapeau

    def test_analyser_acquittements(self):
        self.assertEqual(H.analyser_acquittements(None), (set(), None))
        self.assertEqual(H.analyser_acquittements(["GEL_HOTE_48H,NOTREADY_2H", "VMDK_INATTENDU"]),
                         ({"GEL_HOTE_48H", "NOTREADY_2H", "VMDK_INATTENDU"}, None))
        self.assertEqual(H.analyser_acquittements([" GEL_HOTE_48H , ,"]), ({"GEL_HOTE_48H"}, None))
        for mauvais in (["gel_hote_48h"], ["INCONNU"], ["PRIMAIRE_CNPG_ACCEPTE"], ["GEL_HOTE_48H,PAS_UN_CODE"], ["*"], ["ALL"]):
            with self.subTest(valeurs=mauvais):
                codes, erreur = H.analyser_acquittements(mauvais)
                self.assertEqual(codes, set())
                self.assertIn("inconnu ou non acquittable", erreur)

    def test_go_refuse_un_orange_non_acquitte_sans_aucune_ecriture(self):
        fabriquer_vm(self.tmp, "k8s-worker1", journal=FAUX_GEL)         # gel de l'hote il y a 5 h => ORANGE GEL_HOTE_48H
        m = monde(dispo=12639 * MIO, helios="detect")
        c, s, d = self.go(m)
        self.assertEqual(c, 1, s)
        self.assertIn("REFUS : ORANGE non acquitte", s)
        self.assertIn("--accepte-orange GEL_HOTE_48H", s)
        self.assertEqual(ecritures(m), [])
        self.assertEqual([a for _, a in d.exe.ssh_appels if any(v in a for v in ("fallocate", "mkdir", "rm", "systemd-run"))], [])
        self.assertIsNone(m.incident)

    def test_go_accepte_l_orange_acquitte_code_par_code(self):
        fabriquer_vm(self.tmp, "k8s-worker1", journal=FAUX_GEL)
        m = monde(dispo=12639 * MIO, helios="detect")
        c, s, d = self.go(m, ["--accepte-orange", "GEL_HOTE_48H"])
        self.assertEqual(c, 3, s)
        self.assertIn("acquitte par --accepte-orange GEL_HOTE_48H", s)
        self.assertIn("DETECTION_SEULE", s)

    def test_acquittement_partiel_refuse_l_orange_restant(self):
        fabriquer_vm(self.tmp, "k8s-worker1", journal=FAUX_GEL)         # ORANGE GEL_HOTE_48H
        ancien = {"configmap": e2e_pret()["configmap"], "ouverts": [{"id": 7, "app": "k8s-worker2", "statut": "OPEN"}]}   # + E2E_INCIDENTS_DISQUE_ANCIENS
        m = monde(dispo=12639 * MIO, helios="detect")
        c, s, d = self.go(m, ["--accepte-orange", "GEL_HOTE_48H"], e2e=e2e_pret(**ancien))
        self.assertEqual(c, 1, s)
        self.assertIn("E2E_INCIDENTS_DISQUE_ANCIENS", s.split("REFUS : ORANGE non acquitte")[1])
        self.assertNotIn("GEL_HOTE_48H", s.split("REFUS : ORANGE non acquitte")[1])
        self.assertEqual(ecritures(m), [])
        m2 = monde(dispo=12639 * MIO, helios="detect")
        c2, s2, d2 = self.go(m2, ["--accepte-orange", "GEL_HOTE_48H,E2E_INCIDENTS_DISQUE_ANCIENS"], e2e=e2e_pret(**ancien))
        self.assertEqual(c2, 3, s2)

    def test_l_acquittement_ne_couvre_jamais_un_rouge(self):
        fabriquer_vm(self.tmp, "k8s-worker1", journal=FAUX_GEL)
        m = monde(dispo=12639 * MIO, helios="detect")
        c, s, d = self.go(m, ["--accepte-orange", "GEL_HOTE_48H"], ram=500)
        self.assertEqual(c, 1, s)
        self.assertIn("REFUS : pre-vol ROUGE", s)
        self.assertEqual(ecritures(m), [])

    def test_le_risque_cnpg_ne_s_acquitte_que_par_son_propre_drapeau(self):
        m = monde(helios="detect")
        d = self.deps(m, primaire_sur="k8s-worker2", flag="true")
        c, s = self.cli(["--target", "155", "--go", "--confirm", "DISK-155-81-82-83", "--accepte-orange", "REMEDIATION_NON_ACTIVEE",
                         "--vm-base", self.tmp], d)
        self.assertEqual(c, 1, s)                                       # .155 reste ROUGE : aucun --accepte-orange ne vaut acceptation du risque
        self.assertIn("NON accepte", s)
        self.assertEqual(ecritures(m), [])
        c, s = self.cli(["--target", "155", "--go", "--confirm", "DISK-155-81-82-83", "--accepte-orange", "PRIMAIRE_CNPG_ACCEPTE"], PiegeDeps())
        self.assertEqual(c, 2, s)                                       # et ce code n'est meme pas acquittable par --accepte-orange

    def test_l_aide_en_ligne_liste_chaque_code_acquittable_avec_sa_description(self):
        aide = " ".join(H.construire_parseur().format_help().split())
        for code in H.CODES_ORANGE_ACQUITTABLES:
            self.assertIn("%s = %s" % (code, H.CODES_ORANGE[code]), aide)
        self.assertNotIn(H.CODE_CNPG_ACCEPTE, aide)                         # le risque CNPG n'a que son propre drapeau
        aide.encode("ascii")

    def test_code_orange_inconnu_est_refuse_avant_toute_collecte(self):
        c, s = self.cli(["--target", "158", "--go", "--confirm", "DISK-158-81-82-83", "--accepte-orange", "BIDON"], PiegeDeps())
        self.assertEqual(c, 2, s)
        self.assertIn("inconnu ou non acquittable", s)

    def test_les_options_ne_peuvent_pas_etre_abregees(self):
        for argv in (["--target", "155", "--accepte"], ["--target", "155", "--accepte-risque"], ["--target", "158", "--conf", "x"],
                     ["--target", "158", "--accepte-or", "GEL_HOTE_48H"], ["--tar", "158"], ["--target", "158", "--pre"]):
            with self.subTest(argv=argv), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as cm:
                H.principal(argv, PiegeDeps(), lambda s: None)
            self.assertEqual(cm.exception.code, 2)

    def test_un_drapeau_de_remediation_illisible_est_rouge_alors_que_false_et_absent_sont_orange(self):
        p = H.valider_parametres()
        for dr in (None, "peut-etre", ""):
            with self.subTest(drapeau=dr):
                r = H.evaluer(faits("158", drapeau=dr), "158", p)
                self.assertEqual(r["verdict"], "ROUGE")
                self.assertIn("illisible", textes(r, "ROUGE"))
        for dr in ("false", "absent"):
            self.assertEqual(H.evaluer(faits("158", drapeau=dr), "158", p)["verdict"], "ORANGE")

    def test_la_simulation_et_le_pre_vol_affichent_les_acquittements_a_donner(self):
        c, s = self.cli(["--target", "155", "--accepte-risque-primaire-cnpg"], PiegeDeps())       # .155 : detection seule, inchange
        self.assertIn("--accepte-orange REMEDIATION_NON_ACTIVEE", s)
        self.assertIn("NOTE : en lancement reel, --go REFUSERAIT", s)
        c, s = self.cli(["--target", "158"], PiegeDeps())                                         # .158 : jamais REMEDIATION_NON_ACTIVEE
        self.assertNotIn("REMEDIATION_NON_ACTIVEE", s)
        self.assertIn("PORTE est obligatoire", s)
        self.assertIn("--go --confirm DISK-158-81-82-83 --image-backend <TAG>", s)
        fabriquer_vm(self.tmp, "k8s-worker1", journal=FAUX_GEL)                                   # ORANGE reel : GEL_HOTE_48H
        m = monde(dispo=12639 * MIO)
        c, s = self.cli(["--target", "158", "--preflight", "--vm-base", self.tmp], self.deps(m))
        self.assertIn("acquittement exige pour --go : --accepte-orange GEL_HOTE_48H", s)
        self.assertIn("--go --confirm DISK-158-81-82-83 --image-backend %s --accepte-orange GEL_HOTE_48H" % E2E_IMAGE, s)


# ------------------------------------------------------------------------------------------------ M3 : sondes illisibles
class SondesIllisiblesTests(CliBase):
    """M3 : une sonde (Prometheus, base de donnees) illisible est une ERREUR : jamais "aucun incident", jamais "tout va bien"."""

    def test_lire_prom_distingue_vide_et_illisible(self):
        class Exe(object):
            def __init__(self, reponses):
                self.r = list(reponses)

            def http_json(self, url, delai=25):
                return self.r.pop(0)

        def rep(v):
            return {"status": "success", "data": {"result": [{"value": [0, v]}]}}
        vide = {"status": "success", "data": {"result": []}}
        ok = H.lire_prom(Exe([rep("1"), rep("70.4"), rep("0")]), "192.168.148.158", "k8s-worker1")
        self.assertEqual(ok, {"series": 1, "usage": 70.4, "incident_actif": 0, "illisibles": []})
        self.assertEqual(H.lire_prom(Exe([vide, rep("70.4"), rep("0")]), "x", "n")["series"], 0)      # valide mais sans serie : 0, pas illisible
        mauvaises = [None, {"status": "error"}, {"status": "success"}, "pas un dict", [],
                     {"status": "error", "data": {"result": [{"value": [0, "1"]}]}},               # erreur annoncee MAIS des donnees presentes
                     {"status": "error", "data": {"result": []}},                                   # erreur annoncee et liste vide : pas "0 serie"
                     {"status": "success", "data": {"result": [{"value": [0, "abc"]}]}}, {"status": "success", "data": {"result": "x"}}]
        for mauvais in mauvaises:
            for rang, cle in enumerate(("series", "usage", "incident_actif")):
                reps = [rep("1"), rep("70.4"), rep("0")]
                reps[rang] = mauvais
                with self.subTest(requete=cle, reponse=mauvais):
                    r = H.lire_prom(Exe(reps), "x", "n")
                    self.assertEqual(r["illisibles"], [cle])
                    self.assertIsNone(r[cle])
        deux = {"status": "success", "data": {"result": [{"value": [0, "1"]}, {"value": [0, "2"]}]}}      # ambigu : valide mais inutilisable
        r = H.lire_prom(Exe([rep("1"), deux, rep("0")]), "x", "n")
        self.assertEqual((r["usage"], r["illisibles"]), (None, []))
        f = faits("158", drapeau="true")
        f["prom"] = r
        self.assertEqual(H.evaluer(f, "158", H.valider_parametres())["verdict"], "ROUGE")

    def test_chaque_requete_prometheus_illisible_rend_le_pre_vol_rouge(self):
        for kind in ("series", "usage", "incident"):
            for mode in ("none", "statut", "json", "valeur"):
                with self.subTest(requete=kind, mode=mode):
                    m = monde(dispo=12639 * MIO)
                    c, s = self.cli(["--target", "158", "--preflight", "--vm-base", self.tmp],
                                    self.deps(m, prom_pannes={kind: mode}))
                    self.assertEqual(c, 1, s)
                    self.assertIn("VERDICT : ROUGE", s)
                    self.assertIn("Prometheus illisible", s)
                    self.assertEqual(ecritures(m), [])

    def test_sonde_illisible_pendant_le_suivi_est_une_erreur_et_non_une_absence_de_detection(self):
        res, lignes, m = lancer(monde(helios="absent", pannes={"sonde_illisible": True}))
        self.assertEqual(res["verdict"], "ERREUR", res)
        self.assertNotEqual(res["verdict"], "AUCUNE_DETECTION")
        self.assertIn("illisible", res["raison"])
        self.assertTrue(res["liberation_ok"])
        self.assertEqual((m.fichiers, m.dossier), ({}, None))
        self.assertLessEqual(m.t, 20)                       # des la 1re lecture (15 s), pas apres les 12 minutes d'observation

    def test_sonde_illisible_a_partir_de_la_troisieme_lecture(self):
        res, lignes, m = lancer(monde(helios="absent", pannes={"sonde_illisible": 3}))
        self.assertEqual(res["verdict"], "ERREUR", res)
        self.assertTrue(44 <= m.t <= 50, m.t)               # 3e lecture = t=45
        self.assertEqual((m.fichiers, m.dossier), ({}, None))

    def test_sonde_reelle_distingue_aucune_ligne_et_echec(self):
        m = monde()
        exe = ExecEmule(m)
        s = H.SondeReelle(exe, "k8s-worker1", "postgres-8", 123)
        self.assertIsNone(s.lire())                                                  # aucune ligne : pas d'incident
        m.incident = {"id": 90001, "statut": "OPEN", "cree": 0}
        self.assertEqual(s.lire(), {"id": 90001, "statut": "OPEN", "action": None})
        exe.sonde_pannes = [True, False]                                             # un echec puis un succes : 2e essai
        self.assertEqual(s.lire()["id"], 90001)
        for pannes in ([True, True], "toujours"):
            exe.sonde_pannes = pannes
            with self.assertRaises(H.SondeIllisible):
                s.lire()

        class Charabia(object):
            def local(self, argv, delai=40, entree=None):
                return H.Res(0, "abc|def\n", "")
        with self.assertRaises(H.SondeIllisible):
            H.SondeReelle(Charabia(), "k8s-worker1", "postgres-8", 123).lire()

    def test_go_complet_avec_sonde_en_panne_donne_erreur_et_libere(self):
        m = monde(dispo=12639 * MIO, helios="detect")
        d = self.deps(m, sonde_pannes="toujours")
        c, s = self.cli(["--target", "158", "--go", "--confirm", "DISK-158-81-82-83", "--vm-base", self.tmp], d)
        self.assertEqual(c, 1, s)
        self.assertIn("BILAN : ERREUR", s)
        self.assertEqual((m.fichiers, m.dossier), ({}, None))


# ------------------------------------------------------------------------------------------------ M4 : surveillance et garde cote noeud
class GardeCoteNoeudTests(CliBase):
    """M4 : surveillance toutes les 5 s, remesure apres chaque appel lent, et GARDE cote noeud (awk transitoire) qui libere des
    urgence + 0,5 point, donc avant le seuil du kubelet, meme si l'outil est mort ou ralenti."""

    def noms(self):
        return H.noms_prevus(JETON)

    def chemins(self):
        return [DOSSIER + "/" + n for n in H.noms_prevus(JETON)]

    def test_programme_de_garde_respecte_la_grammaire_independante(self):
        prog = H.programme_garde(self.chemins(), 8350, 510)
        g = ExecEmule.RE_GARDE.fullmatch(prog)
        self.assertIsNotNone(g)
        self.assertEqual((int(g.group("seuil")), int(g.group("boucles"))), (8350, 510))
        self.assertEqual(g.group("chemins").split(" "), self.chemins())
        prog.encode("ascii")

    def test_programme_de_garde_sans_syntaxe_dangereuse(self):
        prog = H.programme_garde(self.chemins(), 8350, 510)
        for car in "$%`\\~":
            self.assertNotIn(car, prog)
        for mot in ("sh -c", "bash", "-rf", " -r ", "find ", "..", "reboot", "shutdown", "dd ", "truncate"):
            self.assertNotIn(mot, prog)

    def test_programme_de_garde_refuse_les_entrees_invalides(self):
        ch = self.chemins()
        for args in ((ch, 7999, 510), (ch, 8801, 510), (ch, "8350", 510), (ch, True, 510), (ch, 8350, 0), (ch, 8350, 2001), (ch, 8350, "5"),
                     ([], 8350, 510), (ch + [ch[0]], 8350, 510), (["/etc/passwd"], 8350, 510), ([DOSSIER + "/*"], 8350, 510),
                     ([DOSSIER + "/../x"], 8350, 510), ([ch[0] + "\n"], 8350, 510), ([None], 8350, 510),
                     (["/var/lib/helios-diskfill-aaaa.bin"], 8350, 510)):
            with self.subTest(args=args), self.assertRaises(H.RefusSecurite):
                H.programme_garde(*args)

    def test_seuil_de_la_garde_entre_l_urgence_et_le_kubelet(self):
        self.assertEqual(H.seuil_garde_x100(H.valider_parametres()), 8350)
        self.assertEqual(H.seuil_garde_x100(H.valider_parametres(urgence=82.5)), 8300)
        for cible, plafond, urgence in ((81.0, 82.0, 83.0), (80.5, 81.5, 82.5), (80.3, 80.9, 81.2), (81.0, 81.5, 82.0)):
            q = H.valider_parametres(cible=cible, plafond=plafond, urgence=urgence)
            g = H.seuil_garde_x100(q) / 100.0
            self.assertTrue(q.urgence < g <= 85.0 - 1.5 + 1e-9, (q, g))          # au moins 1,5 point sous les 85 % du kubelet

    def test_armer_garde_commande_exacte_et_verifiee(self):
        exe = ExecEmule(monde())
        n = H.NoeudSsh(exe, "192.168.148.158")
        n.armer_garde(JETON, self.noms(), 8350, 1020)
        argv = exe.ssh_appels[0][1]
        self.assertEqual(argv[:9], ["sudo", "-n", "systemd-run", "--quiet", "--collect", "--unit=helios-diskfill-garde-" + JETON,
                                    "--property=RuntimeMaxSec=1080", "--description=HELIOS test disque : garde-fou avant le seuil du kubelet",
                                    "/usr/bin/awk"])
        self.assertEqual(len(argv), 10)
        garde = exe.gardes_armees[0]
        self.assertEqual((garde["seuil"], garde["boucles"], garde["duree_max"], garde["chemins"]), (8350, 510, 1080, self.chemins()))
        self.assertTrue(n.garde_active(JETON))
        n.desarmer_garde(JETON)
        self.assertFalse(n.garde_active(JETON))
        appels = [a for _, a in exe.ssh_appels]
        self.assertIn(["systemctl", "is-active", "helios-diskfill-garde-%s.service" % JETON], appels)
        self.assertIn(["sudo", "-n", "systemctl", "stop", "helios-diskfill-garde-%s.service" % JETON], appels)
        self.assertEqual((exe.violations, exe.inconnues), ([], []))

    def test_armer_garde_parametres_invalides_refuses_avant_tout_appel_ssh(self):
        exe = ExecEmule(monde())
        n = H.NoeudSsh(exe, "192.168.148.158")
        for jeton, noms, seuil, delai in (("zz", self.noms(), 8350, 1020), (JETON + "\n", self.noms(), 8350, 1020), (JETON, [], 8350, 1020),
                                         (JETON, self.noms(), 7000, 1020), (JETON, self.noms(), 8350, 59), (JETON, self.noms(), 8350, 3601),
                                         (JETON, self.noms(), 8350, "1020"), (JETON, self.noms(), 8350, True), (JETON, ["../x"], 8350, 1020)):
            with self.subTest(jeton=jeton, seuil=seuil, delai=delai), self.assertRaises(H.RefusSecurite):
                n.armer_garde(jeton, noms, seuil, delai)
        self.assertEqual(exe.ssh_appels, [])

    def test_le_programme_de_garde_survit_au_quoting_ssh(self):
        exe = ExecEmule(monde())
        H.NoeudSsh(exe, "192.168.148.158").armer_garde(JETON, self.noms(), 8350, 1020)
        argv = exe.ssh_appels[0][1]
        self.assertEqual(shlex.split(" ".join(shlex.quote(a) for a in argv)), argv)    # le shell distant reconstruit les memes arguments

    def prepare(self):
        """Noeud simule ou l'outil a arme ses deux filets et alloue jusqu'a ~81 %, puis "meurt" : plus personne ne mesure."""
        m = monde(helios="absent")
        ops = H.NoeudSimule(m)
        ops.creer_dossier()
        ops.armer_expiration(JETON, self.noms(), 1020)
        ops.armer_garde(JETON, self.noms(), 8350, 1020)
        for i in range(1, 9):
            ops.allouer(H.nom_fichier(JETON, i), 512 * MIO if i < 8 else 433 * MIO)
        return m

    def test_la_garde_libere_quand_l_outil_est_mort_avant_le_seuil_du_kubelet(self):
        m = self.prepare()
        self.assertTrue(81.0 <= m.fs().usage <= 81.02)
        m.croissance = 20 * MIO                              # un tiers ecrit 20 Mo/s ; l'outil est mort
        m.avancer(60)
        evenements = [e for _, e in m.journal]
        self.assertIn("garde", evenements)
        self.assertNotIn("expiration", evenements)
        self.assertEqual([n for n in m.fichiers if H.RE_FICHIER.match(n)], [])
        self.assertTrue(83.5 <= m.usage_vrai_max <= 83.7, m.usage_vrai_max)      # coupee au 1er controle apres 83,5 % : jamais 85 %

    def test_la_garde_ne_tire_pas_sous_son_seuil(self):
        m = self.prepare()
        m.avancer(900)
        self.assertNotIn("garde", [e for _, e in m.journal])
        self.assertEqual(len(m.fichiers), 8)

    def test_la_garde_et_le_minuteur_ont_une_duree_de_vie_bornee(self):
        m = self.prepare()
        m.avancer(1020 + 61)
        self.assertEqual(len(m.fichiers), 0)                  # le minuteur a tire a 1020 s
        self.assertFalse(m.minuteurs[JETON][2])
        self.assertFalse(m.gardes[JETON][2])                  # la garde s'est arretee d'elle-meme (RuntimeMaxSec = expiration + 60)

    def test_l_outil_vivant_reagit_en_moins_de_5_secondes_apres_le_franchissement_de_83_pct(self):
        res, lignes, m = lancer(monde(helios="absent", croissance_o_s=20 * MIO))
        self.assertEqual(res["verdict"], "URGENCE", res)
        t_rm = [t for t, e in m.journal if e == "rm"][0]
        self.assertLessEqual(t_rm - m.t_urgence, H.PAS_MESURE_S, (t_rm, m.t_urgence))
        self.assertNotIn("garde", [e for _, e in m.journal])            # la garde (83,5 %) n'a pas eu a agir : l'outil etait plus rapide
        self.assertLess(m.usage_vrai_max, 83.5)

    def test_urgence_detectee_aussitot_apres_une_sonde_lente(self):
        # la sonde dure 12 s ; un tiers ecrit 31 Mo/s : le 83 % est franchi PENDANT la sonde (t=25) ; l'outil remesure juste apres (t=27),
        # avant que la garde (83,5 % vers t=31, controle a t=32) n'ait a agir
        res, lignes, m = lancer(monde(helios="absent", croissance_o_s=31 * MIO, pannes={"latence_sonde_s": 12}))
        self.assertEqual(res["verdict"], "URGENCE", res)
        self.assertNotIn("garde", [e for _, e in m.journal])
        t_rm = [t for t, e in m.journal if e == "rm"][0]
        self.assertLessEqual(t_rm - m.t_urgence, 12)

    def test_la_garde_prend_le_relais_quand_l_outil_est_ralenti(self):
        class Ralenti(H.HorlogeSimulee):
            """Apres l'allocation (1er sommeil du suivi) : ssh devient tres lent (30 s par appel) et un tiers ecrit 30 Mo/s un moment."""

            def __init__(self, monde):
                super().__init__(monde)
                self.n = 0

            def dormir(self, s):
                self.n += 1
                if self.n == 1:
                    self.m.pannes["latence_ssh_s"] = 30
                    self.m.croissance = 30 * MIO
                elif self.n == 2:
                    self.m.croissance = 0.0                           # la rafale d'ecriture est finie
                super().dormir(s)
        m = monde(helios="absent")
        lignes = []
        res = H.executer_test(H.NoeudSimule(m), H.SondeSimulee(m), Ralenti(m), H.valider_parametres(), JETON, lignes.append)
        self.assertEqual(res["verdict"], "URGENCE", res)
        self.assertEqual(res["liberee_par"], "garde")
        evenements = [e for _, e in m.journal]
        self.assertIn("garde", evenements)
        self.assertNotIn("expiration", evenements)
        self.assertLess(m.usage_vrai_max, 84.0)                       # jamais le 85 % du kubelet
        self.assertEqual((m.fichiers, m.dossier), ({}, None))
        self.assertTrue(any("GARDE COTE NOEUD DECLENCHEE" in l for l in lignes))

    def test_garde_non_armable_aucune_allocation_et_le_minuteur_est_desarme(self):
        res, lignes, m = lancer(monde(pannes={"armer_garde": True}))
        self.assertEqual(res["verdict"], "ERREUR", res)
        self.assertNotIn("fallocate", m.appels)
        self.assertEqual((m.fichiers, m.dossier), ({}, None))
        self.assertFalse(m.minuteurs[JETON][2])                       # le minuteur arme juste avant est desarme

    def test_garde_inactive_aucune_allocation(self):
        res, lignes, m = lancer(monde(pannes={"garde_inactive": True}))
        self.assertEqual(res["verdict"], "REFUS", res)
        self.assertIn("garde cote noeud n'est pas active", res["raison"])
        self.assertNotIn("fallocate", m.appels)
        self.assertIn("systemctl-stop", m.appels)
        self.assertIn("systemctl-stop-garde", m.appels)
        self.assertEqual((m.fichiers, m.dossier), ({}, None))

    def test_la_garde_reste_armee_tant_que_la_liberation_n_est_pas_verifiee(self):
        res, lignes, m = lancer(monde(helios="absent", pannes={"liberer": 99}))
        self.assertFalse(res["liberation_ok"])
        self.assertTrue(m.gardes[JETON][2] and m.minuteurs[JETON][2])
        self.assertNotIn("systemctl-stop-garde", m.appels)
        self.assertTrue(any("RESTENT ARMES" in l for l in lignes))
        m.croissance = 20 * MIO                                       # et c'est la garde qui protege : un tiers ecrit, le seuil est franchi
        m.avancer(120)
        self.assertIn("garde", [e for _, e in m.journal])

    def test_surveillance_toutes_les_5_secondes_et_remesure_apres_chaque_sonde_lente(self):
        res, lignes, m = lancer(monde(helios="absent", pannes={"latence_sonde_s": 12}), H.valider_parametres(observe_s=60))
        ecarts = [round(b - a, 6) for a, b in zip(m.instants_stats, m.instants_stats[1:]) if b - a > 0]
        self.assertEqual(min(ecarts), H.PAS_MESURE_S)
        self.assertEqual(max(ecarts), 12)             # jamais un trou plus long que la sonde elle-meme : remesure juste apres
        self.assertEqual(H.PAS_SONDE_S % H.PAS_MESURE_S, 0)


# ------------------------------------------------------------------------------------------------ absence d'effets de bord (statique + execution)
class SansEffetsDeBordTests(SansInfra):
    @classmethod
    def setUpClass(cls):
        with open(SRC, encoding="utf-8") as f:
            cls.source = f.read()
        cls.arbre = ast.parse(cls.source)

    def commandes(self, racine=None):
        """Litteraux liste dont le premier element est une constante texte d'allure de VERBE (un seul mot : `stat`, `/usr/bin/rm`...)
        = une commande (verbe + arguments). Les listes de messages (phrases) ne sont pas des commandes."""
        for n in ast.walk(racine or self.arbre):
            if (isinstance(n, ast.List) and n.elts and isinstance(n.elts[0], ast.Constant) and isinstance(n.elts[0].value, str)
                    and re.fullmatch(r"[A-Za-z0-9_./-]+", n.elts[0].value)):
                yield [e.value for e in n.elts if isinstance(e, ast.Constant) and isinstance(e.value, str)]

    def classe(self, nom):
        return [n for n in ast.walk(self.arbre) if isinstance(n, ast.ClassDef) and n.name == nom][0]

    def test_source_ascii_pur(self):
        self.source.encode("ascii")

    def test_source_sans_commande_destructrice(self):
        interdits = [r"rm\s+-[a-zA-Z]*r", r"\bfind\s", r"-delete\b", r"\bmkfs", r"\bdd\s+(if|of)=", r"\btruncate\b", r"/tmp\b", r"/var/log",
                     r"crictl", r"\brmi\b", r"docker", r"containerd", r"vmrun", r"poweroff", r"shutdown", r"reboot", r"\bhalt\b",
                     r"os\.(remove|unlink|rmdir|rename|makedirs|mkdir)", r"rmtree", r"\.write\(", r"open\([^)]*[\"'][wax+]"]
        for motif in interdits:
            with self.subTest(motif=motif):
                self.assertIsNone(re.search(motif, self.source), motif)

    def test_aucune_commande_hors_du_jeu_ferme(self):
        """Toute commande litterale du source (kubectl, systemctl, ssh du noeud...) appartient a un jeu ferme de verbes."""
        autorises = {"kubectl", "stat", "findmnt", "hostname", "test", "ls", "cat", "mkdir", "rmdir", "fallocate", "rm", "systemd-run",
                     "systemctl", "sudo", "ssh", "psql"}
        vus = set()
        for mots in self.commandes():
            vus.add(mots[0])
            self.assertIn(mots[0], autorises | set(H.SSH[:1]), mots)
        for verbe in ("kubectl", "stat", "findmnt", "hostname", "test", "ls", "cat", "mkdir", "rmdir", "fallocate", "rm", "systemd-run",
                      "systemctl"):
            self.assertIn(verbe, vus, verbe)

    def test_systemctl_se_limite_a_is_active_et_stop(self):
        verbes = {mots[1] for mots in self.commandes() if mots[0] == "systemctl"}
        self.assertEqual(verbes, {"is-active", "stop"})                              # jamais start/restart/enable/mask/kill

    def test_awk_n_est_utilise_que_par_la_garde_et_aucun_shell_n_est_lance(self):
        constantes = [n for n in ast.walk(self.arbre) if isinstance(n, ast.Constant) and n.value == "/usr/bin/awk"]
        self.assertEqual(len(constantes), 1)
        dedans = [f for f in ast.walk(self.arbre) if isinstance(f, ast.FunctionDef) and any(c is constantes[0] for c in ast.walk(f))]
        self.assertEqual([f.name for f in dedans], ["armer_garde"])                  # uniquement NoeudSsh.armer_garde
        for mots in self.commandes():
            self.assertNotIn(mots[0], ("sh", "bash", "dash", "zsh", "/bin/sh", "/bin/bash", "/usr/bin/env"), mots)
            if "-c" in mots:                                                         # jamais "sh -c" : seulement le format de stat ...
                self.assertIn(mots[0], ("stat", "kubectl"), mots)
                if mots[0] == "kubectl":                                             # ... ou le conteneur de kubectl exec
                    self.assertEqual(mots[mots.index("-c") + 1], "postgres", mots)
        self.assertNotIn("shell=True", self.source)

    def test_le_noeud_ssh_n_emet_que_les_verbes_du_jeu_ferme(self):
        verbes = {mots[0] for mots in self.commandes(self.classe("NoeudSsh"))} - {"sudo"}          # sudo -n : prefixe ajoute par _r(sudo=True)
        self.assertEqual(verbes, {"stat", "findmnt", "hostname", "test", "ls", "cat", "mkdir", "rmdir", "fallocate", "rm", "systemd-run",
                                  "systemctl"})
        self.assertEqual(verbes, ExecEmule.VERBES)
        for mots in self.commandes(self.classe("NoeudSsh")):
            if mots[0] == "rm":
                self.assertEqual(mots[:3], ["rm", "-f", "--"], mots)               # jamais -r, jamais de joker : noms exacts apres "--"
            if mots[0] == "rmdir":
                self.assertEqual(mots[:2], ["rmdir", "--"], mots)                   # rmdir seulement (refuse un dossier non vide)
            if mots[0] == "mkdir":
                self.assertEqual(mots[:3], ["mkdir", "-m", "0700"], mots)

    def test_un_seul_endroit_appelle_subprocess_et_urllib(self):
        debut, fin = self.source.index("class ExecReel"), self.source.index("class Dependances")
        for motif, attendu in (("subprocess.run(", 2), ("urllib.request.urlopen(", 1)):
            positions = [m.start() for m in re.finditer(re.escape(motif), self.source)]
            self.assertEqual(len(positions), attendu, motif)
            self.assertTrue(all(debut < p < fin for p in positions), motif)
        for motif in ("Popen", "os.system", "check_output", "check_call", "socket"):
            self.assertNotIn(motif, self.source)
        for n in ast.walk(self.arbre):                       # ni eval / exec / compile / __import__ (re.compile est un attribut : permis)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name):
                self.assertNotIn(n.func.id, ("eval", "exec", "compile", "__import__", "system"), n.lineno)

    def test_kubectl_en_lecture_seule(self):
        listes = [mots for mots in self.commandes() if mots[0] == "kubectl"]
        self.assertGreaterEqual(len(listes), 6)
        for mots in listes:
            reste = mots[1:]
            verbe = reste[2] if reste[:1] == ["-n"] else reste[0]
            self.assertIn(verbe, ("get", "exec"), mots)
            if verbe == "exec":
                self.assertIn("psql", mots)                     # kubectl exec : uniquement pour un SELECT dans PostgreSQL
                self.assertNotIn("bash", mots)
                self.assertNotIn("sh", mots)

    def test_les_requetes_sql_sont_des_select(self):
        constantes = [n.value for n in ast.walk(self.arbre) if isinstance(n, ast.Constant) and isinstance(n.value, str)]
        selects = [s for s in constantes if re.match(r"(?is)\s*select\s", s)]
        instructions = [i.strip() for s in selects for i in s.split(";") if i.strip()]
        self.assertGreaterEqual(len(instructions), 4)           # pre-vol : DISQUE, NOTREADY, MAXID ; suivi : incident du test
        for s in constantes:
            self.assertIsNone(re.match(r"(?is)\s*(insert|update|delete|drop|alter|truncate|grant|revoke|create|copy|call|do)\b", s), s[:60])
        for s in selects:
            for instruction in [i.strip() for i in s.split(";") if i.strip()]:
                self.assertTrue(instruction.lower().startswith("select "), instruction[:60])

    def test_les_requetes_sql_ne_contiennent_aucun_mot_d_ecriture(self):
        for n in ast.walk(self.arbre):
            if isinstance(n, ast.Constant) and isinstance(n.value, str) and re.match(r"(?is)\s*select\s", n.value):
                self.assertIsNone(re.search(r"(?i)\b(insert|update|delete|drop|alter|truncate|grant|revoke|create|into)\b", n.value), n.value[:80])

    def test_simulation_n_ecrit_aucun_fichier_et_ne_change_rien(self):
        tmp = tempfile.mkdtemp(prefix="helios_disk_test_")
        self.addCleanup(shutil.rmtree, tmp, True)
        cwd, env = os.getcwd(), dict(os.environ)
        os.chdir(tmp)
        try:
            avant = sorted(os.listdir(tmp))
            lignes = []
            code = H.principal(["--target", "158"], PiegeDeps(), lignes.append)
            apres = sorted(os.listdir(tmp))
        finally:
            os.chdir(cwd)
        self.assertEqual((code, avant, apres), (0, [], []))
        self.assertEqual(dict(os.environ), env)

    def test_simulation_sans_aucune_dependance_reelle(self):
        with mock.patch.object(H, "Dependances", side_effect=AssertionError("Dependances")), \
                mock.patch.object(H, "ExecReel", side_effect=AssertionError("ExecReel")), \
                mock.patch.object(H, "HorlogeReelle", side_effect=AssertionError("HorlogeReelle")), \
                mock.patch.object(H, "collecter_reel", side_effect=AssertionError("collecter_reel")):
            self.assertEqual(H.principal(["--target", "158"], None, lambda s: None), 0)
            self.assertEqual(H.principal(["--target", "155"], None, lambda s: None), 1)

    def test_le_noeud_simule_est_le_seul_utilise_en_simulation(self):
        m = monde()
        with mock.patch.object(H, "MondeSimule", return_value=m) as ctor:
            H.principal(["--target", "158"], None, lambda s: None)
        self.assertTrue(ctor.called)
        self.assertEqual(m.fichiers, {})

    def test_toutes_les_issues_laissent_le_noeud_propre(self):
        scenarios = [dict(helios="resolve"), dict(helios="detect"), dict(helios="absent"), dict(helios="absent", croissance_o_s=20 * MIO),
                     dict(helios="absent", pannes={"stats_apres": 4}), dict(helios="absent", pannes={"allouer": True}),
                     dict(helios="absent", pannes={"saut_apres": (1, 3 * GIO)}), dict(pannes={"armer": True}),
                     dict(pannes={"expiration_inactive": True}), dict(pannes={"mkdir": True})]
        for kw in scenarios:
            with self.subTest(kw=kw):
                res, lignes, m = lancer(monde(**kw))
                self.assertEqual(m.fichiers, {}, res)
                self.assertIsNone(m.dossier, res)
                self.assertTrue(all(not t[2] for t in m.minuteurs.values()), res)
                self.assertTrue(res["liberation_ok"], res)


# ================================================================================================ DISK-E2E-158-02 : PORTE DU SCENARIO SUR LE LANCEMENT REEL DE .158
# La porte (helios_disk_e2e.evaluer_e2e) est appliquee, avec des faits REELS collectes en lecture seule, AVANT toute injection, par
# `--preflight` ET `--go`. Ces tests la font tourner de bout en bout sur l'emulateur (monde E2E) : jamais d'acces reel.
def deploy_e2e(**kw):
    d = dict(e2e_pret()["deploy"])
    d.update(kw)
    return d


PONT = {"HELIOS_BRIDGE_ENABLED": "true", "REMEDIATION_COOLDOWN_MINUTES": "2"}
ANCIENNE_IMAGE = "pfe-backend:0.2.164-vm3auto"

# (nom, surcharges du monde E2E, ancien drapeau, parametres de l'hote/emulateur, code E2E attendu dans la sortie)
CAS_REFUS_PORTE = (
    ("drapeau du scenario absent", {"configmap": dict(PONT)}, None, {}, "E2E_DRAPEAU_DISQUE"),
    ("drapeau du scenario false", {"configmap": dict(PONT, HOST_DISK_E2E_158_ENABLED="false")}, None, {}, "E2E_DRAPEAU_DISQUE"),
    ("drapeau du scenario illisible (valueFrom)", {"deploy": deploy_e2e(env=[{"name": "HOST_DISK_E2E_158_ENABLED", "valueFrom": {}}])},
     None, {}, "E2E_DRAPEAU_DISQUE"),
    ("l'env directe false l'emporte sur la ConfigMap", {"deploy": deploy_e2e(env=[{"name": "HOST_DISK_E2E_158_ENABLED", "value": "false"}])},
     None, {}, "E2E_DRAPEAU_DISQUE"),
    ("ConfigMap illisible", {"cm_illisible": True}, None, {}, "E2E_DRAPEAU_DISQUE"),
    ("ancien drapeau actif", {}, "true", {}, "E2E_DRAPEAU_LEGACY"),
    ("ancien drapeau actif par l'env directe", {"deploy": deploy_e2e(env=[{"name": "HOST_DISK_REMEDIATION_ENABLED", "value": "true"}])},
     None, {}, "E2E_DRAPEAU_LEGACY"),
    ("HELIOS_BRIDGE_ENABLED absent", {"configmap": {"HOST_DISK_E2E_158_ENABLED": "true"}}, None, {}, "E2E_DRAPEAU_BRIDGE"),
    ("Bridge injoignable", {"bridge_curl_rc": 7}, None, {}, "E2E_BRIDGE_INJOIGNABLE"),
    ("Bridge : reponse /actions illisible", {"bridge_actions": None}, None, {}, "E2E_BRIDGE_INJOIGNABLE"),
    ("Bridge sans HOST_RECLAIM_DISK (ancienne version)", {"bridge_actions": ["HOST_IMAGE_GC", "RESTART_POD"]}, None, {},
     "E2E_BRIDGE_CAPACITE_ABSENTE"),
    ("Bridge deploye != source de verite", {"bridge_sha": "0" * 64}, None, {}, "E2E_BRIDGE_VERSION"),
    ("Bridge : empreinte illisible", {"bridge_sha": None}, None, {}, "E2E_BRIDGE_VERSION_ILLISIBLE"),
    ("Bridge : la sonde (liaison ssh Bridge -> .158) echoue", {"sonde": "rc"}, None, {}, "E2E_BRIDGE_SONDE"),
    ("Bridge : jeton du Bridge illisible sur .161", {"sonde": "jeton"}, None, {}, "E2E_BRIDGE_SONDE"),
    ("Bridge : reponse de la sonde illisible", {"sonde": "json"}, None, {}, "E2E_BRIDGE_SONDE"),
    ("Bridge : la validation seule est REFUSEE", {"sonde": "rejet"}, None, {}, "E2E_BRIDGE_SONDE"),
    ("Bridge : la sonde a EXECUTE au lieu de valider seulement", {"sonde": "execute"}, None, {}, "E2E_BRIDGE_SONDE"),
    ("Bridge : mode de la reponse != RECOMMEND_ONLY", {"sonde": {"execution_mode": "EXECUTE"}}, None, {}, "E2E_BRIDGE_SONDE"),
    ("Bridge : occupation de / illisible", {"sonde": {"usage_avant_pct": None}}, None, {}, "E2E_BRIDGE_SONDE"),
    ("Bridge : occupation de / incoherente avec celle du lanceur", {"sonde": {"usage_avant_pct": 90.0}}, None, {}, "E2E_BRIDGE_MESURE"),
    ("Backend : deploiement illisible", {"deploy": deploy_e2e(illisible=True)}, None, {}, "E2E_BACKEND_NON_PRET"),
    ("Backend : pods illisibles", {"pods_illisibles": True}, None, {}, "E2E_BACKEND_NON_PRET"),
    ("Backend : une replique sur deux prete", {"deploy": deploy_e2e(ready=1, available=1)}, None, {}, "E2E_BACKEND_NON_PRET"),
    ("Backend : generation non observee", {"deploy": deploy_e2e(observed=6)}, None, {}, "E2E_BACKEND_NON_PRET"),
    ("Backend : ancienne image", {"deploy": deploy_e2e(image=ANCIENNE_IMAGE)}, None, {}, "E2E_BACKEND_IMAGE"),
    ("Backend : un pod a encore l'ancienne image",
     {"pods": [{"image": E2E_IMAGE, "start": "2026-11-14T00:30:00Z", "phase": "Running"},
               {"image": ANCIENNE_IMAGE, "start": "2026-11-14T00:31:00Z", "phase": "Running"}]}, None, {}, "E2E_BACKEND_IMAGE"),
    ("Backend : pod demarre AVANT la derniere modification de la ConfigMap",
     {"pods": [{"image": E2E_IMAGE, "start": "2026-11-13T20:00:00Z", "phase": "Running"}]}, None, {}, "E2E_BACKEND_ENV_PERIME"),
    ("ConfigMap sans date de modification", {"cm_time": None}, None, {}, "E2E_BACKEND_ENV_PERIME"),
    ("systemd-run absent sur .158", {"systemd_run_rc": 1}, None, {}, "E2E_SYSTEMD_RUN"),
    ("systemd-run non verifiable (ssh en echec)", {"systemd_run_rc": 255}, None, {}, "E2E_SYSTEMD_RUN"),
    ("awk absent sur .158", {"awk_rc": 1}, None, {}, "E2E_AWK"),
    ("unite helios-diskfill-* residuelle", {"unites": ["helios-diskfill-expire-0123.timer"]}, None, {}, "E2E_UNITES_RESIDUELLES"),
    ("unites illisibles", {"unites": None}, None, {}, "E2E_UNITES_RESIDUELLES"),
    ("cooldown : action HOST_RECLAIM_DISK il y a 100 s", {"ages": [100]}, None, {}, "E2E_COOLDOWN_BACKEND"),
    ("cooldown : base illisible", {"sql_illisible": True}, None, {}, "E2E_COOLDOWN_ILLISIBLE"),
    ("incident disque deja ouvert sur .158", {"ouverts": [{"id": 9, "app": "k8s-worker1", "statut": "OPEN"}]}, None, {},
     "E2E_INCIDENT_DISQUE_CONCURRENT"),
    ("RAM de l'hote 2400 Mo < 2500", {}, None, {"ram": 2400}, "E2E_HOTE_RAM"),
    ("pagination de l'hote 6000 pages/s", {"pages": [6000, 6200, 5900]}, None, {}, "E2E_HOTE_PAGES"),
    ("pagination de l'hote illisible", {"pages": None}, None, {}, "E2E_HOTE_PAGES_ILLISIBLES"),
)
COMMANDES_ECRITURE = ("fallocate", "mkdir", "rm", "rmdir", "systemd-run")


class PorteE2EDuLanceurTests(CliBase):
    """Le lancement manuel de .158 (--preflight / --go) ne peut pas contourner la porte : fait manquant, illisible ou ROUGE, ou ORANGE non
    acquitte => AUCUNE charge, AUCUN incident. Chaque refus est prouve par : executer_test jamais appele, noeud intact, aucun incident."""

    def go(self, m, e2e=None, flag=None, image=True, extra=(), **exe):
        d = self.deps(m, flag=flag, e2e=e2e_pret() if e2e is None else e2e, **exe)
        c, s = self.cli(["--target", "158", "--go", "--confirm", "DISK-158-81-82-83", "--vm-base", self.tmp] + list(extra), d, image=image)
        return c, s, d

    def assert_aucune_charge(self, m, d, s=""):
        self.assertEqual(ecritures(m), [], s)
        self.assertEqual([a for _, a in d.exe.ssh_appels if any(v in a for v in COMMANDES_ECRITURE)], [], "aucune commande d'ecriture sur le noeud")
        self.assertEqual((m.fichiers, m.dossier, m.minuteurs, m.gardes), ({}, None, {}, {}))
        self.assertIsNone(m.incident, "aucun incident artificiel")
        self.assertEqual((d.exe.violations, d.exe.inconnues), ([], []))

    def test_chaque_prerequis_manquant_ou_illisible_refuse_sans_aucune_charge(self):
        for nom, e2e, flag, exe, code in CAS_REFUS_PORTE:
            with self.subTest(prerequis=nom):
                m = monde(dispo=12639 * MIO, helios="resolve")
                with mock.patch.object(H, "executer_test", side_effect=AssertionError("INJECTION : la porte aurait du refuser")):
                    c, s, d = self.go(m, e2e=e2e_pret(**e2e), flag=flag, **exe)
                self.assertEqual(c, 1, s)
                self.assertIn("REFUS : pre-vol ROUGE", s)
                self.assertIn(code, s)
                self.assertIn("AUCUNE charge injectee, AUCUN incident cree", s)
                self.assert_aucune_charge(m, d, s)

    def test_le_monde_de_reference_passe_la_porte(self):
        m = monde(dispo=12639 * MIO, helios="resolve")
        c, s, d = self.go(m)
        self.assertEqual(c, 0, s)
        self.assertIn("VERDICT : VERT", s)
        self.assertIn("BILAN : OK", s)
        self.assertEqual((m.fichiers, m.dossier), ({}, None))
        self.assertEqual((d.exe.violations, d.exe.inconnues), ([], []))

    def test_l_env_directe_du_conteneur_l_emporte_sur_la_configmap(self):
        """Une env directe `true` ouvre le scenario meme si la ConfigMap ne porte pas la cle (et inversement : cf. CAS_REFUS_PORTE)."""
        monde_e2e = e2e_pret(configmap=dict(PONT), deploy=deploy_e2e(env=[{"name": "HOST_DISK_E2E_158_ENABLED", "value": "true"}]))
        m = monde(dispo=12639 * MIO, helios="resolve")
        c, s, d = self.go(m, e2e=monde_e2e)
        self.assertEqual(c, 0, s)

    def test_sans_image_backend_declaree_aucune_charge(self):
        m = monde(dispo=12639 * MIO, helios="resolve")
        with mock.patch.object(H, "executer_test", side_effect=AssertionError("INJECTION")):
            c, s, d = self.go(m, image=False)
        self.assertEqual(c, 1, s)
        self.assertIn("E2E_BACKEND_IMAGE_NON_DECLAREE", s)
        self.assert_aucune_charge(m, d, s)

    def test_une_autre_image_que_celle_declaree_refuse(self):
        m = monde(dispo=12639 * MIO, helios="resolve")
        with mock.patch.object(H, "executer_test", side_effect=AssertionError("INJECTION")):
            c, s, d = self.go(m, extra=["--image-backend", ANCIENNE_IMAGE])           # le deploiement porte E2E_IMAGE, pas celle-ci
        self.assertEqual(c, 1, s)
        self.assertIn("E2E_BACKEND_IMAGE", s)
        self.assert_aucune_charge(m, d, s)

    def test_les_parametres_figes_ne_se_surchargent_pas_sur_158(self):
        """Resserrer un seuil reste permis par l'outil, mais le scenario a des parametres FIGES : la porte refuse toute autre valeur."""
        for opt, confirm in ((["--recovery-pct", "70"], "DISK-158-81-82-83"), (["--observe-s", "600", "--expiry-s", "900"], "DISK-158-81-82-83"),
                             (["--chunk-mb", "256"], "DISK-158-81-82-83"), (["--target-pct", "80.5"], "DISK-158-80.5-82-83")):
            with self.subTest(opt=opt):
                m = monde(dispo=12639 * MIO, helios="resolve")
                d = self.deps(m, e2e=e2e_pret())
                with mock.patch.object(H, "executer_test", side_effect=AssertionError("INJECTION")):
                    c, s = self.cli(["--target", "158", "--go", "--confirm", confirm, "--vm-base", self.tmp] + opt, d)
                self.assertEqual(c, 1, s)
                self.assertIn("E2E_PARAMETRES", s)
                self.assert_aucune_charge(m, d, s)

    def test_le_preflight_applique_la_meme_porte(self):
        for e2e, code in (({"bridge_sha": "0" * 64}, "E2E_BRIDGE_VERSION"), ({"systemd_run_rc": 1}, "E2E_SYSTEMD_RUN"),
                          ({"deploy": deploy_e2e(image=ANCIENNE_IMAGE)}, "E2E_BACKEND_IMAGE")):
            with self.subTest(code=code):
                m = monde(dispo=12639 * MIO)
                d = self.deps(m, e2e=e2e_pret(**e2e))
                c, s = self.cli(["--target", "158", "--preflight", "--vm-base", self.tmp], d)
                self.assertEqual(c, 1, s)
                self.assertIn(code, s)
                self.assertEqual(ecritures(m), [])

    def test_orange_non_acquitte_refuse_puis_acquitte_passe(self):
        ancien = {"ouverts": [{"id": 7, "app": "k8s-worker2", "statut": "OPEN"}]}
        m = monde(dispo=12639 * MIO, helios="resolve")
        with mock.patch.object(H, "executer_test", side_effect=AssertionError("INJECTION")):
            c, s, d = self.go(m, e2e=e2e_pret(**ancien))
        self.assertEqual(c, 1, s)
        self.assertIn("REFUS : ORANGE non acquitte", s)
        self.assertIn("E2E_INCIDENTS_DISQUE_ANCIENS", s)
        self.assert_aucune_charge(m, d, s)
        m2 = monde(dispo=12639 * MIO, helios="resolve")
        c2, s2, d2 = self.go(m2, e2e=e2e_pret(**ancien), extra=["--accepte-orange", "E2E_INCIDENTS_DISQUE_ANCIENS"])
        self.assertEqual(c2, 0, s2)
        self.assertIn("BILAN : OK", s2)

    def test_un_incident_de_fond_sur_monitoring_est_tolere(self):
        m = monde(dispo=12639 * MIO, helios="resolve")
        c, s, d = self.go(m, e2e=e2e_pret(ouverts=[{"id": 3858, "app": "monitoring", "statut": "OPEN"}]))
        self.assertEqual(c, 0, s)
        self.assertIn("monitoring (.160) tolere", s)

    def test_le_cooldown_vient_de_la_configmap(self):
        """REMEDIATION_COOLDOWN_MINUTES=2 : une action vieille de 250 s passe (seuil 240 s), 200 s refuse ; cle absente : defaut 30 min."""
        for ages, cooldown, attendu in (([250], "2", 0), ([200], "2", 1), ([250], None, 1), ([1921], None, 0), ([1919], None, 1)):
            cm = dict(PONT, HOST_DISK_E2E_158_ENABLED="true")
            if cooldown is None:
                del cm["REMEDIATION_COOLDOWN_MINUTES"]
            else:
                cm["REMEDIATION_COOLDOWN_MINUTES"] = cooldown
            m = monde(dispo=12639 * MIO, helios="resolve")
            c, s, d = self.go(m, e2e=e2e_pret(configmap=cm, ages=ages))
            with self.subTest(ages=ages, cooldown=cooldown):
                self.assertEqual(c, attendu, s)

    def test_la_porte_ne_lit_qu_en_lecture_seule(self):
        m = monde(dispo=12639 * MIO, helios="resolve")
        c, s, d = self.go(m)
        self.assertEqual(c, 0, s)
        attendu = sorted([("curl", "-sS", "--max-time", "8", URL_ACTIONS_SIM), ("sha256sum", BRIDGE_DEPLOYE_SIM),
                          ("bash", "-c", H.module_porte_e2e().script_sonde_bridge()),                      # sonde du Bridge : validation seule
                          ("test", "-x", "/usr/bin/systemd-run"), ("test", "-x", "/usr/bin/awk"),
                          ("systemctl", "list-units", "--all", "--no-legend", "--plain", "helios-diskfill-*")])
        self.assertEqual(attendu, sorted(tuple(a) for _, a in d.exe.e2e_ssh))
        for argv in d.exe.e2e_local:
            if argv[:1] == ["powershell"]:
                self.assertEqual(argv[:3], ["powershell", "-NoProfile", "-NonInteractive"])
            elif argv[:2] == ["kubectl", "exec"]:
                self.assertIn("psql", argv)
            else:
                self.assertEqual(argv[:4], ["kubectl", "-n", "pfe", "get"], argv)
        self.assertGreaterEqual(len(d.exe.e2e_local), 5)                    # configmap, deploiement, pods, SELECT, pages/s
        for sql in d.exe.sql:
            for instruction in [i.strip() for i in sql.split(";") if i.strip()]:
                self.assertTrue(instruction.lower().startswith("select "), instruction[:60])

    def test_la_sonde_n_est_jamais_envoyee_a_un_bridge_non_conforme(self):
        """Un ancien Bridge remplacerait une demande NODE_DISK_FULL par HOST_IMAGE_GC (action reelle) : la sonde (POST) ne part que si l'empreinte
        du Bridge deploye = source de verite ET la capacite est annoncee. Sinon : aucune requete, et aucun ROUGE de sonde en plus."""
        for nom, e2e in (("empreinte differente", {"bridge_sha": "0" * 64}), ("empreinte illisible", {"bridge_sha": None}),
                         ("capacite absente", {"bridge_actions": ["HOST_IMAGE_GC", "RESTART_POD"]}), ("catalogue illisible", {"bridge_actions": None}),
                         ("Bridge injoignable", {"bridge_curl_rc": 7})):
            with self.subTest(nom):
                m = monde(dispo=12639 * MIO, helios="resolve")
                with mock.patch.object(H, "executer_test", side_effect=AssertionError("INJECTION")):
                    c, s, d = self.go(m, e2e=e2e_pret(**e2e))
                self.assertEqual(c, 1, s)
                self.assertEqual([a for _, a in d.exe.ssh_appels if a[:1] == ["bash"]], [], "aucune requete envoyee au Bridge")
                self.assertNotIn("E2E_BRIDGE_SONDE", s, "la sonde n'a pas ete tentee : pas de ROUGE de sonde en plus")
                self.assert_aucune_charge(m, d, s)

    def test_la_sonde_reussie_est_signalee_sans_etre_une_preuve_de_remediation(self):
        m = monde(dispo=12639 * MIO, helios="resolve")
        d = self.deps(m, e2e=e2e_pret())
        c, s = self.cli(["--target", "158", "--preflight", "--vm-base", self.tmp], d, image=True)
        self.assertEqual(c, 0, s)
        self.assertIn("liaison ssh Bridge -> .158 verifiee", s)
        self.assertIn("aucune suppression", s)
        self.assertIn("RIEN n'a ete cree ni modifie", s)
        self.assertIn("N'EST PAS une preuve de remediation", s)

    def test_la_porte_ne_peut_pas_etre_contournee_et_recoit_les_acquittements_et_l_image(self):
        porte = mock.Mock()
        porte.collecter_faits_e2e.return_value = {}
        porte.evaluer_e2e.return_value = {"raisons": [("ROUGE", "refus de test", "", "E2E_TEST")], "verdict": "ROUGE", "go": False, "plan": None}
        porte.afficher_rapport_e2e = lambda rapport, sortie: sortie("RAPPORT-DE-LA-PORTE")
        fabriquer_vm(self.tmp, "k8s-worker1", journal=FAUX_GEL)
        m = monde(dispo=12639 * MIO, helios="resolve")
        with mock.patch.object(H, "module_porte_e2e", return_value=porte), \
                mock.patch.object(H, "executer_test", side_effect=AssertionError("INJECTION")):
            c, s, d = self.go(m, extra=["--accepte-orange", "GEL_HOTE_48H"])
        self.assertEqual(c, 1, s)
        self.assertIn("RAPPORT-DE-LA-PORTE", s)
        self.assertIn("REFUS : pre-vol ROUGE", s)
        self.assert_aucune_charge(m, d, s)
        porte.evaluer_e2e.assert_called_once()
        appel = porte.evaluer_e2e.call_args
        self.assertEqual(appel.args[2:4], (H.valider_parametres(), "158"))
        self.assertEqual(appel.kwargs, {"image_backend_attendue": E2E_IMAGE, "acquittes": {"GEL_HOTE_48H"}})

    def test_le_lanceur_honore_la_decision_go_de_la_porte_meme_si_le_verdict_parait_vert(self):
        """Defense en profondeur : une porte qui dit go=False (verdict VERT, aucun ORANGE) n'est jamais suivie d'une injection."""
        porte = mock.Mock()
        porte.collecter_faits_e2e.return_value = {}
        porte.evaluer_e2e.return_value = {"raisons": [("INFO", "tout semble bon", "", None)], "verdict": "VERT", "go": False, "plan": None}
        porte.afficher_rapport_e2e = lambda rapport, sortie: sortie("RAPPORT-DE-LA-PORTE")
        m = monde(dispo=12639 * MIO, helios="resolve")
        with mock.patch.object(H, "module_porte_e2e", return_value=porte), \
                mock.patch.object(H, "executer_test", side_effect=AssertionError("INJECTION")):
            c, s, d = self.go(m)
        self.assertEqual(c, 1, s)
        self.assertIn("la porte du scenario DISK_E2E_158 n'autorise pas le lancement", s)
        self.assert_aucune_charge(m, d, s)

    def test_la_porte_est_consultee_pour_158_jamais_pour_155_ni_en_simulation(self):
        with mock.patch.object(H, "module_porte_e2e", side_effect=AssertionError("porte chargee")):
            self.assertEqual(self.cli(["--target", "158"], PiegeDeps())[0], 0)                       # simulation .158
            self.assertEqual(self.cli(["--target", "155"], PiegeDeps())[0], 1)                       # simulation .155
            m = monde()
            c, s = self.cli(["--target", "155", "--preflight", "--vm-base", self.tmp], self.deps(m, primaire_sur="k8s-worker2"))
            self.assertEqual(c, 1, s)                                                               # pre-vol reel .155 : chemin d'origine
            self.assertNotIn("DISK_E2E_158", s)

    def test_la_porte_se_charge_depuis_le_dossier_de_l_outil_et_compose_ce_module(self):
        sauve = sys.modules.pop("helios_disk_e2e", None)
        try:
            mod = H.module_porte_e2e()
            self.assertEqual(os.path.dirname(os.path.abspath(mod.__file__)), HERE)
            self.assertEqual(mod.H.__file__, H.__file__)
            self.assertIs(H.module_porte_e2e(), mod)                                  # deja charge : reutilise, jamais une seconde copie
            self.assertTrue(callable(mod.evaluer_e2e) and callable(mod.collecter_faits_e2e))
        finally:
            sys.modules.pop("helios_disk_e2e", None)
            if sauve is not None:
                sys.modules["helios_disk_e2e"] = sauve

    def test_une_porte_qui_ne_se_charge_pas_ne_laisse_aucun_module_a_moitie_charge_et_aucune_injection(self):
        class Charge(object):
            def exec_module(self, module):
                raise RuntimeError("boom : fichier de la porte illisible")
        spec = types.SimpleNamespace(loader=Charge())
        sauve = sys.modules.pop("helios_disk_e2e", None)
        try:
            with mock.patch.object(H.importlib.util, "spec_from_file_location", return_value=spec), \
                    mock.patch.object(H.importlib.util, "module_from_spec", return_value=types.ModuleType("helios_disk_e2e")), \
                    mock.patch.object(H, "executer_test", side_effect=AssertionError("INJECTION")):
                m = monde(dispo=12639 * MIO, helios="resolve")
                with self.assertRaises(RuntimeError):
                    self.go(m)                                              # le lanceur s'arrete AVANT toute charge : exception, pas de test
                self.assertNotIn("helios_disk_e2e", sys.modules)
                self.assertEqual(ecritures(m), [])
                self.assertIsNone(m.incident)
        finally:
            sys.modules.pop("helios_disk_e2e", None)
            if sauve is not None:
                sys.modules["helios_disk_e2e"] = sauve

    def test_l_enregistrement_sous_le_nom_de_l_outil_exige_que_ce_soit_bien_ce_module(self):
        """Verifie par un wrapper de verification : `exec` dans un dict avec __name__ == "__main__" ne doit JAMAIS faire adopter le __main__ du
        wrapper (qui n'a ni MondeSimule ni le reste) comme module outil de la porte."""
        etranger = types.ModuleType(H.__name__)
        with mock.patch.dict(sys.modules, {H.__name__: etranger}):
            sys.modules.pop("helios_disk_test", None)
            H.enregistrer_comme_outil()
            self.assertNotIn("helios_disk_test", sys.modules)
        with mock.patch.dict(sys.modules, {H.__name__: H}):
            sys.modules.pop("helios_disk_test", None)
            H.enregistrer_comme_outil()
            self.assertIs(sys.modules["helios_disk_test"], H)

    def test_evaluer_sans_controle_de_drapeau_n_emet_aucune_raison_de_drapeau(self):
        p = H.valider_parametres()
        for dr in ("true", "false", "absent", None, "n'importe quoi"):
            with self.subTest(drapeau=dr):
                r = H.evaluer(faits("158", drapeau=dr), "158", p, controle_drapeau=False)
                self.assertEqual(r["verdict"], "VERT", r["raisons"])
                self.assertNotIn("drapeau", textes(r).lower())
                self.assertNotIn("HOST_DISK_REMEDIATION_ENABLED", textes(r))

    def test_pre_vol_155_precise_que_le_backend_ne_remedie_jamais_155(self):
        p = H.valider_parametres()
        self.assertIn("ne remedie JAMAIS", textes(H.evaluer(faits("155", drapeau="true"), "155", p, True), "INFO"))
        self.assertNotIn("ne remedie JAMAIS", textes(H.evaluer(faits("158", drapeau="true"), "158", p)))

    def test_le_lanceur_n_importe_la_porte_que_dans_principal_et_sans_nouvel_acces_reel(self):
        with open(SRC, encoding="utf-8") as f:
            source = f.read()
        arbre = ast.parse(source)
        self.assertNotIn("import helios_disk_e2e", source)                           # chargement dynamique : pas d'import circulaire
        appelants = set()
        for fonction in [n for n in ast.walk(arbre) if isinstance(n, ast.FunctionDef)]:
            for n in ast.walk(fonction):
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "module_porte_e2e":
                    appelants.add(fonction.name)
        self.assertEqual(appelants, {"principal"})
        principal = [n for n in ast.walk(arbre) if isinstance(n, ast.FunctionDef) and n.name == "principal"][0]
        appels = {n.func.attr for n in ast.walk(principal) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
        self.assertTrue({"collecter_faits_e2e", "evaluer_e2e", "afficher_rapport_e2e"} <= appels)


if __name__ == "__main__":
    unittest.main()
