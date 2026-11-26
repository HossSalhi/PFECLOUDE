# -*- coding: utf-8 -*-
"""CYCLE DE VIE SYSTEMD du service local DISK de .158 : `sudo systemctl start helios-diskfill.service` DOIT SUFFIRE a derouler le scenario.

Ce fichier ajoute un BANC systemd (classe `Systemd`) PILOTE PAR LES VRAIS FICHIERS D'UNITE de infra/helios-sot/diskfill/ : rien du comportement de
systemd n'y est code en dur sans venir de ces fichiers. Il emule, au strict necessaire :
  - `Requires=` + `After=` : UN SEUL `systemctl start` demarre la garde independante AVANT le service principal ;
  - `ExecStartPre=+` (hors bac a sable) -> `ExecStart=` (dans le bac a sable) -> `ExecStopPost=` (toujours, meme apres un refus ou un coup de SIGKILL) ;
  - `Type=simple` (`systemctl start` rend la main apres ExecStartPre, pas a la fin du scenario), `Restart=no`, `RemainAfterExit=no` ;
  - `RuntimeMaxSec=` (SIGTERM puis `Result=timeout`), `TimeoutStartSec=`, `SuccessExitStatus=`, `KillSignal=SIGTERM` ;
  - `StateDirectory=`/`RuntimeDirectory=` + `StateDirectoryMode=` (defaut de systemd 0755 si absent) : le repertoire existant n'est jamais corrige ;
  - `ProtectSystem=strict` : une ecriture hors des repertoires d'etat declares echoue (EROFS), sauf pour une commande prefixee `+` ;
  - l'etat de l'unite (`UnitFileState` static / disabled selon la presence de [Install]) lu par `systemctl show` ;
  - le demarrage de la machine (`/run` vide, nouveau boot_id) : aucune unite sans [Install] ne demarre seule.
Le programme du noeud est le VRAI (diskfill.py) ; HELIOS (detection, Bridge) est simule sur le VRAI `disque_plan` du Bridge ; la garde est le VRAI
`garde_tour`. Le monde est celui de test_helios_diskfill_service.py (horloge simulee, systeme de fichiers en memoire).

DEMARRAGE DIRECT (decision du 15 nov. 2026) : AUCUN armement ni preparation avant `systemctl start` ; chaque refus vient d'un controle INTERNE
(identite, empreintes des unites, repertoire, inodes, cooldown, garde...). L'ancien exercice « refus sans armement » n'existe plus : la preuve non
destructive d'une installation est `diskfill.py verifier --a-blanc`.

CE QUE CE BANC NE PROUVE PAS : le comportement du vrai systemd 255 sur .158 (ordre `Requires=`/`+`/bac a sable, creation des repertoires, format de
`systemctl show`). Il documente les HYPOTHESES (ci-dessus) et verifie que les fichiers d'unite sont coherents avec elles ; la preuve finale est
l'exercice non destructif apres installation (voir RUNBOOK-DISKFILL-SERVICE-158.md).

AUCUN reseau, AUCUNE VM, AUCUN ssh, AUCUN subprocess, AUCUN sleep reel.

Lancer : python -I -B test_helios_diskfill_systemd.py -v        (depuis scripts/helios-ops)
"""
import contextlib
import copy
import errno
import importlib.util
import os
import re
import shlex
import sys
import types
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
DIR_SVC = os.path.join(REPO, "infra", "helios-sot", "diskfill")
if HERE not in sys.path:
    sys.path.insert(0, HERE)


def _charger(nom, chemin):
    spec = importlib.util.spec_from_file_location(nom, chemin)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[nom] = mod
    spec.loader.exec_module(mod)
    return mod


TA = _charger("test_arm_pour_systemd", os.path.join(HERE, "test_helios_diskfill_arm.py"))      # Banc (hote + noeud) ; importer n'execute aucun test
TS = TA.TS                                                                                       # MondeEmule du noeud
D = TS.D
MIO = D.MIO
PRINCIPALE, GARDE = D.UNITE_PRINCIPALE, D.UNITE_GARDE
CHEMINS_UNITES = {PRINCIPALE: os.path.join(DIR_SVC, "helios-diskfill.service"), GARDE: os.path.join(DIR_SVC, "helios-diskguard.service")}


# ------------------------------------------------------------------------------------------------ fichiers d'unite
def analyser_unite(texte):
    """Texte d'une unite -> {section: {cle: [valeurs]}}. Refuse ce que l'emulateur ne sait pas interpreter (jamais d'interpretation devinee)."""
    sections, courante = {}, None
    for ligne in texte.split("\n"):
        s = ligne.strip()
        if not s or s[0] in "#;":
            continue
        m = re.fullmatch(r"\[([A-Za-z]+)\]", s)
        if m:
            courante = sections.setdefault(m.group(1), {})
            continue
        if courante is None or "=" not in s or s.endswith("\\"):
            raise AssertionError("ligne d'unite non geree par l'emulateur : %r" % ligne)
        cle, _, valeur = s.partition("=")
        if valeur.strip() == "":
            raise AssertionError("affectation vide (remise a zero) non geree par l'emulateur : %r" % ligne)
        courante.setdefault(cle.strip(), []).append(valeur.strip())
    return sections


def texte_unite(nom):
    with open(CHEMINS_UNITES[nom], "rb") as f:
        return f.read().decode("ascii")


class Unite(object):
    def __init__(self, nom, texte):
        self.nom, self.s = nom, analyser_unite(texte)

    def un(self, cle, defaut=None, section="Service"):
        v = self.s.get(section, {}).get(cle)
        return v[-1] if v else defaut

    def mots(self, cle, section="Unit"):
        sortie = []
        for v in self.s.get(section, {}).get(cle, []):
            sortie.extend(v.split())
        return sortie

    def lignes(self, cle, section="Service"):
        return list(self.s.get(section, {}).get(cle, []))

    def a_install(self):
        return "Install" in self.s

    def proprietes(self):
        """Ce que `systemctl show -p ...` rapporte pour cette unite (le defaut de StateDirectoryMode est 0755)."""
        return {"LoadState": "loaded", "FragmentPath": D.CHEMIN_UNITE[self.nom], "DropInPaths": "",
                "UnitFileState": "disabled" if self.a_install() else "static",
                "StateDirectoryMode": self.un("StateDirectoryMode", "0755"), "Restart": self.un("Restart", "no"),
                "NeedDaemonReload": "no"}                                                         # charge juste apres un daemon-reload


# ------------------------------------------------------------------------------------------------ processus tue (SIGKILL)
class ProcessusTue(BaseException):
    """SIGKILL : le processus disparait SANS executer ses blocs `finally`. Python ne sait pas les sauter : l'emulateur restaure l'etat du monde pris
    au moment du coup (ce que le `finally` a fait ensuite n'a jamais eu lieu)."""

    def __init__(self, instantane):
        BaseException.__init__(self)
        self.instantane = instantane


def _instantane(m):
    return (copy.deepcopy(m.fichiers), copy.deepcopy(m.etrangers), copy.deepcopy(m.textes), list(m.appels))


def _restaurer(m, snap):
    fichiers, etrangers, textes, appels = snap
    m.fichiers.clear()
    m.fichiers.update(fichiers)
    m.etrangers.clear()
    m.etrangers.update(etrangers)
    m.textes.clear()
    m.textes.update(textes)
    m.appels[:] = appels


def tuer(m):
    raise ProcessusTue(_instantane(m))


class Journal(list):
    """Journal d'une unite : chaque ligne est aussi inscrite, datee, dans la chronologie commune."""

    def __init__(self, sd, nom):
        list.__init__(self)
        self.sd, self.nom = sd, nom

    def append(self, ligne):
        list.append(self, ligne)
        self.sd.chrono.append((self.sd.m.t, self.nom, ligne))


