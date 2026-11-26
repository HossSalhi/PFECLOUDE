# -*- coding: utf-8 -*-
"""Tests du SERVICE LOCAL DISK de .158 (infra/helios-sot/diskfill/diskfill.py) sur un MONDE EMULE : DEMARRAGE DIRECT (aucun armement externe : tous
les controles internes sont executes a chaque demarrage), identite de .158, empreintes des unites, inodes, remplissage controle, observation,
garde independante, nettoyage, orphelins, etat.

Le monde emule a une horloge simulee (aucun sleep reel), un systeme de fichiers en memoire, un HELIOS simule (detection, puis liberation par le
VRAI disque_plan() du Bridge SoT : tout ce que le service cree doit etre accepte par le Bridge) et execute le VRAI code de la garde
(`garde_tour`) a chaque pas de temps simule.

AUCUN reseau, AUCUNE VM, AUCUN ssh, AUCUN subprocess, AUCUN sleep reel : chaque test echoue si le code tente d'y acceder.

Lancer : python -I -B test_helios_diskfill_service.py -v        (depuis scripts/helios-ops)
"""
import copy
import importlib.util
import json
import os
import signal
import socket
import subprocess
import sys
import tempfile
import time
import unittest
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


D = _charger("diskfill_mod", os.path.join(DIR_SVC, "diskfill.py"))
B = _charger("bridge_cible_diskfill", os.path.join(REPO, "infra", "helios-sot", "bridge", "bridge.py"))

MIO = D.MIO
GIO = D.GIO
GARDE_PID = 4242
KUBELET = ('apiVersion: kubelet.config.k8s.io/v1beta1\nkind: KubeletConfiguration\nevictionHard:\n    imagefs.available: "15%"\n'
           '    memory.available: "100Mi"\n    nodefs.available: "10%"\n    nodefs.inodesFree: "5%"\nevictionPressureTransitionPeriod: 0s\n')
# les unites ont l'empreinte VALIDEE figee dans le programme (SHA_UNITES) ; le programme lui-meme n'a pas d'empreinte de reference sur le noeud
SHAS = {"diskfill.py": "a" * 64, "helios-diskfill.service": D.SHA_UNITES["helios-diskfill.service"],
        "helios-diskguard.service": D.SHA_UNITES["helios-diskguard.service"]}
CHEMIN_PAR_NOM = {"diskfill.py": D.CHEMIN_SCRIPT, "helios-diskfill.service": D.CHEMIN_UNITE[D.UNITE_PRINCIPALE],
                  "helios-diskguard.service": D.CHEMIN_UNITE[D.UNITE_GARDE]}


def _interdit(*a, **k):
    raise AssertionError("ACCES REEL INTERDIT dans un test : %r" % (a[:1],))


class SansInfra(unittest.TestCase):
    """Fait echouer tout test dont le code tente un acces reseau, ssh, subprocess, sleep ou commande systeme."""

    def setUp(self):
        cibles = [(subprocess, "run"), (subprocess, "Popen"), (subprocess, "call"), (subprocess, "check_call"), (subprocess, "check_output"),
                  (urllib.request, "urlopen"), (socket, "create_connection"), (socket.socket, "connect"), (time, "sleep"), (os, "system")]
        self._patchs = [mock.patch.object(o, n, _interdit) for o, n in cibles]
        for p in self._patchs:
            p.start()
            self.addCleanup(p.stop)


