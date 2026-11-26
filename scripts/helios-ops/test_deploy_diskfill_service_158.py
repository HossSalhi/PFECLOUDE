# -*- coding: utf-8 -*-
"""Tests du script d'INSTALLATION du service local DISK (scripts/helios-ops/deploy_diskfill_service_158.py) sur un NOEUD FICTIF.

Le noeud fictif emule les seules commandes que le script emet (jeu ferme : toute autre commande fait echouer le test), un systeme de fichiers en
memoire, systemd (proprietes chargees seulement apres `daemon-reload`, derivees du TEXTE des unites installees) et, pour `etat --json` et
`verifier --a-blanc`, le VRAI programme du noeud (diskfill.py) execute sur un monde emule.

Ce script n'a JAMAIS ete execute contre .158 : ces tests sont la seule preuve de son comportement.

AUCUN reseau, AUCUNE VM, AUCUN ssh, AUCUN subprocess reel, AUCUN sleep reel.

Lancer : python -I -B test_deploy_diskfill_service_158.py -v        (depuis scripts/helios-ops)
"""
import ast
import hashlib
import importlib.util
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import helios_disk_test as H              # noqa: E402
import helios_diskfill_service as S       # noqa: E402
import deploy_diskfill_service_158 as P   # noqa: E402

D = S.noeud_module()


def _charger(nom, chemin):
    spec = importlib.util.spec_from_file_location(nom, chemin)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[nom] = mod
    spec.loader.exec_module(mod)
    return mod


TS = _charger("test_service_pour_deploiement", os.path.join(HERE, "test_helios_diskfill_service.py"))
TMP = "/tmp/helios-diskfill-install.Ab12Cd"
STAMP_FIXE = 1800000000


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


def sha(octets):
    return hashlib.sha256(octets).hexdigest()


COMMANDES_AUTORISEES = {"hostname", "python3", "systemctl", "test", "ls", "sha256sum", "mktemp", "install", "cp", "rm", "rmdir", "systemd-analyze",
                        "/usr/bin/python3"}
MOTS_INTERDITS = {"enable", "start", "restart", "mask", "stop", "kill", "reboot", "poweroff", "-r", "-R", "-rf", "-fr", "-f -r", "disable", "reload"}