# ------------------------------------------------------------------------------------------------ le banc systemd
class Systemd(object):
    def __init__(self, m, textes=None, substitutions=None, repertoires_avant_pre=True, espace_de_noms_unite=True):
        """`repertoires_avant_pre=False` : variante PESSIMISTE (non verifiable hors VM) ou systemd ne creerait les repertoires de l'unite principale
        qu'au lancement de ExecStart, pas avant ExecStartPre=+ : seuls ceux de la garde (demarree avant) existeraient au moment du controle."""
        self.m = m
        self.repertoires_avant_pre = repertoires_avant_pre
        self.espace_de_noms_unite = espace_de_noms_unite           # CONSTATE sur .158 (systemd 255) : meme `ExecStartPre=+` voit les StateDirectory en bind mount
        self.unites = {}
        for nom in CHEMINS_UNITES:
            texte = (textes or {}).get(nom)
            self.unites[nom] = Unite(nom, texte if texte is not None else texte_unite(nom))
            m.unites[nom] = self.unites[nom].proprietes()
        self.substitutions = dict(substitutions or {})
        self.chrono = []                  # (t, unite|"systemctl", ligne)
        self.journaux = dict((nom, Journal(self, nom)) for nom in self.unites)
        m.journal_garde = self.journaux[GARDE]
        self.etat = dict((nom, {"ActiveState": "inactive", "Result": "success", "ExecMainStatus": None, "t_active": None}) for nom in self.unites)
        self.commandes = []               # (instant, commande systemctl) dans l'ordre d'emission
        self.allocations = []             # preuve prise AVANT CHAQUE allocation : la garde est-elle vivante et liee au jeton du lancement ?
        self.ecritures_refusees = []
        self._pile = []                   # contextes d'execution : (Unite, privilegie) ; privilegie=True pour une commande `+` (sans bac a sable)
        self._n_invocation = 0
        self._n_boot = 1
        self._installer()

    # --- branchements sur le monde emule
    def _installer(self):
        m, sd = self.m, self

        def autorises():
            if not sd._pile or sd._pile[-1][1]:
                return None
            u = sd._pile[-1][0]
            return ["/var/lib/" + n for n in u.mots("StateDirectory", "Service")] + ["/run/" + n for n in u.mots("RuntimeDirectory", "Service")]

        def controler(chemin):
            ok = autorises()
            if ok is not None and not any(chemin == p or chemin.startswith(p + "/") for p in ok):
                sd.ecritures_refusees.append(chemin)
                raise OSError(errno.EROFS, "Read-only file system (ProtectSystem=strict)", chemin)

        vrai = dict((n, getattr(m, n)) for n in ("ecrire_json", "supprimer", "allouer", "liberer", "_tour_garde", "info_chemin"))

        def ecrire_json(chemin, obj):
            controler(chemin)
            return vrai["ecrire_json"](chemin, obj)

        def supprimer(chemin):
            controler(chemin)
            return vrai["supprimer"](chemin)

        def allouer(nom, octets):
            controler(D.DOSSIER)
            sd._preuve_allocation(nom)
            return vrai["allouer"](nom, octets)

        def liberer(noms):
            controler(D.DOSSIER)
            return vrai["liberer"](noms)

        def info_chemin(chemin):
            """Dans l'espace de noms d'une unite (commande `+` comprise), un StateDirectory/RuntimeDirectory est un BIND MOUNT de lui-meme."""
            info = vrai["info_chemin"](chemin)
            if info is not None and sd.espace_de_noms_unite and sd._pile:
                u = sd._pile[-1][0]
                dossiers = ["/var/lib/" + n for n in u.mots("StateDirectory", "Service")] + ["/run/" + n for n in u.mots("RuntimeDirectory", "Service")]
                if chemin in dossiers:
                    info = dict(info, montage=chemin)
            return info

        def unite_occupee(unite):
            if "occupee_illisible" in m.pannes:
                raise D.ErreurNoeud("systemctl is-active illisible")
            return sd.actif(unite)

        def tour_garde():
            if not m.garde_active:
                return
            g = sd.unites[GARDE]
            rms = int(g.un("RuntimeMaxSec", "0"))
            if rms and m.t - sd.etat[GARDE]["t_active"] >= rms:
                sd.journaux[GARDE].append("Service reached runtime time limit. Stopping.")
                m.garde_active, m.garde_code = False, None
                sd.etat[GARDE].update(ActiveState="failed", Result="timeout")
                return
            sd._pile.append((g, False))
            try:
                vrai["_tour_garde"]()
            finally:
                sd._pile.pop()
            if not m.garde_active:
                sd._fin_garde()

        m.ecrire_json, m.supprimer, m.allouer, m.liberer = ecrire_json, supprimer, allouer, liberer
        m.unite_occupee, m._tour_garde, m.info_chemin = unite_occupee, tour_garde, info_chemin

    def _preuve_allocation(self, nom):
        m = self.m
        etat, bat = m.textes.get(D.F_ETAT) or {}, m.textes.get(D.F_BATTEMENT) or {}
        self.allocations.append({"t": m.t, "nom": nom, "garde_active": bool(m.garde_active), "jeton_etat": etat.get("jeton"),
                                 "jeton_battement": bat.get("jeton"), "age_battement": (m.t - bat["epoch"]) if "epoch" in bat else None})

    def _fin_garde(self):
        code = self.m.garde_code
        self.etat[GARDE].update(ActiveState="inactive" if code == 0 else "failed", Result="success" if code == 0 else "exit-code", ExecMainStatus=code)

    # --- etat des unites
    def actif(self, nom):
        if nom == GARDE:
            return bool(self.m.garde_active)
        return self.etat[nom]["ActiveState"] in ("activating", "active", "deactivating")

    def show(self, nom):
        return {"ActiveState": "active" if nom == GARDE and self.m.garde_active else self.etat[nom]["ActiveState"], "Result": self.etat[nom]["Result"],
                "ExecMainStatus": self.etat[nom]["ExecMainStatus"], "UnitFileState": self.unites[nom].proprietes()["UnitFileState"]}

    def attendre(self, secondes):
        self.m.dormir(secondes)

    # --- commandes systemctl
    def enable(self, nom):
        """Une unite SANS section [Install] ne peut pas etre activee : systemctl le dit et ne cree AUCUN lien."""
        self.commandes.append((self.m.t, "systemctl enable " + nom))
        return "enabled" if self.unites[nom].a_install() else "no-install-config"

    def boot_units(self):
        """Unites demarrees au boot : celles qui declarent WantedBy=/RequiredBy= (section [Install])."""
        return [n for n, u in self.unites.items() if u.mots("WantedBy", "Install") or u.mots("RequiredBy", "Install")]

    def reboot(self):
        """Redemarrage du noeud : /run (tmpfs) est vide, nouveau boot_id, plus aucune unite active ; /var/lib et les fichiers du disque persistent."""
        m = self.m
        for chemin in [c for c in m.textes if c.startswith(D.RUN_DIR + "/")]:
            del m.textes[chemin]
        m.infos.pop(D.RUN_DIR, None)
        self._n_boot += 1
        m.boot = "boot-%04d" % self._n_boot
        m.garde_active = False
        for nom in self.unites:
            self.etat[nom].update(ActiveState="inactive", Result="success", ExecMainStatus=None, t_active=None)
        m.t += 90.0
        self.chrono.append((m.t, "systemctl", "REBOOT"))
        for nom in self.boot_units():                         # seules les unites avec [Install] reviennent seules (jamais les notres)
            self.start(nom)

    def _repertoires(self, u):
        """systemd cree les repertoires declares AVANT le premier processus de l'unite ; un repertoire EXISTANT n'est jamais corrige (hypothese prudente)."""
        m = self.m
        for nom in u.mots("StateDirectory", "Service"):
            chemin = "/var/lib/" + nom
            if chemin not in m.infos or (chemin == D.DOSSIER and not m.dossier_present):
                mode = u.un("StateDirectoryMode", "0755").lstrip("0") or "0"
                m.infos[chemin] = dict(type="directory", uid=0, gid=0, mode=mode, dev=64512, fstype="ext4", montage="/")
                if chemin == D.DOSSIER:
                    m.dossier_present = True
        for nom in u.mots("RuntimeDirectory", "Service"):
            chemin = "/run/" + nom
            if chemin not in m.infos:
                mode = u.un("RuntimeDirectoryMode", "0755").lstrip("0") or "0"
                m.infos[chemin] = dict(type="directory", uid=0, gid=0, mode=mode, dev=21, fstype="tmpfs", montage="/run")

    def _exec(self, u, ligne, journal):
        brut, privilegie, ignorer = ligne, False, False
        while brut[:1] in ("+", "-"):
            privilegie = privilegie or brut[0] == "+"
            ignorer = ignorer or brut[0] == "-"
            brut = brut[1:]
        if brut[:1] in ("!", "@", ":"):
            raise AssertionError("prefixe systemd non gere par l'emulateur : %r" % ligne)
        argv = shlex.split(brut)
        if argv[:4] != ["/usr/bin/python3", "-I", "-B", "-u"] or argv[4] != D.CHEMIN_SCRIPT:
            raise AssertionError("commande inattendue dans l'unite : %r" % (argv,))
        args = argv[5:]
        D.construire_parseur().parse_args(args)                                      # le VRAI analyseur doit accepter la ligne de l'unite
        self._pile.append((u, privilegie))
        try:
            if args[0] in self.substitutions:
                rc = self.substitutions[args[0]](self.m, journal.append)
            else:
                rc = D.principal(args, monde=self.m, sortie=journal.append)
        finally:
            self._pile.pop()
        return rc, ignorer

    # --- demarrage de la garde (dependance)
    def _demarrer_garde(self):
        """Demarre l'unite de garde (Type=simple : `demarree` des la creation du processus). False = le job de demarrage echoue."""
        m, g = self.m, self.unites[GARDE]
        for chemin in g.mots("AssertPathExists"):
            if m.sha256(chemin) is None:
                self.journaux[GARDE].append("Assertion failed: AssertPathExists=%s" % chemin)
                return False
        if "garde_refuse" in m.pannes:
            self.etat[GARDE].update(ActiveState="failed", Result="exec", ExecMainStatus=203)
            return False
        self._exec_a_blanc(g, g.lignes("ExecStart")[0], attendu="garde")
        self._repertoires(g)
        self.etat[GARDE].update(ActiveState="active", Result="success", ExecMainStatus=None, t_active=m.t)
        self.chrono.append((m.t, "systemctl", "unite %s demarree" % GARDE))
        if "garde_meurt" in m.pannes:                                                 # le processus part puis meurt avant son premier battement
            m.garde_active, m.garde_code = False, 1
            self.journaux[GARDE].append("Main process exited, code=exited, status=1/FAILURE")
            self._fin_garde()
            return True
        self._pile.append((g, False))
        try:
            m.demarrer_garde()
        finally:
            self._pile.pop()
        if not m.garde_active:
            self._fin_garde()
        return True

    def _exec_a_blanc(self, u, ligne, attendu):
        brut = ligne.lstrip("+-")
        argv = shlex.split(brut)
        assert argv[:4] == ["/usr/bin/python3", "-I", "-B", "-u"] and argv[4] == D.CHEMIN_SCRIPT, argv
        D.construire_parseur().parse_args(argv[5:])
        assert argv[5:] == [attendu], argv

    # --- systemctl start
    def start(self, nom=None, ops=()):
        """`sudo systemctl start <unite>` : UNE commande. `ops` = actions de l'exploitant APRES le demarrage, [(delai_s, "stop"|"restart"|"kill")]
        (delai compte depuis le moment ou `systemctl start` rend la main). Retourne le compte rendu."""
        nom = nom or PRINCIPALE
        m, u = self.m, self.unites[nom]
        r = types.SimpleNamespace(nom=nom, t_debut=m.t, t_retour=None, t_fin=None, rc=None, result=None, exit_status=None, no_op=False,
                                  delai_arret=None, redemarrage=None, tue=False)
        self.commandes.append((m.t, "systemctl start " + nom))
        self.chrono.append((m.t, "systemctl", "start " + nom))
        if self.actif(nom):
            r.rc, r.no_op = 0, True                                                    # deja actif : systemd ne relance RIEN
            return r
        if nom == GARDE:
            r.rc = 0 if self._demarrer_garde() else 1
            return r
        for chemin in u.mots("AssertPathExists"):
            if m.sha256(chemin) is None:
                r.rc, r.result, r.t_fin = 1, "assert", m.t
                return r
        for dep in u.mots("Requires"):
            if not self.actif(dep) and not self._demarrer_garde():
                self.etat[nom].update(ActiveState="failed", Result="dependency")
                r.rc, r.result, r.t_fin = 1, "dependency", m.t
                return r
        st, journal = self.etat[nom], self.journaux[nom]
        st.update(ActiveState="activating", Result="success", ExecMainStatus=None, t_active=None)   # actif des que le job demarre (apres la garde)
        self._n_invocation += 1
        m.inv = "inv-%04d" % self._n_invocation
        m.dormir(1)                                                                      # latence d'un job systemd : le temps ne s'arrete jamais
        if self.repertoires_avant_pre:
            self._repertoires(u)
        resultat = "success"
        pre_ok = True
        for ligne in u.lignes("ExecStartPre"):
            rc, ignorer = self._exec(u, ligne, journal)
            self.chrono.append((m.t, nom, "ExecStartPre termine rc=%s" % rc))
            if rc != 0 and not ignorer:
                pre_ok, resultat = False, "exit-code"
                break
        if pre_ok and m.t - r.t_debut > int(u.un("TimeoutStartSec", "90")):
            pre_ok, resultat = False, "timeout"
        if not pre_ok:
            r.rc = 1
            return self._finir(u, r, resultat, None)
        if not self.repertoires_avant_pre:
            self._repertoires(u)
        r.t_retour, r.rc = m.t, 0                                                       # Type=simple : `systemctl start` rend la main ICI
        t_active = m.t
        st.update(ActiveState="active", t_active=t_active)
        rms = int(u.un("RuntimeMaxSec", "0"))
        limite = t_active + rms if rms else None
        arret, redemarrer = None, False
        for delai, action in ops:
            instant = t_active + delai
            if action == "kill":
                self.commandes.append((instant, "systemctl kill -s KILL " + nom))
                m.evenements.append((instant, tuer))
            else:
                self.commandes.append((instant, "systemctl %s %s" % (action, nom)))
                arret = instant if arret is None else min(arret, instant)
                redemarrer = redemarrer or action == "restart"
        candidats = [x for x in (arret, limite) if x is not None]
        sigterm_a = min(candidats) if candidats else None
        m.arret_a = sigterm_a
        signal, rc = None, None
        try:
            rc, _ = self._exec(u, u.lignes("ExecStart")[0], journal)
        except ProcessusTue as e:
            _restaurer(m, e.instantane)
            signal = "KILL"
            journal.append("Main process exited, code=killed, status=9/KILL")
        finally:
            fin = m.t
            if m.arret_a is not None and m.arret_a > fin:
                m.arret_a = None
            m.arret_a = None
            m.evenements[:] = [e for e in m.evenements if e[1] is not tuer]
        succes = [int(x) for x in u.mots("SuccessExitStatus", "Service")]
        if signal == "KILL":
            resultat, r.tue, r.exit_status = "signal", True, "9/KILL"
        else:
            r.exit_status = rc
            if sigterm_a is not None and limite is not None and sigterm_a == limite and fin >= limite:
                resultat = "timeout"                                                   # RuntimeMaxSec : le premier echec garde la main (systemd)
            elif rc == 0 or rc in succes:
                resultat = "success"
            else:
                resultat = "exit-code"
            if arret is not None and fin >= arret:
                r.delai_arret = fin - arret
                if r.delai_arret > int(u.un("TimeoutStopSec", "90")):
                    resultat = "timeout" if resultat == "success" else resultat       # systemd aurait envoye SIGKILL
        self._finir(u, r, resultat, r.exit_status)
        if redemarrer:
            r.redemarrage = self.start(nom)
        return r

    def _finir(self, u, r, resultat, exit_status):
        m, st, journal = self.m, self.etat[u.nom], self.journaux[u.nom]
        st["ActiveState"] = "deactivating"
        for ligne in u.lignes("ExecStopPost"):
            rc_post, _ = self._exec(u, ligne, journal)
            if rc_post != 0 and resultat == "success":
                resultat = "exit-code"
        st.update(ActiveState="inactive" if resultat == "success" else "failed", Result=resultat, ExecMainStatus=exit_status)
        r.result, r.t_fin = resultat, m.t
        self.chrono.append((m.t, "systemctl", "unite %s : %s (%s)" % (u.nom, st["ActiveState"], resultat)))
        return r