# ------------------------------------------------------------------------------------------------ monde emule
class MondeEmule(object):
    """Noeud .158 en memoire (interface de MondeReel) : volume racine, repertoire exclusif, fichiers d'etat, systemd, Prometheus, HELIOS simule et
    garde independante (execution du VRAI garde_tour a chaque pas de temps)."""
    BS = 4096

    def __init__(self, usage0=68.6, taille=39036352 * 1024, helios="absent", delai_detection=40.0, delai_action=4.0, croissance_o_s=0.0,
                 garde=True, duree_alloc_s=0.0):
        self.t = 1800000000.0
        self.taille = taille
        self.base_dispo = int(taille * (1.0 - usage0 / 100.0))
        self.organique = 0.0
        self.croissance_o_s = croissance_o_s
        self.duree_alloc_s = duree_alloc_s
        self.fichiers = {}
        self.etrangers = {}
        self.dossier_present = True
        self.infos = {"/": dict(type="directory", uid=0, gid=0, mode="755", dev=64512, fstype="ext4", montage="/"),
                      D.DOSSIER: dict(type="directory", uid=0, gid=0, mode="700", dev=64512, fstype="ext4", montage="/"),
                      D.DOSSIER_ETAT: dict(type="directory", uid=0, gid=0, mode="700", dev=64512, fstype="ext4", montage="/"),
                      D.RUN_DIR: dict(type="directory", uid=0, gid=0, mode="700", dev=21, fstype="tmpfs", montage="/run")}
        self.textes = {}
        self.corrompus = set()
        self.hostname_ = "k8s-worker1"
        self.ips = {D.IP_CIBLE, "127.0.0.1"}
        self.boot = "boot-0001"
        self.inv = "inv-0001"
        self.script = D.CHEMIN_SCRIPT
        self.contenu_sha = dict(SHAS)
        self.unites = dict((u, {"LoadState": "loaded", "FragmentPath": D.CHEMIN_UNITE[u], "DropInPaths": "", "UnitFileState": "static",
                                "StateDirectoryMode": "0700", "Restart": "no", "NeedDaemonReload": "no"})
                           for u in (D.UNITE_PRINCIPALE, D.UNITE_GARDE))
        self.inodes_total, self.inodes_libres = 2490368, 2217595                 # .158 reel : 11 % utilises
        self.occupees = {}
        self.transitoires = []
        self.kubelet = KUBELET
        self.fallocate = True
        self.prom_illisible = set()
        self.prom_series = 1
        self.prom_usage_decalage = 0.0
        self.prom_incident_force = False
        self.helios = helios
        self.preflight_hote_resultat = {"verdict": "VERT", "go": True, "reasons": [{"level": "INFO", "code": "OK"}]}
        self.delai_detection, self.delai_action = delai_detection, delai_action
        self.appels = []
        self.usage_max = 0.0
        self.arret_a = None
        self.pannes = set()
        self.evenements = []
        self.garde_active = False
        self.garde_t0 = None
        self.garde_code = None
        self._garde_prochain = None
        self.journal_garde = []
        self.bridge_refus = None
        self.supprimes_par_helios = []
        self._t_depasse = None
        self._t_incident = None
        self.incident = False
        self._helios_fait = False
        self._en_garde = False
        self._compteur_jeton = 0
        if garde:
            self.demarrer_garde()

    def preflight_hote(self):
        self.appels.append(("preflight_hote",))
        if "agent_hote_illisible" in self.pannes:
            raise D.ErreurNoeud("agent Windows injoignable")
        return copy.deepcopy(self.preflight_hote_resultat)

    # --- etat du volume
    def dispo(self):
        utilises = sum((f["taille"] + self.BS - 1) // self.BS * self.BS for f in self.fichiers.values() if f.get("volume", True))
        return max(0, int(self.base_dispo - utilises - self.organique))

    def fs(self):
        return D.Fs(self.taille, self.dispo())

    def usage(self):
        return self.fs().usage

    def _maj_max(self):
        self.usage_max = max(self.usage_max, self.usage())

    # --- temps
    def maintenant(self):
        return self.t

    def dormir(self, secondes):
        self.appels.append(("dormir", secondes))
        restant = float(secondes)
        while restant > 1e-9:
            pas = min(1.0, restant)
            self.t += pas
            restant -= pas
            self._tick(pas)

    def _tick(self, pas):
        self.organique += self.croissance_o_s * pas
        for evt in [e for e in self.evenements if self.t >= e[0]]:
            self.evenements.remove(evt)
            evt[1](self)
        self._maj_max()
        self._helios()
        self._tour_garde()
        self._maj_max()

    def _helios(self):
        if self.helios == "absent":
            return
        if self.usage() > D.DETECTION_PCT:
            if self._t_depasse is None:
                self._t_depasse = self.t
            if not self.incident and self.t - self._t_depasse >= self.delai_detection:
                self.incident, self._t_incident = True, self.t
        if self.incident and self.helios == "resolve" and not self._helios_fait and self.t - self._t_incident >= self.delai_action:
            self._bridge_reclame()
        if self.incident and self._helios_fait and self.t - self._t_incident >= self.delai_action + 20:
            self.incident = False

    def _bridge_reclame(self):
        """HELIOS simule : le Bridge applique SON contrat (repertoire directory|root|700 puis VRAI disque_plan) ; sinon refus, rien n'est touche."""
        self._helios_fait = True
        info = self.infos[D.DOSSIER]
        lu = "%s|%s|%s" % ("directory" if info["type"] == "directory" else info["type"], "root" if info["uid"] == 0 else "autre", info["mode"])
        if not self.dossier_present or lu != "directory|root|700":
            self.bridge_refus = {"rejet": "UNSAFE_DIRECTORY", "lu": lu}
            return
        entrees = [{"type": e["type"], "proprio": "root" if e["uid"] == 0 else "autre", "mode": "644", "liens": e["nlink"], "taille": e["taille"],
                    "nom": n} for n, e in sorted(list(self.fichiers.items()) + list(self.etrangers.items()))]
        plan = B.disque_plan(entrees, 0)
        if plan["refus"]:
            self.bridge_refus = plan
            return
        for n in plan["fichiers"]:
            del self.fichiers[n]
        self.supprimes_par_helios = list(plan["fichiers"])

    def _tour_garde(self):
        if not self.garde_active:
            return
        if self._garde_prochain is not None and self.t < self._garde_prochain:
            return
        self._garde_prochain = self.t + D.GARDE_PAS_S
        self._en_garde = True                                  # la garde est un AUTRE processus : elle lit le vrai volume, pas la lecture du service
        try:
            code = D.garde_tour(self, self.garde_t0, self.journal_garde.append)
        finally:
            self._en_garde = False
        if code is not None:
            self.garde_active, self.garde_code = False, code

    def reinit_helios(self):
        self._helios_fait, self.incident, self._t_depasse, self._t_incident = False, False, None, None
        self.bridge_refus, self.supprimes_par_helios = None, []

    def demarrer_garde(self):
        self.garde_active, self.garde_t0, self._garde_prochain, self.garde_code = True, self.t, None, None
        code = D.garde_tour(self, self.garde_t0, self.journal_garde.append)
        if code is not None:
            self.garde_active, self.garde_code = False, code

    # --- interface MondeReel
    def installer_signaux(self):
        pass

    def arret_demande(self):
        return self.arret_a is not None and self.t >= self.arret_a

    def hostname(self):
        return self.hostname_

    def ips_locales(self):
        return set(self.ips)

    def boot_id(self):
        return self.boot

    def invocation_id(self):
        return self.inv

    def pid(self):
        return GARDE_PID

    def pid_vivant(self, pid):
        return pid == GARDE_PID and self.garde_active

    def chemin_script(self):
        return self.script

    def nouveau_jeton(self):
        self._compteur_jeton += 1
        return "%032x" % (0xabc0000 + self._compteur_jeton)

    def stats(self):
        if "stats_illisible" in self.pannes:
            raise D.ErreurNoeud("stat -f a echoue")
        return self.fs()

    def inodes(self):
        if "inodes_illisibles" in self.pannes:
            raise D.ErreurNoeud("statvfs a echoue")
        return self.inodes_total, self.inodes_libres

    def info_chemin(self, chemin):
        if "info_illisible" in self.pannes:
            raise D.ErreurNoeud("stat illisible")
        if chemin == D.DOSSIER and not self.dossier_present:
            return None
        info = self.infos.get(chemin)
        return dict(info) if info else None

    def contenu_dossier(self):
        if not self.dossier_present:
            return None
        return sorted(list(self.fichiers) + list(self.etrangers))

    def entrees_dossier(self):
        if not self.dossier_present:
            return None
        sortie = []
        for n, e in sorted(list(self.fichiers.items()) + list(self.etrangers.items())):
            sortie.append({"nom": n, "type": e["type"], "uid": e["uid"], "nlink": e["nlink"], "taille": e["taille"]})
        return sortie

    def fallocate_present(self):
        return self.fallocate

    def allouer(self, nom, octets):
        self.appels.append(("allouer", nom, octets))
        D.chemin_de(nom)
        if "allouer_echoue" in self.pannes:
            raise D.ErreurNoeud("fallocate a echoue (rc=1)")
        if nom in self.fichiers:
            raise D.ErreurNoeud("le fichier %s existe deja : refus" % nom)
        if self.duree_alloc_s:
            self.dormir(self.duree_alloc_s)
        self.fichiers[nom] = {"taille": octets, "uid": 0, "nlink": 1, "type": "f", "volume": "hors_volume" not in self.pannes}
        self._maj_max()

    def liberer(self, noms):
        self.appels.append(("liberer", list(noms)))
        for n in noms:
            D.chemin_de(n)
        if "liberer_refuse" in self.pannes:
            raise D.RefusSecurite("entree homonyme non conforme (refus de la supprimer) : %s" % sorted(noms)[0])
        supprimes = []
        for n in noms:
            e = self.fichiers.get(n)
            if e is None:
                continue
            if e["type"] != "f" or e["uid"] != 0 or e["nlink"] != 1:
                raise D.RefusSecurite("entree homonyme non conforme (refus de la supprimer) : %s" % n)
            del self.fichiers[n]
            supprimes.append(n)
        return supprimes

    def lire_texte(self, chemin, max_octets=65536):
        if chemin in self.corrompus:
            raise D.ErreurNoeud("lecture de %s impossible" % chemin)
        return "x" if chemin in self.textes else None

    def lire_json(self, chemin):
        if chemin in self.corrompus:
            raise D.ErreurNoeud("%s illisible (JSON invalide)" % chemin)
        v = self.textes.get(chemin)
        return copy.deepcopy(v)

    def ecrire_json(self, chemin, obj):
        self.appels.append(("ecrire_json", chemin))
        if ("ecriture:" + chemin) in self.pannes:
            raise D.ErreurNoeud("ecriture de %s impossible" % chemin)
        self.textes[chemin] = json.loads(json.dumps(obj))
        self.corrompus.discard(chemin)

    def supprimer(self, chemin):
        self.appels.append(("supprimer", chemin))
        self.textes.pop(chemin, None)

    def sha256(self, chemin):
        for nom, c in CHEMIN_PAR_NOM.items():
            if c == chemin:
                return self.contenu_sha.get(nom)
        return None

    def unite_proprietes(self, unite, proprietes):
        if "systemctl_ko" in self.pannes:
            raise D.ErreurNoeud("systemctl show %s a echoue" % unite)
        return dict(self.unites[unite])

    def unite_occupee(self, unite):
        if "occupee_illisible" in self.pannes:
            raise D.ErreurNoeud("systemctl is-active illisible")
        return bool(self.occupees.get(unite))

    def unites_transitoires(self):
        return None if "transitoires_illisibles" in self.pannes else list(self.transitoires)

    def config_kubelet(self):
        return self.kubelet

    def prom(self, requete):
        if "prom_totale" in self.pannes:
            return None
        if requete.startswith("count(node_filesystem_avail_bytes"):
            return None if "series" in self.prom_illisible else [float(self.prom_series)]
        if requete.startswith("(1-(node_filesystem_avail_bytes"):
            return None if "usage" in self.prom_illisible else [self.usage() + self.prom_usage_decalage]
        if requete.startswith("count(helios_incident"):
            return None if "incident" in self.prom_illisible else [1.0 if (self.incident or self.prom_incident_force) else 0.0]
        raise AssertionError("requete Prometheus inattendue : %r" % (requete,))

    # --- aides de test
    def appels_de(self, nom):
        return [a for a in self.appels if a[0] == nom]

    def noms_alloues(self):
        return [a[1] for a in self.appels if a[0] == "allouer"]


def cycle(m, sortie=None):
    """Imite systemd : ExecStartPre (verifier) ; si OK, ExecStart (run) ; ExecStopPost (nettoyer) dans TOUS les cas."""
    out = [] if sortie is None else sortie
    c1 = D.verifier(m, False, out.append)
    c2 = D.executer(m, out.append) if c1 == 0 else None
    c3 = D.nettoyer(m, out.append)
    return c1, c2, c3, out


def monde_pret(**kw):
    """Noeud .158 nominal, pret pour un DEMARRAGE DIRECT : aucun armement, aucun fichier prealable (il n'en existe plus)."""
    return MondeEmule(**kw)


# ------------------------------------------------------------------------------------------------ verification de demarrage
def attestation_requise(vrai=True):
    """Contexte : fixe ATTESTATION_HOTE_REQUISE (constante du programme du noeud) le temps d'un test. DEFAUT du programme : False = demarrage AUTONOME
    (decision de l'exploitant, 17 nov. 2026 : comme helios-cpuload.service). True = l'agent Windows DISK redevient obligatoire (echec ferme)."""
    return mock.patch.object(D, "ATTESTATION_HOTE_REQUISE", vrai)


class TestVerifierNominal(SansInfra):
    def test_demarrage_direct_cree_le_jeton_l_etat_et_le_cooldown_sans_aucun_armement(self):
        m = monde_pret()
        self.assertEqual([c for c in m.textes if "armement" in c], [], "le monde nominal ne contient AUCUN fichier d'armement")
        out = []
        self.assertEqual(D.verifier(m, False, out.append), 0, "\n".join(out))
        etat = m.textes[D.F_ETAT]
        self.assertTrue(D.valider_etat(etat))
        self.assertEqual(etat["phase"], "pret")
        self.assertEqual(etat["invocation_id"], "inv-0001")
        self.assertEqual(etat["echeance_epoch"] - etat["debut_epoch"], D.EXPIRATION_S)
        self.assertEqual(etat["noms"], D.noms_prevus(etat["jeton"]))
        for cle in ("armement_nonce", "orange", "preflight"):
            self.assertNotIn(cle, etat, "l'etat ne porte plus rien venant d'un armement")
        self.assertEqual(m.textes[D.F_COOLDOWN]["debut_epoch"], etat["debut_epoch"])
        self.assertEqual(m.appels_de("allouer"), [], "verifier n'alloue JAMAIS")
        self.assertEqual(m.appels_de("liberer"), [], "verifier ne supprime JAMAIS")
        self.assertIn("VERIFICATION OK", out[-1])
        self.assertIn("aucun armement requis", out[-1])
        self.assertFalse(any("] ARMEMENT" in l for l in out), "aucun controle d'armement n'existe plus")

    def test_tous_les_controles_internes_s_executent_a_chaque_demarrage(self):
        m = monde_pret()
        out = []
        self.assertEqual(D.verifier(m, False, out.append), 0, "\n".join(out))
        codes = [l.split("]", 1)[1].split(":", 1)[0].strip() for l in out if l.startswith("   [")]
        for attendu in ("INVOCATION", "IDENTITE", "INSTALLATION", "DOSSIERS", "RESIDUS", "SYSTEME", "INODES", "COOLDOWN", "PROMETHEUS", "GARDE", "HOTE_WINDOWS"):
            self.assertIn(attendu, codes, "le controle %s doit s'executer au demarrage direct" % attendu)
        self.assertNotIn("ARMEMENT", codes)

    def test_un_ancien_fichier_d_armement_n_est_ni_lu_ni_consomme_ni_requis(self):
        m = monde_pret()
        chemin = D.RUN_DIR + "/armement.json"
        ancien = {"version": 1, "nonce": "0" * 32, "sceau": "x"}
        m.textes[chemin] = copy.deepcopy(ancien)
        lus = []
        lire = m.lire_json
        m.lire_json = lambda c: (lus.append(c), lire(c))[1]
        self.assertEqual(D.verifier(m, False, [].append), 0)
        self.assertNotIn(chemin, lus, "un fichier d'armement n'est JAMAIS lu")
        self.assertEqual(m.textes[chemin], ancien, "ni consomme, ni modifie, ni supprime")

    def test_a_blanc_n_a_aucun_effet(self):
        m = monde_pret()
        avant = copy.deepcopy(m.textes)
        n_avant = len(m.appels)
        m.inv = None
        out = []
        self.assertEqual(D.verifier(m, True, out.append), 0, "\n".join(out))
        self.assertFalse(any(a[0] == "preflight_hote" for a in m.appels[n_avant:]), "a blanc ne contacte jamais Windows")
        self.assertEqual(m.textes, avant, "a blanc : rien n'est ecrit ni cree")
        self.assertEqual([a for a in m.appels[n_avant:] if a[0] in ("ecrire_json", "supprimer", "allouer", "liberer")], [])
        self.assertIn("A BLANC", out[-1])

    def test_a_blanc_traite_en_information_ce_que_systemd_cree_au_demarrage(self):
        m = monde_pret(garde=False)
        m.dossier_present = False
        self.assertEqual(D.verifier(m, True, [].append), 0)
        self.assertEqual(D.verifier(m, False, [].append), 2, "au demarrage reel, la garde et le repertoire doivent exister")

    def test_a_blanc_refuse_quand_un_controle_echoue(self):
        m = MondeEmule()
        m.hostname_ = "k8s-worker2"
        self.assertEqual(D.verifier(m, True, [].append), 2)

    def test_par_defaut_l_attestation_windows_est_desactivee_et_l_agent_n_est_jamais_contacte(self):
        """Decision de l'exploitant (17 nov. 2026) : comme helios-cpuload.service, le demarrage ne depend PAS de l'hote Windows."""
        self.assertIs(D.ATTESTATION_HOTE_REQUISE, False)
        cas = (("agent_absent", lambda m: m.pannes.add("agent_hote_illisible")),
               ("windows_rouge", lambda m: setattr(m, "preflight_hote_resultat",
                                                   {"verdict": "ROUGE", "go": False, "reasons": [{"level": "ROUGE", "code": "E2E_HOTE_RAM"}]})),
               ("windows_orange_non_autorise", lambda m: setattr(m, "preflight_hote_resultat",
                                                                 {"verdict": "ORANGE", "go": True, "reasons": [{"level": "ORANGE", "code": "NOTREADY_2H"}]})))
        for nom, panne in cas:
            with self.subTest(nom):
                m = monde_pret()
                panne(m)
                out = []
                self.assertEqual(D.verifier(m, False, out.append), 0, "\n".join(out))
                self.assertEqual(m.appels_de("preflight_hote"), [], "aucune requete vers Windows")
                self.assertTrue(any("[..] HOTE_WINDOWS" in l and "NON requise" in l for l in out), "\n".join(out))
                self.assertIn("attestation Windows non requise", out[-1])
                self.assertTrue(D.valider_etat(m.textes[D.F_ETAT]))

    def test_par_defaut_un_demarrage_complet_ne_contacte_jamais_windows(self):
        m = monde_pret(helios="resolve")
        m.pannes.add("agent_hote_illisible")                                              # agent absent : sans effet
        c1, c2, c3, out = cycle(m)
        self.assertEqual((c1, c2, c3), (0, 0, 0), "\n".join(out))
        self.assertEqual(m.appels_de("preflight_hote"), [])

    # ----- mode OPTIONNEL « requis » : le code de l'attestation est CONSERVE ; ces tests l'exercent avec ATTESTATION_HOTE_REQUISE = True
    def test_agent_windows_vert_avec_orange_est_refuse(self):
        with attestation_requise():
            m = monde_pret()
            m.preflight_hote_resultat = {"verdict": "VERT", "go": True, "reasons": [{"level": "ORANGE", "code": "GEL_HOTE_48H"}]}
            out = []
            self.assertEqual(D.verifier(m, False, out.append), 2, "\n".join(out))
            self.assertIn("HOTE_WINDOWS", "\n".join(out))
            self.assertNotIn(D.F_ETAT, m.textes)

    def test_agent_windows_orange_autorise_est_accepte_sans_armement(self):
        with attestation_requise():
            m = monde_pret()
            m.preflight_hote_resultat = {"verdict": "ORANGE", "go": True,
                                         "reasons": [{"level": "ORANGE", "code": "GEL_HOTE_48H"}]}
            out = []
            self.assertEqual(D.verifier(m, False, out.append), 0, "\n".join(out))
            self.assertIn("HOTE_WINDOWS", "\n".join(out))
            self.assertEqual(len(m.appels_de("preflight_hote")), 1, "l'agent est consulte une fois")
            self.assertTrue(D.valider_etat(m.textes[D.F_ETAT]), "verification acceptee : l'etat du lancement est cree")
            self.assertEqual(m.appels_de("allouer"), [], "la verification n'alloue jamais")
            self.assertEqual(m.appels_de("liberer"), [], "la verification ne supprime jamais")
            self.assertIn("attestation Windows authentifiee", out[-1])

    def test_agent_windows_orange_non_autorise_est_refuse(self):
        with attestation_requise():
            m = monde_pret()
            m.preflight_hote_resultat = {"verdict": "ORANGE", "go": True,
                                         "reasons": [{"level": "ORANGE", "code": "NOTREADY_2H"}]}
            out = []
            self.assertEqual(D.verifier(m, False, out.append), 2, "\n".join(out))
            self.assertIn("HOTE_WINDOWS", "\n".join(out))
            self.assertNotIn(D.F_ETAT, m.textes)

    def test_agent_windows_non_vert_refuse_avant_creation_etat_ou_cooldown(self):
        with attestation_requise():
            for verdict, niveau in (("ORANGE", "ORANGE"), ("ROUGE", "ROUGE")):
                m = monde_pret()
                m.preflight_hote_resultat = {"verdict": verdict, "go": False, "reasons": [{"level": niveau, "code": "TEST"}]}
                avant = copy.deepcopy(m.textes)
                out = []
                self.assertEqual(D.verifier(m, False, out.append), 2, "\n".join(out))
                self.assertIn("[KO] HOTE_WINDOWS", "\n".join(out))
                self.assertEqual(m.textes, avant, "refus Windows : aucun etat/cooldown cree")
                self.assertEqual(m.appels_de("allouer"), [])

    def test_agent_windows_un_rouge_dans_les_raisons_est_refuse_meme_avec_go_vrai_et_un_verdict_acceptable(self):
        with attestation_requise():
            for verdict in ("VERT", "ORANGE"):
                with self.subTest(verdict):
                    m = monde_pret()
                    m.preflight_hote_resultat = {"verdict": verdict, "go": True,
                                                 "reasons": [{"level": "ROUGE", "code": "E2E_HOTE_RAM"}, {"level": "ORANGE", "code": "GEL_HOTE_48H"}]}
                    out = []
                    self.assertEqual(D.verifier(m, False, out.append), 2, "\n".join(out))
                    self.assertIn("[KO] HOTE_WINDOWS", "\n".join(out))
                    self.assertNotIn(D.F_ETAT, m.textes)

    def test_a_blanc_ne_contacte_jamais_windows_meme_en_mode_requis(self):
        with attestation_requise():
            m = monde_pret()
            n_avant = len(m.appels)
            self.assertEqual(D.verifier(m, True, [].append), 0)
            self.assertFalse(any(a[0] == "preflight_hote" for a in m.appels[n_avant:]))

    def test_hors_unite_systemd_refus(self):
        m = monde_pret()
        m.inv = None
        out = []
        self.assertEqual(D.verifier(m, False, out.append), 2)
        self.assertTrue(any("INVOCATION" in l for l in out))
        self.assertNotIn(D.F_ETAT, m.textes)
        self.assertNotIn(D.F_COOLDOWN, m.textes)


REFUS = [
    # (nom, mutation appliquee au noeud nominal, controle attendu en echec)
    ("hostname_autre", lambda m: setattr(m, "hostname_", "k8s-worker2"), "IDENTITE"),
    ("hostname_fqdn_ok_mais_autre_nom", lambda m: setattr(m, "hostname_", "k8s-worker1x.lan"), "IDENTITE"),
    ("ip_absente", lambda m: setattr(m, "ips", {"127.0.0.1", "192.168.148.155"}), "IDENTITE"),
    ("script_hors_opt", lambda m: setattr(m, "script", "/tmp/diskfill.py"), "INSTALLATION"),
    ("sha_unite_principale_modifiee", lambda m: m.contenu_sha.__setitem__("helios-diskfill.service", "d" * 64), "INSTALLATION"),
    ("sha_unite_garde_modifiee", lambda m: m.contenu_sha.__setitem__("helios-diskguard.service", "d" * 64), "INSTALLATION"),
    ("sha_unite_principale_illisible", lambda m: m.contenu_sha.__setitem__("helios-diskfill.service", None), "INSTALLATION"),
    ("sha_unite_garde_illisible", lambda m: m.contenu_sha.__setitem__("helios-diskguard.service", None), "INSTALLATION"),
    ("sha_programme_illisible", lambda m: m.contenu_sha.__setitem__("diskfill.py", None), "INSTALLATION"),
    ("systemctl_ko", lambda m: m.pannes.add("systemctl_ko"), "INSTALLATION"),
    ("unite_non_chargee", lambda m: m.unites[D.UNITE_PRINCIPALE].update(LoadState="not-found"), "INSTALLATION"),
    ("fragment_autre", lambda m: m.unites[D.UNITE_PRINCIPALE].update(FragmentPath="/usr/lib/systemd/system/helios-diskfill.service"), "INSTALLATION"),
    ("drop_in_principale", lambda m: m.unites[D.UNITE_PRINCIPALE].update(DropInPaths="/etc/systemd/system/helios-diskfill.service.d/x.conf"),
     "INSTALLATION"),
    ("drop_in_garde", lambda m: m.unites[D.UNITE_GARDE].update(DropInPaths="/etc/systemd/system/helios-diskguard.service.d/x.conf"), "INSTALLATION"),
    ("unite_enabled", lambda m: m.unites[D.UNITE_PRINCIPALE].update(UnitFileState="enabled"), "INSTALLATION"),
    ("garde_enabled", lambda m: m.unites[D.UNITE_GARDE].update(UnitFileState="enabled"), "INSTALLATION"),
    ("state_directory_mode_0755_principale", lambda m: m.unites[D.UNITE_PRINCIPALE].update(StateDirectoryMode="0755"), "INSTALLATION"),
    ("state_directory_mode_0755_garde", lambda m: m.unites[D.UNITE_GARDE].update(StateDirectoryMode="0755"), "INSTALLATION"),
    ("state_directory_mode_absent", lambda m: m.unites[D.UNITE_PRINCIPALE].update(StateDirectoryMode=""), "INSTALLATION"),
    ("state_directory_mode_0770", lambda m: m.unites[D.UNITE_PRINCIPALE].update(StateDirectoryMode="0770"), "INSTALLATION"),
    ("restart_on_failure", lambda m: m.unites[D.UNITE_PRINCIPALE].update(Restart="on-failure"), "INSTALLATION"),
    ("unite_principale_a_recharger", lambda m: m.unites[D.UNITE_PRINCIPALE].update(NeedDaemonReload="yes"), "INSTALLATION"),
    ("unite_garde_a_recharger", lambda m: m.unites[D.UNITE_GARDE].update(NeedDaemonReload="yes"), "INSTALLATION"),
    ("need_daemon_reload_illisible", lambda m: m.unites[D.UNITE_PRINCIPALE].pop("NeedDaemonReload"), "INSTALLATION"),
    ("dossier_mode_755", lambda m: m.infos[D.DOSSIER].update(mode="755"), "DOSSIERS"),
    ("dossier_mode_750", lambda m: m.infos[D.DOSSIER].update(mode="750"), "DOSSIERS"),
    ("dossier_proprietaire", lambda m: m.infos[D.DOSSIER].update(uid=1000), "DOSSIERS"),
    ("dossier_groupe", lambda m: m.infos[D.DOSSIER].update(gid=1000), "DOSSIERS"),
    ("dossier_lien_symbolique", lambda m: m.infos[D.DOSSIER].update(type="symbolic link"), "DOSSIERS"),
    ("dossier_autre_volume", lambda m: m.infos[D.DOSSIER].update(dev=99), "DOSSIERS"),
    ("dossier_montage_autre", lambda m: m.infos[D.DOSSIER].update(montage="/var/lib"), "DOSSIERS"),
    ("dossier_autre_volume_monte_dessus", lambda m: m.infos[D.DOSSIER].update(montage=D.DOSSIER, dev=99), "DOSSIERS"),
    ("dossier_absent", lambda m: setattr(m, "dossier_present", False), "DOSSIERS"),
    ("dossier_etat_755", lambda m: m.infos[D.DOSSIER_ETAT].update(mode="755"), "DOSSIERS"),
    ("run_dir_755", lambda m: m.infos[D.RUN_DIR].update(mode="755"), "DOSSIERS"),
    ("orphelin_fichier_du_scenario", lambda m: m.fichiers.__setitem__(D.nom_fichier("e" * 32, 1), {"taille": 512 * MIO, "uid": 0, "nlink": 1, "type": "f"}),
     "DOSSIERS"),
    ("entree_etrangere", lambda m: m.etrangers.__setitem__("README", {"taille": 12, "uid": 0, "nlink": 1, "type": "f"}), "DOSSIERS"),
    ("unite_du_lanceur_residuelle", lambda m: setattr(m, "transitoires", ["helios-diskfill-garde-" + "a" * 32 + ".service"]), "RESIDUS"),
    ("unites_du_lanceur_illisibles", lambda m: m.pannes.add("transitoires_illisibles"), "RESIDUS"),
    ("occupation_80_5", lambda m: setattr(m, "base_dispo", int(m.taille * (1 - 0.805))), "SYSTEME"),
    ("occupation_78_recuperation_impossible", lambda m: setattr(m, "base_dispo", int(m.taille * (1 - 0.78))), "SYSTEME"),
    ("occupation_83_5", lambda m: setattr(m, "base_dispo", int(m.taille * (1 - 0.835))), "SYSTEME"),
    ("occupation_trop_basse_plan_superieur_a_6_gio", lambda m: setattr(m, "base_dispo", int(m.taille * (1 - 0.50))), "SYSTEME"),
    ("fs_racine_btrfs", lambda m: m.infos["/"].update(fstype="btrfs"), "SYSTEME"),
    ("fallocate_absent", lambda m: setattr(m, "fallocate", False), "SYSTEME"),
    ("kubelet_illisible", lambda m: setattr(m, "kubelet", None), "SYSTEME"),
    ("kubelet_gc_a_80", lambda m: setattr(m, "kubelet", KUBELET + "imageGCHighThresholdPercent: 80\n"), "SYSTEME"),
    ("stats_illisibles", lambda m: m.pannes.add("stats_illisible"), "SYSTEME"),
    ("inodes_85_1_pct_utilises", lambda m: (setattr(m, "inodes_total", 1000000), setattr(m, "inodes_libres", 149000)), "INODES"),
    ("inodes_97_pct_utilises", lambda m: (setattr(m, "inodes_total", 1000000), setattr(m, "inodes_libres", 30000)), "INODES"),
    ("inodes_libres_sous_le_plancher_absolu", lambda m: (setattr(m, "inodes_total", 2000), setattr(m, "inodes_libres", D.INODES_LIBRES_MIN - 1)), "INODES"),
    ("inodes_illisibles", lambda m: m.pannes.add("inodes_illisibles"), "INODES"),
    ("inodes_total_nul", lambda m: setattr(m, "inodes_total", 0), "INODES"),
    ("inodes_libres_superieurs_au_total", lambda m: setattr(m, "inodes_libres", m.inodes_total + 1), "INODES"),
    ("cooldown_1_min", lambda m: m.textes.__setitem__(D.F_COOLDOWN, {"debut_epoch": m.t - 60}), "COOLDOWN"),
    ("cooldown_une_seconde_avant_la_fin", lambda m: m.textes.__setitem__(D.F_COOLDOWN, {"debut_epoch": m.t - (D.COOLDOWN_LOCAL_S - 1)}), "COOLDOWN"),
    ("cooldown_altere", lambda m: m.textes.__setitem__(D.F_COOLDOWN, ["n'importe quoi"]), "COOLDOWN"),
    ("cooldown_illisible", lambda m: m.corrompus.add(D.F_COOLDOWN), "COOLDOWN"),
    ("cooldown_dans_le_futur", lambda m: m.textes.__setitem__(D.F_COOLDOWN, {"debut_epoch": m.t + 300}), "COOLDOWN"),
    ("prometheus_illisible", lambda m: m.pannes.add("prom_totale"), "PROMETHEUS"),
    ("prometheus_deux_series", lambda m: setattr(m, "prom_series", 2), "PROMETHEUS"),
    ("prometheus_ecart_3_points", lambda m: setattr(m, "prom_usage_decalage", 3.0), "PROMETHEUS"),
    ("incident_actif", lambda m: setattr(m, "prom_incident_force", True), "PROMETHEUS"),
    ("incident_illisible", lambda m: m.prom_illisible.add("incident"), "PROMETHEUS"),
    ("garde_absente", lambda m: setattr(m, "garde_active", False), "GARDE"),
    ("garde_battement_perime", lambda m: (setattr(m, "garde_active", False), m.textes[D.F_BATTEMENT].update(epoch=m.t - 60)), "GARDE"),
    ("garde_deja_affectee", lambda m: m.textes[D.F_BATTEMENT].update(jeton="f" * 32), "GARDE"),
]

# Refus propres au mode OPTIONNEL « attestation Windows requise » (ATTESTATION_HOTE_REQUISE = True) : en mode par defaut (autonome) ils ne refusent PAS.
REFUS_AGENT = [
    ("preflight_hote_orange", lambda m: m.preflight_hote_resultat.update(verdict="ORANGE", go=False, reasons=[{"level": "ORANGE", "code": "GEL_HOTE_48H"}]), "HOTE_WINDOWS"),
    ("preflight_hote_rouge", lambda m: m.preflight_hote_resultat.update(verdict="ROUGE", go=False, reasons=[{"level": "ROUGE", "code": "E2E_HOTE_RAM"}]), "HOTE_WINDOWS"),
    ("agent_hote_illisible", lambda m: m.pannes.add("agent_hote_illisible"), "HOTE_WINDOWS"),
]


class TestVerifierRefus(SansInfra):
    def test_chaque_refus_ne_cree_rien_n_alloue_rien(self):
        for nom, mutation, controle in REFUS:
            with self.subTest(nom):
                m = monde_pret()
                mutation(m)
                avant_cooldown = copy.deepcopy(m.textes.get(D.F_COOLDOWN))
                out = []
                code = D.verifier(m, False, out.append)
                self.assertEqual(code, 2, "%s : refus attendu\n%s" % (nom, "\n".join(out)))
                self.assertTrue(any(("[KO] %s" % controle) in l for l in out), "%s : controle %s attendu en echec\n%s" % (nom, controle, "\n".join(out)))
                self.assertIn("REFUS", out[-1])
                self.assertIn("RESULTAT : verdict=REFUS code=2 etape=verifier", "\n".join(out))
                self.assertNotIn(D.F_ETAT, m.textes, "%s : aucun etat ne doit etre cree" % nom)
                self.assertEqual(m.textes.get(D.F_COOLDOWN), avant_cooldown, "%s : le cooldown ne doit pas etre touche" % nom)
                self.assertEqual([a for a in m.appels if a[0] in ("allouer", "liberer")], [], "%s : aucune allocation, aucune suppression" % nom)

    def test_en_mode_requis_chaque_refus_de_l_agent_ne_cree_rien_n_alloue_rien(self):
        with attestation_requise():
            for nom, mutation, controle in REFUS_AGENT:
                with self.subTest(nom):
                    m = monde_pret()
                    mutation(m)
                    avant = copy.deepcopy(m.textes)
                    out = []
                    self.assertEqual(D.verifier(m, False, out.append), 2, "%s : refus attendu\n%s" % (nom, "\n".join(out)))
                    self.assertTrue(any(("[KO] %s" % controle) in l for l in out), "\n".join(out))
                    self.assertIn("RESULTAT : verdict=REFUS code=2 etape=verifier", "\n".join(out))
                    self.assertEqual(m.textes, avant, "aucun etat, aucun cooldown")
                    m2 = monde_pret()
                    mutation(m2)
                    c1, c2, c3, _ = cycle(m2)
                    self.assertEqual((c1, c2), (2, None), nom)
                    self.assertEqual(m2.appels_de("allouer"), [], nom)

    def test_en_mode_par_defaut_les_conditions_de_l_agent_ne_refusent_rien(self):
        for nom, mutation, _controle in REFUS_AGENT:
            with self.subTest(nom):
                m = monde_pret()
                mutation(m)
                out = []
                self.assertEqual(D.verifier(m, False, out.append), 0, "%s doit etre IGNORE en mode autonome\n%s" % (nom, "\n".join(out)))
                self.assertEqual(m.appels_de("preflight_hote"), [])

    def test_une_exception_inattendue_dans_un_controle_est_un_refus_jamais_un_ok(self):
        """Echec SUR : une erreur imprevue (ni ErreurNoeud ni RefusSecurite) dans un controle refuse le demarrage et ne cree rien."""
        def casse(*a, **k):
            raise RuntimeError("panne imprevue")
        for controle, methode in (("IDENTITE", "ips_locales"), ("SYSTEME", "config_kubelet"), ("RESIDUS", "unites_transitoires"), ("INODES", "inodes"),
                                  ("PROMETHEUS", "prom")):
            with self.subTest(controle):
                m = monde_pret()
                setattr(m, methode, casse)
                out = []
                self.assertEqual(D.verifier(m, False, out.append), 2, "\n".join(out))
                self.assertTrue(any(l.lstrip().startswith("[KO] %s" % controle) and "lecture impossible (RuntimeError)" in l for l in out), "\n".join(out))
                self.assertNotIn(D.F_ETAT, m.textes)
                self.assertEqual(m.appels_de("allouer"), [])

    def test_le_refus_pour_orphelins_indique_le_nettoyage_manuel_et_ne_supprime_rien(self):
        m = monde_pret()
        m.fichiers[D.nom_fichier("e" * 32, 1)] = {"taille": 512 * MIO, "uid": 0, "nlink": 1, "type": "f"}
        out = []
        self.assertEqual(D.verifier(m, False, out.append), 2)
        texte = "\n".join(out)
        self.assertIn("ORPHELINS", texte)
        self.assertIn("nettoyer-orphelins", texte)
        self.assertIn("aucun nettoyage automatique", texte)
        self.assertEqual(m.appels_de("liberer"), [])
        self.assertEqual(len(m.fichiers), 1)

    def test_un_cooldown_ecoule_est_accepte(self):
        m = monde_pret()
        m.textes[D.F_COOLDOWN] = {"debut_epoch": m.t - 31 * 60}
        self.assertEqual(D.verifier(m, False, [].append), 0)
        m = monde_pret()
        m.textes[D.F_COOLDOWN] = {"debut_epoch": m.t - D.COOLDOWN_LOCAL_S}
        self.assertEqual(D.verifier(m, False, [].append), 0)

    def test_un_refus_ne_depend_pas_d_un_seul_controle(self):
        m = monde_pret()
        m.hostname_ = "autre"
        m.fallocate = False
        m.inodes_libres = 10
        out = []
        D.verifier(m, False, out.append)
        refus = out[-1]
        self.assertIn("IDENTITE", refus)
        self.assertIn("SYSTEME", refus)
        self.assertIn("INODES", refus)

    def test_le_refus_est_toujours_le_dernier_mot_aucun_demarrage_partiel(self):
        """Quel que soit le controle en echec, `verifier` rend 2 sans avoir ecrit l'etat : `run` ne peut pas demarrer (aucun etat de CE lancement)."""
        for nom, mutation, _controle in REFUS:
            with self.subTest(nom):
                m = monde_pret()
                mutation(m)
                c1, c2, c3, _out = cycle(m)
                self.assertEqual((c1, c2), (2, None), nom)
                self.assertEqual(m.appels_de("allouer"), [], nom)


class TestRepertoireMonteEnBind(SansInfra):
    """CONSTATE sur .158 (systemd 255) : dans l'espace de noms de l'unite, le StateDirectory est un bind mount de lui-meme ; `findmnt` le rapporte
    comme point de montage. Il est accepte (meme peripherique que /, ext4) ; tout autre montage ou volume reste refuse."""

    def test_un_repertoire_monte_en_bind_sur_lui_meme_est_accepte(self):
        m = monde_pret()
        m.infos[D.DOSSIER].update(montage=D.DOSSIER)
        out = []
        self.assertEqual(D.verifier(m, False, out.append), 0, "\n".join(out))

    def test_un_autre_point_de_montage_ou_un_autre_volume_reste_refuse(self):
        for sur in ({"montage": "/var/lib"}, {"montage": "/", "dev": 99}, {"montage": D.DOSSIER, "dev": 99}, {"montage": D.DOSSIER, "fstype": "tmpfs"},
                    {"montage": D.DOSSIER_ETAT}):
            m = monde_pret()
            m.infos[D.DOSSIER].update(sur)
            self.assertEqual(D.verifier(m, False, [].append), 2, sur)
            self.assertEqual(m.appels_de("allouer"), [])


class TestIdentite(SansInfra):
    """Contrainte explicite : la cible est EXACTEMENT le noeud .158 (nom d'hote ET adresse IP)."""

    def test_identite_nom_court_insensible_a_la_casse_et_au_domaine(self):
        for nom in ("k8s-worker1", "K8S-WORKER1", "k8s-worker1.lan", "k8s-worker1.orange.tn"):
            m = monde_pret()
            m.hostname_ = nom
            self.assertEqual(D.verifier(m, False, [].append), 0, nom)

    def test_identite_les_autres_machines_sont_refusees(self):
        for nom in ("k8s-worker2", "k8s-master", "monitoring", "k8s-aicamunda", "k8s-lb2", ""):
            m = monde_pret()
            m.hostname_ = nom
            self.assertEqual(D.verifier(m, False, [].append), 2, nom)

    def test_identite_l_ip_de_158_doit_etre_presente(self):
        m = monde_pret()
        m.ips = {"192.168.148.155"}
        self.assertEqual(D.verifier(m, False, [].append), 2)

    def test_identite_le_bon_nom_avec_la_mauvaise_ip_ou_l_inverse_est_refuse(self):
        for nom, ips in (("k8s-worker1", {"127.0.0.1", "192.168.148.153"}), ("k8s-master", {"127.0.0.1", D.IP_CIBLE})):
            m = monde_pret()
            m.hostname_, m.ips = nom, set(ips)
            out = []
            self.assertEqual(D.verifier(m, False, out.append), 2, nom)
            self.assertEqual(m.appels_de("allouer"), [])
            self.assertTrue(any("[KO] IDENTITE" in l for l in out))

    def test_les_constantes_de_cible_sont_figees(self):
        self.assertEqual((D.NOM_CIBLE, D.IP_CIBLE), ("k8s-worker1", "192.168.148.158"))


class TestEmpreintesDesUnites(SansInfra):
    """L'armement liait les fichiers installes a la source de verite (SHA-256 verifie au pre-vol de l'hote). Sans armement, cette liaison est faite PAR LE
    NOEUD : les empreintes des deux unites validees sont figees dans le programme ; l'unite chargee doit aussi etre celle du fichier."""

    def test_les_deux_unites_ont_une_empreinte_figee_valide(self):
        self.assertEqual(set(D.SHA_UNITES), {D.UNITE_PRINCIPALE, D.UNITE_GARDE})
        for nom, sha in D.SHA_UNITES.items():
            self.assertRegex(sha, r"^[0-9a-f]{64}$", nom)

    def test_une_unite_conforme_est_acceptee(self):
        m = monde_pret()
        self.assertEqual(m.contenu_sha["helios-diskfill.service"], D.SHA_UNITES[D.UNITE_PRINCIPALE])
        out = []
        self.assertEqual(D.verifier(m, False, out.append), 0, "\n".join(out))
        self.assertTrue(any("identique a l'unite validee" in l for l in out))

    def test_chaque_octet_modifie_dans_une_unite_est_refuse_sans_rien_creer(self):
        for unite in (D.UNITE_PRINCIPALE, D.UNITE_GARDE):
            for nouveau in ("0" * 64, "f" * 64, D.SHA_UNITES[unite][:-1] + ("0" if D.SHA_UNITES[unite][-1] != "0" else "1")):
                m = monde_pret()
                m.contenu_sha[unite] = nouveau
                out = []
                self.assertEqual(D.verifier(m, False, out.append), 2, (unite, nouveau))
                self.assertTrue(any("EMPREINTE DIFFERENTE" in l for l in out), (unite, nouveau))
                self.assertNotIn(D.F_ETAT, m.textes)
                self.assertEqual(m.appels_de("allouer"), [])

    def test_une_unite_a_recharger_est_refusee_meme_si_le_fichier_est_conforme(self):
        for unite in (D.UNITE_PRINCIPALE, D.UNITE_GARDE):
            m = monde_pret()
            m.unites[unite]["NeedDaemonReload"] = "yes"
            out = []
            self.assertEqual(D.verifier(m, False, out.append), 2, unite)
            self.assertTrue(any("NeedDaemonReload=yes" in l for l in out))

    def test_les_proprietes_lues_incluent_need_daemon_reload(self):
        self.assertIn("NeedDaemonReload", D.PROPRIETES_UNITE)


class TestInodes(SansInfra):
    """Contrainte explicite : refuser si les inodes sont insuffisants. Regle : celle de NODE_INODES_FULL (detecteur) - refus des 85 % - plus un plancher
    absolu d'inodes libres."""

    def test_constantes(self):
        self.assertEqual((D.INODES_MAX_PCT, D.INODES_LIBRES_MIN), (85.0, 1024))

    def test_noeud_reel_11_pct_accepte(self):
        m = monde_pret()
        out = []
        self.assertEqual(D.verifier(m, False, out.append), 0, "\n".join(out))
        self.assertTrue(any("[OK] INODES" in l and "11.0 %" in l for l in out), "\n".join(out))

    def test_juste_sous_et_juste_au_dessus_du_seuil(self):
        for libres, attendu in ((151000, 0), (150001, 0), (149000, 2), (100000, 2)):
            m = monde_pret()
            m.inodes_total, m.inodes_libres = 1000000, libres
            self.assertEqual(D.verifier(m, False, [].append), attendu, libres)

    def test_plancher_absolu_d_inodes_libres(self):
        """Petit systeme de fichiers (2 000 inodes) : le pourcentage reste bas (< 85 %) mais le plancher absolu refuse sous INODES_LIBRES_MIN libres."""
        for libres, attendu in ((D.INODES_LIBRES_MIN, 0), (D.INODES_LIBRES_MIN - 1, 2), (0, 2)):
            m = monde_pret()
            m.inodes_total, m.inodes_libres = 2000, libres
            self.assertEqual(D.verifier(m, False, [].append), attendu, libres)

    def test_mesure_incoherente_ou_illisible_est_un_refus_jamais_une_valeur_devinee(self):
        for total, libres in ((0, 0), (-5, 3), (100, 101), (100, -1), (True, 5), (10.5, 5), (None, None)):
            with self.assertRaises(D.ErreurNoeud, msg=(total, libres)):
                D.usage_inodes_pct(total, libres)
        m = monde_pret()
        m.pannes.add("inodes_illisibles")
        out = []
        self.assertEqual(D.verifier(m, False, out.append), 2)
        self.assertTrue(any("[KO] INODES" in l for l in out))

    def test_formule_identique_a_la_regle_du_detecteur(self):
        """(1 - libres/total) x 100 : celle de NODE_INODES_FULL (detecter.py)."""
        self.assertAlmostEqual(D.usage_inodes_pct(2490368, 2217595), (1 - 2217595 / 2490368.0) * 100.0)
        self.assertAlmostEqual(D.usage_inodes_pct(100, 50), 50.0)

    def test_le_noeud_reel_lit_statvfs(self):
        appels = []

        class Sv(object):
            f_files, f_ffree = 2490368, 2217595
        with mock.patch.object(D.os, "statvfs", lambda c: (appels.append(c), Sv())[1], create=True):
            self.assertEqual(D.MondeReel().inodes(), (2490368, 2217595))
        self.assertEqual(appels, ["/"], "les inodes sont ceux de la racine /")


class TestCycleComplet(SansInfra):
    def verifier_invariants(self, m, c1, c2, c3, out):
        """Invariants valables pour TOUT scenario : jamais > 82 % par les allocations du service, <= 16 fichiers, <= 6 Gio, un seul jeton,
        noms exacts, aucun fichier restant, aucune entree etrangere touchee."""
        noms = m.noms_alloues()
        self.assertLessEqual(len(noms), D.MAX_FICHIERS)
        self.assertLessEqual(sum(a[2] for a in m.appels_de("allouer")), D.MAX_TOTAL)
        self.assertEqual(len(set(D.RE_FICHIER.fullmatch(n).group(1) for n in noms)), 1 if noms else 0)
        self.assertEqual(sorted(m.fichiers), [], "aucun fichier de test ne doit rester : %r" % sorted(m.fichiers))

    def test_nominal_helios_libere_le_service_ne_supprime_rien(self):
        m = monde_pret(helios="resolve")
        c1, c2, c3, out = cycle(m)
        texte = "\n".join(out)
        self.assertEqual((c1, c2, c3), (0, 0, 0), texte)
        self.assertIn("BILAN : OK", texte)
        self.assertEqual(m.appels_de("liberer"), [], "NOMINAL : le service ne supprime RIEN, seul HELIOS libere\n" + texte)
        self.assertIsNone(m.bridge_refus, "le Bridge doit accepter exactement ce que le service cree")
        self.assertEqual(len(m.supprimes_par_helios), 10)
        self.assertEqual(sorted(m.supprimes_par_helios), sorted(m.noms_alloues()))
        self.assertLessEqual(m.usage_max, D.PLAFOND_PCT, "jamais plus de 82 %")
        self.assertGreaterEqual(m.usage_max, D.CIBLE_PCT)
        self.assertLessEqual(m.usage(), D.RECUPERATION_PCT)
        self.verifier_invariants(m, c1, c2, c3, out)
        etat = m.textes[D.F_ETAT]
        self.assertEqual((etat["phase"], etat["verdict"]), ("terminee", "OK"))
        self.assertIn("incident HELIOS visible", texte)
        self.assertIn("retour a", texte)
        self.assertIn("attribution a HELIOS", texte)

    def test_nominal_10_blocs_de_512_mo_sauf_le_dernier_conformes_au_plan_theorique(self):
        m = monde_pret(helios="resolve")
        plan = D.planifier(m.fs())
        cycle(m)
        tailles = [a[2] for a in m.appels_de("allouer")]
        self.assertEqual(tailles, plan, "les blocs reels (mesures fraiches) suivent le plan theorique")
        self.assertEqual(tailles[:-1], [512 * MIO] * (len(tailles) - 1))
        self.assertLessEqual(tailles[-1], 512 * MIO)
        self.assertEqual(len(tailles), 10)
        self.assertAlmostEqual(m.usage_max, D.CIBLE_PCT, delta=0.01)

    def test_la_garde_est_verifiee_avant_la_premiere_allocation(self):
        m = monde_pret(helios="resolve")
        premier = {}
        vrai = m.allouer

        def allouer_espion(nom, octets):
            premier.setdefault("battement", copy.deepcopy(m.textes.get(D.F_BATTEMENT)))
            premier.setdefault("t", m.t)
            return vrai(nom, octets)
        m.allouer = allouer_espion
        out = []
        D.verifier(m, False, out.append)
        D.executer(m, out.append)
        jeton = m.textes[D.F_ETAT]["jeton"]
        self.assertIn("garde independante armee pour ce jeton", "\n".join(out))
        self.assertEqual(premier["battement"]["jeton"], jeton, "au moment de la 1re allocation la garde a deja adopte CE jeton")
        self.assertLessEqual(premier["t"] - premier["battement"]["epoch"], D.BATTEMENT_MAX_S, "avec un battement frais")

    def test_helios_absent_aucune_detection_le_service_libere_par_securite(self):
        m = monde_pret(helios="absent")
        c1, c2, c3, out = cycle(m)
        texte = "\n".join(out)
        self.assertEqual((c1, c2), (0, D.CODES_SORTIE["AUCUNE_DETECTION"]), texte)
        self.assertGreaterEqual(len(m.appels_de("liberer")), 1, "sans HELIOS le service libere LUI-MEME (securite)")
        self.assertLessEqual(m.usage_max, D.PLAFOND_PCT)
        self.assertLessEqual(m.usage(), D.RECUPERATION_PCT)
        self.verifier_invariants(m, c1, c2, c3, out)
        self.assertEqual(m.textes[D.F_ETAT]["verdict"], "AUCUNE_DETECTION")

    def test_helios_detecte_sans_remedier_detection_seule(self):
        m = monde_pret(helios="detect")
        c1, c2, c3, out = cycle(m)
        self.assertEqual(c2, D.CODES_SORTIE["DETECTION_SEULE"], "\n".join(out))
        self.verifier_invariants(m, c1, c2, c3, out)

    def test_le_bridge_refuse_un_repertoire_en_0755_et_le_service_aurait_refuse_avant(self):
        # 1) le Bridge simule applique SON contrat : 0755 => UNSAFE_DIRECTORY, rien n'est supprime
        m = MondeEmule(helios="resolve")
        m.infos[D.DOSSIER]["mode"] = "755"
        for i in range(1, 4):
            m.fichiers[D.nom_fichier("e" * 32, i)] = {"taille": 512 * MIO, "uid": 0, "nlink": 1, "type": "f"}
        m._bridge_reclame()
        self.assertEqual(m.bridge_refus["rejet"], "UNSAFE_DIRECTORY")
        self.assertEqual(len(m.fichiers), 3)
        # 2) le service refuse AVANT toute allocation si systemd n'a pas donne 0700
        m = monde_pret(helios="resolve")
        m.infos[D.DOSSIER]["mode"] = "755"
        out = []
        c1, c2, c3, out = cycle(m)
        self.assertEqual(c1, 2)
        self.assertEqual(m.appels_de("allouer"), [])
        self.assertTrue(any("0700" in l or "700" in l for l in out))

    def test_croissance_organique_pendant_le_remplissage_urgence_a_83(self):
        m = monde_pret(helios="resolve", croissance_o_s=30 * MIO)
        c1, c2, c3, out = cycle(m)
        self.assertEqual(c2, D.CODES_SORTIE["URGENCE"], "\n".join(out))
        self.assertIn("ARRET D'URGENCE", "\n".join(out))
        self.assertEqual(sorted(m.fichiers), [], "les fichiers du service sont liberes immediatement")
        self.assertGreaterEqual(len(m.appels_de("liberer")), 1)

    def test_croissance_organique_pendant_l_observation_urgence_avant_la_garde(self):
        m = monde_pret(helios="absent")
        # apres le remplissage (~30 s), une croissance externe pousse l'occupation vers 83 %
        m.evenements.append((m.t + 60, lambda mm: setattr(mm, "croissance_o_s", 8 * MIO)))
        c1, c2, c3, out = cycle(m)
        self.assertEqual(c2, D.CODES_SORTIE["URGENCE"], "\n".join(out))
        self.assertFalse(D.F_MARQUEUR_GARDE in m.textes, "le service agit a 83 %, avant la garde (83,5 %)")
        self.assertEqual(sorted(m.fichiers), [])

    def test_la_garde_a_libere_le_service_le_constate_et_conclut_urgence(self):
        m = monde_pret(helios="absent")
        # le service est 'aveugle' un instant : la garde (83,5 %) tire avant lui
        def pousser(mm):
            mm.organique += (0.8350 - mm.usage() / 100.0 + 0.001) * mm.taille
        m.evenements.append((m.t + 60, pousser))
        vrai = m.stats
        etat = {"aveugle": True}

        def stats_aveugle():
            if etat["aveugle"] and not m._en_garde and m.fichiers and m.usage() >= D.GARDE_PCT and not m.textes.get(D.F_MARQUEUR_GARDE):
                return D.Fs(m.taille, int(m.taille * (1 - 0.80)))       # le SERVICE lit 80 % (mesure en retard) ; la garde lit le vrai volume
            return vrai()
        m.stats = stats_aveugle
        c1, c2, c3, out = cycle(m)
        texte = "\n".join(out)
        self.assertIn(D.F_MARQUEUR_GARDE, m.textes, texte)
        self.assertEqual(c2, D.CODES_SORTIE["URGENCE"], texte)
        self.assertIn("garde independante a libere", texte)
        self.assertEqual(sorted(m.fichiers), [])

    def test_l_echeance_a_libere_pendant_l_observation_test_invalide(self):
        m = monde_pret(helios="absent")

        def echeance(mm):
            mm.textes[D.F_MARQUEUR_EXPIRATION] = {"epoch": mm.t}
            for n in list(mm.fichiers):
                del mm.fichiers[n]
        m.evenements.append((m.t + 100, echeance))
        c1, c2, c3, out = cycle(m)
        self.assertEqual(c2, D.CODES_SORTIE["ERREUR"], "\n".join(out))
        self.assertIn("echeance a libere", "\n".join(out))

    def test_effet_non_mesure_les_fichiers_ne_sont_pas_sur_le_volume_refus(self):
        m = monde_pret(helios="resolve")
        m.pannes.add("hors_volume")
        c1, c2, c3, out = cycle(m)
        self.assertEqual(c2, D.CODES_SORTIE["REFUS"], "\n".join(out))
        self.assertIn("sans effet mesure", "\n".join(out))
        self.assertEqual(len(m.appels_de("allouer")), 1, "arret des la 1re allocation")
        self.assertEqual(sorted(m.fichiers), [])

    def test_fallocate_en_echec_erreur_et_liberation(self):
        m = monde_pret(helios="resolve")
        m.pannes.add("allouer_echoue")
        c1, c2, c3, out = cycle(m)
        self.assertEqual(c2, D.CODES_SORTIE["ERREUR"])

    def test_garde_perdue_en_cours_de_route_erreur_et_liberation(self):
        m = monde_pret(helios="absent")
        m.evenements.append((m.t + 60, lambda mm: setattr(mm, "garde_active", False)))
        c1, c2, c3, out = cycle(m)
        self.assertEqual(c2, D.CODES_SORTIE["ERREUR"], "\n".join(out))
        self.assertIn("PERDUE", "\n".join(out))
        self.assertEqual(sorted(m.fichiers), [])

    def test_garde_non_armee_au_run_aucune_allocation(self):
        m = monde_pret(helios="resolve")
        out = []
        self.assertEqual(D.verifier(m, False, out.append), 0)
        m.garde_active = False
        code = D.executer(m, out.append)
        self.assertEqual(code, D.CODES_SORTIE["REFUS"], "\n".join(out))
        self.assertEqual(m.appels_de("allouer"), [], "aucune allocation sans garde armee pour ce jeton")

    def test_sigterm_pendant_le_remplissage_libere_immediatement(self):
        m = monde_pret(helios="resolve", duree_alloc_s=5)          # chaque bloc dure 5 s : le remplissage dure ~50 s
        m.arret_a = m.t + 20
        c1, c2, c3, out = cycle(m)
        self.assertEqual(c2, D.CODES_SORTIE["ARRET"], "\n".join(out))
        self.assertIn("pendant le remplissage", "\n".join(out))
        self.assertLess(len(m.appels_de("allouer")), 10, "le remplissage s'arrete aussitot")
        self.assertEqual(sorted(m.fichiers), [])

    def test_urgence_detectee_a_la_mesure_fraiche_avant_l_allocation(self):
        m = monde_pret(helios="resolve")
        self.assertEqual(D.verifier(m, False, [].append), 0)
        vrai = m.stats
        n = {"appels": 0}

        def stats_qui_grimpe():
            if m._en_garde:                                        # la garde est un autre processus : sa lecture n'est pas comptee
                return vrai()
            n["appels"] += 1
            if n["appels"] == 2:                                   # 1re = plan de run ; 2e = mesure fraiche de la 1re iteration
                return D.Fs(m.taille, int(m.taille * (1 - 0.84)))
            return vrai()
        m.stats = stats_qui_grimpe
        out = []
        code = D.executer(m, out.append)
        self.assertEqual(code, D.CODES_SORTIE["URGENCE"], "\n".join(out))
        self.assertEqual(m.appels_de("allouer"), [], "a 84 % avant la 1re allocation : aucune allocation")

    def test_le_jeton_vient_du_monde_et_change_a_chaque_lancement(self):
        m = monde_pret(helios="resolve")
        D.verifier(m, False, [].append)
        j1 = m.textes[D.F_ETAT]["jeton"]
        self.assertEqual(j1, "%032x" % (0xabc0000 + 1))
        self.assertEqual(m._compteur_jeton, 1)
        m2 = monde_pret(helios="resolve")
        m2._compteur_jeton = 7
        D.verifier(m2, False, [].append)
        self.assertNotEqual(m2.textes[D.F_ETAT]["jeton"], j1)
        self.assertEqual(m2.textes[D.F_ETAT]["jeton"], "%032x" % (0xabc0000 + 8))

    def test_sigterm_pendant_l_observation_libere_immediatement(self):
        m = monde_pret(helios="absent")
        m.arret_a = m.t + 120
        c1, c2, c3, out = cycle(m)
        self.assertEqual(c2, D.CODES_SORTIE["ARRET"], "\n".join(out))
        self.assertEqual(sorted(m.fichiers), [])
        self.assertEqual(m.textes[D.F_ETAT]["phase"], "terminee")

    def test_run_sans_verification_prealable_refuse(self):
        m = MondeEmule(helios="resolve")
        out = []
        self.assertEqual(D.executer(m, out.append), D.CODES_SORTIE["REFUS"])
        self.assertEqual(m.appels_de("allouer"), [])

    def test_run_avec_l_etat_d_un_autre_lancement_refuse(self):
        m = monde_pret(helios="resolve")
        D.verifier(m, False, [].append)
        m.inv = "inv-0002"
        self.assertEqual(D.executer(m, [].append), D.CODES_SORTIE["REFUS"])
        self.assertEqual(m.appels_de("allouer"), [])
        self.assertEqual(m.textes[D.F_ETAT]["phase"], "pret", "l'etat d'un autre lancement n'est pas modifie")

    def test_run_hors_unite_systemd_refuse(self):
        m = monde_pret(helios="resolve")
        D.verifier(m, False, [].append)
        m.inv = None
        self.assertEqual(D.executer(m, [].append), D.CODES_SORTIE["REFUS"])

    def test_run_repertoire_non_vide_refuse(self):
        m = monde_pret(helios="resolve")
        D.verifier(m, False, [].append)
        m.fichiers[D.nom_fichier("e" * 32, 1)] = {"taille": MIO, "uid": 0, "nlink": 1, "type": "f"}
        self.assertEqual(D.executer(m, [].append), D.CODES_SORTIE["REFUS"])
        self.assertEqual(m.appels_de("allouer"), [])
        self.assertEqual(m.appels_de("liberer"), [], "les fichiers d'un autre lancement ne sont JAMAIS supprimes")

    def test_budget_d_allocation_depasse_refus_et_liberation(self):
        m = monde_pret(helios="resolve", duree_alloc_s=60)
        c1, c2, c3, out = cycle(m)
        self.assertEqual(c2, D.CODES_SORTIE["REFUS"], "\n".join(out))
        self.assertIn("delai insuffisant", "\n".join(out))
        self.assertEqual(sorted(m.fichiers), [])

    def test_retour_non_confirme_si_l_occupation_reste_haute(self):
        m = monde_pret(helios="resolve")
        m.evenements.append((m.t + 30, lambda mm: setattr(mm, "organique", mm.organique + 0.09 * mm.taille)))
        c1, c2, c3, out = cycle(m)
        self.assertNotEqual(c2, 0, "\n".join(out))
        self.assertIn(m.textes[D.F_ETAT]["verdict"], ("ERREUR", "URGENCE"))

    def test_prometheus_illisible_pendant_l_observation_est_informatif(self):
        m = monde_pret(helios="resolve")
        m.evenements.append((m.t + 20, lambda mm: mm.pannes.add("prom_totale")))
        c1, c2, c3, out = cycle(m)
        self.assertEqual(c2, 0, "\n".join(out))
        self.assertIn("Prometheus illisible (informatif", "\n".join(out))

    def test_liberation_impossible_etat_incomplet_et_la_garde_reste_armee(self):
        m = monde_pret(helios="absent")
        m.pannes.add("liberer_refuse")
        c1, c2, c3, out = cycle(m)
        texte = "\n".join(out)
        self.assertNotEqual(c2, 0)
        self.assertEqual(c3, 1, "nettoyer echoue aussi : liberation non verifiee")
        self.assertEqual(m.textes[D.F_ETAT]["phase"], "liberation_incomplete")
        self.assertIn("LIBERATION NON VERIFIEE", texte)
        self.assertTrue(m.garde_active, "la garde reste armee : elle liberera a son seuil ou a l'echeance")
        self.assertGreater(len(m.fichiers), 0)

    def test_les_entrees_etrangeres_et_les_fichiers_d_un_autre_jeton_ne_sont_jamais_touches(self):
        m = monde_pret(helios="absent")

        def intrus(mm):
            mm.etrangers["README"] = {"taille": 12, "uid": 0, "nlink": 1, "type": "f"}
            mm.etrangers[D.nom_fichier("e" * 32, 3)] = {"taille": 1000, "uid": 0, "nlink": 1, "type": "f"}
        m.evenements.append((m.t + 50, intrus))
        c1, c2, c3, out = cycle(m)
        self.assertEqual(sorted(m.etrangers), sorted(["README", D.nom_fichier("e" * 32, 3)]))
        self.assertEqual(sorted(m.fichiers), [])

    def test_ne_depasse_jamais_82_pour_aucune_configuration(self):
        for usage0 in (30.0, 50.0, 60.0, 68.6, 70.0, 76.9, 77.0):
            for taille_gio in (10, 20, 38, 60, 100):
                m = monde_pret(usage0=usage0, taille=taille_gio * GIO, helios="resolve")
                c1, c2, c3, out = cycle(m)
                with self.subTest(usage0=usage0, taille_gio=taille_gio):
                    self.assertLessEqual(m.usage_max, D.PLAFOND_PCT + 1e-9 if m.appels_de("allouer") else 100.0)
                    self.assertLessEqual(sum(a[2] for a in m.appels_de("allouer")), D.MAX_TOTAL)
                    self.assertEqual(sorted(m.fichiers), [])
                    self.assertIn(c2 if c1 == 0 else None, (0, D.CODES_SORTIE["REFUS"], None), "\n".join(out))

    def test_un_plan_superieur_a_6_gio_est_refuse_sans_allocation(self):
        m = monde_pret(usage0=30.0, taille=100 * GIO, helios="resolve")
        c1, c2, c3, out = cycle(m)
        self.assertEqual(c1, 2)
        self.assertEqual(m.appels_de("allouer"), [])
        self.assertTrue(any("limite" in l for l in out))

    def test_double_cycle_deux_lancements_consecutifs_exigent_le_cooldown(self):
        m = monde_pret(helios="resolve")
        self.assertEqual(cycle(m)[:2], (0, 0))
        c1, c2, c3, out = cycle(m)
        self.assertEqual(c1, 2, "le cooldown local de 5 min refuse un 2e demarrage immediat : sans armement a usage unique, c'est LUI qui l'empeche")
        self.assertTrue(any("[KO] COOLDOWN" in l for l in out), "\n".join(out))
        m.dormir(D.COOLDOWN_LOCAL_S)
        m.demarrer_garde()
        m.reinit_helios()
        m.inv = "inv-0002"
        self.assertEqual(cycle(m)[:2], (0, 0), "apres le cooldown, un nouveau demarrage direct est accepte")

    def test_le_refus_du_cooldown_explique_quoi_faire_et_n_alloue_rien(self):
        """17 nov. 2026 : la commande unique etait refusee sans que le journal dise quoi faire ; le [KO] COOLDOWN doit l'expliquer."""
        m = monde_pret(helios="resolve")
        self.assertEqual(cycle(m)[:2], (0, 0))
        allocations = len(m.appels_de("allouer"))
        c1, c2, c3, out = cycle(m)
        self.assertEqual(c1, 2)
        lignes = [l for l in out if "[KO] COOLDOWN" in l]
        self.assertEqual(len(lignes), 1, "\n".join(out))
        self.assertIn("relancer la meme commande", lignes[0])
        self.assertEqual(len(m.appels_de("allouer")), allocations, "un refus n'alloue rien")

    def test_un_refus_ne_prolonge_pas_le_cooldown(self):
        """Le cooldown court depuis le DEBUT du dernier lancement ABOUTI : des relances refusees ne reecrivent pas l'horodatage."""
        m = monde_pret(helios="resolve")
        self.assertEqual(cycle(m)[:2], (0, 0))
        horodatage = dict(m.textes[D.F_COOLDOWN])
        for _ in range(3):
            self.assertEqual(cycle(m)[0], 2)
        self.assertEqual(m.textes[D.F_COOLDOWN], horodatage, "un refus ne doit pas reecrire l'horodatage du cooldown")

    def test_le_cooldown_dure_exactement_5_minutes_depuis_le_debut_du_lancement(self):
        m = monde_pret(helios="resolve")
        self.assertEqual(cycle(m)[:2], (0, 0))
        debut = m.textes[D.F_COOLDOWN]["debut_epoch"]
        self.assertEqual(D.COOLDOWN_LOCAL_S, 300)
        self.assertEqual(D.cooldown_restant_s(m, debut), (300, None))
        self.assertEqual(D.cooldown_restant_s(m, debut + 299), (1, None))
        self.assertEqual(D.cooldown_restant_s(m, debut + 300), (0, None))

    def test_un_lancement_precedent_incomplet_n_est_pas_nettoye_au_demarrage(self):
        m = monde_pret(helios="absent")
        m.pannes.add("liberer_refuse")
        cycle(m)
        reste = sorted(m.fichiers)
        self.assertGreater(len(reste), 0)
        m.pannes.discard("liberer_refuse")
        m.garde_active = False                                             # l'ancienne garde a disparu (reboot)
        m.dormir(D.COOLDOWN_LOCAL_S)
        m.demarrer_garde()
        m.inv = "inv-0002"
        n_avant = len(m.appels_de("liberer"))
        c1, c2, c3, out = cycle(m)
        self.assertEqual(c1, 2, "orphelins : refus\n" + "\n".join(out))
        self.assertTrue(any("[KO] DOSSIERS" in l and "ORPHELINS" in l for l in out), "\n".join(out))
        self.assertEqual(sorted(m.fichiers), reste, "AUCUN nettoyage automatique des orphelins au demarrage")
        self.assertEqual(c3, 0, "nettoyer ne touche pas ce qui n'appartient pas a CE lancement")
        self.assertEqual(len(m.appels_de("liberer")), n_avant, "aucune suppression tentee par verifier / nettoyer")


# ------------------------------------------------------------------------------------------------ garde
class TestGarde(SansInfra):
    def etat(self, m, **sur):
        e = {"version": 1, "jeton": "a" * 32, "invocation_id": "inv-0001", "cree_epoch": m.t, "debut_epoch": m.t, "echeance_epoch": m.t + 1020,
             "phase": "observation", "noms": [], "verdict": None}
        e.update(sur)
        m.textes[D.F_ETAT] = e
        return e

    def fichiers(self, m, n=3, jeton="a" * 32):
        for i in range(1, n + 1):
            m.fichiers[D.nom_fichier(jeton, i)] = {"taille": 512 * MIO, "uid": 0, "nlink": 1, "type": "f"}

    def test_au_repos_la_garde_bat_et_sort_apres_120_s_sans_lancement(self):
        m = MondeEmule(garde=False)
        out = []
        m.inv = "inv-g"
        t0 = m.t
        codes = []
        while True:
            c = D.garde_tour(m, t0, out.append)
            if c is not None:
                codes.append(c)
                break
            self.assertIsNone(m.textes[D.F_BATTEMENT]["jeton"])
            m.dormir(2)
        self.assertEqual(codes, [0])
        self.assertGreater(m.t - t0, D.GARDE_ATTENTE_ETAT_S)
        self.assertLessEqual(m.t - t0, D.GARDE_ATTENTE_ETAT_S + 3)

    def test_adopte_l_etat_cree_apres_son_demarrage(self):
        m = MondeEmule(garde=False)
        t0 = m.t
        m.dormir(5)
        self.etat(m)
        D.garde_tour(m, t0, [].append)
        self.assertEqual(m.textes[D.F_BATTEMENT]["jeton"], "a" * 32)

    def test_declenche_a_83_5_et_pas_a_83_49(self):
        for usage, attendu in ((83.49, False), (83.5, True), (84.0, True)):
            m = MondeEmule(garde=False, usage0=70.0)
            t0 = m.t - 10
            self.etat(m)
            self.fichiers(m, 3)
            m.organique = (usage / 100.0 - m.usage() / 100.0) * m.taille + 1.0 if usage < 83.5 else (usage / 100.0 - m.usage() / 100.0) * m.taille + 4096
            out = []
            code = D.garde_tour(m, t0, out.append)
            self.assertEqual(code == 0, attendu, "%s -> %r %s" % (usage, code, m.usage()))
            self.assertEqual(sorted(m.fichiers) == [], attendu)
            self.assertEqual(D.F_MARQUEUR_GARDE in m.textes, attendu)

    def test_ne_declenche_pas_a_83_5_sans_fichier_du_lancement(self):
        m = MondeEmule(garde=False, usage0=84.0)
        self.etat(m)
        self.assertIsNone(D.garde_tour(m, m.t - 10, [].append))
        self.assertEqual(m.appels_de("liberer"), [])

    def test_echeance_avec_fichiers_libere_et_pose_le_marqueur(self):
        m = MondeEmule(garde=False)
        t0 = m.t - 10
        self.etat(m, echeance_epoch=m.t + 5)
        self.fichiers(m, 4)
        self.assertIsNone(D.garde_tour(m, t0, [].append))
        m.dormir(6)
        self.assertEqual(D.garde_tour(m, t0, [].append), 0)
        self.assertEqual(sorted(m.fichiers), [])
        self.assertIn(D.F_MARQUEUR_EXPIRATION, m.textes)

    def test_echeance_sans_fichier_sort_sans_rien_supprimer(self):
        m = MondeEmule(garde=False)
        self.etat(m, echeance_epoch=m.t - 1)
        self.assertEqual(D.garde_tour(m, m.t - 10, [].append), 0)
        self.assertEqual(m.appels_de("liberer"), [])

    def test_ignore_l_etat_d_un_lancement_precedent_aucun_nettoyage_au_demarrage(self):
        m = MondeEmule(garde=False, usage0=84.0)
        self.etat(m, cree_epoch=m.t - 3600, echeance_epoch=m.t - 100, phase="liberation_incomplete")
        self.fichiers(m, 3)
        out = []
        t0 = m.t
        for _ in range(30):
            self.assertIsNone(D.garde_tour(m, t0, out.append), "une garde NEUVE n'adopte jamais l'etat d'un lancement precedent")
            m.dormir(2)
        self.assertEqual(len(m.fichiers), 3, "les orphelins restent (nettoyage manuel seulement)")
        self.assertEqual(m.appels_de("liberer"), [])

    def test_sort_quand_le_lancement_est_termine(self):
        m = MondeEmule(garde=False)
        self.etat(m, phase="terminee")
        self.assertEqual(D.garde_tour(m, m.t - 10, [].append), 0)

    def test_reste_armee_quand_la_liberation_est_incomplete(self):
        m = MondeEmule(garde=False)
        self.etat(m, phase="liberation_incomplete")
        self.fichiers(m, 2)
        self.assertIsNone(D.garde_tour(m, m.t - 10, [].append))

    def test_ne_supprime_que_les_noms_exacts_du_jeton(self):
        m = MondeEmule(garde=False, usage0=84.0)
        self.etat(m)
        self.fichiers(m, 3)
        m.etrangers["README"] = {"taille": 1, "uid": 0, "nlink": 1, "type": "f"}
        autre = D.nom_fichier("b" * 32, 1)
        m.etrangers[autre] = {"taille": 1, "uid": 0, "nlink": 1, "type": "f"}
        self.assertEqual(D.garde_tour(m, m.t - 10, [].append), 0)
        self.assertEqual(sorted(m.etrangers), sorted(["README", autre]))
        appels = m.appels_de("liberer")
        self.assertTrue(appels)
        for _, noms in appels:
            for n in noms:
                self.assertTrue(D.RE_FICHIER.fullmatch(n))
                self.assertEqual(D.RE_FICHIER.fullmatch(n).group(1), "a" * 32)

    def test_etat_corrompu_ou_invalide_ignore(self):
        m = MondeEmule(garde=False, usage0=84.0)
        self.fichiers(m, 2)
        m.corrompus.add(D.F_ETAT)
        self.assertIsNone(D.garde_tour(m, m.t - 10, [].append))
        m.corrompus.clear()
        m.textes[D.F_ETAT] = {"jeton": "../../etc", "phase": "observation"}
        self.assertIsNone(D.garde_tour(m, m.t - 10, [].append))
        self.assertEqual(m.appels_de("liberer"), [])

    def test_garde_refuse_de_tourner_hors_systemd(self):
        m = MondeEmule(garde=False)
        m.inv = None
        out = []
        self.assertEqual(D.garde(m, out.append), D.CODES_SORTIE["REFUS"])

    def test_boucle_garde_arret_demande(self):
        m = MondeEmule(garde=False)
        m.arret_a = m.t + 10
        out = []
        self.assertEqual(D.garde(m, out.append), 0)
        self.assertIn("arret demande", out[-1])

    def test_boucle_garde_complete_declenche_puis_sort(self):
        m = MondeEmule(garde=False, usage0=70.0)
        m.inv = "inv-g"
        t0 = m.t
        m.dormir(1)
        self.etat(m)
        self.fichiers(m, 5)
        m.evenements.append((m.t + 10, lambda mm: setattr(mm, "organique", mm.organique + 0.2 * mm.taille)))
        out = []
        self.assertEqual(D.garde(m, out.append), 0)
        self.assertEqual(sorted(m.fichiers), [])
        self.assertIn(D.F_MARQUEUR_GARDE, m.textes)


# ------------------------------------------------------------------------------------------------ nettoyer (ExecStopPost) et orphelins
class TestNettoyer(SansInfra):
    def preparer(self, m, n=3, phase="observation", inv="inv-0001"):
        e = {"version": 1, "jeton": "a" * 32, "invocation_id": inv, "cree_epoch": m.t, "debut_epoch": m.t, "echeance_epoch": m.t + 1020,
             "phase": phase, "noms": [], "verdict": None}
        m.textes[D.F_ETAT] = e
        for i in range(1, n + 1):
            m.fichiers[D.nom_fichier("a" * 32, i)] = {"taille": MIO, "uid": 0, "nlink": 1, "type": "f"}

    def test_sans_etat_ne_fait_rien_et_ne_touche_pas_aux_orphelins(self):
        m = MondeEmule(garde=False)
        m.fichiers[D.nom_fichier("a" * 32, 1)] = {"taille": MIO, "uid": 0, "nlink": 1, "type": "f"}
        out = []
        self.assertEqual(D.nettoyer(m, out.append), 0)
        self.assertEqual(len(m.fichiers), 1)
        self.assertEqual(m.appels_de("liberer"), [])
        self.assertIn("rien a faire", out[-1])

    def test_etat_d_un_autre_lancement_ne_fait_rien(self):
        m = MondeEmule(garde=False)
        self.preparer(m, inv="inv-ancien")
        self.assertEqual(D.nettoyer(m, [].append), 0)
        self.assertEqual(len(m.fichiers), 3)
        self.assertEqual(m.appels_de("liberer"), [])

    def test_hors_systemd_ne_fait_rien(self):
        m = MondeEmule(garde=False)
        self.preparer(m)
        m.inv = None
        self.assertEqual(D.nettoyer(m, [].append), 0)
        self.assertEqual(len(m.fichiers), 3)

    def test_libere_par_noms_exacts_les_fichiers_de_ce_lancement(self):
        m = MondeEmule(garde=False)
        self.preparer(m)
        m.etrangers["README"] = {"taille": 1, "uid": 0, "nlink": 1, "type": "f"}
        m.etrangers[D.nom_fichier("b" * 32, 1)] = {"taille": 1, "uid": 0, "nlink": 1, "type": "f"}
        self.assertEqual(D.nettoyer(m, [].append), 0)
        self.assertEqual(sorted(m.fichiers), [])
        self.assertEqual(len(m.etrangers), 2)
        self.assertEqual(m.textes[D.F_ETAT]["phase"], "terminee")

    def test_deja_termine_ne_fait_rien(self):
        m = MondeEmule(garde=False)
        self.preparer(m, phase="terminee")
        self.assertEqual(D.nettoyer(m, [].append), 0)
        self.assertEqual(len(m.fichiers), 3)

    def test_ne_refait_pas_de_suppression_quand_helios_a_deja_libere(self):
        m = MondeEmule(garde=False)
        self.preparer(m, n=0)
        self.assertEqual(D.nettoyer(m, [].append), 0)
        self.assertEqual(m.appels_de("liberer"), [], "rien ne reste : aucune suppression n'est meme tentee")
        self.assertEqual(m.textes[D.F_ETAT]["phase"], "terminee")

    def test_echec_de_liberation_signale_et_laisse_la_garde_armee(self):
        m = MondeEmule(garde=False)
        self.preparer(m)
        m.pannes.add("liberer_refuse")
        out = []
        self.assertEqual(D.nettoyer(m, out.append), 1)
        self.assertEqual(m.textes[D.F_ETAT]["phase"], "liberation_incomplete")
        self.assertIn("garde", out[-1])

    def test_trois_tentatives_de_liberation_relecture_pas_code_retour(self):
        m = MondeEmule(garde=False)
        self.preparer(m)
        vrai = m.liberer
        essais = {"n": 0}

        def liberer_capricieux(noms):
            essais["n"] += 1
            if essais["n"] < 3:
                raise D.ErreurNoeud("rm a echoue (rc=1)")
            return vrai(noms)
        m.liberer = liberer_capricieux
        self.assertEqual(D.nettoyer(m, [].append), 0)
        self.assertEqual(essais["n"], 3)
        self.assertEqual(sorted(m.fichiers), [])
        # un 'succes' d'une commande qui ne supprime rien n'est PAS une preuve : la relecture tranche
        m = MondeEmule(garde=False)
        self.preparer(m)
        m.liberer = lambda noms: []
        self.assertEqual(D.nettoyer(m, [].append), 1)
        self.assertEqual(len(m.fichiers), 3)

    def test_etat_invalide_ou_jeton_hors_motif_ne_supprime_rien(self):
        m = MondeEmule(garde=False)
        m.fichiers[D.nom_fichier("a" * 32, 1)] = {"taille": MIO, "uid": 0, "nlink": 1, "type": "f"}
        m.textes[D.F_ETAT] = {"jeton": "*", "invocation_id": "inv-0001", "phase": "observation", "cree_epoch": 1, "debut_epoch": 1, "echeance_epoch": 2}
        self.assertEqual(D.nettoyer(m, [].append), 0)
        self.assertEqual(len(m.fichiers), 1)


class TestOrphelins(SansInfra):
    def orphelins(self, m, n=3, jeton="c" * 32):
        for i in range(1, n + 1):
            m.fichiers[D.nom_fichier(jeton, i)] = {"taille": 512 * MIO, "uid": 0, "nlink": 1, "type": "f"}

    def test_plan_par_defaut_ne_supprime_rien(self):
        m = MondeEmule(garde=False)
        self.orphelins(m)
        out = []
        self.assertEqual(D.orphelins(m, None, out.append), 0)
        self.assertEqual(len(m.fichiers), 3)
        self.assertEqual(m.appels_de("liberer"), [])
        self.assertIn("SUPPRIMER-ORPHELINS-cccccccc", "\n".join(out))

    def test_confirmation_exacte_supprime_les_noms_exacts(self):
        m = MondeEmule(garde=False)
        self.orphelins(m)
        m.etrangers["README"] = {"taille": 1, "uid": 0, "nlink": 1, "type": "f"}
        out = []
        self.assertEqual(D.orphelins(m, "SUPPRIMER-ORPHELINS-cccccccc", out.append), 0, "\n".join(out))
        self.assertEqual(sorted(m.fichiers), [])
        self.assertEqual(sorted(m.etrangers), ["README"])

    def test_mauvaise_confirmation_ne_supprime_rien(self):
        m = MondeEmule(garde=False)
        self.orphelins(m)
        for texte in ("", "oui", "SUPPRIMER-ORPHELINS-bbbbbbbb", "supprimer-orphelins-cccccccc", "SUPPRIMER-ORPHELINS-cccccccc "):
            D.orphelins(m, texte, [].append)
        self.assertEqual(len(m.fichiers), 3)

    def test_refuse_pendant_qu_un_lancement_est_actif(self):
        for unite in (D.UNITE_PRINCIPALE, D.UNITE_GARDE):
            m = MondeEmule(garde=False)
            self.orphelins(m)
            m.occupees[unite] = True
            self.assertEqual(D.orphelins(m, "SUPPRIMER-ORPHELINS-cccccccc", [].append), 2)
            self.assertEqual(len(m.fichiers), 3)

    def test_refuse_si_l_etat_des_unites_est_illisible(self):
        m = MondeEmule(garde=False)
        self.orphelins(m)
        m.pannes.add("occupee_illisible")
        self.assertEqual(D.orphelins(m, "SUPPRIMER-ORPHELINS-cccccccc", [].append), 2)
        self.assertEqual(len(m.fichiers), 3)

    def test_refuse_deux_jetons_et_entrees_non_conformes(self):
        m = MondeEmule(garde=False)
        self.orphelins(m, 2, "c" * 32)
        self.orphelins(m, 2, "d" * 32)
        self.assertEqual(D.orphelins(m, "SUPPRIMER-ORPHELINS-cccccccc", [].append), 2)
        self.assertEqual(len(m.fichiers), 4)
        m = MondeEmule(garde=False)
        self.orphelins(m, 2)
        m.fichiers[D.nom_fichier("c" * 32, 1)]["nlink"] = 2
        self.assertEqual(D.orphelins(m, "SUPPRIMER-ORPHELINS-cccccccc", [].append), 2)
        m = MondeEmule(garde=False)
        self.orphelins(m, 2)
        m.fichiers[D.nom_fichier("c" * 32, 2)]["uid"] = 1000
        self.assertEqual(D.orphelins(m, "SUPPRIMER-ORPHELINS-cccccccc", [].append), 2)

    def test_refuse_si_le_repertoire_n_est_pas_conforme(self):
        m = MondeEmule(garde=False)
        self.orphelins(m)
        m.infos[D.DOSSIER]["mode"] = "755"
        self.assertEqual(D.orphelins(m, "SUPPRIMER-ORPHELINS-cccccccc", [].append), 2)
        self.assertEqual(len(m.fichiers), 3)

    def test_aucun_orphelin(self):
        m = MondeEmule(garde=False)
        self.assertEqual(D.orphelins(m, None, [].append), 0)
        m.dossier_present = False
        self.assertEqual(D.orphelins(m, None, [].append), 0)

    def test_n_est_jamais_appele_par_le_cycle_automatique(self):
        corps = open(os.path.join(DIR_SVC, "diskfill.py"), "rb").read().decode("ascii")
        import ast
        arbre = ast.parse(corps)
        appels = [n.func.id for n in ast.walk(arbre) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)]
        # `orphelins` n'est appelee que par principal() (ligne de commande manuelle)
        for f in arbre.body:
            if isinstance(f, ast.FunctionDef) and f.name not in ("principal", "orphelins"):
                autres = [n.func.id for n in ast.walk(f) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)]
                self.assertNotIn("orphelins", autres, f.name)