class NoeudInstall(object):
    """Noeud .158 fictif pour l'installateur (voir l'en-tete)."""

    def __init__(self):
        self.fichiers = {}                       # chemin -> octets
        self.hostname = "k8s-worker1"
        self.python = "Python 3.12.3"
        self.systemd = "systemd 255 (255.4-1ubuntu8.14)"
        self.outils = set(P.OUTILS)
        self.transitoires = []
        self.dossier = None                      # None = absent ; liste = contenu
        self.dossier_illisible = False
        self.actives = {}
        self.reload_fait = False
        self.pannes = set()
        self.journal = []
        self.tmp_cree = False
        self.dossiers = set()

    # --- derivation de l'etat systemd a partir du TEXTE des unites installees
    def proprietes(self, unite):
        chemin = D.CHEMIN_UNITE[unite]
        texte = self.fichiers.get(chemin)
        if texte is None or not self.reload_fait:
            return {"LoadState": "not-found", "FragmentPath": "", "DropInPaths": "", "UnitFileState": "", "StateDirectoryMode": "", "Restart": "",
                    "NeedDaemonReload": ""}
        t = texte.decode("ascii")
        mode = re.search(r"(?m)^StateDirectoryMode=(\S+)", t)
        restart = re.search(r"(?m)^Restart=(\S+)", t)
        mode_lu = "0755" if "mode_0755" in self.pannes else (mode.group(1) if mode else "0755")
        a_install = re.search(r"(?m)^\[Install\]\s*$", t) is not None          # une VRAIE section (systemd ignore les commentaires)
        return {"LoadState": "loaded", "FragmentPath": chemin, "DropInPaths": "", "UnitFileState": "enabled" if a_install else "static",
                "StateDirectoryMode": mode_lu, "Restart": restart.group(1) if restart else "on-failure",
                "NeedDaemonReload": "yes" if "a_recharger" in self.pannes else "no"}               # `no` juste apres un daemon-reload

    def monde(self):
        m = TS.MondeEmule()
        chemins = S.chemins_noeud()
        m.contenu_sha = dict((n, sha(self.fichiers[c]) if c in self.fichiers else None) for n, c in chemins.items())
        for u in (D.UNITE_PRINCIPALE, D.UNITE_GARDE):
            m.unites[u] = self.proprietes(u)
            m.occupees[u] = bool(self.actives.get(u))
        m.transitoires = list(self.transitoires)
        m.dossier_present = self.dossier is not None
        return m

    # --- executeur du lanceur
    def ssh(self, ip, argv, delai=45):
        assert ip == D.IP_CIBLE
        a = list(argv)
        self.journal.append(("ssh", list(argv)))
        for mot in a:
            assert mot not in MOTS_INTERDITS, "verbe interdit dans une commande : %r" % (argv,)
            # seul joker admis : le MOTIF d'une lecture `systemctl list-units helios-diskfill-*` (aucune suppression ne peut en porter)
            assert ("*" not in mot and "?" not in mot) or (mot == "helios-diskfill-*" and "list-units" in a), "joker interdit : %r" % (argv,)
        if a[:2] == ["sudo", "-n"]:
            a = a[2:]
        cmd = a[0]
        assert cmd in COMMANDES_AUTORISEES, "commande inattendue : %r" % (argv,)
        R = H.Res
        if cmd == "hostname":
            return R(0, self.hostname + "\n", "")
        if cmd == "python3":
            return R(0, self.python + "\n", "")
        if cmd == "systemctl" and a[1] == "--version":
            return R(0, self.systemd + "\n+PAM +AUDIT\n", "")
        if cmd == "test" and a[1] == "-x":
            return R(0 if a[2] in self.outils else 1, "", "")
        if cmd == "systemctl" and a[1] == "list-units":
            return R(0, "".join("%s loaded inactive dead x\n" % u for u in self.transitoires), "")
        if cmd == "systemctl" and a[1] == "is-active":
            return R(0 if self.actives.get(a[2]) else 3, "active\n" if self.actives.get(a[2]) else "inactive\n", "")
        if cmd == "systemctl" and a[1] == "daemon-reload":
            if "reload_ko" in self.pannes:
                return R(1, "", "Failed to reload")
            self.reload_fait = True
            return R(0, "", "")
        if cmd == "ls":
            if self.dossier_illisible:
                return R(1, "", "Permission denied")
            if self.dossier is None:
                return R(2, "", "ls: cannot access: No such file or directory")
            return R(0, "".join(n + "\n" for n in self.dossier), "")
        if cmd == "sha256sum":
            chemins = a[a.index("--") + 1:]
            lignes = ["%s  %s" % (sha(self.fichiers[c]), c) for c in chemins if c in self.fichiers]
            if "sha_envoye_different" in self.pannes:
                lignes = [re.sub(r"^[0-9a-f]{64}", "0" * 64, l) if l.endswith("/diskfill.py") and "/tmp/" in l else l for l in lignes]
            return R(0 if len(lignes) == len(chemins) else 1, "".join(l + "\n" for l in lignes), "")
        if cmd == "mktemp":
            if "mktemp_ko" in self.pannes:
                return R(1, "", "mktemp: failed")
            self.tmp_cree = True
            self.dossiers.add(TMP)
            return R(0, TMP + "\n", "")
        if cmd == "install":
            if a[1] == "-d":
                self.dossiers.add(a[-1])
                return R(0 if "install_d_ko" not in self.pannes else 1, "", "")
            src, dst = a[-2], a[-1]
            if ("install_ko:" + os.path.basename(dst)) in self.pannes:
                return R(1, "", "install: cannot create")
            self.fichiers[dst] = self.fichiers[src]
            return R(0, "", "")
        if cmd == "cp":
            src, dst = a[-2], a[-1]
            if "cp_ko" in self.pannes:
                return R(1, "", "cp failed")
            self.fichiers[dst] = self.fichiers[src]
            return R(0, "", "")
        if cmd == "rm":
            assert a[1] == "-f" and a[2] == "--", "rm doit etre `rm -f --` sur des noms exacts : %r" % (argv,)
            for c in a[3:]:
                self.fichiers.pop(c, None)
            return R(0, "", "")
        if cmd == "rmdir":
            self.dossiers.discard(a[-1])
            return R(0, "", "")
        if cmd == "systemd-analyze":
            return R(1 if "analyze_ko" in self.pannes else 0, "" if "analyze_ko" not in self.pannes else "unit is bad\n", "")
        if cmd == "/usr/bin/python3":
            assert a[1:5] == ["-I", "-B", D.CHEMIN_SCRIPT, a[4]]
            sous = a[4:]
            if D.CHEMIN_SCRIPT not in self.fichiers:
                return R(2, "", "python3: can't open file")
            sortie = []
            if sous[0] == "verifier" and "a_blanc_ko" in self.pannes:
                return R(2, "[KO] SYSTEME : x\n", "")
            code = TS.D.principal(sous, self.monde(), sortie.append)
            return R(code, "\n".join(sortie) + "\n", "")
        raise AssertionError("commande non geree : %r" % (argv,))

    def local(self, argv, delai=40, entree=None):
        self.journal.append(("local", list(argv)))
        assert argv[0] == "scp", "commande locale inattendue : %r" % (argv,)
        if "scp_ko" in self.pannes:
            return H.Res(1, "", "scp failed")
        src, dest = argv[-2], argv[-1]
        assert dest.startswith("kube@%s:%s/" % (D.IP_CIBLE, TMP)), dest
        with open(src, "rb") as f:
            self.fichiers[dest.split(":", 1)[1]] = f.read()
        return H.Res(0, "", "")

    def http_json(self, url, delai=25):
        raise AssertionError("HTTP inattendu")

    # --- aides de test
    def commandes(self):
        out = []
        for genre, argv in self.journal:
            a = list(argv)
            if a[:2] == ["sudo", "-n"]:
                a = a[2:]
            out.append((genre, a))
        return out

    def ecritures(self):
        """Commandes qui MODIFIENT le noeud (tout sauf les lectures)."""
        lectures = {"hostname", "python3", "test", "ls", "sha256sum", "systemd-analyze"}
        sortie = []
        for genre, a in self.commandes():
            if genre == "local":
                sortie.append(a)
            elif a[0] in lectures:
                continue
            elif a[0] == "systemctl" and a[1] in ("--version", "list-units", "is-active", "show"):
                continue
            elif a[0] == "/usr/bin/python3" and a[4] in ("etat", "verifier"):
                continue
            else:
                sortie.append(a)
        return sortie

    def installes(self):
        return dict((c, sha(self.fichiers[c])) for c in S.chemins_noeud().values() if c in self.fichiers)