# ------------------------------------------------------------------------------------------------ aides de scenario
def noeud(helios="resolve", textes=None, substitutions=None, repertoires_avant_pre=True, **kw):
    """Noeud .158 emule SANS garde preexistante (c'est le `systemctl start` qui la demarre)."""
    m = TS.MondeEmule(garde=False, helios=helios, **kw)
    return m, Systemd(m, textes=textes, substitutions=substitutions, repertoires_avant_pre=repertoires_avant_pre)


def resultats(journal):
    """Lignes `RESULTAT` d'un journal -> liste de dictionnaires (cle -> valeur texte)."""
    sortie = []
    for ligne in journal:
        m = re.fullmatch(r"helios-diskfill : RESULTAT : (verdict=\S+ code=\d+(?: [a-z_0-9]+=\S+)*)", ligne)
        if m:
            sortie.append(dict(kv.split("=", 1) for kv in m.group(1).split()))
    return sortie


def variante(nom, *remplacements):
    """Texte d'une unite avec des remplacements EXACTS (chacun doit apparaitre une seule fois : jamais une modification silencieuse)."""
    texte = texte_unite(nom)
    for ancien, nouveau in remplacements:
        if texte.count(ancien) != 1:
            raise AssertionError("motif %r : %d occurrence(s) dans %s" % (ancien, texte.count(ancien), nom))
        texte = texte.replace(ancien, nouveau)
    return texte


def croissance(points):
    """Evenement : l'occupation de / augmente de `points` points de pourcentage (autre processus du noeud)."""
    return lambda mm: setattr(mm, "organique", mm.organique + points / 100.0 * mm.taille)


def evenement_a(m, delai, fonction):
    m.evenements.append((m.t + delai, fonction))


def fichier_etranger(nom):
    return lambda mm: mm.etrangers.__setitem__(nom, {"taille": 1, "uid": 0, "nlink": 1, "type": "f"})


class Base(TS.SansInfra):
    def noms_alloues(self, m):
        return [a[1] for a in m.appels_de("allouer")]

    def assertUnResultat(self, sd, nom=PRINCIPALE, **attendu):
        liste = resultats(sd.journaux[nom])
        self.assertEqual(len(liste), 1, "exactement UNE ligne RESULTAT attendue dans le journal :\n" + "\n".join(sd.journaux[nom]))
        for cle, valeur in attendu.items():
            self.assertEqual(liste[0].get(cle), valeur, "%s : %r != %r\n%s" % (cle, liste[0].get(cle), valeur, "\n".join(sd.journaux[nom])))
        return liste[0]

    def garde_evidence(self, sd):
        """A CHAQUE allocation : garde vivante, liee au jeton du lancement, battement recent."""
        self.assertTrue(sd.allocations, "aucune allocation")
        for a in sd.allocations:
            self.assertTrue(a["garde_active"], a)
            self.assertIsNotNone(a["jeton_etat"], a)
            self.assertEqual(a["jeton_battement"], a["jeton_etat"], "la garde doit etre liee au jeton AVANT l'allocation : %r" % (a,))
            self.assertLessEqual(a["age_battement"], D.BATTEMENT_MAX_S, a)


# ------------------------------------------------------------------------------------------------ les unites pilotent bien le banc
class TestUnitesEtBanc(Base):
    def test_les_commandes_des_unites_sont_acceptees_par_le_vrai_analyseur(self):
        m, sd = noeud()
        for nom, attendu in ((PRINCIPALE, ["verifier", "run", "nettoyer"]), (GARDE, ["garde"])):
            u = sd.unites[nom]
            lignes = u.lignes("ExecStartPre") + u.lignes("ExecStart") + u.lignes("ExecStopPost")
            sous = []
            for ligne in lignes:
                argv = shlex.split(ligne.lstrip("+-"))
                self.assertEqual(argv[:5], ["/usr/bin/python3", "-I", "-B", "-u", D.CHEMIN_SCRIPT])
                D.construire_parseur().parse_args(argv[5:])
                sous.append(argv[5])
            self.assertEqual(sous, attendu)

    def test_les_deux_unites_declarent_les_memes_repertoires_et_modes(self):
        """La garde demarre AVANT le controle de l'unite principale : elle doit creer, avec le meme mode, les memes repertoires."""
        _, sd = noeud()
        p, g = sd.unites[PRINCIPALE], sd.unites[GARDE]
        for cle in ("StateDirectory", "RuntimeDirectory"):
            self.assertEqual(sorted(p.mots(cle, "Service")), sorted(g.mots(cle, "Service")), cle)
        for cle in ("StateDirectoryMode", "RuntimeDirectoryMode", "RuntimeDirectoryPreserve"):
            self.assertEqual(p.un(cle), g.un(cle), cle)
        self.assertEqual(p.un("StateDirectoryMode"), "0700")

    def test_arret_demande_par_l_exploitant_n_est_pas_une_panne_mais_le_reste_l_est(self):
        _, sd = noeud()
        self.assertEqual(sd.unites[PRINCIPALE].mots("SuccessExitStatus", "Service"), [str(D.CODES_SORTIE["ARRET"])])
        self.assertEqual(sd.unites[GARDE].mots("SuccessExitStatus", "Service"), [])

    def test_aucune_section_install_donc_rien_au_demarrage_de_linux(self):
        m, sd = noeud()
        self.assertEqual(sd.boot_units(), [])
        for nom in (PRINCIPALE, GARDE):
            self.assertEqual(m.unites[nom]["UnitFileState"], "static")
        self.assertEqual(sd.enable(PRINCIPALE), "no-install-config")
        self.assertEqual(sd.enable(GARDE), "no-install-config")
        self.assertEqual(sd.boot_units(), [], "`systemctl enable` ne cree AUCUN lien : une unite sans [Install] ne peut pas etre activee")
        self.assertEqual(m.unites[PRINCIPALE]["UnitFileState"], "static")

    def test_le_bac_a_sable_refuse_une_ecriture_hors_des_repertoires_declares(self):
        """Controle du banc lui-meme : si ce test echouait, les tests qui reussissent sous le bac a sable ne prouveraient rien."""
        m, sd = noeud()
        sd._pile.append((sd.unites[PRINCIPALE], False))
        try:
            with self.assertRaises(OSError) as c:
                m.ecrire_json("/etc/helios-test.json", {})
            self.assertEqual(c.exception.errno, errno.EROFS)
            m.ecrire_json(D.F_ETAT, {"ok": 1})                                           # /run/helios-diskfill : declare (RuntimeDirectory)
            m.ecrire_json(D.F_COOLDOWN, {"ok": 1})                                       # /var/lib/helios-diskfill-etat : declare (StateDirectory)
        finally:
            sd._pile.pop()
        sd._pile.append((sd.unites[PRINCIPALE], True))                                   # `+` : hors bac a sable
        try:
            m.ecrire_json("/etc/helios-test.json", {})
        finally:
            sd._pile.pop()
        self.assertEqual(sd.ecritures_refusees, ["/etc/helios-test.json"])


