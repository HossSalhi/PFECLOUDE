# -*- coding: utf-8 -*-
"""Coherence du SERVICE LOCAL DISK de .158 avec le test DISK deja valide et avec le Bridge :
  * constantes IDENTIQUES au lanceur (helios_disk_test.py), a la porte (helios_disk_e2e.py) et au Bridge SoT ;
  * fonctions pures IDENTIQUES au lanceur (test differentiel sur une grille de volumes et d'occupations) ;
  * CONTRAT DU BRIDGE : ce que le service cree est accepte par le VRAI disque_plan() du Bridge, le repertoire doit etre directory|root|700 ;
  * unites systemd (lint) : StateDirectoryMode=0700 EXPLICITE, Restart=no, aucune section [Install], RuntimeMaxSec, durcissement ;
  * source verrouille : ASCII, aucune suppression hors des deux primitives par nom exacts, aucun shell, aucune variable d'environnement de seuil ;
  * DEMARRAGE DIRECT (decision du 15 nov. 2026) : plus aucun armement dans le programme ni dans les unites, liste FERMEE des controles internes,
    empreintes SHA-256 des unites figees dans le programme = celles du depot, regle des inodes = celle du detecteur, attestation Windows HMAC fraiche et VERT obligatoire ;
  * empreintes SHA-256 du depot (SHA256SUMS).

AUCUN reseau, AUCUNE VM, AUCUN ssh, AUCUN subprocess, AUCUN sleep reel.

Lancer : python -I -B test_helios_diskfill_coherence.py -v        (depuis scripts/helios-ops)
"""
import ast
import fnmatch
import hashlib
import importlib.util
import json
import os
import re
import socket
import subprocess
import sys
import time
import unittest
import urllib.parse
import urllib.request
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
DIR_SVC = os.path.join(REPO, "infra", "helios-sot", "diskfill")


def _charger(nom, chemin):
    spec = importlib.util.spec_from_file_location(nom, chemin)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[nom] = mod
    spec.loader.exec_module(mod)
    return mod


D = _charger("diskfill_mod_coh", os.path.join(DIR_SVC, "diskfill.py"))
H = _charger("helios_disk_test_mod_coh", os.path.join(HERE, "helios_disk_test.py"))
sys.modules.setdefault("helios_disk_test", H)
E = H.module_porte_e2e()
B = _charger("bridge_cible_diskfill_coh", os.path.join(REPO, "infra", "helios-sot", "bridge", "bridge.py"))

MIO, GIO = D.MIO, D.GIO
CHEMIN_SOURCE = os.path.join(DIR_SVC, "diskfill.py")
CHEMIN_UNITES = {D.UNITE_PRINCIPALE: os.path.join(DIR_SVC, "helios-diskfill.service"), D.UNITE_GARDE: os.path.join(DIR_SVC, "helios-diskguard.service")}


def _interdit(*a, **k):
    raise AssertionError("ACCES REEL INTERDIT dans un test : %r" % (a[:1],))


class SansInfra(unittest.TestCase):
    def setUp(self):
        cibles = [(subprocess, "run"), (subprocess, "Popen"), (subprocess, "call"), (subprocess, "check_call"), (subprocess, "check_output"),
                  (urllib.request, "urlopen"), (socket, "create_connection"), (socket.socket, "connect"), (time, "sleep"), (os, "system")]
        self._patchs = [mock.patch.object(o, n, _interdit) for o, n in cibles]
        for p in self._patchs:
            p.start()
            self.addCleanup(p.stop)


def lire_octets(chemin):
    with open(chemin, "rb") as f:
        return f.read()


def lire_unite(chemin):
    """Unite systemd -> {section: {cle: [valeurs]}} (les cles repetees sont conservees ; commentaires et lignes vides ignores)."""
    sections, courante = {}, None
    for ligne in lire_octets(chemin).decode("ascii").split("\n"):
        s = ligne.strip()
        if not s or s.startswith("#"):
            continue
        m = re.match(r"^\[([A-Za-z]+)\]$", s)
        if m:
            courante = m.group(1)
            sections.setdefault(courante, {})
            continue
        cle, sep, valeur = s.partition("=")
        assert sep and courante, "ligne d'unite illisible : %r" % ligne
        sections[courante].setdefault(cle.strip(), []).append(valeur.strip())
    return sections


def resultat(f):
    """('ok', valeur) ou ('refus', None) : les exceptions de securite du lanceur et du service sont equivalentes."""
    try:
        return "ok", f()
    except (H.RefusSecurite, H.ErreurNoeud, D.RefusSecurite, D.ErreurNoeud):
        return "refus", None