class FauxDeps(object):
    def __init__(self, noeud):
        self.noeud = noeud

    def executeur(self):
        return self.noeud


def lancer(noeud, *args):
    out = []
    code = P.principal(list(args), deps=FauxDeps(noeud), sortie=out.append, maintenant=STAMP_FIXE)
    return code, "\n".join(out)


def sha_depot():
    return S.sommes_calculees()


class TestPlanEtConfirmation(SansInfra):
    def test_plan_par_defaut_n_execute_aucune_commande(self):
        n = NoeudInstall()
        code, sortie = lancer(n, "--installer")
        self.assertEqual(code, 0, sortie)
        self.assertEqual(n.journal, [], "sans --confirm : AUCUNE connexion, AUCUNE commande")
        self.assertIn("PLAN SEULEMENT", sortie)
        self.assertIn(P.CONFIRM_INSTALLER, sortie)
        self.assertNotIn("enable", sortie.replace("jamais enable", ""))

    def test_mauvaise_confirmation_refusee_sans_rien_faire(self):
        for confirm in ("", "oui", "INSTALLER-DISKFILL-155", "installer-diskfill-158", P.CONFIRM_INSTALLER + " ", P.CONFIRM_RETIRER):
            n = NoeudInstall()
            code, _ = lancer(n, "--installer", "--confirm", confirm)
            self.assertEqual(code, 2, confirm)
            self.assertEqual(n.journal, [])

    def test_le_plan_ne_contient_aucun_verbe_dangereux(self):
        for titre, argv in P.plan_installation("20261114T150000Z"):
            for mot in argv:
                self.assertNotIn(mot, MOTS_INTERDITS, titre)
        textes = " ".join(" ".join(a) for _, a in P.plan_installation("x"))
        self.assertIn("daemon-reload", textes)
        self.assertNotIn("enable", textes)

    def test_le_retrait_exige_sa_propre_confirmation(self):
        n = NoeudInstall()
        code, sortie = lancer(n, "--retirer")
        self.assertEqual(code, 0)
        self.assertIn("PLAN SEULEMENT", sortie)
        self.assertEqual(n.ecritures(), [])
        code, _ = lancer(n, "--retirer", "--confirm", P.CONFIRM_INSTALLER)
        self.assertEqual(code, 2)
        self.assertEqual(n.ecritures(), [])