class TestEspaceDeNomsDeLUnite(Base):
    """CONSTATE sur .158 (20:59 UTC) : dans l'espace de noms de l'unite, le repertoire d'etat est un point de montage (bind mount de lui-meme)."""

    def test_le_banc_reproduit_le_constat_reel(self):
        m, sd = noeud()
        self.assertEqual(m.info_chemin(D.DOSSIER)["montage"], "/", "hors unite (espace racine) : le repertoire est sur /")
        sd._pile.append((sd.unites[PRINCIPALE], True))                                   # ExecStartPre=+ : MEME espace de noms de l'unite
        try:
            self.assertEqual(m.info_chemin(D.DOSSIER)["montage"], D.DOSSIER)
            self.assertEqual(m.info_chemin("/")["montage"], "/")
        finally:
            sd._pile.pop()

    def test_le_demarrage_direct_est_accepte_malgre_le_bind_mount_sans_aucun_armement(self):
        m, sd = noeud()
        self.assertEqual([c for c in m.textes if "armement" in c], [], "aucun fichier d'armement n'existe ni n'est requis")
        r = sd.start()
        self.assertEqual((r.rc, r.result), (0, "success"), "\n".join(sd.journaux[PRINCIPALE]))
        self.assertFalse(any("[KO]" in l for l in sd.journaux[PRINCIPALE]), "\n".join(sd.journaux[PRINCIPALE]))
        self.assertUnResultat(sd, verdict="OK", code="0")

    def test_un_autre_volume_monte_sur_le_repertoire_reste_refuse_dans_l_unite(self):
        m, sd = noeud()
        m.infos[D.DOSSIER]["dev"] = 7                                                    # un AUTRE peripherique monte sur le repertoire de test
        r = sd.start()
        self.assertNotEqual(r.rc, 0)
        self.assertEqual(self.noms_alloues(m), [])
        self.assertTrue(any("[KO] DOSSIERS" in l and "peripherique" in l for l in sd.journaux[PRINCIPALE]))


# ------------------------------------------------------------------------------------------------ refus par un controle interne
def pas_la_bonne_machine(m):
    m.hostname_ = "k8s-worker2"


def la_bonne_machine(m):
    m.hostname_ = "k8s-worker1"


class TestRefusSansAllocation(Base):
    def verifier_refus(self, m, sd, r, controle):
        self.assertNotEqual(r.rc, 0, "`systemctl start` doit ECHOUER (refus visible dans le terminal)")
        self.assertEqual(r.result, "exit-code")
        self.assertEqual(sd.etat[PRINCIPALE]["ActiveState"], "failed")
        self.assertEqual(self.noms_alloues(m), [], "AUCUNE allocation")
        self.assertEqual(m.fichiers, {})
        self.assertLessEqual(m.usage_max, 68.7, "le disque n'a pas ete rempli")
        self.assertNotIn(D.F_ETAT, m.textes, "aucun etat de lancement")
        self.assertNotIn(D.F_COOLDOWN, m.textes, "le cooldown ne demarre pas sur un refus")
        self.assertTrue(any(("[KO] %s" % controle) in l for l in sd.journaux[PRINCIPALE]), "\n".join(sd.journaux[PRINCIPALE]))
        self.assertUnResultat(sd, verdict="REFUS", code="2", etape="verifier", alloue="non")

    def test_un_controle_interne_en_echec_refuse_le_demarrage_et_rien_n_est_alloue(self):
        m, sd = noeud()
        pas_la_bonne_machine(m)
        r = sd.start()
        self.verifier_refus(m, sd, r, "IDENTITE")
        self.assertEqual(self.assertUnResultat(sd)["controles_ko"], "IDENTITE")
        self.assertIn("garde independante", "\n".join(sd.journaux[PRINCIPALE]), "la garde, demarree par la dependance, etait bien armee")

    def test_chaque_controle_interne_refuse_le_demarrage_direct_par_systemd(self):
        """Un refus par controle, de bout en bout (unite, ExecStartPre, ExecStopPost) : `systemctl start` echoue, rien n'est alloue ni cree."""
        cas = (("IDENTITE", pas_la_bonne_machine), ("INSTALLATION", lambda m: m.contenu_sha.__setitem__("helios-diskguard.service", "9" * 64)),
               ("DOSSIERS", lambda m: m.infos[D.DOSSIER].update(mode="755")), ("RESIDUS", lambda m: setattr(m, "transitoires", ["helios-diskfill-x.service"])),
               ("SYSTEME", lambda m: setattr(m, "fallocate", False)), ("INODES", lambda m: setattr(m, "inodes_libres", 100)),
               ("COOLDOWN", lambda m: m.textes.__setitem__(D.F_COOLDOWN, {"debut_epoch": m.t - 60})),
               ("PROMETHEUS", lambda m: setattr(m, "prom_incident_force", True)))
        for controle, panne in cas:
            with self.subTest(controle):
                m, sd = noeud()
                panne(m)
                avant_cooldown = copy.deepcopy(m.textes.get(D.F_COOLDOWN))
                r = sd.start()
                self.assertNotEqual(r.rc, 0)
                self.assertEqual(self.noms_alloues(m), [])
                self.assertTrue(any(("[KO] %s" % controle) in l for l in sd.journaux[PRINCIPALE]), "\n".join(sd.journaux[PRINCIPALE]))
                self.assertNotIn(D.F_ETAT, m.textes)
                self.assertEqual(m.textes.get(D.F_COOLDOWN), avant_cooldown)
                self.assertUnResultat(sd, verdict="REFUS", code="2", etape="verifier", alloue="non")
                self.assertIn(controle, resultats(sd.journaux[PRINCIPALE])[0]["controles_ko"])

    def test_apres_un_refus_la_garde_inutile_sort_vite_et_ne_reste_pas_active(self):
        m, sd = noeud()
        pas_la_bonne_machine(m)
        sd.start()
        self.assertTrue(sd.actif(GARDE), "juste apres le refus, la garde tourne encore (elle n'a pas encore relu le marqueur)")
        sd.attendre(2 * D.GARDE_PAS_S + 1)
        self.assertFalse(sd.actif(GARDE), "la garde sort d'elle-meme tres vite : pas de ROUGE 'unite active' au pre-vol suivant")
        self.assertEqual(sd.etat[GARDE]["Result"], "success")
        self.assertLess(m.t - sd.etat[GARDE]["t_active"], D.GARDE_ATTENTE_ETAT_S, "bien avant le delai d'oisivete de %d s" % D.GARDE_ATTENTE_ETAT_S)
        self.assertIn("demarrage du service refuse", "\n".join(sd.journaux[GARDE]))

    def test_la_garde_qui_garde_un_lancement_ne_sort_jamais_sur_un_refus_tardif(self):
        """Un marqueur de refus ne concerne que la garde OISIVE : une garde liee a un jeton dont les fichiers restent continue de garder."""
        m, sd = noeud(helios="absent")
        m.pannes.add("liberer_refuse")                                                   # nettoyer ne peut pas liberer : liberation incomplete
        sd.start(ops=[(60, "kill")])
        self.assertEqual(m.textes[D.F_ETAT]["phase"], "liberation_incomplete")
        self.assertTrue(m.fichiers)
        m.textes[D.F_MARQUEUR_ARRET_GARDE] = {"epoch": m.t + 1, "invocation_id": "x"}   # un refus ulterieur signale a la garde de sortir
        sd.attendre(10)
        self.assertTrue(sd.actif(GARDE), "garde liee a un jeton dont les fichiers restent : elle reste armee malgre le marqueur")

    def test_un_marqueur_perime_n_arrete_pas_une_garde_neuve(self):
        m, sd = noeud()
        pas_la_bonne_machine(m)
        sd.start()
        sd.attendre(2 * D.GARDE_PAS_S + 1)
        self.assertIn(D.F_MARQUEUR_ARRET_GARDE, m.textes)
        la_bonne_machine(m)
        m.dormir(1)
        r = sd.start()                                                                   # nouvelle garde (t0 posterieur au marqueur)
        self.assertEqual(r.rc, 0, "\n".join(sd.journaux[PRINCIPALE]))
        self.assertNotIn(D.F_MARQUEUR_ARRET_GARDE, m.textes, "le marqueur est efface a la creation de l'etat du nouveau lancement")

    def test_un_nouveau_start_juste_apres_un_refus_garde_la_garde_armee_pendant_qu_il_demarre(self):
        """La garde oisive ne part que si le service principal est INACTIF : un nouveau `systemctl start` en cours la retient, meme si son prochain
        tour tombe pendant ce demarrage (sinon le controle 'garde armee' echouerait ou, pire, la garde partirait apres la creation de l'etat)."""
        m, sd = noeud(helios="resolve")
        pas_la_bonne_machine(m)
        sd.start()                                                                       # refus (mauvaise machine) : marqueur pose
        self.assertIn(D.F_MARQUEUR_ARRET_GARDE, m.textes)
        la_bonne_machine(m)
        m._garde_prochain = m.t + 1                                                      # le prochain tour de la garde tombe PENDANT le 2e demarrage
        r2 = sd.start()
        self.assertEqual((r2.rc, r2.result), (0, "success"), "\n".join(sd.journaux[PRINCIPALE]))
        self.garde_evidence(sd)
        self.assertEqual(resultats(sd.journaux[PRINCIPALE])[-1]["verdict"], "OK")
        self.assertNotIn(D.F_MARQUEUR_ARRET_GARDE, m.textes)

    def test_nettoyer_ne_pose_le_marqueur_que_dans_une_unite_et_jamais_apres_un_lancement_abouti(self):
        m, sd = noeud()
        m.inv = None                                                                     # hors unite systemd
        self.assertEqual(D.nettoyer(m, [].append), 0)
        self.assertNotIn(D.F_MARQUEUR_ARRET_GARDE, m.textes, "hors unite : aucun marqueur")
        m, sd = noeud()
        sd.start()                                                                       # lancement abouti
        self.assertNotIn(D.F_MARQUEUR_ARRET_GARDE, m.textes, "un lancement abouti ne pose pas de marqueur de refus")
        m, sd = noeud()
        pas_la_bonne_machine(m)
        sd.start()                                                                       # refus
        marque = m.textes[D.F_MARQUEUR_ARRET_GARDE]
        self.assertEqual(sorted(marque), ["epoch", "invocation_id"])
        self.assertEqual(marque["invocation_id"], m.inv)

    def test_la_garde_part_si_aucun_demarrage_n_est_en_cours_au_moment_du_marqueur(self):
        m, sd = noeud(helios="resolve")
        pas_la_bonne_machine(m)
        sd.start()
        m._garde_prochain = m.t + 1
        sd.attendre(2)
        self.assertFalse(sd.actif(GARDE), "service principal inactif + marqueur recent : la garde oisive part")
        la_bonne_machine(m)
        r = sd.start()                                                                   # 2e start : une garde NEUVE est demarree par la dependance
        self.assertEqual((r.rc, r.result), (0, "success"), "\n".join(sd.journaux[PRINCIPALE]))
        self.garde_evidence(sd)

    def test_un_deuxieme_start_apres_un_lancement_abouti_est_refuse_par_le_cooldown_puis_accepte_apres_5_min(self):
        """Plus d'armement a usage unique : c'est le cooldown local (5 min) qui empeche un 2e remplissage rapproche."""
        m, sd = noeud(helios="resolve")
        r1 = sd.start()
        self.assertEqual((r1.rc, r1.result), (0, "success"), "\n".join(sd.journaux[PRINCIPALE]))
        n = len(self.noms_alloues(m))
        sd.attendre(5)
        m.reinit_helios()
        r2 = sd.start()
        self.assertNotEqual(r2.rc, 0)
        self.assertEqual(len(self.noms_alloues(m)), n, "aucune nouvelle allocation")
        self.assertEqual(resultats(sd.journaux[PRINCIPALE])[-1]["verdict"], "REFUS")
        self.assertTrue(any("[KO] COOLDOWN" in l for l in sd.journaux[PRINCIPALE]))
        m.dormir(D.COOLDOWN_LOCAL_S)
        sd.attendre(2 * D.GARDE_PAS_S + 1)
        m.reinit_helios()
        r3 = sd.start()
        self.assertEqual((r3.rc, r3.result), (0, "success"), "apres 5 min un nouveau demarrage direct est accepte\n" + "\n".join(sd.journaux[PRINCIPALE]))
        self.assertGreater(len(self.noms_alloues(m)), n)

    def test_une_unite_modifiee_est_refusee_par_l_empreinte_figee(self):
        for unite in (PRINCIPALE, GARDE):
            with self.subTest(unite):
                m, sd = noeud()
                m.contenu_sha[unite] = "9" * 64
                r = sd.start()
                self.verifier_refus(m, sd, r, "INSTALLATION")
                self.assertTrue(any("EMPREINTE DIFFERENTE" in l for l in sd.journaux[PRINCIPALE]), "\n".join(sd.journaux[PRINCIPALE]))

    def test_toute_variante_des_unites_aurait_une_empreinte_differente_donc_serait_refusee(self):
        """Limites de duree/consommation, durcissement, dependances : tout le contenu des unites est couvert par l'empreinte figee dans le programme."""
        variantes = [(PRINCIPALE, variante(PRINCIPALE, ("RuntimeMaxSec=1020", "RuntimeMaxSec=0"))),
                     (PRINCIPALE, variante(PRINCIPALE, ("MemoryMax=64M", "MemoryMax=infinity"))),
                     (PRINCIPALE, variante(PRINCIPALE, ("TasksMax=32", "TasksMax=infinity"))),
                     (PRINCIPALE, variante(PRINCIPALE, ("Requires=helios-diskguard.service", "Wants=helios-diskguard.service"))),
                     (PRINCIPALE, variante(PRINCIPALE, ("SuccessExitStatus=6", "SuccessExitStatus=0 1 2 3 4 5 6"))),
                     (GARDE, variante(GARDE, ("RuntimeMaxSec=1080", "RuntimeMaxSec=0"))),
                     (GARDE, variante(GARDE, ("MemoryMax=32M", "MemoryMax=infinity"))),
                     (GARDE, variante(GARDE, ("ProtectSystem=strict", "ProtectSystem=no")))]
        import hashlib
        for i, (unite, texte) in enumerate(variantes):
            with self.subTest(unite=unite, variante=i):
                sha = hashlib.sha256(texte.encode("ascii")).hexdigest()
                self.assertNotEqual(sha, D.SHA_UNITES[unite], "la variante doit avoir une empreinte differente")
                m, sd = noeud()
                m.contenu_sha[unite] = sha                                               # le noeud porte CETTE variante : le programme la refuse
                r = sd.start()
                self.verifier_refus(m, sd, r, "INSTALLATION")

    def test_inodes_insuffisants_refus_sans_allocation(self):
        m, sd = noeud()
        m.inodes_total, m.inodes_libres = 1000000, 100000                                # 90 % utilises
        r = sd.start()
        self.verifier_refus(m, sd, r, "INODES")
        self.assertEqual(self.assertUnResultat(sd)["controles_ko"], "INODES")

    def test_unite_a_recharger_refusee_meme_si_le_fichier_est_conforme(self):
        m, sd = noeud()
        m.unites[PRINCIPALE]["NeedDaemonReload"] = "yes"
        r = sd.start()
        self.verifier_refus(m, sd, r, "INSTALLATION")
        self.assertTrue(any("NeedDaemonReload=yes" in l for l in sd.journaux[PRINCIPALE]))

    def test_orphelins_d_un_lancement_precedent_refus_sans_nettoyage_automatique(self):
        m, sd = noeud()
        m.fichiers[D.nom_fichier("e" * 32, 1)] = {"taille": 512 * MIO, "uid": 0, "nlink": 1, "type": "f"}
        r = sd.start()
        self.assertNotEqual(r.rc, 0)
        self.assertEqual(m.appels_de("liberer"), [], "AUCUN nettoyage automatique des orphelins")
        self.assertEqual(len(m.fichiers), 1)
        self.assertTrue(any("ORPHELINS" in l for l in sd.journaux[PRINCIPALE]))
        self.assertEqual(self.noms_alloues(m), [])