# ------------------------------------------------------------------------------------------------ etat (lecture seule)
class TestEtat(SansInfra):
    def test_etat_est_en_lecture_seule(self):
        m = monde_pret()
        avant = copy.deepcopy(m.textes)
        n_appels = len(m.appels)
        out = []
        self.assertEqual(D.etat_cmd(m, True, out.append), 0)
        self.assertEqual(m.textes, avant)
        ecritures = [a for a in m.appels[n_appels:] if a[0] in ("ecrire_json", "supprimer", "allouer", "liberer")]
        self.assertEqual(ecritures, [])
        f = json.loads(out[0])
        self.assertEqual(f["hostname"], "k8s-worker1")
        self.assertTrue(f["ip_presente"])
        self.assertEqual(f["sha256"], SHAS)
        self.assertNotIn("armement", f, "plus aucune notion d'armement dans les faits du noeud")
        self.assertEqual(f["inodes_pct"], 11.0)
        self.assertEqual(f["unites"][D.UNITE_PRINCIPALE]["StateDirectoryMode"], "0700")
        self.assertEqual(f["dossier"]["anomalies"], [])
        self.assertEqual(f["contenu_dossier"], [])
        self.assertEqual(f["cooldown"]["local_min"], 5)

    def test_etat_une_lecture_impossible_est_null_jamais_devinee(self):
        m = MondeEmule()
        m.pannes.update(["systemctl_ko", "stats_illisible", "transitoires_illisibles"])
        f = json.loads(self.sortie(m))
        self.assertIsNone(f["unites"][D.UNITE_PRINCIPALE])
        self.assertIsNone(f["usage_pct"])
        self.assertIsNone(f["unites_transitoires"])

    def sortie(self, m):
        out = []
        D.etat_cmd(m, True, out.append)
        return out[0]

    def test_etat_lisible_par_un_humain(self):
        m = monde_pret()
        out = []
        self.assertEqual(D.etat_cmd(m, False, out.append), 0)
        texte = "\n".join(out)
        self.assertNotIn("armement", texte)
        self.assertIn("inodes de / : 11.0 %", texte)
        self.assertIn("cooldown local", texte)