class TestInstallation(SansInfra):
    def installer(self, n=None, **pannes):
        n = n or NoeudInstall()
        n.pannes.update(pannes)
        code, sortie = lancer(n, "--installer", "--confirm", P.CONFIRM_INSTALLER)
        return n, code, sortie

    def test_installation_nominale(self):
        n, code, sortie = self.installer()
        self.assertEqual(code, 0, sortie)
        chemins = S.chemins_noeud()
        for nom, c in chemins.items():
            self.assertEqual(sha(n.fichiers[c]), sha_depot()[nom], "%s : octets LF identiques au depot" % nom)
        self.assertTrue(n.reload_fait)
        self.assertEqual(sorted(n.fichiers), sorted(chemins.values()), "tmp retire : seuls les 3 fichiers installes restent")
        self.assertNotIn(TMP, n.dossiers, "repertoire temporaire retire")
        self.assertIn(P.RACINE_SCRIPT, n.dossiers)
        self.assertIn("INSTALLATION VERIFIEE", sortie)
        self.assertIn("NI active NI demarre", sortie)
        self.assertIn("UNE SEULE commande directement sur .158", sortie)
        self.assertIn("SANS armement", sortie)
        self.assertIn("SANS dependance de l'hote", sortie)
        self.assertNotIn("PRE-REQUIS AVANT TOUT DEMARRAGE", sortie, "par defaut, aucun pre-requis Windows")
        self.assertIn("sudo systemctl start helios-diskfill.service", sortie, "la commande directe est donnee A TITRE D'INDICATION : l'installateur ne la lance jamais")
        self.assertNotIn("armement depuis l'hote", sortie)
        self.assertIn("StateDirectoryMode=0700", sortie)

    def test_en_mode_requis_le_message_final_exige_l_agent_windows(self):
        with mock.patch.object(P.D, "ATTESTATION_HOTE_REQUISE", True):
            n, code, sortie = self.installer()
        self.assertEqual(code, 0, sortie)
        self.assertIn("PRE-REQUIS AVANT TOUT DEMARRAGE", sortie)
        self.assertIn("agent Windows DISK", sortie)

    def test_ordre_des_operations_et_jeu_ferme_de_commandes(self):
        n, code, _ = self.installer()
        self.assertEqual(code, 0)
        suite = []
        for genre, a in n.commandes():
            if genre == "local":
                suite.append("scp")
            elif a[0] == "mktemp":
                suite.append("mktemp")
            elif a[0] == "install":
                suite.append("install-d" if a[1] == "-d" else "install")
            elif a[0] == "systemd-analyze":
                suite.append("analyze")
            elif a[:2] == ["systemctl", "daemon-reload"]:
                suite.append("reload")
        self.assertEqual(suite, ["mktemp", "scp", "scp", "scp", "install-d", "install", "install", "install", "analyze", "reload"])
        # les pre-controles precedent toute ecriture
        premiere_ecriture = next(i for i, (g, a) in enumerate(n.commandes()) if g == "local" or a[0] in ("mktemp", "install", "cp", "rm"))
        lectures_avant = [a[0] for g, a in n.commandes()[:premiere_ecriture]]
        self.assertIn("hostname", lectures_avant)
        self.assertIn("sha256sum", lectures_avant)

    def test_jamais_enable_start_restart_mask(self):
        n, code, _ = self.installer()
        for genre, a in n.commandes():
            for mot in a:
                self.assertNotIn(mot, MOTS_INTERDITS)
            if a[0] == "systemctl":
                self.assertIn(a[1], ("--version", "list-units", "is-active", "show", "daemon-reload"))
        self.assertEqual(n.actives, {})

    def test_les_modes_et_proprietaires(self):
        n, _, _ = self.installer()
        installs = [a for g, a in n.commandes() if g == "ssh" and a[0] == "install" and a[1] != "-d"]
        self.assertEqual(len(installs), 3)
        for a in installs:
            self.assertEqual(a[1:7], ["-m", "0644", "-o", "root", "-g", "root"])
            self.assertEqual(a[7], "--")
            self.assertTrue(a[8].startswith(TMP + "/"))
        d = [a for g, a in n.commandes() if g == "ssh" and a[0] == "install" and a[1] == "-d"][0]
        self.assertEqual(d[1:], ["-d", "-m", "0755", "-o", "root", "-g", "root", "--", P.RACINE_SCRIPT])

    def test_les_octets_envoyes_sont_lf_meme_si_le_checkout_est_crlf(self):
        with tempfile.TemporaryDirectory() as t:
            for nom in S.NOMS_SOT:
                with open(os.path.join(t, nom), "wb") as f:
                    f.write(S.lire_source(nom).replace(b"\n", b"\r\n"))
            attendu = sha_depot()
            with mock.patch.object(S, "DIR_SVC", t):
                n, code, sortie = self.installer()
                self.assertEqual(code, 1 if S.lire_sommes_fichier() != attendu else 0, sortie)
        # SHA256SUMS absent de la copie CRLF : refus propre (DEP_SOMMES) ; avec le fichier de sommes copie : installation LF
        with tempfile.TemporaryDirectory() as t:
            for nom in S.NOMS_SOT:
                with open(os.path.join(t, nom), "wb") as f:
                    f.write(S.lire_source(nom).replace(b"\n", b"\r\n"))
            with open(os.path.join(t, "SHA256SUMS"), "wb") as f:
                f.write(S.contenu_sommes(attendu).encode("ascii"))
            with mock.patch.object(S, "DIR_SVC", t):
                n, code, sortie = self.installer()
        self.assertEqual(code, 0, sortie)
        for nom, c in S.chemins_noeud().items():
            self.assertNotIn(b"\r", n.fichiers[c])
            self.assertEqual(sha(n.fichiers[c]), attendu[nom])

    def test_verification_apres_installation_porte_sur_les_proprietes_chargees(self):
        n, code, sortie = self.installer()
        self.assertEqual(code, 0)
        m = n.monde()
        for u in (D.UNITE_PRINCIPALE, D.UNITE_GARDE):
            p = m.unites[u]
            self.assertEqual((p["LoadState"], p["UnitFileState"], p["StateDirectoryMode"], p["Restart"], p["DropInPaths"], p["NeedDaemonReload"]),
                             ("loaded", "static", "0700", "no", "", "no"))