# ------------------------------------------------------------------------------------------------ la garde AVANT la premiere allocation
class TestGardeAvantAllocation(Base):
    def test_un_seul_start_demarre_la_garde_avant_le_controle_et_avant_toute_allocation(self):
        m, sd = noeud()
        r = sd.start()
        self.assertEqual(r.rc, 0)
        lignes = [(t, u, l) for t, u, l in sd.chrono]
        i_garde = next(i for i, (t, u, l) in enumerate(lignes) if u == "systemctl" and l == "unite %s demarree" % GARDE)
        i_pre = next(i for i, (t, u, l) in enumerate(lignes) if u == PRINCIPALE and "VERIFICATION DE DEMARRAGE" in l)
        i_bloc = next(i for i, (t, u, l) in enumerate(lignes) if u == PRINCIPALE and "bloc 1" in l)
        self.assertLess(i_garde, i_pre, "la garde demarre AVANT ExecStartPre (Requires= + After=)")
        self.assertLess(i_pre, i_bloc)
        self.garde_evidence(sd)
        self.assertEqual([c for _, c in sd.commandes], ["systemctl start " + PRINCIPALE], "UNE SEULE commande : la garde n'est jamais lancee a la main")

    def test_la_garde_est_vivante_et_liee_au_jeton_a_chaque_allocation(self):
        m, sd = noeud(duree_alloc_s=2.0)
        sd.start()
        self.assertGreaterEqual(len(sd.allocations), 8)
        self.garde_evidence(sd)

    def test_sans_la_dependance_un_seul_start_ne_suffit_pas_et_rien_n_est_alloue(self):
        """Documente POURQUOI Requires=/After= existent : sans eux la garde ne demarre pas seule, ExecStartPre refuse, aucune allocation."""
        sans = variante(PRINCIPALE, ("After=network.target helios-diskguard.service", "After=network.target"),
                        ("Requires=helios-diskguard.service\n", ""))
        m, sd = noeud(textes={PRINCIPALE: sans})
        r = sd.start()
        self.assertNotEqual(r.rc, 0)
        self.assertFalse(sd.actif(GARDE))
        self.assertEqual(self.noms_alloues(m), [])
        self.assertTrue(any("[KO] GARDE" in l for l in sd.journaux[PRINCIPALE]))
        self.assertNotIn(D.F_ETAT, m.textes, "aucun etat : le refus survient AVANT la creation du jeton")
        self.assertGreaterEqual(m.t - r.t_debut, D.ATTENTE_GARDE_S - 1, "attente bornee de la garde")

    def test_la_garde_qui_ne_peut_pas_demarrer_empeche_le_service_de_demarrer(self):
        m, sd = noeud()
        m.pannes.add("garde_refuse")
        r = sd.start()
        self.assertNotEqual(r.rc, 0)
        self.assertEqual(r.result, "dependency")
        self.assertEqual(sd.journaux[PRINCIPALE], [], "ExecStartPre n'est meme pas lance")
        self.assertEqual(self.noms_alloues(m), [])
        self.assertNotIn(D.F_ETAT, m.textes, "ExecStartPre n'a meme pas tourne : aucun etat")

    def test_la_garde_qui_meurt_au_demarrage_provoque_un_refus_avant_la_creation_de_l_etat(self):
        m, sd = noeud()
        m.pannes.add("garde_meurt")
        r = sd.start()
        self.assertNotEqual(r.rc, 0)
        self.assertEqual(self.noms_alloues(m), [])
        self.assertNotIn(D.F_ETAT, m.textes)
        self.assertTrue(any("[KO] GARDE" in l for l in sd.journaux[PRINCIPALE]))
        self.assertLessEqual(m.t - r.t_debut, int(sd.unites[PRINCIPALE].un("TimeoutStartSec")))
        self.assertUnResultat(sd, verdict="REFUS", etape="verifier", controles_ko="GARDE")

    def test_la_garde_qui_disparait_apres_le_controle_empeche_toute_allocation(self):
        m, sd = noeud()
        evenement_a(m, 2, lambda mm: setattr(mm, "garde_active", False))               # juste apres la creation du jeton, avant que la garde l'adopte
        r = sd.start()
        self.assertEqual(self.noms_alloues(m), [], "aucune allocation sans garde liee au jeton")
        self.assertIn(D.F_COOLDOWN, m.textes, "le lancement a ete accepte par verifier (etat et cooldown ecrits) : le refus intervient dans run, avant toute allocation")
        self.assertEqual(r.result, "exit-code")
        self.assertUnResultat(sd, verdict="REFUS", code="2", etape="run", liberee_par="aucune", blocs="0")

    def test_premier_demarrage_les_repertoires_n_existent_pas_encore(self):
        m, sd = noeud()
        for chemin in (D.DOSSIER_ETAT, D.RUN_DIR):
            m.infos.pop(chemin)
        m.dossier_present = False
        r = sd.start()
        self.assertEqual((r.rc, r.result), (0, "success"), "\n".join(sd.journaux[PRINCIPALE]))
        for chemin in (D.DOSSIER, D.DOSSIER_ETAT):
            self.assertEqual(m.infos[chemin]["mode"], "700", chemin)
        self.assertEqual(m.infos[D.RUN_DIR]["mode"], "700")
        self.assertUnResultat(sd, verdict="OK")

    def test_premier_demarrage_meme_si_les_repertoires_de_l_unite_principale_n_existaient_qu_a_execstart(self):
        """Hypothese pessimiste : seuls les repertoires de la garde (demarree avant) existent quand ExecStartPre controle. Ils doivent suffire."""
        m, sd = noeud(repertoires_avant_pre=False)
        for chemin in (D.DOSSIER_ETAT, D.RUN_DIR):
            m.infos.pop(chemin)
        m.dossier_present = False
        r = sd.start()
        self.assertEqual((r.rc, r.result), (0, "success"), "\n".join(sd.journaux[PRINCIPALE]))
        for chemin in (D.DOSSIER, D.DOSSIER_ETAT):
            self.assertEqual(m.infos[chemin]["mode"], "700", chemin)

    def test_sans_la_declaration_des_deux_repertoires_par_la_garde_le_cas_pessimiste_serait_refuse(self):
        """Documente POURQUOI la garde declare aussi helios-diskfill-etat : sinon, dans le cas pessimiste, le tout premier demarrage serait refuse."""
        textes = {GARDE: variante(GARDE, ("StateDirectory=helios-diskfill helios-diskfill-etat\n", "StateDirectory=helios-diskfill\n"))}
        m, sd = noeud(textes=textes, repertoires_avant_pre=False)
        for chemin in (D.DOSSIER_ETAT, D.RUN_DIR):
            m.infos.pop(chemin)
        m.dossier_present = False
        r = sd.start()
        self.assertNotEqual(r.rc, 0)
        self.assertEqual(self.noms_alloues(m), [])
        self.assertTrue(any("[KO] DOSSIERS" in l and D.DOSSIER_ETAT in l for l in sd.journaux[PRINCIPALE]), "\n".join(sd.journaux[PRINCIPALE]))

    def test_sans_state_directory_mode_le_defaut_0755_de_systemd_provoque_un_refus(self):
        """Le Bridge exigerait directory|root|700 : sans StateDirectoryMode explicite, rien ne doit etre alloue."""
        textes = {PRINCIPALE: variante(PRINCIPALE, ("StateDirectoryMode=0700\n", "")), GARDE: variante(GARDE, ("StateDirectoryMode=0700\n", ""))}
        m, sd = noeud(textes=textes)
        for chemin in (D.DOSSIER_ETAT, D.RUN_DIR):
            m.infos.pop(chemin)
        m.dossier_present = False
        r = sd.start()
        self.assertNotEqual(r.rc, 0)
        self.assertEqual(self.noms_alloues(m), [])
        self.assertEqual(m.infos[D.DOSSIER]["mode"], "755")
        self.assertTrue(any("[KO] INSTALLATION" in l for l in sd.journaux[PRINCIPALE]))
        self.assertTrue(any("[KO] DOSSIERS" in l for l in sd.journaux[PRINCIPALE]))

    def test_une_section_install_rendrait_l_unite_activable_et_le_demarrage_est_refuse(self):
        textes = {PRINCIPALE: texte_unite(PRINCIPALE) + "\n[Install]\nWantedBy=multi-user.target\n"}
        m, sd = noeud(textes=textes)
        self.assertEqual(sd.boot_units(), [PRINCIPALE], "avec [Install], l'unite demarrerait au boot apres `enable`")
        r = sd.start()
        self.assertNotEqual(r.rc, 0)
        self.assertEqual(self.noms_alloues(m), [])
        self.assertTrue(any("UnitFileState=disabled" in l for l in sd.journaux[PRINCIPALE]))