# ------------------------------------------------------------------------------------------------ constantes
class TestConstantesIdentiques(SansInfra):
    def test_parametres_figes_identiques_au_lanceur_valide(self):
        p = H.valider_parametres()
        self.assertEqual((D.CIBLE_PCT, D.PLAFOND_PCT, D.URGENCE_PCT, D.RECUPERATION_PCT), (p.cible, p.plafond, p.urgence, p.recuperation))
        self.assertEqual((D.CIBLE_PCT, D.PLAFOND_PCT, D.URGENCE_PCT, D.RECUPERATION_PCT), (81.0, 82.0, 83.0, 77.0))
        self.assertEqual(int(round(D.GARDE_PCT * 100)), H.seuil_garde_x100(p))
        self.assertEqual(D.GARDE_PCT, 83.5)
        self.assertEqual(D.BLOC, p.bloc)
        self.assertEqual(D.BLOC, H.BLOC_DEFAUT)
        self.assertEqual(D.BLOC_MAX, H.BLOC_MAX)
        self.assertEqual((D.OBSERVE_S, D.EXPIRATION_S), (p.observe, p.expiration))
        self.assertEqual((D.OBSERVE_S, D.EXPIRATION_S), (720, 1020))
        self.assertEqual(D.BUDGET_ALLOC_S, H.budget_allocation(p))
        self.assertEqual(D.BUDGET_ALLOC_S, 180)
        for nom_d, nom_h in (("MAX_FICHIERS", "MAX_FICHIERS"), ("MAX_TOTAL", "MAX_TOTAL"), ("MARGE_FIN_S", "MARGE_FIN_S"),
                             ("DUREE_BLOC_MIN_S", "DUREE_BLOC_MIN_S"), ("EFFET_MIN", "EFFET_MIN"), ("GARDE_PAS_S", "GARDE_PAS_S"),
                             ("MARGE_KUBELET_PCT", "MARGE_KUBELET_PCT"), ("OBSERVE_MIN_S", "OBSERVE_MIN_S"), ("DETECTION_PCT", "DETECTION_PCT"),
                             ("DOSSIER", "DOSSIER"), ("TYPES_FS_OK", "TYPES_FS_OK")):
            self.assertEqual(getattr(D, nom_d), getattr(H, nom_h), nom_d)

    def test_identiques_a_la_porte_du_scenario(self):
        for nom_d, nom_e in (("CIBLE_PCT", "CIBLE_PCT"), ("PLAFOND_PCT", "PLAFOND_PCT"), ("URGENCE_PCT", "URGENCE_PCT"), ("GARDE_PCT", "GARDE_PCT"),
                             ("RECUPERATION_PCT", "RECUPERATION_PCT"), ("DOSSIER", "DOSSIER"), ("MAX_TOTAL", "MAX_TOTAL_OCTETS"),
                             ("MAX_FICHIERS", "MAX_FICHIERS"), ("OBSERVE_S", "OBSERVE_S"), ("EXPIRATION_S", "EXPIRATION_S"),
                             ("NOM_CIBLE", "NOEUD_K8S"), ("IP_CIBLE", "IP"), ("DETECTION_PCT", "DETECTION_PCT")):
            self.assertEqual(getattr(D, nom_d), getattr(E, nom_e), nom_d)
        self.assertEqual(D.BLOC // D.MIO, E.BLOC_MO)
        self.assertEqual(D.IP_CIBLE, H.CIBLES["158"][1])
        self.assertEqual(D.NOM_CIBLE, H.CIBLES["158"][0])
        self.assertEqual(tuple(E.parametres()), tuple(H.valider_parametres()))

    def test_identiques_au_bridge_sot(self):
        self.assertEqual(D.DOSSIER, B.DISQUE_DOSSIER)
        self.assertEqual(D.MAX_FICHIERS, B.DISQUE_MAX_FICHIERS)
        self.assertEqual(D.MAX_TOTAL, B.DISQUE_MAX_TOTAL)
        self.assertEqual(D.RECUPERATION_PCT, B.DISQUE_RECUPERATION_PCT)
        self.assertEqual(D.NOM_CIBLE, B.DISQUE_CIBLE_K8S)
        self.assertEqual(D.RE_FICHIER.pattern, B.DISQUE_RE_FICHIER.pattern, "meme motif de nom que le Bridge (fullmatch)")
        self.assertEqual(D.RE_JETON.pattern, B.DISQUE_RE_JETON.pattern)

    def test_le_cooldown_local_est_de_5_min_et_couvre_le_cooldown_du_backend_configure_plus_un_lancement_complet(self):
        """17 nov. 2026 : ramene de 30 a 5 min (la commande unique etait refusee pendant 30 min apres chaque essai).
        INVARIANT conserve : cooldown local >= cooldown du Backend REELLEMENT configure (REMEDIATION_COOLDOWN_MINUTES de la ConfigMap du depot)
        + un lancement complet jusqu'a RESOLVED (~2 min : 78 s le 14 nov., 119 s le 17 nov.) + 1 min de marge ; sinon un 2e lancement rapproche ne
        serait pas remedie par HELIOS. Le DEFAUT du code Backend (30 min, applique seulement si la cle est absente) n'est plus couvert : le pre-vol
        facultatif de l'hote lit la valeur reelle et signale SVC_COOLDOWN_INFERIEUR_BACKEND si elle depasse le cooldown local."""
        self.assertEqual(D.COOLDOWN_LOCAL_S, 5 * 60)
        texte = lire_octets(os.path.join(REPO, "backend", "backend", "infra", "k8s", "backend-configmap.yaml")).decode("utf-8")
        trouves = re.findall(r'^\s*REMEDIATION_COOLDOWN_MINUTES:\s*"?(\d+)"?\s*(?:#.*)?$', texte, re.M)
        self.assertEqual(len(trouves), 1, "REMEDIATION_COOLDOWN_MINUTES doit etre defini UNE fois dans la ConfigMap du depot")
        backend_s, lancement_complet_s, marge_s = int(trouves[0]) * 60, 2 * 60, 60
        self.assertGreaterEqual(D.COOLDOWN_LOCAL_S, backend_s + lancement_complet_s + marge_s,
                                "cooldown local %d s < Backend %d s + lancement complet %d s + marge %d s : realigner (decision distincte)" % (
                                    D.COOLDOWN_LOCAL_S, backend_s, lancement_complet_s, marge_s))
        self.assertIn("SVC_COOLDOWN_INFERIEUR_BACKEND", lire_octets(os.path.join(HERE, "helios_diskfill_service.py")).decode("utf-8"),
                      "le pre-vol facultatif doit toujours signaler un cooldown du Backend superieur au cooldown local")

    def test_le_noeud_ne_connait_plus_aucun_code_orange(self):
        # Les codes ORANGE restent definis dans la porte du lanceur ; le noeud refuse une attestation Windows non VERT.
        for nom in ("CODES_INTERDITS", "RE_CODE", "RE_IMAGE", "RE_HEX64", "CLES_CHARGE", "CLES_ARMEMENT", "CLES_PREFLIGHT"):
            self.assertFalse(hasattr(D, nom), "%s : vestige de l'armement" % nom)
        self.assertIn("GEL_HOTE_48H", H.CODES_ORANGE_ACQUITTABLES, "GEL_HOTE_48H reste un ORANGE du lanceur (jamais un blocage de 48 h)")

    def test_les_noms_d_unites_ne_correspondent_pas_au_motif_des_residus_du_lanceur(self):
        """La porte du lanceur liste `systemctl list-units helios-diskfill-*` comme residus : les unites statiques ne doivent pas les declencher."""
        for unite in (D.UNITE_PRINCIPALE, D.UNITE_GARDE):
            self.assertFalse(fnmatch.fnmatch(unite, "helios-diskfill-*"), unite)
        source = lire_octets(os.path.join(HERE, "helios_disk_e2e.py")).decode("utf-8")
        self.assertIn('"helios-diskfill-*"', source, "le motif des residus du lanceur a change : revoir les noms d'unites")

    def test_les_chemins_d_etat_sont_hors_du_repertoire_du_bridge(self):
        for chemin in (D.DOSSIER_ETAT, D.RUN_DIR, D.F_COOLDOWN, D.F_ETAT, D.F_BATTEMENT):
            self.assertFalse(chemin == D.DOSSIER or chemin.startswith(D.DOSSIER + "/"), "%s serait vu comme entree etrangere par le Bridge" % chemin)

    def test_url_prometheus_et_requetes_identiques_au_lanceur(self):
        self.assertEqual(D.URL_PROM + "?", H.PROM)
        vues_h, vues_d = [], []

        class ExeH(object):
            def http_json(self, url, delai=25):
                vues_h.append(urllib.parse.parse_qs(urllib.parse.urlparse(url).query)["query"][0])
                return {"status": "success", "data": {"result": [{"value": [0, "1"]}]}}

        class MondeD(object):
            def prom(self, requete):
                vues_d.append(requete)
                return [1.0]
        H.lire_prom(ExeH(), D.IP_CIBLE, D.NOM_CIBLE)
        D.lire_prom_noeud(MondeD())
        self.assertEqual(vues_d, vues_h, "memes trois requetes Prometheus que le pre-vol du lanceur")


# ------------------------------------------------------------------------------------------------ fonctions pures
USAGES = (0.0, 30.0, 50.0, 60.0, 68.6, 70.0, 76.9, 77.0, 77.1, 79.9, 80.0, 80.5, 81.0, 82.0, 82.5, 83.0, 83.4, 90.0, 99.9)
TAILLES = (4 * GIO, 10 * GIO, 20 * GIO, 38 * GIO, 40 * GIO, 60 * GIO, 100 * GIO, 400 * GIO)


class TestDifferentiel(SansInfra):
    def test_usage_octets_pour_et_arrondi(self):
        for taille in TAILLES:
            for u in USAGES:
                dispo = int(taille * (1.0 - u / 100.0))
                fd, fh = D.Fs(taille, dispo), H.Fs(taille, dispo)
                self.assertEqual(fd.usage, fh.usage)
                for pct in (77.0, 80.0, 81.0, 82.0, 83.0):
                    self.assertEqual(D.octets_pour(fd, pct), H.octets_pour(fh, pct))
        for n in (0, 1, MIO - 1, MIO, MIO + 1, 5 * MIO, 512 * MIO, 123456789):
            self.assertEqual(D.arrondi_mio(n), H.arrondi_mio(n))

    def test_planifier_identique(self):
        p = H.valider_parametres()
        n_ok = 0
        for taille in TAILLES:
            for u in USAGES:
                dispo = int(taille * (1.0 - u / 100.0))
                rd = resultat(lambda: D.planifier(D.Fs(taille, dispo)))
                rh = resultat(lambda: H.planifier(H.Fs(taille, dispo), p))
                with self.subTest(taille_gio=taille // GIO, usage=u):
                    self.assertEqual(rd, rh)
                n_ok += rd[0] == "ok"
        self.assertGreater(n_ok, 10, "la grille doit contenir de vrais plans acceptes")

    def test_garde_allocation_identique(self):
        p = H.valider_parametres()
        for taille in (10 * GIO, 38 * GIO, 100 * GIO):
            for u in (50.0, 68.6, 77.0, 79.0, 80.0, 80.9, 81.0, 81.5, 82.0, 82.9, 83.0, 85.0):
                dispo = int(taille * (1.0 - u / 100.0))
                for octets in (1, 64 * MIO, 512 * MIO, 1024 * MIO, dispo, dispo + 1):
                    for deja in (0, 1 * GIO, 5 * GIO + 600 * MIO, 6 * GIO):
                        rd = resultat(lambda: D.garde_allocation(D.Fs(taille, dispo), octets, deja))
                        rh = resultat(lambda: H.garde_allocation(H.Fs(taille, dispo), octets, p, deja))
                        with self.subTest(taille_gio=taille // GIO, usage=u, octets=octets, deja=deja):
                            self.assertEqual(rd[0], rh[0])
                            if rd[0] == "ok":
                                self.assertEqual(rd[1][0], rh[1][0], "meme decision")

    def test_noms_de_fichiers_identiques(self):
        for jeton in ("a" * 32, "0123456789abcdef" * 2):
            self.assertEqual(D.noms_prevus(jeton), H.noms_prevus(jeton))
            for i in range(1, 17):
                self.assertEqual(D.nom_fichier(jeton, i), H.nom_fichier(jeton, i))
                self.assertEqual(D.chemin_de(D.nom_fichier(jeton, i)), H.chemin_de(H.nom_fichier(jeton, i)))
        for mauvais_jeton, i in (("A" * 32, 1), ("a" * 31, 1), ("a" * 33, 1), ("a" * 32, 0), ("a" * 32, 17), (None, 1), ("a" * 32, True)):
            self.assertEqual(resultat(lambda: D.nom_fichier(mauvais_jeton, i))[0], "refus", (mauvais_jeton, i))

    def test_noms_acceptes_le_service_est_aussi_strict_que_le_bridge(self):
        jeton = "ab" * 16
        noms = [D.nom_fichier(jeton, 1), D.nom_fichier(jeton, 16), "helios-diskfill-%s-017.bin" % jeton, "helios-diskfill-%s-000.bin" % jeton,
                "helios-diskfill-%s-1.bin" % jeton, "helios-diskfill-%s-0001.bin" % jeton, "helios-diskfill-%s-001.bin\n" % jeton,
                "helios-diskfill-%s-001.bin.bak" % jeton, "helios-diskfill-%s-001.BIN" % jeton, "helios-diskfill-" + jeton.upper() + "-001.bin",
                "helios-diskfill-%s-001.bin " % jeton, "README", "../" + D.nom_fichier(jeton, 1), D.nom_fichier(jeton, 1).replace("-001", "-0*1"),
                "helios-diskfill-%s-001.bin" % ("a" * 31), "helios-diskfill-%s-001.bin" % ("a" * 33), ""]
        for n in noms:
            m_bridge = B.DISQUE_RE_FICHIER.fullmatch(n)
            ok_bridge = bool(m_bridge) and 1 <= int(m_bridge.group(2)) <= B.DISQUE_MAX_FICHIERS
            ok_service = resultat(lambda: D.chemin_de(n))[0] == "ok"
            self.assertEqual(ok_service, ok_bridge, repr(n))
            if ok_service:
                self.assertTrue(H.RE_FICHIER.match(n), "tout nom accepte par le service l'est par le lanceur : %r" % n)

    def test_parse_kubelet_et_seuil_identiques(self):
        textes = [
            'apiVersion: kubelet.config.k8s.io/v1beta1\nkind: KubeletConfiguration\nevictionHard:\n    imagefs.available: "15%"\n'
            '    memory.available: "100Mi"\n    nodefs.available: "10%"\n    nodefs.inodesFree: "5%"\nevictionPressureTransitionPeriod: 0s\n',
            "kind: KubeletConfiguration\n",
            "kind: KubeletConfiguration\nimageGCHighThresholdPercent: 80\n",
            "kind: KubeletConfiguration\nimageGCHighThresholdPercent: abc\n",
            "kind: KubeletConfiguration\nevictionHard:\n    imagefs.available: 15Gi\n    nodefs.available: \"10%\"\n",
            "kind: KubeletConfiguration\nevictionHard:\n    imagefs.available: \"20%\"\n    nodefs.available: \"10%\"\nevictionSoft:\n    nodefs.available: \"25%\"\n",
            "pas un kubelet", "", None,
        ]
        for t in textes:
            self.assertEqual(D.parse_kubelet(t), H.parse_kubelet(t), repr(t)[:60])
            self.assertEqual(D.seuil_kubelet(D.parse_kubelet(t)), H.seuil_kubelet(H.parse_kubelet(t)))
        self.assertEqual(D.seuil_kubelet(D.parse_kubelet(textes[0])), 85.0)

    def test_valeurs_prom_identiques(self):
        for obj in ({"status": "success", "data": {"result": [{"value": [0, "1.5"]}, {"value": [0, "2"]}]}}, {"status": "error"}, {}, None,
                    {"status": "success", "data": {"result": [{"value": [0, "abc"]}]}}, {"status": "success", "data": {"result": []}}):
            self.assertEqual(D.valeurs_prom(obj), H.valeurs_prom(obj))

    def test_anomalies_du_repertoire_identiques(self):
        racine = {"type": "directory", "uid": 0, "gid": 0, "mode": "755", "dev": 64512, "fstype": "ext4", "montage": "/"}
        base = {"type": "directory", "uid": 0, "gid": 0, "mode": "700", "dev": 64512, "fstype": "ext4", "montage": "/"}
        variantes = [{}, {"mode": "755"}, {"mode": "750"}, {"mode": "777"}, {"uid": 1000}, {"gid": 1000}, {"type": "symbolic link"}, {"dev": 7},
                     {"fstype": "tmpfs"}, {"fstype": "xfs"}, {"montage": "/var"}]
        def sans_precision(liste):
            return [re.sub(r" \(le Bridge exige directory\|root\|700\)$", "", a) for a in liste]      # seule precision ajoutee par le service
        for v in variantes:
            info = dict(base, **v)
            self.assertEqual(sans_precision(D.anomalies_dossier(info, racine)), H.anomalies_dossier(info, racine), v)
        self.assertEqual(D.anomalies_dossier(None, racine), H.anomalies_dossier(None, racine))
        self.assertEqual(D.anomalies_dossier(base, racine), [])
        # ECART DECLARE avec le lanceur : dans l'espace de noms d'une unite systemd, le repertoire d'etat est un bind mount de lui-meme (constate
        # sur .158). Le service l'accepte (meme peripherique que /, ext4) ; le lanceur, qui tourne dans l'espace racine, le refuse toujours.
        bind = dict(base, montage=D.DOSSIER)
        self.assertEqual(D.anomalies_dossier(bind, racine), [])
        self.assertTrue(H.anomalies_dossier(bind, racine))
        for pire in ({"dev": 7}, {"fstype": "tmpfs"}, {"mode": "755"}, {"uid": 1000}, {"type": "symbolic link"}):
            self.assertTrue(D.anomalies_dossier(dict(bind, **pire), racine), "un bind mount ne rend pas conforme un autre volume : %r" % pire)
        for autre in (D.DOSSIER_ETAT, D.RUN_DIR, "/var/lib", "/var/lib/helios-diskfill/x"):
            self.assertTrue(D.anomalies_dossier(dict(base, montage=autre), racine), "seul le repertoire de test lui-meme est admis : %r" % autre)


# ------------------------------------------------------------------------------------------------ contrat du Bridge
def entrees_bridge(entrees):
    return [{"type": e["type"], "proprio": "root" if e["uid"] == 0 else "autre", "mode": "644", "liens": e["nlink"], "taille": e["taille"], "nom": e["nom"]}
            for e in entrees]


def e(nom, taille=512 * MIO, type_="f", uid=0, nlink=1):
    return {"nom": nom, "type": type_, "uid": uid, "nlink": nlink, "taille": taille}


class TestContratDuBridge(SansInfra):
    def test_ce_que_le_service_cree_est_accepte_par_le_vrai_disque_plan_du_bridge(self):
        jeton = "c" * 32
        for n, taille in ((1, 512 * MIO), (5, 512 * MIO), (10, 512 * MIO), (12, 512 * MIO), (16, 300 * MIO)):
            entrees = [e(D.nom_fichier(jeton, i), taille) for i in range(1, n + 1)]
            plan = B.disque_plan(entrees_bridge(entrees), 0)
            self.assertEqual(plan["refus"], "", plan)
            self.assertEqual(plan["jeton"], jeton)
            self.assertEqual(plan["fichiers"], sorted(x["nom"] for x in entrees))
            self.assertEqual(plan["etrangers"], 0)
        # la limite cumulee du service (6 Gio) est celle du Bridge : au-dela, le Bridge refuserait TOUT (TOO_LARGE)
        trop = [e(D.nom_fichier(jeton, i), 512 * MIO) for i in range(1, 14)]
        self.assertEqual(B.disque_plan(entrees_bridge(trop), 0)["rejet"], "TOO_LARGE")
        self.assertGreater(sum(x["taille"] for x in trop), D.MAX_TOTAL)

    def test_le_plan_theorique_du_service_reste_dans_les_limites_du_bridge(self):
        for taille in (10 * GIO, 38 * GIO, 40 * GIO):
            for u in (30.0, 50.0, 68.6, 76.9):
                fs = D.Fs(taille, int(taille * (1 - u / 100.0)))
                r = resultat(lambda: D.planifier(fs))
                if r[0] == "ok":
                    blocs = r[1]
                    self.assertLessEqual(len(blocs), B.DISQUE_MAX_FICHIERS)
                    self.assertLessEqual(sum(blocs), B.DISQUE_MAX_TOTAL)
                    entrees = [e(D.nom_fichier("d" * 32, i + 1), b) for i, b in enumerate(blocs)]
                    self.assertEqual(B.disque_plan(entrees_bridge(entrees), 0)["refus"], "")

    def test_le_plan_des_orphelins_est_celui_du_bridge(self):
        j1, j2 = "c" * 32, "d" * 32
        jeux = {
            "valide": [e(D.nom_fichier(j1, i)) for i in range(1, 6)],
            "etranger_intact": [e(D.nom_fichier(j1, 1)), e("README", 12), e("notes.txt", 5)],
            "deux_jetons": [e(D.nom_fichier(j1, 1)), e(D.nom_fichier(j2, 1))],
            "deux_liens": [e(D.nom_fichier(j1, 1), nlink=2)],
            "autre_proprietaire": [e(D.nom_fichier(j1, 1), uid=1000)],
            "lien_symbolique": [e(D.nom_fichier(j1, 1), type_="l")],
            "repertoire_homonyme": [e(D.nom_fichier(j1, 1), type_="d")],
            "taille_nulle": [e(D.nom_fichier(j1, 1), taille=0)],
            "index_0": [e("helios-diskfill-%s-000.bin" % j1)],
            "index_17": [e("helios-diskfill-%s-017.bin" % j1)],
            "trop_gros": [e(D.nom_fichier(j1, 1), 7 * GIO)],
            "vide": [],
        }
        for nom, entrees in jeux.items():
            pd = D.plan_orphelins(entrees)
            pb = B.disque_plan(entrees_bridge(entrees), 0)
            with self.subTest(nom):
                self.assertEqual(bool(pd["refus"]), bool(pb["refus"]))
                self.assertEqual(pd["jeton"], pb["jeton"])
                self.assertEqual(pd["fichiers"], pb["fichiers"])
                self.assertEqual(pd["etrangers"], pb["etrangers"])
        # 17 fichiers du meme jeton : le Bridge refuse (TOO_MANY_FILES) ; le plan du service aussi
        trop = [e("helios-diskfill-%s-%03d.bin" % (j1, i)) for i in range(1, 17)] + [e("helios-diskfill-%s-017.bin" % j1)]
        self.assertTrue(D.plan_orphelins(trop)["refus"])
        self.assertTrue(B.disque_plan(entrees_bridge(trop), 0)["refus"])

    def test_le_repertoire_doit_etre_directory_root_700_comme_l_exige_le_bridge(self):
        racine = {"type": "directory", "uid": 0, "gid": 0, "mode": "755", "dev": 1, "fstype": "ext4", "montage": "/"}
        ok = {"type": "directory", "uid": 0, "gid": 0, "mode": "700", "dev": 1, "fstype": "ext4", "montage": "/"}
        self.assertEqual(D.anomalies_dossier(ok, racine), [])
        for mode in ("0755", "755", "750", "770", "777", "600", "500", "1700", "2700", "7"):
            info = dict(ok, mode=mode.lstrip("0") if mode != "0755" else "755")
            self.assertTrue(D.anomalies_dossier(info, racine), "mode %s doit etre refuse (le Bridge exige 700 exactement)" % mode)
        # le Bridge lit `stat -c '%F|%U|%a'` et exige EXACTEMENT directory|root|700 : le meme texte decide
        source_bridge = lire_octets(os.path.join(REPO, "infra", "helios-sot", "bridge", "bridge.py")).decode("utf-8")
        self.assertIn('lu != "directory|root|700"', source_bridge)

    def test_les_deux_unites_declarent_explicitement_state_directory_mode_0700(self):
        """CONTRAINTE : StateDirectoryMode=0700 EXPLICITE dans les DEUX unites (le defaut de systemd est 0755 : le Bridge REFUSERAIT)."""
        for unite, chemin in CHEMIN_UNITES.items():
            s = lire_unite(chemin)["Service"]
            self.assertEqual(s.get("StateDirectoryMode"), ["0700"], unite)
            self.assertIn("helios-diskfill", s["StateDirectory"][0].split(), unite)
        principale = lire_unite(CHEMIN_UNITES[D.UNITE_PRINCIPALE])["Service"]
        self.assertEqual(principale["StateDirectory"][0].split(), ["helios-diskfill", "helios-diskfill-etat"])
        self.assertEqual("/var/lib/" + "helios-diskfill", D.DOSSIER)
        self.assertEqual("/var/lib/" + "helios-diskfill-etat", D.DOSSIER_ETAT)

    def test_le_controle_runtime_du_mode_refuse_le_defaut_de_systemd(self):
        """Meme si StateDirectoryMode est present, le service relit le mode REEL du repertoire et du systemctl show avant d'allouer."""
        src = lire_octets(CHEMIN_SOURCE).decode("ascii")
        self.assertIn("StateDirectoryMode", src)
        self.assertIn('mode == "700"', src)
        self.assertIn("anomalies_dossier(d, racine)", src)


# ------------------------------------------------------------------------------------------------ unites systemd (lint)
class TestUnites(SansInfra):
    def setUp(self):
        super().setUp()
        self.p = lire_unite(CHEMIN_UNITES[D.UNITE_PRINCIPALE])
        self.g = lire_unite(CHEMIN_UNITES[D.UNITE_GARDE])

    def test_aucune_section_install_jamais_enable(self):
        for nom, u in (("principale", self.p), ("garde", self.g)):
            self.assertEqual(sorted(u), ["Service", "Unit"], nom)
            for cle in ("WantedBy", "RequiredBy", "Also", "Alias", "DefaultInstance"):
                for section in u.values():
                    self.assertNotIn(cle, section, nom)

    def test_pas_de_redemarrage_automatique(self):
        for nom, u in (("principale", self.p), ("garde", self.g)):
            s = u["Service"]
            self.assertEqual(s["Restart"], ["no"], nom)
            self.assertEqual(s["RemainAfterExit"], ["no"], nom)
            self.assertEqual(s["Type"], ["simple"], nom)

    def test_expirations(self):
        self.assertEqual(self.p["Service"]["RuntimeMaxSec"], [str(D.EXPIRATION_S)])
        self.assertEqual(self.g["Service"]["RuntimeMaxSec"], [str(D.EXPIRATION_S + 60)])
        self.assertGreater(int(self.g["Service"]["RuntimeMaxSec"][0]), int(self.p["Service"]["RuntimeMaxSec"][0]))

    def test_commandes_exactes(self):
        script = D.CHEMIN_SCRIPT
        py = "/usr/bin/python3 -I -B -u " + script
        s = self.p["Service"]
        self.assertEqual(s["ExecStartPre"], ["+" + py + " verifier"])
        self.assertEqual(s["ExecStart"], [py + " run"])
        self.assertEqual(s["ExecStopPost"], [py + " nettoyer"])
        self.assertEqual(self.g["Service"]["ExecStart"], [py + " garde"])
        for nom, u in (("principale", self.p), ("garde", self.g)):
            for cle in ("ExecReload", "ExecStop", "ExecCondition", "ExecStartPost"):
                self.assertNotIn(cle, u["Service"], nom)
            self.assertEqual(u["Unit"]["AssertPathExists"], [script])

    def test_aucune_surcharge_possible_des_parametres(self):
        for nom, u in (("principale", self.p), ("garde", self.g)):
            for cle in ("Environment", "EnvironmentFile", "PassEnvironment", "UnsetEnvironment", "User", "Group", "DynamicUser", "SupplementaryGroups"):
                self.assertNotIn(cle, u["Service"], "%s : %s" % (nom, cle))

    def test_la_garde_est_une_dependance_dure_et_demarre_avant(self):
        u = self.p["Unit"]
        self.assertEqual(u["Requires"], [D.UNITE_GARDE])
        self.assertIn(D.UNITE_GARDE, u["After"][0].split())
        for cle in ("Requires", "BindsTo", "PartOf", "Wants"):
            self.assertNotIn(cle, self.g["Unit"], "la garde ne depend pas du service principal : elle doit survivre a un service bloque")

    def test_repertoires_d_etat(self):
        for nom, u in (("principale", self.p), ("garde", self.g)):
            s = u["Service"]
            self.assertEqual(s["RuntimeDirectory"], [os.path.basename(D.RUN_DIR)], nom)
            self.assertEqual(s["RuntimeDirectoryMode"], ["0700"], nom)
            self.assertEqual(s["RuntimeDirectoryPreserve"], ["yes"], nom)
            self.assertEqual(s["StateDirectoryMode"], ["0700"], nom)

    def test_durcissement_et_limites(self):
        for nom, u in (("principale", self.p), ("garde", self.g)):
            s = u["Service"]
            for cle in ("NoNewPrivileges", "PrivateTmp", "ProtectHome", "PrivateDevices", "ProtectKernelTunables", "ProtectKernelModules",
                        "ProtectControlGroups"):
                self.assertEqual(s[cle], ["yes"], "%s : %s" % (nom, cle))
            self.assertEqual(s["ProtectSystem"], ["strict"], nom)
            self.assertIn("MemoryMax", s)
            self.assertIn("TasksMax", s)
            self.assertEqual(s["KillSignal"], ["SIGTERM"])
            self.assertEqual(s["KillMode"], ["control-group"])
            self.assertEqual(s["StandardOutput"], ["journal"])

    def test_aucun_chemin_d_ecriture_supplementaire(self):
        for nom, u in (("principale", self.p), ("garde", self.g)):
            for cle in ("ReadWritePaths", "ReadWriteDirectories", "BindPaths", "InaccessiblePaths"):
                self.assertNotIn(cle, u["Service"], "ProtectSystem=strict : l'ecriture n'est possible QUE dans les repertoires d'etat declares")

    def test_execstartpre_hors_bac_a_sable_et_seul(self):
        s = self.p["Service"]
        self.assertEqual(len(s["ExecStartPre"]), 1)
        self.assertTrue(s["ExecStartPre"][0].startswith("+"), "verifier doit voir les montages et les unites reels")
        self.assertFalse(s["ExecStart"][0].startswith("+"), "run reste dans le bac a sable")
        self.assertFalse(s["ExecStopPost"][0].startswith("+"))

    def test_delais_d_arret_couvrent_la_liberation(self):
        s = self.p["Service"]
        self.assertGreaterEqual(int(s["TimeoutStopSec"][0]), 60, "3 tentatives de liberation avant que systemd ne tue ExecStopPost")
        self.assertGreaterEqual(int(s["TimeoutStartSec"][0]), D.ATTENTE_GARDE_S + 4 * 4 + 30, "attente de la garde + Prometheus (3 requetes) + marge")

    def test_syslog_identifiers_distincts(self):
        self.assertNotEqual(self.p["Service"]["SyslogIdentifier"], self.g["Service"]["SyslogIdentifier"])

    def test_fichiers_ascii_et_fins_de_ligne_lf(self):
        for chemin in list(CHEMIN_UNITES.values()) + [CHEMIN_SOURCE]:
            data = lire_octets(chemin)
            self.assertTrue(all(b < 128 for b in data), chemin)
            self.assertNotIn(b"\r", data, "LF seulement (fichiers destines a Linux) : %s" % chemin)
            self.assertTrue(data.endswith(b"\n"))
            self.assertNotIn(b"\t", data, chemin)


# ------------------------------------------------------------------------------------------------ source verrouille
class TestSourceVerrouille(SansInfra):
    @classmethod
    def setUpClass(cls):
        cls.texte = lire_octets(CHEMIN_SOURCE).decode("ascii")
        cls.arbre = ast.parse(cls.texte)
        cls.parent = {}
        for n in ast.walk(cls.arbre):
            for c in ast.iter_child_nodes(n):
                cls.parent[c] = n

    def fonction_de(self, noeud):
        while noeud in self.parent:
            noeud = self.parent[noeud]
            if isinstance(noeud, (ast.FunctionDef, ast.ClassDef)):
                if isinstance(noeud, ast.FunctionDef):
                    classe = self.parent.get(noeud)
                    return ("%s.%s" % (classe.name, noeud.name)) if isinstance(classe, ast.ClassDef) else noeud.name
        return "<module>"

    def appels(self, motif_attr=None, motif_nom=None):
        sortie = []
        for n in ast.walk(self.arbre):
            if isinstance(n, ast.Call):
                f = n.func
                if isinstance(f, ast.Attribute) and motif_attr and re.fullmatch(motif_attr, f.attr):
                    sortie.append((self.fonction_de(n), n))
                elif isinstance(f, ast.Name) and motif_nom and re.fullmatch(motif_nom, f.id):
                    sortie.append((self.fonction_de(n), n))
        return sortie

    def test_imports_autorises(self):
        autorises = {"argparse", "hashlib", "json", "math", "os", "re", "secrets", "socket", "stat", "subprocess", "sys", "time",
                     "urllib.parse", "urllib.request", "collections", "signal", "hmac"}
        vus = set()
        for n in ast.walk(self.arbre):
            if isinstance(n, ast.Import):
                vus.update(a.name for a in n.names)
            elif isinstance(n, ast.ImportFrom):
                vus.add(n.module)
        self.assertLessEqual(vus, autorises, vus - autorises)
        for interdit in ("shutil", "glob", "tempfile", "ctypes", "pty", "multiprocessing", "threading", "pathlib", "importlib", "pickle", "eval", "exec"):
            self.assertNotIn(interdit, vus)

    def test_aucun_shell_aucune_commande_arbitraire(self):
        self.assertNotIn("shell=True", self.texte)
        self.assertEqual(self.appels(r"system|popen|spawn.*|exec.*|fork|startfile"), [])
        self.assertEqual(self.appels(r"Popen|call|check_call|check_output"), [])
        for f, _ in self.appels(r"run"):
            self.assertIn(f, ("MondeReel._cmd", "MondeReel.unites_transitoires"))
        # TOUT argument litteral des commandes executees (subprocess.run / _cmd) appartient a une liste fermee
        autorises = {"/usr/bin/findmnt", "-n", "-o", "FSTYPE,TARGET", "-T", "/usr/bin/fallocate", "-l", "--", "/usr/bin/systemctl", "show", "--no-pager",
                     "-p", "is-active", "list-units", "--all", "--no-legend", "--plain", "helios-diskfill-*"}
        vus = set()
        for f, n in self.appels(r"run|_cmd"):
            for sous in ast.walk(ast.Module(body=[ast.Expr(value=a) for a in n.args], type_ignores=[])):
                if isinstance(sous, ast.Constant) and isinstance(sous.value, str):
                    vus.add(sous.value)
        self.assertLessEqual(vus, autorises, vus - autorises)
        for appel_interdit in (r"posix_fallocate\(", r"\.truncate\(", r"shutil\.", r"rmtree", r"os\.system", r"\bPopen\b"):
            self.assertIsNone(re.search(appel_interdit, self.texte), appel_interdit)

    def test_les_seuls_binaires_executes(self):
        binaires = set(re.findall(r'"(/(?:usr|bin|sbin)/[A-Za-z0-9_./-]+)"', self.texte))
        self.assertEqual(binaires, {"/usr/bin/findmnt", "/usr/bin/fallocate", "/usr/bin/systemctl"})

    def test_suppression_seulement_par_nom_exact_ou_fichier_d_etat(self):
        suppressions = self.appels(r"unlink|remove|rmdir|removedirs|rmtree|rename|replace|truncate|ftruncate")
        autorises = {("MondeReel.liberer", "unlink"), ("MondeReel.supprimer", "unlink"), ("MondeReel.ecrire_json", "replace")}
        for f, n in suppressions:
            self.assertIn((f, n.func.attr), autorises, "primitive de suppression/renommage inattendue dans %s : %s" % (f, n.func.attr))
        liberer = [n for f, n in suppressions if f == "MondeReel.liberer"][0]
        self.assertEqual([k.arg for k in liberer.keywords], ["dir_fd"], "unlink uniquement par descripteur de repertoire (noms exacts)")
        # `supprimer` (fichiers d'etat) n'est appelee qu'avec les marqueurs
        for f, n in self.appels(r"supprimer"):
            self.assertEqual(f, "verifier")
            self.assertIsInstance(n.args[0], ast.Name)
            self.assertEqual(n.args[0].id, "marqueur")

    def test_liberer_valide_chaque_nom_avant_toute_suppression(self):
        classe = [c for c in self.arbre.body if isinstance(c, ast.ClassDef) and c.name == "MondeReel"][0]
        corps = [f for f in classe.body if isinstance(f, ast.FunctionDef) and f.name == "liberer"][0]
        src = ast.get_source_segment(self.texte, corps)
        self.assertLess(src.index("chemin_de(n)"), src.index("os.unlink"), "validation du motif AVANT toute suppression")
        self.assertIn("O_NOFOLLOW", src)
        self.assertIn("S_ISREG", src)
        self.assertIn("st_uid != 0", src)
        self.assertIn("st_nlink != 1", src)

    def test_variables_d_environnement(self):
        lectures = [n for n in ast.walk(self.arbre) if isinstance(n, ast.Attribute) and n.attr in ("environ", "getenv", "putenv", "environb")]
        self.assertEqual(len(lectures), 1)
        appels = self.appels(r"get")
        env = [n for f, n in appels if isinstance(n.func.value, ast.Attribute) and n.func.value.attr == "environ"]
        self.assertEqual(len(env), 1)
        self.assertEqual(env[0].args[0].value, "INVOCATION_ID", "seule INVOCATION_ID est lue (aucun parametre ne vient de l'environnement)")

    def test_reseau_uniquement_prometheus_en_lecture(self):
        urls = self.appels(r"urlopen")
        self.assertEqual([f for f, _ in urls], ["MondeReel.prom"])
        self.assertEqual(self.appels(r"connect|bind|listen|sendto|create_connection|socket"), [])
        self.assertEqual([f for f, _ in self.appels(r"gethostname")], ["MondeReel.hostname"])

    def test_sleep_et_horloge_uniquement_dans_le_monde(self):
        for f, _ in self.appels(r"sleep|time"):
            self.assertIn(f, ("MondeReel.dormir", "MondeReel.maintenant", "_utc"), f)

    def test_fallocate_seul_pour_allouer(self):
        r = [(f, n) for f, n in self.appels(r"_cmd") if f == "MondeReel.allouer"]
        self.assertEqual(len(r), 1)
        argv = r[0][1].args[0].elts
        self.assertEqual((argv[0].value, argv[1].value, argv[3].value), ("/usr/bin/fallocate", "-l", "--"))
        self.assertEqual([f for f, _ in self.appels(r"fallocate|posix_fallocate|ftruncate|write|writelines")],
                         ["MondeReel.ecrire_json"], "os.write n'est utilise que pour l'ecriture atomique des fichiers d'etat JSON")

    def test_les_seuils_sont_des_constantes_de_module_codees_en_dur(self):
        affectations = dict()
        for n in self.arbre.body:
            if isinstance(n, ast.Assign) and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name):
                affectations[n.targets[0].id] = n.value
        for cle in ("DETECTION_PCT", "BLOC", "MAX_FICHIERS", "OBSERVE_S", "EXPIRATION_S", "COOLDOWN_LOCAL_S"):
            self.assertIn(cle, affectations)
        self.assertNotIn("argparse.FileType", self.texte)
        # aucune option de ligne de commande ne porte sur un seuil
        for n in ast.walk(self.arbre):
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "add_argument":
                nom = n.args[0].value
                self.assertIn(nom, ("--a-blanc", "--json", "--confirmer"), nom)

    def test_orphelins_n_est_atteignable_que_par_la_ligne_de_commande(self):
        for f, n in self.appels(None, r"orphelins"):
            self.assertEqual(f, "principal", "nettoyer-orphelins ne doit JAMAIS etre appele automatiquement")
        for f, n in self.appels(None, r"plan_orphelins"):
            self.assertEqual(f, "orphelins")

    def test_aucun_nettoyage_automatique_au_demarrage(self):
        # verifier n'appelle ni liberer ni liberer_tout ni nettoyer ni orphelins
        verifier = [f for f in self.arbre.body if isinstance(f, ast.FunctionDef) and f.name == "verifier"][0]
        noms = set()
        for n in ast.walk(verifier):
            if isinstance(n, ast.Call):
                noms.add(n.func.id if isinstance(n.func, ast.Name) else n.func.attr)
        for interdit in ("liberer", "liberer_tout", "nettoyer", "orphelins", "allouer", "plan_orphelins"):
            self.assertNotIn(interdit, noms, "verifier ne supprime ni n'alloue JAMAIS")

    def test_nominal_le_service_ne_supprime_rien_les_seuls_appels_a_liberer_sont_securite(self):
        appelants = sorted(set(f for f, _ in self.appels(None, r"liberer_tout")))
        self.assertEqual(appelants, ["_declencher", "executer", "nettoyer", "orphelins"])
        for f, n in self.appels(r"liberer"):
            self.assertIn(f, ("liberer_tout",), "monde.liberer n'est appele que par liberer_tout (relecture apres chaque tentative)")


class TestDemarrageDirectSansArmement(SansInfra):
    def test_execstartpre_exige_agent_windows_hmac_et_aucun_orange(self):
        """Le CODE de l'attestation Windows est CONSERVE (mode optionnel « requis ») : contrat fige, HMAC, fraicheur, liste ORANGE, echec ferme."""
        src = lire_octets(CHEMIN_SOURCE).decode("ascii")
        self.assertIn("URL_AGENT_HOTE = \"http://192.168.148.1:19158/v1/preflight\"", src)
        self.assertIn("def preflight_hote(self):", src)
        self.assertIn("HOTE_WINDOWS", src)
        self.assertIn("agent_hote_preflight_autorisable(verdict, go, raisons)", src)
        self.assertIn("AGENT_HOTE_FRAICHEUR_S", src)
        self.assertIn("hmac.compare_digest", src)
        self.assertEqual(sorted(D.AGENT_HOTE_CODES_ORANGE_AUTORISES), ["E2E_INCIDENTS_DISQUE_ANCIENS", "GEL_HOTE_48H"])

    def test_l_attestation_windows_est_optionnelle_et_desactivee_par_defaut(self):
        """Decision de l'exploitant (17 nov. 2026) : `systemctl start` suffit DANS la VM, comme helios-cpuload.service ; aucune dependance de Windows."""
        self.assertIs(D.ATTESTATION_HOTE_REQUISE, False)
        affectations = [n for n in ast.parse(lire_octets(CHEMIN_SOURCE).decode("ascii")).body
                        if isinstance(n, ast.Assign) and len(n.targets) == 1 and getattr(n.targets[0], "id", "") == "ATTESTATION_HOTE_REQUISE"]
        self.assertEqual(len(affectations), 1, "une seule definition, constante de module codee en dur (ni argument, ni environnement)")
        self.assertIsInstance(affectations[0].value, ast.Constant)
        self.assertIs(affectations[0].value.value, False, "defaut : demarrage AUTONOME ; passer a True est une decision de version (SHA256SUMS, reinstallation)")

    def test_l_attestation_n_est_consultee_que_si_elle_est_requise(self):
        src = lire_octets(CHEMIN_SOURCE).decode("ascii")
        i = src.index("    def c_hote_windows():")
        j = src.index("att = monde.preflight_hote()", i)
        self.assertIn("if not ATTESTATION_HOTE_REQUISE:", src[i:j], "la consultation de l'agent est precedee du test de la constante")
        self.assertEqual(src.count("monde.preflight_hote()"), 1, "une seule consultation de l'agent")

    """Decision de l'exploitant (15 nov. 2026) : `sudo systemctl start helios-diskfill.service` suffit, SANS armement ; tous les controles internes
    restent actifs et sont executes a chaque demarrage. Cette classe VERROUILLE la consigne dans le code, les unites et les empreintes."""

    @classmethod
    def setUpClass(cls):
        cls.texte = lire_octets(CHEMIN_SOURCE).decode("ascii")
        cls.arbre = ast.parse(cls.texte)
        cls.verifier = [f for f in cls.arbre.body if isinstance(f, ast.FunctionDef) and f.name == "verifier"][0]
        cls.p = lire_unite(CHEMIN_UNITES[D.UNITE_PRINCIPALE])
        cls.g = lire_unite(CHEMIN_UNITES[D.UNITE_GARDE])

    def test_le_programme_n_a_plus_aucune_fonction_ni_constante_d_armement(self):
        interdites = {"armer", "valider_armement", "valider_charge", "valider_preflight", "_valider_commun", "sceau_de", "canonique", "renommer",
                      "preparer_run_dir"}
        definies = {n.name for n in ast.walk(self.arbre) if isinstance(n, ast.FunctionDef)}
        self.assertEqual(definies & interdites, set())
        noms = {n.id for n in ast.walk(self.arbre) if isinstance(n, ast.Name)} | {n.attr for n in ast.walk(self.arbre) if isinstance(n, ast.Attribute)}
        for nom in sorted(noms):
            self.assertIsNone(re.search(r"(?i)armement|^CLES_|TTL_M|ECART_HORLOGE", nom), "identifiant d'armement : %s" % nom)
        chaines = [n.value for n in ast.walk(self.arbre) if isinstance(n, ast.Constant) and isinstance(n.value, str)]
        self.assertNotIn("ARMEMENT", chaines, "aucun controle d'armement")
        for c in chaines:
            for motif in ("armement.json", "armer --charge", "armement.consomme", "armement.remplace"):
                self.assertNotIn(motif, c)

    def test_verifier_ne_lit_que_le_battement_de_la_garde_et_le_cooldown(self):
        lus = set()
        for n in ast.walk(self.verifier):
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr in ("lire_json", "lire_texte") and n.args:
                lus.add(n.args[0].id if isinstance(n.args[0], ast.Name) else ast.dump(n.args[0]))
        self.assertEqual(lus, {"F_BATTEMENT"}, "verifier ne lit AUCUN fichier d'armement ; le cooldown est lu par cooldown_restant_s")
        appeles = {n.func.attr for n in ast.walk(self.verifier) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
        self.assertNotIn("renommer", appeles)

    def test_liste_fermee_des_controles_internes_executes_a_chaque_demarrage(self):
        codes = []
        for n in ast.walk(self.verifier):
            if isinstance(n, ast.For) and isinstance(n.iter, ast.Tuple):
                for e in n.iter.elts:
                    if isinstance(e, ast.Tuple) and len(e.elts) == 2 and isinstance(e.elts[0], ast.Constant):
                        codes.append(e.elts[0].value)
        self.assertEqual(codes, ["INVOCATION", "IDENTITE", "INSTALLATION", "DOSSIERS", "RESIDUS", "SYSTEME", "INODES", "COOLDOWN", "PROMETHEUS", "GARDE", "HOTE_WINDOWS"],
                         "retirer un controle exige de changer ce verrou ET de l'expliquer ; l'armement n'en fait plus partie")

    def test_l_etat_du_lancement_est_cree_en_dernier_apres_tous_les_controles(self):
        src = ast.get_source_segment(self.texte, self.verifier)
        appel = src.rindex("tester(code, fonction)")                       # l'appel de la boucle (la definition de `tester` est plus haut)
        refus = src.index("if refus:")
        a_blanc = src.index("if a_blanc:", refus)                          # la sortie a blanc APRES le refus
        jeton = src.index("monde.nouveau_jeton()")
        etat = src.index("monde.ecrire_json(F_ETAT, etat)")
        self.assertLess(appel, refus, "tous les controles s'executent avant toute decision")
        self.assertLess(refus, a_blanc, "un seul controle en echec = refus, avant toute creation")
        self.assertLess(a_blanc, jeton, "a blanc : jamais de jeton ni d'etat")
        self.assertLess(jeton, etat)

    def test_les_empreintes_figees_des_unites_sont_celles_des_fichiers_du_depot(self):
        self.assertEqual(set(D.SHA_UNITES), {D.UNITE_PRINCIPALE, D.UNITE_GARDE})
        for unite, chemin in CHEMIN_UNITES.items():
            self.assertEqual(D.SHA_UNITES[unite], sha_lf(chemin),
                             "%s modifiee sans mettre a jour SHA_UNITES dans diskfill.py (puis regenerer SHA256SUMS) : le noeud REFUSERAIT de demarrer" % unite)

    def test_les_unites_documentent_le_demarrage_direct_et_ne_mentionnent_aucun_armement(self):
        principale = lire_octets(CHEMIN_UNITES[D.UNITE_PRINCIPALE]).decode("ascii")
        self.assertIn("UNE SEULE commande", principale)
        self.assertIn("COMME helios-cpuload.service", principale)
        self.assertIn("AUCUNE dependance de l'hote Windows", principale)
        self.assertIn("OPTIONNELLE", principale)
        self.assertIn("ATTESTATION_HOTE_REQUISE = False", principale)
        self.assertNotIn("OBLIGATOIRE a chaque lancement", principale, "l'unite ne doit plus pretendre que l'agent Windows est obligatoire")
        for nom, u in (("principale", self.p), ("garde", self.g)):
            for section in u.values():
                for cle, valeurs in section.items():
                    for v in valeurs:
                        self.assertNotRegex(cle + "=" + v, r"(?i)armement|armer", "%s : %s" % (nom, cle))
        self.assertEqual(self.p["Service"]["ExecStartPre"], ["+/usr/bin/python3 -I -B -u " + D.CHEMIN_SCRIPT + " verifier"], "aucun argument d'armement")

    def test_la_regle_des_inodes_est_celle_du_detecteur(self):
        detecteur = lire_octets(os.path.join(REPO, "camunda-direct", "detecteur", "detecter.py")).decode("utf-8")
        expression = '(1-(node_filesystem_files_free{mountpoint="/"}/node_filesystem_files{mountpoint="/"}))*100'
        m = re.search(r'"NODE_INODES_FULL",\s*\'' + re.escape(expression) + r' > (\d+)\'', detecteur)
        self.assertIsNotNone(m, "regle NODE_INODES_FULL du detecteur introuvable ou modifiee : revoir INODES_MAX_PCT")
        self.assertEqual(D.INODES_MAX_PCT, float(m.group(1)))
        self.assertEqual(D.usage_inodes_pct(1000, 100), (1.0 - 100 / 1000.0) * 100.0, "meme formule (1 - libres/total) x 100")

    def test_les_limites_de_duree_et_de_consommation_restent_dans_les_unites_validees(self):
        """Les limites que verifie l'empreinte figee : duree (RuntimeMaxSec) et consommation (MemoryMax, TasksMax) des deux unites."""
        self.assertEqual(self.p["Service"]["RuntimeMaxSec"], [str(D.EXPIRATION_S)])
        self.assertEqual(self.g["Service"]["RuntimeMaxSec"], [str(D.EXPIRATION_S + 60)])
        self.assertEqual((self.p["Service"]["MemoryMax"], self.p["Service"]["TasksMax"]), (["64M"], ["32"]))
        self.assertEqual((self.g["Service"]["MemoryMax"], self.g["Service"]["TasksMax"]), (["32M"], ["16"]))


# ------------------------------------------------------------------------------------------------ empreintes (SHA256SUMS)
def sha_lf(chemin):
    return hashlib.sha256(lire_octets(chemin).replace(b"\r\n", b"\n")).hexdigest()


class TestSommes(SansInfra):
    def test_sha256sums_du_depot_correspond_aux_fichiers(self):
        chemin = os.path.join(DIR_SVC, "SHA256SUMS")
        self.assertTrue(os.path.exists(chemin), "SHA256SUMS absent : python helios_diskfill_service.py --sommes")
        lignes = [l for l in lire_octets(chemin).decode("ascii").split("\n") if l.strip()]
        lus = {}
        for l in lignes:
            m = re.fullmatch(r"([0-9a-f]{64})  ([A-Za-z0-9._-]+)", l)
            self.assertIsNotNone(m, l)
            lus[m.group(2)] = m.group(1)
        self.assertEqual(sorted(lus), sorted(D.NOMS_SOT))
        for nom, sha in lus.items():
            self.assertEqual(sha, sha_lf(os.path.join(DIR_SVC, nom)), "%s : empreinte perimee (fichier modifie sans regenerer SHA256SUMS)" % nom)

    def test_les_fichiers_du_service_sont_exactement_ceux_dont_l_empreinte_est_verifiee(self):
        presents = sorted(f for f in os.listdir(DIR_SVC) if f not in ("SHA256SUMS", ".gitattributes", "__pycache__"))
        self.assertEqual(presents, sorted(D.NOMS_SOT))

    def test_gitattributes_local_impose_lf(self):
        chemin = os.path.join(DIR_SVC, ".gitattributes")
        self.assertTrue(os.path.exists(chemin), "core.autocrlf=true sur ce poste : sans .gitattributes les fichiers du noeud seraient convertis en CRLF")
        texte = lire_octets(chemin).decode("ascii")
        self.assertRegex(texte, r"(?m)^\*\s+text\s+eol=lf\s*$")


if __name__ == "__main__":
    unittest.main(verbosity=2)