class TestPrecontroles(SansInfra):
    def refus(self, mutation, attendu):
        n = NoeudInstall()
        mutation(n)
        code, sortie = lancer(n, "--installer", "--confirm", P.CONFIRM_INSTALLER)
        self.assertEqual(code, 1, sortie)
        self.assertIn(attendu, sortie)
        self.assertEqual(n.ecritures(), [], "un pre-controle ROUGE ne laisse AUCUNE ecriture")
        self.assertEqual(n.fichiers, {})

    def test_chaque_pre_controle_est_bloquant(self):
        cas = [
            ("mauvaise_machine", lambda n: setattr(n, "hostname", "k8s-worker2"), "DEP_IDENTITE"),
            ("hostname_vide", lambda n: setattr(n, "hostname", ""), "DEP_IDENTITE"),
            ("python_ancien", lambda n: setattr(n, "python", "Python 3.6.9"), "DEP_PYTHON"),
            ("python_illisible", lambda n: setattr(n, "python", "?"), "DEP_PYTHON"),
            ("systemd_ancien", lambda n: setattr(n, "systemd", "systemd 237 (237)"), "DEP_SYSTEMD"),
            ("fallocate_absent", lambda n: n.outils.discard("/usr/bin/fallocate"), "DEP_OUTIL"),
            ("analyze_absent", lambda n: n.outils.discard("/usr/bin/systemd-analyze"), "DEP_OUTIL"),
            ("unites_du_lanceur", lambda n: setattr(n, "transitoires", ["helios-diskfill-garde-" + "a" * 32 + ".service"]), "DEP_RESIDUS"),
            ("orphelins", lambda n: setattr(n, "dossier", ["helios-diskfill-" + "e" * 32 + "-001.bin"]), "DEP_ORPHELINS"),
            ("dossier_illisible", lambda n: setattr(n, "dossier_illisible", True), "DEP_DOSSIER"),
            ("service_actif", lambda n: n.actives.update({D.UNITE_PRINCIPALE: True}), "DEP_ACTIVE"),
            ("garde_active", lambda n: n.actives.update({D.UNITE_GARDE: True}), "DEP_ACTIVE"),
        ]
        for nom, mutation, attendu in cas:
            with self.subTest(nom):
                self.refus(mutation, attendu)

    def test_sommes_du_depot_perimees_bloquent(self):
        n = NoeudInstall()
        with mock.patch.object(S, "lire_sommes_fichier", lambda: None):
            code, sortie = lancer(n, "--installer", "--confirm", P.CONFIRM_INSTALLER)
        self.assertEqual(code, 1)
        self.assertIn("DEP_SOMMES", sortie)
        self.assertEqual(n.ecritures(), [])

    def test_tmp_impossible_aucune_ecriture_hors_de_tmp(self):
        n = NoeudInstall()
        n.pannes.add("mktemp_ko")
        code, sortie = lancer(n, "--installer", "--confirm", P.CONFIRM_INSTALLER)
        self.assertEqual(code, 1)
        self.assertEqual(n.fichiers, {})