# ------------------------------------------------------------------------------------------------ nominal : UNE commande, scenario COMPLET
class TestUnSeulStartScenarioComplet(Base):
    def test_un_seul_systemctl_start_detection_helios_liberation_et_verification(self):
        b = TA.Banc(helios="resolve", garde=False)
        sd = Systemd(b.m)
        m = b.m
        self.assertEqual(sd.commandes, [], "aucun armement, aucune preparation : rien n'a ete emis avant la commande")
        self.assertEqual(b.exe.appels, [], "l'hote n'a RIEN fait")
        code, sortie = b.preflight()                                                      # FACULTATIF : pre-vol de l'hote en lecture seule (ne lance rien)
        self.assertEqual(code, 0, sortie)
        self.assertEqual(sortie.count("sudo systemctl start helios-diskfill.service"), 1, "l'hote ne propose QU'UNE commande de lancement")
        self.assertIn("UNE SEULE commande, SANS armement", sortie)
        self.assertIn("ne jamais la lancer a la main", sortie)
        self.assertIn('grep -E "RESULTAT|BILAN|REFUS"', sortie, "l'hote indique comment lire le resultat final dans les journaux systemd")
        self.assertIn("-p Result -p ExecMainStatus", sortie)
        self.assertEqual(sd.commandes, [], "le pre-vol facultatif n'emet aucune commande systemctl")
        self.assertEqual(m.appels_de("allouer"), [])
        self.assertFalse(sd.actif(PRINCIPALE) or sd.actif(GARDE))
        etranger = fichier_etranger("README")
        evenement_a(m, 20, etranger)                                                      # un fichier etranger apparait dans le repertoire en cours de test
        r = sd.start()                                                                    # LA commande de lancement : la seule
        self.assertEqual(r.rc, 0, "\n".join(sd.journaux[PRINCIPALE]))
        self.assertLess(r.t_retour - r.t_debut, 30, "systemctl start rend la main apres ExecStartPre (Type=simple)")
        self.assertEqual((r.result, r.exit_status), ("success", 0))
        self.assertEqual([c for _, c in sd.commandes], ["systemctl start " + PRINCIPALE], "AUCUNE deuxieme commande de lancement")
        # 1. remplissage controle, garde liee au jeton
        noms = self.noms_alloues(m)
        self.assertGreaterEqual(len(noms), 2)
        self.assertLessEqual(len(noms), D.MAX_FICHIERS)
        self.garde_evidence(sd)
        self.assertGreaterEqual(m.usage_max, D.CIBLE_PCT)
        self.assertLessEqual(m.usage_max, D.PLAFOND_PCT, "plafond de 82 % jamais depasse")
        # 2. detection par HELIOS, 3. HOST_RECLAIM_DISK par le Bridge (simule sur le VRAI disque_plan), 4. fichiers EXACTS liberes
        journal = "\n".join(sd.journaux[PRINCIPALE])
        self.assertIn("incident HELIOS visible dans Prometheus", journal)
        self.assertIsNone(m.bridge_refus)
        self.assertEqual(sorted(m.supprimes_par_helios), sorted(noms), "HELIOS libere EXACTEMENT les fichiers du test")
        self.assertEqual(m.appels_de("liberer"), [], "NOMINAL : le service ne supprime rien, seul HELIOS libere")
        self.assertIn("README", m.etrangers, "un fichier etranger n'est jamais touche")
        self.assertEqual(m.fichiers, {})
        # 5. verification du retour, 6. resultat final dans le journal systemd
        self.assertLessEqual(m.usage(), D.RECUPERATION_PCT)
        self.assertIn("retour a", journal)
        res = self.assertUnResultat(sd, verdict="OK", code="0", etape="run", incident_vu="oui", liberee_par="helios_infere", recupere="oui",
                                    liberation_verifiee="oui")
        self.assertEqual(int(res["blocs"]), len(noms))
        self.assertEqual(sd.show(PRINCIPALE)["ActiveState"], "inactive")
        self.assertEqual(m.textes[D.F_ETAT]["phase"], "terminee")
        self.assertEqual(m.textes[D.F_ETAT]["verdict"], "OK")
        sd.attendre(2 * D.GARDE_PAS_S + 1)
        self.assertFalse(sd.actif(GARDE), "la garde sort d'elle-meme quand le lancement est termine")
        self.assertEqual(sd.etat[GARDE]["Result"], "success")

    def test_la_chronologie_du_journal_est_dans_l_ordre_du_scenario(self):
        m, sd = noeud()
        sd.start()
        journal = sd.journaux[PRINCIPALE]
        motifs = ["VERIFICATION DE DEMARRAGE", "VERIFICATION OK", "DEBUT ", "base : occupation", "garde independante armee pour ce jeton", "bloc 1 :",
                  "cible atteinte", "incident HELIOS visible", "fichiers de test liberes", "retour a", "BILAN : OK", "RESULTAT : verdict=OK"]
        positions = []
        for motif in motifs:
            idx = [i for i, l in enumerate(journal) if motif in l]
            self.assertTrue(idx, "motif absent du journal : %r\n%s" % (motif, "\n".join(journal)))
            positions.append(idx[0])
        self.assertEqual(positions, sorted(positions), "ordre du journal : %r" % list(zip(motifs, positions)))
        suite = journal[positions[-1] + 1:]
        self.assertTrue(all(l.startswith("helios-diskfill : nettoyer :") for l in suite),
                        "apres RESULTAT (fin de `run`) il ne reste que la confirmation de ExecStopPost : %r" % suite)

    def test_une_execution_a_blanc_ne_laisse_aucune_ligne_resultat(self):
        """`verifier --a-blanc` n'est pas un lancement : sa sortie ne doit jamais pouvoir passer pour le resultat d'un lancement reel."""
        m, sd = noeud()
        out = []
        self.assertEqual(D.verifier(m, True, out.append), 0)
        self.assertEqual(resultats(out), [])
        m.hostname_ = "autre"
        out = []
        self.assertEqual(D.verifier(m, True, out.append), D.CODES_SORTIE["REFUS"])
        self.assertEqual(resultats(out), [], "meme un refus a blanc ne laisse pas de ligne RESULTAT")
        self.assertFalse(any("RESULTAT" in l for l in out))

    def test_les_autres_refus_de_verifier_laissent_aussi_une_ligne_resultat(self):
        m, sd = noeud()
        pas_la_bonne_machine(m)
        sd.start()
        self.assertUnResultat(sd, verdict="REFUS", code="2", etape="verifier", controles_ko="IDENTITE", alloue="non")
        self.assertEqual(self.noms_alloues(m), [])
        m, sd = noeud()
        m.pannes.add("ecriture:" + D.F_ETAT)                                              # l'etat du lancement ne peut pas etre ecrit
        sd.start()
        self.assertUnResultat(sd, verdict="REFUS", code="2", etape="verifier", controles_ko="ETAT", alloue="non")
        self.assertEqual(self.noms_alloues(m), [])

    def test_la_ligne_resultat_assainit_les_valeurs_une_ligne_ascii_sans_espace_dans_un_champ(self):
        ligne = D.ligne_resultat("OK", 0, a="b c\nd", b=None, c=True, d=False, e=3.14159, f="x" * 200, g="")
        self.assertNotIn("\n", ligne)
        self.assertTrue(all(ord(c) < 128 for c in ligne))
        champs = dict(kv.split("=", 1) for kv in ligne.split(" : RESULTAT : ")[1].split())
        self.assertEqual(champs, {"verdict": "OK", "code": "0", "a": "b_c_d", "b": "inconnu", "c": "oui", "d": "non", "e": "3.14", "f": "x" * 80, "g": "-"})

    def test_la_ligne_resultat_est_ascii_une_seule_ligne_et_le_code_est_celui_du_processus(self):
        m, sd = noeud()
        r = sd.start()
        lignes = [l for l in sd.journaux[PRINCIPALE] if "RESULTAT :" in l]
        self.assertEqual(len(lignes), 1)
        self.assertTrue(all(ord(c) < 128 for c in lignes[0]))
        self.assertNotIn("\n", lignes[0])
        self.assertEqual(int(resultats(sd.journaux[PRINCIPALE])[0]["code"]), r.exit_status)

    def test_rien_ne_redemarre_apres_la_fin(self):
        m, sd = noeud()
        sd.start()
        n = len(self.noms_alloues(m))
        sd.attendre(3600)
        self.assertEqual(len(self.noms_alloues(m)), n, "Restart=no : aucun 2e remplissage")
        self.assertFalse(sd.actif(PRINCIPALE) or sd.actif(GARDE))
        self.assertEqual(sd.unites[PRINCIPALE].un("Restart"), "no")

    def test_un_deuxieme_start_pendant_le_scenario_ne_relance_rien(self):
        m, sd = noeud()
        seconds = []
        evenement_a(m, 30, lambda mm: seconds.append(sd.start()))
        sd.start()
        self.assertEqual(len(seconds), 1)
        self.assertTrue(seconds[0].no_op, "unite deja active : systemd ne fait rien")
        self.assertEqual(sum(1 for l in sd.journaux[PRINCIPALE] if "VERIFICATION DE DEMARRAGE" in l), 1, "ExecStartPre n'a tourne qu'une fois")
        self.assertEqual(sum(1 for a in m.appels if a[0] == "ecrire_json" and a[1] == D.F_COOLDOWN), 1, "l'etat et le cooldown ne sont ecrits qu'une fois")
        self.assertUnResultat(sd, verdict="OK")

    def test_la_duree_totale_reste_sous_l_expiration(self):
        m, sd = noeud()
        r = sd.start()
        self.assertLess(r.t_fin - r.t_debut, D.EXPIRATION_S)
        self.assertLess(r.t_fin - r.t_debut, int(sd.unites[PRINCIPALE].un("RuntimeMaxSec")))