# ------------------------------------------------------------------------------------------------ le vrai MondeReel, sur des couches os simulees
class TestMondeReel(SansInfra):
    def test_liberer_supprime_par_nom_exact_avec_le_descripteur_du_repertoire(self):
        r = D.MondeReel()
        nom = D.nom_fichier("a" * 32, 1)
        appels = []

        class St(object):
            st_mode = 0o100644
            st_uid = 0
            st_nlink = 1

        with mock.patch.object(D.os, "open", lambda chemin, flags: appels.append(("open", chemin)) or 77), \
                mock.patch.object(D.os, "stat", lambda n, **kw: appels.append(("stat", n, kw)) or St()), \
                mock.patch.object(D.os, "unlink", lambda n, **kw: appels.append(("unlink", n, kw))), \
                mock.patch.object(D.os, "close", lambda fd: appels.append(("close", fd))):
            self.assertEqual(r.liberer([nom]), [nom])
        self.assertIn(("unlink", nom, {"dir_fd": 77}), appels, "unlink par DESCRIPTEUR de repertoire, nom exact")
        self.assertIn(("stat", nom, {"dir_fd": 77, "follow_symlinks": False}), appels, "stat sans suivre les liens")
        self.assertEqual(appels[0], ("open", D.DOSSIER))
        self.assertEqual(appels[-1], ("close", 77))

    def test_liberer_refuse_les_noms_hors_motif_et_les_entrees_non_conformes(self):
        r = D.MondeReel()
        for mauvais in ("README", "../etc/passwd", "helios-diskfill-*.bin", D.nom_fichier("a" * 32, 1) + "\n", "helios-diskfill-%s-017.bin" % ("a" * 32)):
            with self.assertRaises(D.RefusSecurite):
                r.liberer([mauvais])

        class Lien(object):
            st_mode = 0o120777
            st_uid = 0
            st_nlink = 1

        class AutreProprio(object):
            st_mode = 0o100644
            st_uid = 1000
            st_nlink = 1

        class DeuxLiens(object):
            st_mode = 0o100644
            st_uid = 0
            st_nlink = 2
        nom = D.nom_fichier("a" * 32, 1)
        for st in (Lien, AutreProprio, DeuxLiens):
            appels = []
            with mock.patch.object(D.os, "open", lambda c, f: 5), mock.patch.object(D.os, "stat", lambda n, **kw: st()), \
                    mock.patch.object(D.os, "unlink", lambda n, **kw: appels.append(n)), mock.patch.object(D.os, "close", lambda fd: None):
                with self.assertRaises(D.RefusSecurite):
                    r.liberer([nom])
            self.assertEqual(appels, [], "une entree non conforme n'est JAMAIS supprimee")

    def test_liberer_tolere_un_fichier_deja_absent_et_un_repertoire_absent(self):
        r = D.MondeReel()
        nom = D.nom_fichier("a" * 32, 1)

        def stat_absent(n, **kw):
            raise FileNotFoundError(n)
        with mock.patch.object(D.os, "open", lambda c, f: 5), mock.patch.object(D.os, "stat", stat_absent), mock.patch.object(D.os, "close", lambda fd: None):
            self.assertEqual(r.liberer([nom]), [])

        def open_absent(c, f):
            raise FileNotFoundError(c)
        with mock.patch.object(D.os, "open", open_absent):
            self.assertEqual(r.liberer([nom]), [])

    def test_allouer_utilise_fallocate_et_aucun_repli(self):
        r = D.MondeReel()
        nom = D.nom_fichier("a" * 32, 1)
        appels = []

        class Res(object):
            returncode = 0
            stdout = ""

        class St(object):
            st_mode = 0o100644
            st_uid = 0
            st_nlink = 1
            st_size = 512 * MIO
        with mock.patch.object(D.subprocess, "run", lambda argv, **kw: appels.append(argv) or Res()), \
                mock.patch.object(D.os.path, "lexists", lambda c: False), mock.patch.object(D.os, "lstat", lambda c: St()):
            r.allouer(nom, 512 * MIO)
        self.assertEqual(appels, [["/usr/bin/fallocate", "-l", str(512 * MIO), "--", D.DOSSIER + "/" + nom]])

    def test_allouer_refuse_ce_qui_sort_des_limites_et_ne_remplace_jamais_un_fichier(self):
        r = D.MondeReel()
        nom = D.nom_fichier("a" * 32, 1)
        for octets in (0, -1, 1024 * MIO + 1, True, "512"):
            with self.assertRaises(D.RefusSecurite):
                r.allouer(nom, octets)
        with mock.patch.object(D.os.path, "lexists", lambda c: True):
            with self.assertRaises(D.ErreurNoeud):
                r.allouer(nom, 512 * MIO)
        with self.assertRaises(D.RefusSecurite):
            r.allouer("README", 512 * MIO)

    def test_allouer_fallocate_en_echec_ne_tente_aucun_repli(self):
        r = D.MondeReel()
        appels = []

        class Res(object):
            returncode = 1
            stdout = ""
        with mock.patch.object(D.subprocess, "run", lambda argv, **kw: appels.append(argv) or Res()), mock.patch.object(D.os.path, "lexists", lambda c: False):
            with self.assertRaises(D.ErreurNoeud):
                r.allouer(D.nom_fichier("a" * 32, 1), 512 * MIO)
        self.assertEqual(len(appels), 1, "un seul appel : jamais dd, truncate ou posix_fallocate en repli")

    def test_allouer_verifie_le_fichier_cree(self):
        r = D.MondeReel()

        class Res(object):
            returncode = 0
            stdout = ""

        class Mauvais(object):
            st_mode = 0o100644
            st_uid = 0
            st_nlink = 1
            st_size = 1
        with mock.patch.object(D.subprocess, "run", lambda argv, **kw: Res()), mock.patch.object(D.os.path, "lexists", lambda c: False), \
                mock.patch.object(D.os, "lstat", lambda c: Mauvais()):
            with self.assertRaises(D.ErreurNoeud):
                r.allouer(D.nom_fichier("a" * 32, 1), 512 * MIO)

    def test_ecriture_json_atomique(self):
        with tempfile.TemporaryDirectory() as t:
            chemin = os.path.join(t, "etat.json")
            r = D.MondeReel()
            r.ecrire_json(chemin, {"a": 1})
            self.assertEqual(r.lire_json(chemin), {"a": 1})
            r.ecrire_json(chemin, {"a": 2})
            self.assertEqual(r.lire_json(chemin), {"a": 2})
            self.assertEqual(sorted(os.listdir(t)), ["etat.json"], "aucun .tmp ne reste")
            self.assertIsNone(r.lire_json(os.path.join(t, "absent.json")))
            with open(chemin, "w") as f:
                f.write("{pas du json")
            with self.assertRaises(D.ErreurNoeud):
                r.lire_json(chemin)

    def test_le_gestionnaire_de_signal_ne_fait_que_poser_un_drapeau(self):
        r = D.MondeReel()
        pris = {}
        with mock.patch.object(signal, "signal", lambda s, h: pris.__setitem__(s, h)):
            r.installer_signaux()
        self.assertIn(signal.SIGTERM, pris)
        self.assertIn(signal.SIGINT, pris)
        self.assertFalse(r.arret_demande())
        with mock.patch("builtins.print", _interdit), mock.patch("builtins.open", _interdit), mock.patch.object(sys.stdout, "write", _interdit):
            pris[signal.SIGTERM](signal.SIGTERM, None)
        self.assertTrue(r.arret_demande())

    def test_info_chemin_mode_octal_et_montage(self):
        r = D.MondeReel()

        class St(object):
            st_mode = 0o040700
            st_uid = 0
            st_gid = 0
            st_dev = 64512

        class Res(object):
            returncode = 0
            stdout = "ext4 /\n"
        with mock.patch.object(D.os, "lstat", lambda c: St()), mock.patch.object(D.subprocess, "run", lambda argv, **kw: Res()):
            info = r.info_chemin(D.DOSSIER)
        self.assertEqual(info, {"type": "directory", "uid": 0, "gid": 0, "mode": "700", "dev": 64512, "fstype": "ext4", "montage": "/"})

        class St755(St):
            st_mode = 0o040755
        with mock.patch.object(D.os, "lstat", lambda c: St755()), mock.patch.object(D.subprocess, "run", lambda argv, **kw: Res()):
            self.assertEqual(r.info_chemin(D.DOSSIER)["mode"], "755")

    def test_invocation_id_vient_de_l_environnement_systemd(self):
        r = D.MondeReel()
        with mock.patch.dict(D.os.environ, {"INVOCATION_ID": "abc"}):
            self.assertEqual(r.invocation_id(), "abc")
        with mock.patch.dict(D.os.environ, {}, clear=True):
            self.assertIsNone(r.invocation_id())

    def test_jeton_32_hexa(self):
        j = D.MondeReel().nouveau_jeton()
        self.assertRegex(j, r"^[0-9a-f]{32}$")
        self.assertNotEqual(j, D.MondeReel().nouveau_jeton())

    def test_la_formule_d_occupation_est_celle_du_bridge(self):
        r = D.MondeReel()

        class Sv(object):
            f_frsize, f_blocks, f_bavail = 4096, 9759088, 3060865
        with mock.patch.object(D.os, "statvfs", lambda c: Sv(), create=True):
            fs = r.stats()
        self.assertAlmostEqual(fs.usage, B.disque_usage_pct(9759088, 3060865), places=9)