class TestRetourArriere(SansInfra):
    def avec_existant(self):
        n = NoeudInstall()
        for nom, c in S.chemins_noeud().items():
            n.fichiers[c] = ("ANCIENNE VERSION de %s\n" % nom).encode("ascii")
        n.reload_fait = True
        return n

    def installer(self, n, **pannes):
        n.pannes.update(pannes)
        return lancer(n, "--installer", "--confirm", P.CONFIRM_INSTALLER)

    def test_scp_en_echec_rien_n_est_installe(self):
        n = NoeudInstall()
        code, sortie = self.installer(n, scp_ko=True)
        self.assertEqual(code, 1)
        self.assertEqual(n.installes(), {})
        self.assertNotIn(TMP, n.dossiers, "tmp retire meme en cas d'echec")

    def test_empreinte_envoyee_differente_rien_n_est_installe(self):
        n = NoeudInstall()
        code, sortie = self.installer(n, sha_envoye_different=True)
        self.assertEqual(code, 1)
        self.assertIn("rien n'est installe", sortie)
        self.assertEqual(n.installes(), {})

    def test_echec_d_une_installation_retour_arriere_complet_sans_existant(self):
        n = NoeudInstall()
        code, sortie = self.installer(n, **{"install_ko:helios-diskguard.service": True})
        self.assertEqual(code, 1)
        self.assertEqual(n.installes(), {}, "les fichiers deja installes par CETTE installation sont retires")
        self.assertIn("retour arriere", sortie)
        self.assertNotIn(TMP, n.dossiers)

    def test_systemd_analyze_en_echec_retour_arriere(self):
        n = NoeudInstall()
        code, sortie = self.installer(n, analyze_ko=True)
        self.assertEqual(code, 1)
        self.assertEqual(n.installes(), {})

    def test_daemon_reload_en_echec_retour_arriere(self):
        n = NoeudInstall()
        code, sortie = self.installer(n, reload_ko=True)
        self.assertEqual(code, 1)
        self.assertEqual(n.installes(), {})
        self.assertIn("daemon-reload : rc=1", sortie)
        self.assertNotIn("VERIFICATION de l'installation", sortie, "un daemon-reload en echec arrete aussitot l'installation (retour arriere immediat)")

    def test_verification_en_echec_par_state_directory_mode_retour_arriere(self):
        """systemd ne rend pas StateDirectoryMode=0700 (defaut 0755) : le Bridge refuserait toute suppression : l'installation est ANNULEE."""
        n = NoeudInstall()
        code, sortie = self.installer(n, mode_0755=True)
        self.assertEqual(code, 1, sortie)
        self.assertIn("SVC_UNITE", sortie)
        self.assertIn("StateDirectoryMode=0755", sortie)
        self.assertEqual(n.installes(), {})

    def test_une_unite_a_recharger_apres_installation_est_refusee_retour_arriere(self):
        """NeedDaemonReload=yes : la configuration CHARGEE n'est pas celle du fichier ; le noeud refuserait de demarrer : l'installation est ANNULEE."""
        n = NoeudInstall()
        code, sortie = self.installer(n, a_recharger=True)
        self.assertEqual(code, 1, sortie)
        self.assertIn("SVC_UNITE", sortie)
        self.assertIn("NeedDaemonReload=yes", sortie)
        self.assertEqual(n.installes(), {})

    def test_a_blanc_du_noeud_en_echec_retour_arriere(self):
        n = NoeudInstall()
        code, sortie = self.installer(n, a_blanc_ko=True)
        self.assertEqual(code, 1)
        self.assertEqual(n.installes(), {})

    def test_mise_a_jour_sauvegarde_puis_remplace(self):
        n = self.avec_existant()
        anciens = dict(n.fichiers)
        code, sortie = self.installer(n)
        self.assertEqual(code, 0, sortie)
        for nom, c in S.chemins_noeud().items():
            self.assertEqual(sha(n.fichiers[c]), sha_depot()[nom])
            sauvegarde = "%s.AVANT-DISKFILL-%s" % (c, P.horodatage_utc(STAMP_FIXE))
            self.assertEqual(n.fichiers[sauvegarde], anciens[c], "copie de sauvegarde conforme a l'ancienne version")

    def test_echec_de_mise_a_jour_restaure_les_anciennes_versions(self):
        n = self.avec_existant()
        anciens = dict(n.fichiers)
        code, sortie = self.installer(n, **{"install_ko:helios-diskguard.service": True})
        self.assertEqual(code, 1)
        for c, contenu in anciens.items():
            self.assertEqual(n.fichiers[c], contenu, "%s restaure" % c)

    def test_sauvegarde_impossible_rien_n_est_remplace(self):
        n = self.avec_existant()
        anciens = dict(n.fichiers)
        code, sortie = self.installer(n, cp_ko=True)
        self.assertEqual(code, 1)
        for c, contenu in anciens.items():
            self.assertEqual(n.fichiers[c], contenu)

    def test_le_retour_arriere_ne_retire_que_des_noms_exacts(self):
        n = NoeudInstall()
        n.fichiers["/etc/systemd/system/autre.service"] = b"autre"
        n.fichiers["/opt/helios-diskfill/notes.txt"] = b"notes"
        self.installer(n, analyze_ko=True)
        self.assertIn("/etc/systemd/system/autre.service", n.fichiers)
        self.assertIn("/opt/helios-diskfill/notes.txt", n.fichiers)
        for genre, a in n.commandes():
            if genre == "ssh" and a[0] == "rm":
                self.assertEqual(a[1:3], ["-f", "--"])
                for c in a[3:]:
                    self.assertIn(c, list(S.chemins_noeud().values()) + [TMP + "/" + x for x in S.NOMS_SOT])