# ------------------------------------------------------------------------------------------------ urgence
class TestUrgence(Base):
    def test_urgence_a_83_pour_cent_le_service_libere_lui_meme_et_signale_l_echec(self):
        m, sd = noeud(helios="absent")
        evenement_a(m, 100, croissance(2.2))                                              # un autre processus du noeud ecrit : 81,0 -> 83,2 %
        r = sd.start()
        self.assertEqual(r.rc, 0)
        self.assertEqual((r.result, r.exit_status), ("exit-code", 5), "urgence : l'unite est en echec visible")
        self.assertEqual(sd.show(PRINCIPALE)["ActiveState"], "failed")
        self.assertTrue(any("ARRET D'URGENCE" in l for l in sd.journaux[PRINCIPALE]))
        self.assertEqual(m.fichiers, {}, "tout est libere")
        self.assertTrue(m.appels_de("liberer"), "c'est le service qui a libere (HELIOS absent)")
        for _, noms in m.appels_de("liberer"):
            self.assertTrue(all(D.RE_FICHIER.fullmatch(n) for n in noms), "noms exacts seulement")
        self.assertLess(m.usage(), D.RECUPERATION_PCT)
        self.assertNotIn(D.F_MARQUEUR_GARDE, m.textes, "83,2 % < 83,5 % : la garde n'a pas eu a agir")
        self.assertUnResultat(sd, verdict="URGENCE", code="5", liberee_par="service", recupere="oui", liberation_verifiee="oui")
        sd.attendre(2 * D.GARDE_PAS_S + 1)
        self.assertFalse(sd.actif(GARDE))

    def test_la_garde_independante_libere_a_83_5_pour_cent_et_le_service_conclut_urgence(self):
        m, sd = noeud(helios="absent")
        evenement_a(m, 100, croissance(3.2))                                              # 81,0 -> 84,2 % : au-dessus du seuil de la garde
        r = sd.start()
        self.assertEqual((r.result, r.exit_status), ("exit-code", 5))
        self.assertIn(D.F_MARQUEUR_GARDE, m.textes, "la garde a libere (noms exacts)")
        self.assertEqual(m.fichiers, {})
        self.assertUnResultat(sd, verdict="URGENCE", code="5", liberee_par="garde")
        self.assertTrue(any("occupation" in l and "liberation par noms exacts" in l for l in sd.journaux[GARDE]))

    def test_urgence_pendant_le_remplissage_arrete_les_allocations(self):
        m, sd = noeud(helios="absent", duree_alloc_s=3.0)
        t0 = m.t
        evenement_a(m, 12, croissance(14.5))                                              # un autre processus remplit pendant que le service alloue
        r = sd.start()
        self.assertEqual((r.result, r.exit_status), ("exit-code", 5))
        self.assertTrue(sd.allocations)
        self.assertLessEqual(max(a["t"] for a in sd.allocations), t0 + 12 + 1, "AUCUNE allocation apres que l'urgence est atteinte")
        self.assertLess(len(sd.allocations), 9, "le remplissage complet n'a pas eu lieu")
        self.assertEqual(m.fichiers, {}, "ce qui avait ete alloue est libere par noms exacts")
        self.assertTrue(any("ARRET D'URGENCE" in l for l in sd.journaux[PRINCIPALE]))
        self.assertUnResultat(sd, verdict="URGENCE", code="5", liberation_verifiee="oui")

    def test_le_service_tue_et_nettoyer_en_echec_la_garde_libere_quand_l_urgence_arrive(self):
        m, sd = noeud(helios="absent")
        m.pannes.add("liberer_refuse")                                                    # ni nettoyer ni le service ne peuvent supprimer
        evenement_a(m, 90, lambda mm: mm.pannes.discard("liberer_refuse"))                # plus tard, la suppression redevient possible
        evenement_a(m, 100, croissance(3.2))
        r = sd.start(ops=[(60, "kill")])
        self.assertEqual((r.result, r.exit_status), ("signal", "9/KILL"))
        self.assertEqual(m.textes[D.F_ETAT]["phase"], "liberation_incomplete")
        self.assertTrue(m.fichiers, "ExecStopPost n'a pas pu liberer : les fichiers restent")
        self.assertTrue(sd.actif(GARDE), "la garde reste armee tant que la liberation est incomplete")
        sd.attendre(60)
        self.assertEqual(m.fichiers, {}, "la garde independante a libere par noms exacts")
        self.assertIn(D.F_MARQUEUR_GARDE, m.textes)
        self.assertFalse(sd.actif(GARDE))
        self.assertUnResultat(sd, verdict="INTERROMPU", code="1", etape="nettoyer", liberation_verifiee="non")


# ------------------------------------------------------------------------------------------------ arret
class TestArret(Base):
    def verifier_arret(self, m, sd, r, **attendu):
        self.assertEqual((r.result, r.exit_status), ("success", 6), "arret demande : l'unite reste propre (SuccessExitStatus=6)")
        self.assertEqual(sd.show(PRINCIPALE)["ActiveState"], "inactive")
        self.assertEqual(m.fichiers, {}, "tout est libere")
        self.assertLessEqual(m.usage(), D.RECUPERATION_PCT)
        self.assertTrue(any("ARRET DEMANDE" in l for l in sd.journaux[PRINCIPALE]))
        self.assertLessEqual(r.delai_arret, D.PAS_S + 4, "le service obeit au SIGTERM en quelques secondes (TimeoutStopSec=90)")
        self.assertUnResultat(sd, verdict="ARRET", code="6", etape="run", liberation_verifiee="oui", **attendu)
        sd.attendre(2 * D.GARDE_PAS_S + 1)
        self.assertFalse(sd.actif(GARDE), "la garde sort apres l'arret")

    def test_stop_pendant_le_remplissage_libere_et_arrete_les_allocations(self):
        m, sd = noeud(helios="absent", duree_alloc_s=3.0)
        r = sd.start(ops=[(12, "stop")])
        self.verifier_arret(m, sd, r, liberee_par="service")
        self.assertGreaterEqual(len(sd.allocations), 3)
        self.assertLess(len(sd.allocations), 8, "les allocations s'arretent avec le stop")
        self.assertTrue(all(a["t"] <= r.t_retour + 12 + 3 for a in sd.allocations), "aucune allocation apres le stop")

    def test_stop_pendant_l_observation_avant_que_helios_agisse(self):
        m, sd = noeud(helios="absent")
        r = sd.start(ops=[(120, "stop")])
        self.verifier_arret(m, sd, r, liberee_par="service", incident_vu="non")
        self.assertTrue(m.appels_de("liberer"))

    def test_stop_juste_apres_la_liberation_par_helios_avant_la_confirmation_du_retour(self):
        m, sd = noeud(helios="resolve")
        vrai = m._bridge_reclame

        def bridge_puis_stop():
            vrai()
            m.arret_a = m.t + 1                                                           # l'exploitant tape `systemctl stop` une seconde apres

        m._bridge_reclame = bridge_puis_stop
        r = sd.start()
        self.assertEqual(r.exit_status, 6)
        self.assertEqual(sorted(m.supprimes_par_helios), sorted(self.noms_alloues(m)))
        self.assertEqual(m.appels_de("liberer"), [], "HELIOS avait deja libere : le service ne supprime rien")
        self.assertUnResultat(sd, verdict="ARRET", code="6", liberee_par="helios_infere", incident_vu="oui", recupere="oui")
        self.assertEqual(r.result, "success")

    def test_stop_avant_la_premiere_allocation_rien_n_est_alloue(self):
        m, sd = noeud()
        r = sd.start(ops=[(0, "stop")])
        self.assertEqual((r.result, r.exit_status), ("success", 6))
        self.assertEqual(self.noms_alloues(m), [])
        self.assertUnResultat(sd, verdict="ARRET", code="6", liberee_par="aucune", blocs="0")
        self.assertIn(D.F_COOLDOWN, m.textes, "le lancement a ete accepte puis arrete avant toute allocation : le cooldown court")

    def test_kill_9_nettoyer_libere_par_noms_exacts_et_le_journal_garde_une_trace(self):
        m, sd = noeud(helios="absent")
        r = sd.start(ops=[(60, "kill")])
        self.assertEqual((r.result, r.exit_status), ("signal", "9/KILL"))
        self.assertEqual(sd.show(PRINCIPALE)["ActiveState"], "failed")
        self.assertEqual(m.fichiers, {}, "ExecStopPost a libere (le processus tue n'a pas pu le faire)")
        self.assertEqual(m.textes[D.F_ETAT]["phase"], "terminee")
        self.assertIsNone(m.textes[D.F_ETAT]["verdict"], "run n'a rendu aucun verdict")
        res = self.assertUnResultat(sd, verdict="INTERROMPU", code="1", etape="nettoyer", liberation_verifiee="oui", recupere="oui")
        self.assertEqual(len(res["jeton"]), 8)
        sd.attendre(2 * D.GARDE_PAS_S + 1)
        self.assertFalse(sd.actif(GARDE))

    def test_restart_n_est_pas_une_echappatoire_le_2e_demarrage_est_refuse(self):
        m, sd = noeud(helios="absent")
        r = sd.start(ops=[(60, "restart")])
        self.assertEqual((r.result, r.exit_status), ("success", 6))
        self.assertIsNotNone(r.redemarrage)
        self.assertNotEqual(r.redemarrage.rc, 0, "le cooldown local refuse le redemarrage immediat (plus d'armement a usage unique)")
        self.assertEqual(r.redemarrage.result, "exit-code")
        self.assertTrue(any("[KO] COOLDOWN" in l for l in sd.journaux[PRINCIPALE]))
        n = sum(1 for l in sd.journaux[PRINCIPALE] if "VERIFICATION DE DEMARRAGE" in l)
        self.assertEqual(n, 2)
        self.assertEqual(len(set(a["jeton_etat"] for a in sd.allocations)), 1, "un seul lancement a alloue")
        self.assertEqual(m.fichiers, {})

    def test_apres_l_echeance_un_signal_n_est_plus_un_arret_de_l_exploitant(self):
        """RuntimeMaxSec : le verdict dit ERREUR (echeance depassee), pas ARRET ; l'unite est en `timeout`."""
        m, sd = noeud(helios="absent", duree_alloc_s=float(D.EXPIRATION_S + 80))         # une allocation qui dure plus que l'echeance (cas extreme)
        r = sd.start()
        self.assertEqual(r.result, "timeout", "systemd a envoye SIGTERM a RuntimeMaxSec")
        self.assertTrue(any("ECHEANCE ATTEINTE" in l for l in sd.journaux[PRINCIPALE]))
        self.assertFalse(any("ARRET DEMANDE" in l for l in sd.journaux[PRINCIPALE]), "ce n'est pas un arret de l'exploitant")
        self.assertEqual(m.fichiers, {}, "le fichier tardif est libere par le service lui-meme")
        self.assertUnResultat(sd, verdict="ERREUR", code="1", liberation_verifiee="oui")