class TestPrincipal(SansInfra):
    def test_sous_commandes_et_aucune_option_de_seuil(self):
        ap = D.construire_parseur()
        for argv in (["verifier"], ["verifier", "--a-blanc"], ["run"], ["garde"], ["nettoyer"], ["etat"], ["etat", "--json"],
                     ["nettoyer-orphelins"], ["nettoyer-orphelins", "--confirmer", "x"]):
            ap.parse_args(argv)
        for mauvais in (["run", "--cible", "90"], ["verifier", "--urgence", "99"], ["run", "--target-pct", "85"], ["verifier", "--a"], ["inconnue"],
                    ["armer", "--charge", "abc"], ["armer"], ["verifier", "--armement", "x"]):
            with self.assertRaises(SystemExit):
                with mock.patch.object(sys, "stderr"):
                    ap.parse_args(mauvais)

    def test_principal_route_vers_les_fonctions(self):
        m = monde_pret(helios="resolve")
        out = []
        self.assertEqual(D.principal(["verifier", "--a-blanc"], m, out.append), 0)
        self.assertEqual(D.principal(["etat", "--json"], m, out.append), 0)
        with mock.patch.object(D.argparse.ArgumentParser, "print_usage", lambda self, fichier=None: None):
            self.assertEqual(D.principal([], m, out.append), 2)

    def test_les_parametres_ne_sont_pas_modifiables_par_l_environnement(self):
        with mock.patch.dict(os.environ, {"CIBLE_PCT": "90", "URGENCE_PCT": "99", "HELIOS_CIBLE": "95"}):
            d2 = _charger("diskfill_mod_env", os.path.join(DIR_SVC, "diskfill.py"))
        self.assertEqual((d2.CIBLE_PCT, d2.PLAFOND_PCT, d2.URGENCE_PCT, d2.GARDE_PCT, d2.RECUPERATION_PCT), (81.0, 82.0, 83.0, 83.5, 77.0))


if __name__ == "__main__":
    unittest.main(verbosity=2)