class TestRetraitEtVerification(SansInfra):
    def installe(self):
        n = NoeudInstall()
        self.assertEqual(lancer(n, "--installer", "--confirm", P.CONFIRM_INSTALLER)[0], 0)
        n.journal.clear()
        return n

    def test_verifier_est_en_lecture_seule(self):
        n = self.installe()
        code, sortie = lancer(n, "--verifier")
        self.assertEqual(code, 0, sortie)
        self.assertEqual(n.ecritures(), [])
        self.assertIn("INSTALLATION CONFORME", sortie)

    def test_verifier_non_installe(self):
        n = NoeudInstall()
        code, sortie = lancer(n, "--verifier")
        self.assertEqual(code, 1)
        self.assertIn("SVC_NON_INSTALLE", sortie)
        self.assertEqual(n.ecritures(), [])

    def test_verifier_detecte_une_unite_modifiee_et_un_mode_incorrect(self):
        n = self.installe()
        c = D.CHEMIN_UNITE[D.UNITE_PRINCIPALE]
        n.fichiers[c] = n.fichiers[c].replace(b"StateDirectoryMode=0700", b"StateDirectoryMode=0755")
        code, sortie = lancer(n, "--verifier")
        self.assertEqual(code, 1)
        self.assertIn("SVC_SHA_HELIOS_DISKFILL_SERVICE", sortie)
        self.assertIn("SVC_UNITE", sortie)

    def test_verifier_detecte_une_unite_a_recharger(self):
        n = self.installe()
        n.pannes.add("a_recharger")
        code, sortie = lancer(n, "--verifier")
        self.assertEqual(code, 1, sortie)
        self.assertIn("SVC_UNITE", sortie)
        self.assertIn("NeedDaemonReload=yes", sortie)
        self.assertEqual(n.ecritures(), [])

    def test_retrait_plan_puis_execution(self):
        n = self.installe()
        n.fichiers["/var/lib/helios-diskfill-etat-intact"] = b"donnees"
        code, sortie = lancer(n, "--retirer")
        self.assertEqual(code, 0)
        self.assertEqual(len(n.installes()), 3, "plan seulement")
        code, sortie = lancer(n, "--retirer", "--confirm", P.CONFIRM_RETIRER)
        self.assertEqual(code, 0, sortie)
        self.assertEqual(n.installes(), {})
        self.assertIn("/var/lib/helios-diskfill-etat-intact", n.fichiers, "rien d'autre n'est supprime")
        self.assertIn("ne sont pas touches", sortie)
        suppressions = [a for g, a in n.commandes() if g == "ssh" and a[0] in ("rm", "rmdir")]
        self.assertEqual(suppressions[0][:3], ["rm", "-f", "--"])
        self.assertEqual(sorted(suppressions[0][3:]), sorted(S.chemins_noeud().values()))
        self.assertEqual(suppressions[1], ["rmdir", "--", P.RACINE_SCRIPT])

    def test_retrait_refuse_pendant_un_lancement_ou_avec_des_orphelins(self):
        for mutation in (lambda n: n.actives.update({D.UNITE_PRINCIPALE: True}), lambda n: n.actives.update({D.UNITE_GARDE: True}),
                         lambda n: setattr(n, "dossier", ["helios-diskfill-" + "e" * 32 + "-001.bin"]), lambda n: setattr(n, "dossier_illisible", True),
                         lambda n: setattr(n, "hostname", "k8s-worker2")):
            n = self.installe()
            mutation(n)
            code, sortie = lancer(n, "--retirer", "--confirm", P.CONFIRM_RETIRER)
            self.assertEqual(code, 1, sortie)
            self.assertEqual(len(n.installes()), 3)
            self.assertEqual(n.ecritures(), [])