# ------------------------------------------------------------------------------------------------ echec de remediation
class TestEchecRemediation(Base):
    def verifier_echec(self, m, sd, r, verdict, code, **attendu):
        self.assertEqual(r.rc, 0, "le lancement a bien demarre")
        self.assertEqual((r.result, r.exit_status), ("exit-code", code), "echec de remediation : l'unite est en echec VISIBLE")
        self.assertEqual(sd.show(PRINCIPALE)["ActiveState"], "failed")
        self.assertEqual(m.fichiers, {}, "le service libere lui-meme par securite a la fin de la fenetre : le disque n'est jamais laisse plein")
        self.assertLessEqual(m.usage(), D.RECUPERATION_PCT)
        self.assertLessEqual(m.usage_max, D.PLAFOND_PCT)
        self.assertEqual(m.supprimes_par_helios, [])
        for _, noms in m.appels_de("liberer"):
            self.assertTrue(all(D.RE_FICHIER.fullmatch(n) for n in noms))
        self.assertGreaterEqual(r.t_fin - r.t_retour, D.OBSERVE_S, "le service a attendu toute la fenetre d'observation avant de conclure")
        self.assertLess(r.t_fin - r.t_debut, D.EXPIRATION_S)
        self.assertUnResultat(sd, verdict=verdict, code=str(code), liberation_verifiee="oui", **attendu)
        sd.attendre(2 * D.GARDE_PAS_S + 1)
        self.assertFalse(sd.actif(GARDE))

    def test_helios_absent_aucune_detection(self):
        m, sd = noeud(helios="absent")
        r = sd.start()
        self.verifier_echec(m, sd, r, "AUCUNE_DETECTION", 4, incident_vu="non", liberee_par="service")

    def test_helios_detecte_mais_ne_remedie_pas(self):
        m, sd = noeud(helios="detecte")
        r = sd.start()
        self.assertTrue(m.incident)
        self.verifier_echec(m, sd, r, "DETECTION_SEULE", 3, incident_vu="oui", liberee_par="service")

    def test_le_bridge_refuse_repertoire_non_conforme_rien_n_est_supprime_par_helios(self):
        m, sd = noeud(helios="resolve")
        evenement_a(m, 25, lambda mm: mm.infos[D.DOSSIER].update(mode="755"))            # quelqu'un change les droits du repertoire apres le pre-vol
        r = sd.start()
        self.assertEqual(m.bridge_refus["rejet"], "UNSAFE_DIRECTORY")
        self.verifier_echec(m, sd, r, "DETECTION_SEULE", 3, incident_vu="oui", liberee_par="service")

    def test_le_bridge_refuse_deux_jetons_ne_devine_pas_et_n_est_pas_contourne(self):
        m, sd = noeud(helios="resolve")
        autre = D.nom_fichier("b" * 32, 1)
        evenement_a(m, 20, lambda mm: mm.etrangers.__setitem__(autre, {"taille": 1, "uid": 0, "nlink": 1, "type": "f"}))
        r = sd.start()
        self.assertEqual(m.bridge_refus["rejet"], "AMBIGUOUS_TOKENS")
        self.assertIn(autre, m.etrangers, "le fichier d'un AUTRE jeton n'est JAMAIS supprime par le service")
        self.assertEqual((r.result, r.exit_status), ("exit-code", 3))
        self.assertEqual(m.fichiers, {}, "les fichiers du lancement sont liberes par noms exacts")
        self.assertEqual(m.etrangers.keys() - {autre}, set())
        self.assertUnResultat(sd, verdict="DETECTION_SEULE", code="3", incident_vu="oui", liberee_par="service")

    def test_helios_libere_mais_le_disque_ne_revient_pas_au_seuil_de_recuperation(self):
        m, sd = noeud(helios="resolve")
        vrai = m._bridge_reclame

        def bridge_puis_croissance():
            vrai()
            m.organique += 0.10 * m.taille                                                # le disque se remplit ailleurs : 68,6 -> 78,6 % apres liberation

        m._bridge_reclame = bridge_puis_croissance
        r = sd.start()
        self.assertEqual((r.result, r.exit_status), ("exit-code", 1))
        self.assertEqual(m.appels_de("liberer"), [], "HELIOS a libere ; le service n'a rien supprime")
        self.assertGreater(m.usage(), D.RECUPERATION_PCT)
        self.assertTrue(any("non revenue" in l for l in sd.journaux[PRINCIPALE]))
        self.assertUnResultat(sd, verdict="ERREUR", code="1", liberee_par="helios_infere", recupere="non", liberation_verifiee="oui")

    def test_helios_trop_tard_apres_la_fenetre_le_service_a_deja_libere(self):
        m, sd = noeud(helios="resolve", delai_action=D.OBSERVE_S + 120.0)
        r = sd.start()
        self.assertEqual((r.result, r.exit_status), ("exit-code", 3))
        self.assertEqual(m.fichiers, {})
        self.assertUnResultat(sd, verdict="DETECTION_SEULE", code="3", liberee_par="service")


# ------------------------------------------------------------------------------------------------ expiration systemd et redemarrage
class TestRuntimeMaxEtBoot(Base):
    def test_un_processus_fige_est_arrete_par_runtime_max_sec_et_les_fichiers_sont_liberes(self):
        """Meme si `run` ne repond plus : la garde libere a l'echeance, systemd envoie SIGTERM a RuntimeMaxSec (`timeout`), ExecStopPost nettoie."""
        def run_fige(m, sortie):
            etat = m.lire_json(D.F_ETAT)
            m.dormir(3)                                                                   # laisse la garde adopter le jeton
            for i in range(1, 4):
                m.allouer(D.nom_fichier(etat["jeton"], i), 512 * MIO)
            while not m.arret_demande() and m.t < etat["debut_epoch"] + 4000:
                m.dormir(5)
            return 0

        m, sd = noeud(helios="absent", substitutions={"run": run_fige})
        r = sd.start()
        self.assertEqual(r.result, "timeout", "RuntimeMaxSec a envoye SIGTERM")
        self.assertGreaterEqual(r.t_fin - r.t_retour, int(sd.unites[PRINCIPALE].un("RuntimeMaxSec")))
        self.assertEqual(m.fichiers, {}, "tout est libere")
        self.assertIn(D.F_MARQUEUR_EXPIRATION, m.textes, "la garde a libere a l'echeance, avant meme systemd")
        sd.attendre(2 * D.GARDE_PAS_S + 1)
        self.assertFalse(sd.actif(GARDE))

    def test_sans_runtime_max_sec_le_processus_fige_ne_serait_jamais_arrete_par_systemd(self):
        """Controle de sensibilite : retirer RuntimeMaxSec change le resultat (la garde reste le seul filet)."""
        def run_fige(m, sortie):
            etat = m.lire_json(D.F_ETAT)
            m.dormir(3)
            m.allouer(D.nom_fichier(etat["jeton"], 1), 512 * MIO)
            while not m.arret_demande() and m.t < etat["debut_epoch"] + 1500:
                m.dormir(5)
            return 0

        m, sd = noeud(helios="absent", textes={PRINCIPALE: variante(PRINCIPALE, ("RuntimeMaxSec=1020\n", ""))}, substitutions={"run": run_fige})
        r = sd.start()
        self.assertNotEqual(r.result, "timeout")

    def test_rien_ne_demarre_au_boot_orphelins_et_aucun_nettoyage_automatique(self):
        m, sd = noeud(helios="absent")
        m.pannes.add("liberer_refuse")
        sd.start(ops=[(40, "kill")])
        m.pannes.discard("liberer_refuse")
        reste = sorted(m.fichiers)
        self.assertTrue(reste)
        appels = len(m.appels_de("liberer"))
        sd.reboot()
        self.assertEqual(sd.boot_units(), [])
        self.assertFalse(sd.actif(PRINCIPALE) or sd.actif(GARDE), "rien ne demarre au boot")
        self.assertEqual(sorted(m.fichiers), reste, "les fichiers sont sur le disque : ils survivent au redemarrage")
        sd.attendre(5)
        m.dormir(D.COOLDOWN_LOCAL_S)
        r = sd.start()
        self.assertNotEqual(r.rc, 0)
        self.assertEqual(sorted(m.fichiers), reste, "AUCUN nettoyage automatique au demarrage")
        self.assertEqual(len(m.appels_de("liberer")), appels)


if __name__ == "__main__":
    unittest.main(verbosity=2)