class TestSourceInstallateur(SansInfra):
    @classmethod
    def setUpClass(cls):
        cls.texte = open(os.path.join(HERE, "deploy_diskfill_service_158.py"), "rb").read().decode("ascii")
        cls.arbre = ast.parse(cls.texte)

    def test_aucun_sous_processus_ni_connexion(self):
        importes = set()
        for n in ast.walk(self.arbre):
            if isinstance(n, ast.Import):
                importes.update(a.name for a in n.names)
            elif isinstance(n, ast.ImportFrom):
                importes.add(n.module)
        for interdit in ("subprocess", "socket", "urllib.request", "shutil", "paramiko", "ctypes"):
            self.assertNotIn(interdit, importes)
        self.assertNotIn("os.system", self.texte)
        self.assertNotIn("shell=True", self.texte)

    def test_aucun_verbe_dangereux_dans_les_chaines_litterales(self):
        litteraux = set(n.value for n in ast.walk(self.arbre) if isinstance(n, ast.Constant) and isinstance(n.value, str))
        self.assertEqual(litteraux & MOTS_INTERDITS, set(), "enable / start / restart / mask / stop / -r ne figurent dans AUCUN argument")
        for lit in litteraux:
            if lit.startswith("rm") or lit.startswith("systemctl "):
                self.assertNotIn(" -r", lit)

    def test_les_seules_ecritures_locales_sont_dans_un_repertoire_temporaire(self):
        ouvertures = [n for n in ast.walk(self.arbre) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "open"]
        self.assertEqual(len(ouvertures), 1)
        self.assertIn("TemporaryDirectory", self.texte)

    def test_les_modes_installes_sont_figes(self):
        self.assertEqual(P.MODES, {"diskfill.py": "0644", "helios-diskfill.service": "0644", "helios-diskguard.service": "0644"})
        self.assertEqual(P.RACINE_SCRIPT, "/opt/helios-diskfill")


if __name__ == "__main__":
    unittest.main(verbosity=2)
