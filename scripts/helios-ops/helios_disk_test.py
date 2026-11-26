#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Lanceur de test DISK HELIOS (NODE_DISK_FULL) : remplissage CONTROLE d'un volume de noeud par `fallocate`, dans un
repertoire de test DEDIE, jusqu'a une cible de 81 %, puis SUIVI de la detection, de la remediation et de la recuperation.

    python helios_disk_test.py --target 155                     # SIMULATION (defaut) : ni reseau, ni ssh, ni VM
    python helios_disk_test.py --target 155 --preflight         # PRE-VOL reel en LECTURE SEULE (ssh, Prometheus, kubectl)
    python helios_disk_test.py --target 155 --go --confirm DISK-155-81-82-83 [--accepte-risque-primaire-cnpg]
                               [--accepte-orange CODE[,CODE...]]
    python helios_disk_test.py --target 158 --go --confirm DISK-158-81-82-83 --image-backend TAG [--accepte-orange CODE[,CODE...]]

.158 (k8s-worker1) = scenario DISK_E2E_158 : son --preflight et son --go passent OBLIGATOIREMENT par la porte de helios_disk_e2e.py
(collecte reelle en lecture seule du Bridge et du Backend deployes, des drapeaux, de la protection systemd, du cooldown, des incidents
ouverts, de la RAM et des pages de l'hote). Fait manquant, illisible ou ROUGE, ou ORANGE non acquitte => AUCUNE charge, AUCUN incident.
--accepte-risque-primaire-cnpg et --accepte-orange REMEDIATION_NON_ACTIVEE y sont refuses (pas de lancement en detection seule).

Parametres valides par l'exploitant : cible 81 %, plafond strict 82 %, arret d'urgence 83 %, recuperation a 77 % ou moins
(occupation = (1 - disponible / taille) x 100 du systeme de fichiers racine, meme formule que la regle NODE_DISK_FULL ;
le kubelet declenche DiskPressure ET le ramasse-miettes d'IMAGES des 85 % : cluster en air-gap, images non retelechargeables).

Garanties (verrouillees par test_helios_disk_test.py) :
  * SIMULATION par defaut : rien n'est execute hors du processus. Une cible EXPLICITE est obligatoire (155 ou 158) ; les
    autres machines sont refusees. Le choix de la cible reste a l'exploitant : le risque d'un noeud qui heberge le primaire
    CNPG n'est JAMAIS presume accepte (refus sauf --accepte-risque-primaire-cnpg).
  * IDENTITE : le nom d'hote repondu en ssh, le nom du noeud Kubernetes et son InternalIP doivent concorder avec la cible
    (pre-vol ET reverification au lancement) ; sinon refus (une IP reattribuee inverserait la porte CNPG).
  * Un SEUL repertoire de test : /var/lib/helios-diskfill. Fichiers helios-diskfill-<jeton>-<NNN>.bin, crees par
    `fallocate` (extents non ecrits : aucune donnee n'est ecrite, le disque virtuel ne grossit pas). Aucun nettoyage
    general, aucun joker, aucune suppression recursive ; seuls les fichiers crees PAR CE LANCEMENT sont retires, par nom exact.
    Son systeme de fichiers REEL est verifie (meme peripherique et meme point de montage que /, ext4 ou xfs), ainsi que son
    type (vrai repertoire, pas un lien), son proprietaire (root:root) et ses permissions (0700).
  * Avant CHAQUE allocation : mesure fraiche, usage < urgence, usage < cible, usage apres allocation <= plafond, total CUMULE
    <= 6 Gio, delai disponible suffisant. APRES chaque allocation : nouvelle mesure ; l'espace disponible doit avoir baisse
    d'au moins 90 % de la taille allouee (sinon refus : les fichiers ne sont pas sur le volume mesure) ; urgence controlee aussitot.
  * Deux filets cote noeud sont armes AVANT la premiere allocation et verifies actifs (sinon rien n'est alloue) : un minuteur
    d'expiration (libere meme si ce processus meurt) et une GARDE (programme awk a modele fixe) qui libere des que l'occupation
    atteint urgence + 0,5 point, soit au moins 1,5 point sous le seuil du kubelet. Le delai restant, compte depuis l'armement
    reel, borne les allocations et la fenetre d'observation. Ils sont volatils (perdus si le noeud redemarre).
  * ORANGE : refus sauf acquittement explicite, code par code (--accepte-orange). Toute sonde illisible (Prometheus, base) = erreur.
  * Toute condition de securite manquante ou illisible = refus (echec sur).
Aucun secret n'est lu ni affiche. Ce fichier n'a JAMAIS ete execute contre l'infrastructure au moment de sa creation.
"""
import argparse
import importlib.util
import json
import math
import os
import re
import secrets
import shlex
import shutil
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from collections import namedtuple
from datetime import datetime, timezone

# ------------------------------------------------------------------------------------------------ cibles et reseau
CIBLES = {"155": ("k8s-worker2", "192.168.148.155"),
          "158": ("k8s-worker1", "192.168.148.158")}
ALIAS = {"155": "155", ".155": "155", "k8s-worker2": "155", "192.168.148.155": "155",
         "158": "158", ".158": "158", "k8s-worker1": "158", "192.168.148.158": "158"}
HORS_PERIMETRE = {"153": "k8s-master", "160": "monitoring", "161": "iacamunda / Bridge", "162": "k8s-lb2"}
VM_DOSSIER = {"155": "k8s-worker2", "158": "k8s-worker1"}
VM_BASE_DEFAUT = r"C:\Users\houss\Documents\Virtual Machines"
IP_MONITORING = "192.168.148.160"
PROM = "http://%s:9090/api/v1/query?" % IP_MONITORING
SSH = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", "-o", "StrictHostKeyChecking=no"]

# ------------------------------------------------------------------------------------------------ seuils (valides)
MIO = 1024 * 1024
GIO = 1024 * MIO
DETECTION_PCT = 80.0               # regle NODE_DISK_FULL : > 80
CIBLE_PCT, PLAFOND_PCT, URGENCE_PCT, RECUPERATION_PCT = 81.0, 82.0, 83.0, 77.0
MAX_CIBLE, MAX_PLAFOND, MAX_URGENCE, MAX_RECUPERATION = 81.0, 82.0, 83.0, 77.0     # on peut RESSERRER, jamais desserrer
MIN_CIBLE = 80.3                   # au moins ~0,3 point au-dessus de la detection
KUBELET_DEFAUT_PCT = 85.0          # imagefs.available 15 % et ramasse-miettes d'images (haut) : valeurs par defaut du kubelet
MARGE_KUBELET_PCT = 2.0            # l'arret d'urgence doit rester a au moins 2 points sous le declenchement du kubelet
BLOC_DEFAUT, BLOC_MIN, BLOC_MAX = 512 * MIO, 64 * MIO, 1024 * MIO
MAX_FICHIERS = 16
MAX_TOTAL = 6 * GIO                # jamais plus de 6 Gio alloues par un lancement
OBSERVE_DEFAUT_S, OBSERVE_MIN_S, OBSERVE_MAX_S = 720, 60, 1800
MARGE_EXPIRATION_S, EXPIRATION_MAX_S = 300, 3600
PAS_MESURE_S, PAS_SONDE_S = 5, 15  # suivi : occupation toutes les 5 s (urgence) ; incident (psql) toutes les 15 s
EFFET_MIN = 0.90                   # apres chaque allocation, l'espace disponible doit avoir baisse d'au moins 90 % de sa taille
BUDGET_ALLOC_MIN_S = 120           # temps minimal reserve aux allocations, compte depuis l'armement du minuteur
MARGE_FIN_S = 120                  # marge de fin (observation finie, liberation, verification) avant l'echeance du minuteur
DUREE_BLOC_MIN_S = 5               # duree estimee minimale d'un bloc (mesure + fallocate + mesure)
MARGE_GARDE_PCT = 0.5              # garde cote noeud : urgence + 0,5 point (>= 1,5 point sous le 85 % du kubelet)
GARDE_PAS_S = 2                    # la garde cote noeud mesure l'occupation toutes les 2 s
SONDE_ESSAIS = 2                   # lectures de l'incident avant de declarer la sonde illisible
HOTE_RAM_MIN_MO = 1500
HOTE_C_LIBRE_MIN_GO = 25.0
VMDK_CROISSANCE_ALERTE = 50 * MIO
GEL_MIN_S = 60                     # un gel VMware >= 60 s
GEL_ROUGE_S, GEL_ORANGE_S = 3600, 48 * 3600
NOTREADY_ROUGE_S, NOTREADY_ORANGE_S = 1800, 7200
DISQUE_RECENT_S = 600              # anti-flapping du backend (5 min) + delai de detection

# ------------------------------------------------------------------------------------------------ repertoire de test
DOSSIER = "/var/lib/helios-diskfill"
RE_JETON = re.compile(r"^[0-9a-f]{32}$")
RE_FICHIER = re.compile(r"^helios-diskfill-([0-9a-f]{32})-([0-9]{3})\.bin$")
PREFIXE_UNITE = "helios-diskfill-expire-"
PREFIXE_UNITE_GARDE = "helios-diskfill-garde-"
PARENT_DOSSIER = "/var/lib"
CHEMINS_INSPECTABLES = ("/", PARENT_DOSSIER, DOSSIER)          # jeu ferme : aucun autre chemin n'est jamais inspecte
RE_HOSTNAME = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?")
TYPES_FS_OK = ("ext4", "xfs")
TYPES_INCIDENT_DISQUE = ("NODE_DISK_FULL", "NODE_DISK_PRESSURE", "NODE_INODES_FULL")
STATUTS_ACTIFS = ("NEW", "OPEN", "IN_PROGRESS", "WAITING_FOR_APPROVAL")
ORDRE_NIVEAU = {"INFO": 0, "ORANGE": 1, "ROUGE": 2}
CODE_CNPG_ACCEPTE = "PRIMAIRE_CNPG_ACCEPTE"                    # acquitte UNIQUEMENT par --accepte-risque-primaire-cnpg
CODES_ORANGE = {
    CODE_CNPG_ACCEPTE: "le noeud heberge le primaire CNPG (risque accepte par --accepte-risque-primaire-cnpg)",
    "REMEDIATION_NON_ACTIVEE": "remediation disque non activee : le test ne validera que la detection",
    "GEL_HOTE_48H": "gel de l'hote dans les 48 dernieres heures",
    "NOTREADY_2H": "NODE_NOT_READY dans les 2 dernieres heures",
    "VMDK_INATTENDU": "type de disque virtuel (VMDK) inattendu",
    "E2E_INCIDENTS_DISQUE_ANCIENS": "incident(s) disque ouvert(s) sur un AUTRE noeud que .158 (porte du scenario DISK_E2E_158)",
}
CODES_ORANGE_ACQUITTABLES = tuple(c for c in CODES_ORANGE if c != CODE_CNPG_ACCEPTE)
CLE_E2E = "158"                    # seule cible du scenario DISK_E2E_158 : son lancement REEL passe par la porte de helios_disk_e2e.py
CODE_REMEDIATION_NON_ACTIVEE = "REMEDIATION_NON_ACTIVEE"


class RefusSecurite(Exception):
    """Une condition de securite n'est pas remplie (ou n'a pas pu etre verifiee) : on s'arrete SANS rien faire."""


class ArretUrgence(Exception):
    """L'occupation a atteint le seuil d'urgence : liberation immediate."""


class ErreurNoeud(Exception):
    """Mesure ou commande impossible sur le noeud."""


class SondeIllisible(ErreurNoeud):
    """Une sonde (Prometheus, base de donnees) n'a pas pu etre lue : on ne conclut RIEN et on libere (verdict ERREUR)."""


Res = namedtuple("Res", "rc out err")


# ------------------------------------------------------------------------------------------------ calculs (purs)
def usage_pct(taille, dispo):
    """Occupation en % = (1 - disponible / taille) x 100 : MEME FORMULE que la regle NODE_DISK_FULL du detecteur."""
    if not isinstance(taille, int) or not isinstance(dispo, int) or taille <= 0 or dispo < 0 or dispo > taille:
        raise ErreurNoeud("mesures incoherentes (taille=%r, disponible=%r)" % (taille, dispo))
    return (1.0 - float(dispo) / float(taille)) * 100.0


class Fs(namedtuple("Fs", "taille dispo")):
    """Systeme de fichiers racine : taille et espace disponible (hors reserve root), en octets."""
    __slots__ = ()

    @property
    def usage(self):
        return usage_pct(self.taille, self.dispo)


def octets_pour(fs, pct):
    """Octets a ajouter pour que l'occupation atteigne `pct` (0 si elle l'est deja)."""
    return max(0, int(math.ceil(pct / 100.0 * fs.taille)) - (fs.taille - fs.dispo))


def arrondi_mio(n):
    return ((int(n) + MIO - 1) // MIO) * MIO


def parse_stat_f(texte):
    """Sortie de `stat -f -c '%S %b %a %f' /` (taille de bloc, blocs, blocs disponibles, blocs libres) -> Fs."""
    mots = (texte or "").split()
    if len(mots) != 4 or not all(m.isdigit() for m in mots):
        raise ErreurNoeud("sortie de stat -f illisible : %r" % (texte or "")[:80])
    fr, blocs, dispo_b, libres = [int(m) for m in mots]
    if fr not in (512, 1024, 2048, 4096, 8192, 16384, 32768, 65536) or blocs <= 0 or dispo_b > libres or libres > blocs:
        raise ErreurNoeud("sortie de stat -f incoherente : %r" % (texte or "")[:80])
    return Fs(fr * blocs, fr * dispo_b)


def parse_hostname(texte):
    """Sortie de `hostname` -> nom (minuscules) ; ErreurNoeud si ce n'est pas UN nom d'hote."""
    t = (texte or "").strip()
    if not RE_HOSTNAME.fullmatch(t):
        raise ErreurNoeud("sortie de hostname illisible : %r" % t[:60])
    return t.lower()


def nom_court(nom):
    """Premier element d'un nom d'hote ('k8s-worker2.lan' -> 'k8s-worker2'), en minuscules."""
    return (nom or "").strip().lower().split(".")[0]


def parse_stat_chemin(texte):
    """Sortie de `stat -c '%F:%u:%g:%a:%d' -- chemin` (type, uid, gid, permissions octales, peripherique) -> dict."""
    champs = (texte or "").strip().split(":")
    if len(champs) != 5:
        raise ErreurNoeud("sortie de stat illisible : %r" % (texte or "")[:80])
    genre, uid, gid, mode, dev = champs
    if (not re.fullmatch(r"[a-z][a-z ]{2,38}", genre) or not (uid.isdigit() and gid.isdigit() and dev.isdigit())
            or not re.fullmatch(r"[0-7]{3,4}", mode)):
        raise ErreurNoeud("sortie de stat incoherente : %r" % (texte or "")[:80])
    return {"type": genre, "uid": int(uid), "gid": int(gid), "mode": mode, "dev": int(dev)}


def parse_findmnt(texte):
    """Sortie de `findmnt -n -o FSTYPE,TARGET -T chemin` -> {'fstype', 'montage'}."""
    mots = (texte or "").split()
    if len(mots) != 2:
        raise ErreurNoeud("sortie de findmnt illisible : %r" % (texte or "")[:80])
    return {"fstype": mots[0], "montage": mots[1]}


def anomalies_dossier(info, racine):
    """Anomalies (liste de textes) d'un repertoire de test EXISTANT ; [] = conforme. PURE. Verifie le vrai type (pas un lien),
    le proprietaire (root:root), les permissions (0700), et que le systeme de fichiers REEL est celui de / (meme peripherique,
    meme point de montage, ext4 ou xfs) : c'est celui que mesurent ssh et Prometheus."""
    if not info or not racine:
        return ["etat du repertoire ou de / illisible"]
    a = []
    if info.get("type") != "directory":
        a.append("n'est pas un repertoire (type : %s)" % info.get("type"))
    if info.get("uid") != 0 or info.get("gid") != 0:
        a.append("proprietaire %s:%s au lieu de root:root" % (info.get("uid"), info.get("gid")))
    if info.get("mode") != "700":
        a.append("permissions %s au lieu de 700" % info.get("mode"))
    a.extend(_anomalies_volume(info, racine))
    return a


def anomalies_parent(info, racine):
    """Idem pour le repertoire PARENT d'un repertoire de test absent (vrai repertoire, meme volume que /)."""
    if not info or not racine:
        return ["etat du repertoire parent ou de / illisible"]
    a = []
    if info.get("type") != "directory":
        a.append("n'est pas un repertoire (type : %s)" % info.get("type"))
    a.extend(_anomalies_volume(info, racine))
    return a


def _anomalies_volume(info, racine):
    a = []
    if info.get("dev") != racine.get("dev"):
        a.append("peripherique %s != celui de / (%s) : autre systeme de fichiers que celui mesure" % (info.get("dev"), racine.get("dev")))
    if info.get("fstype") not in TYPES_FS_OK:
        a.append("systeme de fichiers %s (ext4 ou xfs requis)" % info.get("fstype"))
    if info.get("montage") != "/":
        a.append("point de montage %s au lieu de /" % info.get("montage"))
    return a


def nom_fichier(jeton, i):
    if not RE_JETON.match(jeton or "") or not isinstance(i, int) or not 1 <= i <= MAX_FICHIERS:
        raise RefusSecurite("nom de fichier de test invalide (jeton=%r, index=%r)" % (jeton, i))
    return "helios-diskfill-%s-%03d.bin" % (jeton, i)


def noms_prevus(jeton):
    return [nom_fichier(jeton, i) for i in range(1, MAX_FICHIERS + 1)]


def chemin_de(nom):
    """Chemin complet d'un fichier de test ; refuse tout nom qui n'est pas exactement de la forme attendue (pas de joker)."""
    if not isinstance(nom, str) or not RE_FICHIER.match(nom):
        raise RefusSecurite("nom de fichier refuse (hors du motif du repertoire de test) : %r" % (nom,))
    return DOSSIER + "/" + nom


def nom_unite(jeton):
    if not RE_JETON.match(jeton or ""):
        raise RefusSecurite("jeton invalide : %r" % (jeton,))
    return PREFIXE_UNITE + jeton


def nom_unite_garde(jeton):
    if not isinstance(jeton, str) or not RE_JETON.fullmatch(jeton):      # fullmatch : un saut de ligne final est refuse
        raise RefusSecurite("jeton invalide : %r" % (jeton,))
    return PREFIXE_UNITE_GARDE + jeton


Params = namedtuple("Params", "cible plafond urgence recuperation bloc observe expiration")


def valider_parametres(cible=CIBLE_PCT, plafond=PLAFOND_PCT, urgence=URGENCE_PCT, recuperation=RECUPERATION_PCT,
                       bloc=BLOC_DEFAUT, observe_s=OBSERVE_DEFAUT_S, expiration_s=None):
    """Parametres -> Params, ou RefusSecurite. On peut resserrer les seuils valides, jamais les desserrer."""
    try:
        cible, plafond, urgence, recuperation = float(cible), float(plafond), float(urgence), float(recuperation)
        bloc, observe_s = int(bloc), int(observe_s)
        expiration_s = int(observe_s + MARGE_EXPIRATION_S) if expiration_s is None else int(expiration_s)
    except (TypeError, ValueError):
        raise RefusSecurite("parametre non numerique")
    for v in (cible, plafond, urgence, recuperation):
        if v != v or v in (float("inf"), float("-inf")):
            raise RefusSecurite("seuil non fini")
    if not MIN_CIBLE <= cible <= MAX_CIBLE:
        raise RefusSecurite("cible %.2f %% hors de [%.1f ; %.1f]" % (cible, MIN_CIBLE, MAX_CIBLE))
    if not cible < plafond <= MAX_PLAFOND:
        raise RefusSecurite("plafond %.2f %% invalide (cible < plafond <= %.1f)" % (plafond, MAX_PLAFOND))
    if not plafond < urgence <= MAX_URGENCE:
        raise RefusSecurite("arret d'urgence %.2f %% invalide (plafond < urgence <= %.1f)" % (urgence, MAX_URGENCE))
    if not 0.0 < recuperation <= MAX_RECUPERATION:
        raise RefusSecurite("recuperation %.2f %% invalide (0 < recuperation <= %.1f)" % (recuperation, MAX_RECUPERATION))
    if not BLOC_MIN <= bloc <= BLOC_MAX:
        raise RefusSecurite("taille de bloc %d Mo hors de [%d ; %d] Mo" % (bloc // MIO, BLOC_MIN // MIO, BLOC_MAX // MIO))
    if not OBSERVE_MIN_S <= observe_s <= OBSERVE_MAX_S:
        raise RefusSecurite("duree d'observation %d s hors de [%d ; %d]" % (observe_s, OBSERVE_MIN_S, OBSERVE_MAX_S))
    if not observe_s + BUDGET_ALLOC_MIN_S + MARGE_FIN_S <= expiration_s <= EXPIRATION_MAX_S:
        raise RefusSecurite("expiration %d s invalide (observation + %d s <= expiration <= %d s : %d s pour les allocations + %d s de "
                            "marge de fin)" % (expiration_s, BUDGET_ALLOC_MIN_S + MARGE_FIN_S, EXPIRATION_MAX_S,
                                               BUDGET_ALLOC_MIN_S, MARGE_FIN_S))
    return Params(cible, plafond, urgence, recuperation, bloc, observe_s, expiration_s)


def budget_allocation(p):
    """Secondes, COMPTEES DEPUIS L'ARMEMENT du minuteur, pendant lesquelles on peut encore allouer : le reste de l'expiration doit
    couvrir toute l'observation puis la marge de fin (liberation + verification) : l'observation ne peut pas depasser l'echeance."""
    return p.expiration - p.observe - MARGE_FIN_S


def seuil_garde_x100(p):
    """Seuil de la garde cote noeud, en centiemes de % : urgence + 0,5 point (au moins 1,5 point sous le 85 % du kubelet)."""
    return int(round((p.urgence + MARGE_GARDE_PCT) * 100))


def jeton_confirmation(cle, p):
    """Texte a retaper apres --confirm pour un lancement reel (pas d'accident possible avec un simple --go)."""
    return "DISK-%s-%g-%g-%g" % (cle, p.cible, p.plafond, p.urgence)


def resoudre_cible(valeur):
    """Cible explicite obligatoire : '155' ou '158' (ou k8s-worker2 / k8s-worker1). Tout le reste est refuse."""
    if valeur is None or not str(valeur).strip():
        raise RefusSecurite("cible EXPLICITE obligatoire : --target 155 ou --target 158 (aucune valeur par defaut)")
    v = str(valeur).strip()
    if v in ALIAS:
        return ALIAS[v]
    suffixe = v.lstrip(".").split(".")[-1]
    if suffixe in HORS_PERIMETRE:
        raise RefusSecurite("la cible .%s (%s) est HORS PERIMETRE : seules .155 et .158 sont autorisees" % (suffixe, HORS_PERIMETRE[suffixe]))
    raise RefusSecurite("cible inconnue : %r (valeurs : 155, 158)" % v)


def planifier(fs, p):
    """Plan theorique (pur) : tailles des blocs a allouer pour atteindre la cible. Les blocs REELS sont recalcules, a partir
    de mesures fraiches, avant chaque allocation. Refuse tout plan qui depasserait une limite."""
    u = fs.usage
    if u >= p.urgence:
        raise RefusSecurite("occupation %.2f %% >= seuil d'urgence %.1f %%" % (u, p.urgence))
    if u >= DETECTION_PCT:
        raise RefusSecurite("occupation %.2f %% >= seuil de detection (%.0f %%) : un incident naturel existerait" % (u, DETECTION_PCT))
    if u > p.recuperation:
        raise RefusSecurite("occupation de base %.2f %% > seuil de recuperation %.1f %% : recuperation impossible" % (u, p.recuperation))
    reste = octets_pour(fs, p.cible)
    if reste <= 0:
        raise RefusSecurite("cible deja atteinte")
    if reste > MAX_TOTAL:
        raise RefusSecurite("remplissage de %d Mo > limite de %d Mo" % (reste // MIO, MAX_TOTAL // MIO))
    blocs, cumul = [], 0
    while reste > 0:
        b = min(arrondi_mio(reste), p.bloc)
        blocs.append(b)
        reste -= b
        cumul += b
        if Fs(fs.taille, fs.dispo - cumul).usage > p.plafond:
            raise RefusSecurite("le plan depasserait le plafond de %.1f %%" % p.plafond)
    if len(blocs) > MAX_FICHIERS:
        raise RefusSecurite("trop de blocs (%d > %d)" % (len(blocs), MAX_FICHIERS))
    return blocs


def garde_allocation(fs, octets, p, deja_alloue=0):
    """(ok, raison) a evaluer AVANT chaque allocation, sur des mesures FRAICHES. `deja_alloue` = total cumule du lancement."""
    if not isinstance(octets, int) or octets <= 0 or octets > p.bloc:
        return False, "taille de bloc hors limites (%r)" % (octets,)
    if not isinstance(deja_alloue, int) or deja_alloue < 0 or deja_alloue + octets > MAX_TOTAL:
        return False, "plafond cumule de %d Mo depasse (deja %s Mo + bloc de %d Mo)" % (
            MAX_TOTAL // MIO, deja_alloue // MIO if isinstance(deja_alloue, int) else deja_alloue, octets // MIO)
    u = fs.usage
    if u >= p.urgence:
        return False, "occupation %.2f %% >= seuil d'urgence %.1f %%" % (u, p.urgence)
    if u >= p.cible:
        return False, "cible deja atteinte (%.2f %%)" % u
    if octets >= fs.dispo:
        return False, "le bloc depasserait l'espace disponible"
    apres = Fs(fs.taille, fs.dispo - octets).usage
    if apres > p.plafond:
        return False, "occupation apres allocation %.2f %% > plafond %.1f %%" % (apres, p.plafond)
    return True, "ok"


def programme_garde(chemins, seuil_x100, boucles):
    """Programme awk de la GARDE cote noeud (MODELE FIXE, aucun `sh -c` : pas de `$`, pas de `%`, pas de `${`). Toutes les
    GARDE_PAS_S secondes il lit `df -P -k /` ; des que (taille - disponible) / taille >= seuil (formule du detecteur) il retire les
    fichiers EXACTS de ce lancement (`rm -f --`, noms valides, aucun joker) puis s'arrete ; sinon il s'arrete apres `boucles`
    tours. Seules valeurs inserees : deux entiers bornes et des chemins qui ont passe le motif exact du repertoire de test."""
    if not isinstance(seuil_x100, int) or isinstance(seuil_x100, bool) or not 8000 <= seuil_x100 <= 8800:
        raise RefusSecurite("seuil de garde hors limites : %r" % (seuil_x100,))
    if not isinstance(boucles, int) or isinstance(boucles, bool) or not 1 <= boucles <= 2000:
        raise RefusSecurite("nombre de tours de garde hors limites : %r" % (boucles,))
    if not chemins or len(chemins) > MAX_FICHIERS:
        raise RefusSecurite("liste de fichiers de garde invalide")
    prefixe = DOSSIER + "/"
    for c in chemins:
        if not isinstance(c, str) or not c.startswith(prefixe) or not RE_FICHIER.fullmatch(c[len(prefixe):]):
            raise RefusSecurite("chemin refuse pour la garde : %r" % (c,))
    return ('BEGIN { for (n = 0; n < %d; n++) { c = "df -P -k /"; c | getline h; c | getline d; close(c); split(d, f, " "); '
            'if (f[2] > 0 && (f[2] - f[4]) * 10000 / f[2] >= %d) { system("/usr/bin/rm -f -- %s"); exit 0 } '
            'system("sleep %d") } }' % (boucles, seuil_x100, " ".join(chemins), GARDE_PAS_S))


# ------------------------------------------------------------------------------------------------ parsing (pur)
def _bloc_yaml(texte, nom):
    """Valeurs d'un bloc YAML a un niveau (cle: valeur indentees) ; None si le bloc est absent."""
    dedans, sortie = False, {}
    for ligne in (texte or "").splitlines():
        if re.match(r"^%s:\s*$" % re.escape(nom), ligne):
            dedans = True
            continue
        if dedans:
            if re.match(r"^\s+\S", ligne):
                k, _, v = ligne.strip().partition(":")
                sortie[k.strip()] = v.strip().strip("\"'")
            else:
                break
    return sortie if dedans else None


def _pct(valeur):
    m = re.match(r"^([0-9]+(?:\.[0-9]+)?)%$", valeur or "")
    return float(m.group(1)) if m else None


def parse_kubelet(texte):
    """config.yaml du kubelet -> seuils d'eviction / ramasse-miettes, ou None si le fichier n'est pas lisible.
    Une valeur absente = defaut du kubelet ; une valeur non exprimee en pourcentage = inconnue (None)."""
    if not texte or "KubeletConfiguration" not in texte:
        return None
    dur = _bloc_yaml(texte, "evictionHard")
    souple = _bloc_yaml(texte, "evictionSoft") or {}
    defauts = {"imagefs.available": "15%", "nodefs.available": "10%"}
    dur = dict(defauts) if dur is None else dict(dur)
    for k, v in defauts.items():
        dur.setdefault(k, v)

    def haut(cle, defaut):
        m = re.search(r"^%s:\s*([0-9]+)\s*$" % re.escape(cle), texte, re.M)
        if m:
            return float(m.group(1))
        return None if re.search(r"^%s:" % re.escape(cle), texte, re.M) else defaut
    seuils = {}
    for cle, nom in (("imagefs.available", "imagefs"), ("nodefs.available", "nodefs")):
        vals = [_pct(dur.get(cle))]
        if cle in souple:
            vals.append(_pct(souple[cle]))
        seuils[nom] = None if any(v is None for v in vals) else max(vals)
    return {"imagefs": seuils["imagefs"], "nodefs": seuils["nodefs"],
            "gc_haut": haut("imageGCHighThresholdPercent", 85.0), "gc_bas": haut("imageGCLowThresholdPercent", 80.0)}


def seuil_kubelet(k):
    """Occupation (%) a partir de laquelle le kubelet agit (DiskPressure, ramasse-miettes d'images) ; None si inconnue."""
    if not k or k.get("imagefs") is None or k.get("nodefs") is None or k.get("gc_haut") is None:
        return None
    return min(100.0 - max(k["imagefs"], k["nodefs"]), k["gc_haut"])


def valeurs_prom(obj):
    """Reponse JSON de l'API Prometheus -> liste de flottants ([] si absente ou invalide)."""
    try:
        if obj["status"] != "success":
            return []
        return [float(x["value"][1]) for x in obj["data"]["result"]]
    except (KeyError, TypeError, ValueError, IndexError):
        return []


def parse_noeuds(sortie, nom):
    """Sortie de `kubectl get nodes --no-headers` -> (prets, total, cible_prete)."""
    lignes = [l.split() for l in (sortie or "").splitlines() if l.strip()]
    prets = sum(1 for l in lignes if len(l) > 1 and l[1] == "Ready")
    cible = any(len(l) > 1 and l[0] == nom and l[1] == "Ready" for l in lignes)
    return prets, len(lignes), cible


def parse_descripteur(texte):
    """Descripteur texte d'un VMDK (createType, parent, extents) ; None s'il n'est pas un descripteur."""
    if not (texte or "").startswith("# Disk DescriptorFile"):
        return None
    info = {"extents": []}
    for ligne in texte.splitlines():
        ligne = ligne.strip()
        m = re.match(r'^(RW|RDONLY|NOACCESS)\s+([0-9]+)\s+(\w+)\s+"([^"]+)"', ligne)
        if m:
            info["extents"].append((m.group(1), int(m.group(2)), m.group(3), m.group(4)))
            continue
        m = re.match(r'^(createType|parentCID|parentFileNameHint)\s*=\s*"?(.*?)"?$', ligne)
        if m:
            info[m.group(1)] = m.group(2)
    return info


def analyser_vmdk(dossier_vm):
    """Type et occupation du disque virtuel d'une VM (LECTURE de fichiers de l'hote) -> dict, ou None si illisible."""
    try:
        vmx = [f for f in os.listdir(dossier_vm) if f.endswith(".vmx")]
        if len(vmx) != 1:
            return None
        fichier = None
        with open(os.path.join(dossier_vm, vmx[0]), encoding="utf-8", errors="replace") as f_vmx:
            for ligne in f_vmx:
                m = re.match(r'\s*scsi0:0\.fileName\s*=\s*"(.*?)"', ligne)
                if m:
                    fichier = m.group(1)
        if not fichier:
            return None
        chemin = os.path.join(dossier_vm, fichier)
        info = parse_descriptor_fichier(chemin)
        if info is None:
            return None
        capacite = sum(e[1] for e in info["extents"]) * 512
        alloue = sum(os.path.getsize(os.path.join(dossier_vm, e[3])) for e in info["extents"])
        snap = 0
        vmsd = os.path.join(dossier_vm, vmx[0][:-4] + ".vmsd")
        if os.path.exists(vmsd):
            with open(vmsd, encoding="utf-8", errors="replace") as f_vmsd:
                m = re.search(r'snapshot\.numSnapshots\s*=\s*"([0-9]+)"', f_vmsd.read())
            snap = int(m.group(1)) if m else 0
        delta = [f for f in os.listdir(dossier_vm) if re.search(r"-0000[0-9][0-9](-s[0-9]+)?\.vmdk$", f)]
        ct = info.get("createType", "")
        return {"type": ct, "extents": len(info["extents"]), "capacite": capacite, "alloue": alloue,
                "reste": max(0, capacite - alloue), "croissant": "Sparse" in ct, "prealloue": "Flat" in ct or "flat" in ct,
                "parent": info.get("parentCID", "ffffffff") != "ffffffff" or bool(info.get("parentFileNameHint")),
                "snapshots": snap + len(delta)}
    except (OSError, ValueError):
        return None


def parse_descriptor_fichier(chemin):
    with open(chemin, "rb") as f:
        debut = f.read(64)
    if not debut.startswith(b"# Disk DescriptorFile"):
        return None
    with open(chemin, encoding="latin-1", errors="replace") as f:
        return parse_descripteur(f.read())


RE_GEL = re.compile(r"^([0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2})\.[0-9]+Z .*GuestInfo collection interval longer than "
                    r"expected; actual=([0-9]+) sec")


def gels_vmware(base, maintenant, fenetre_s=GEL_ORANGE_S):
    """Gels (>= 60 s) vus par VMware dans les vmware.log de toutes les VM de `base`, sur la fenetre -> [(age_s, duree_s, vm)],
    ou None si les journaux sont illisibles (jamais une liste vide par defaut : l'absence d'information n'est pas un feu vert)."""
    try:
        sortie = []
        vus = 0
        for vm in sorted(os.listdir(base)):
            dossier = os.path.join(base, vm)
            if not os.path.isdir(dossier):
                continue
            logs = [f for f in sorted(os.listdir(dossier)) if re.match(r"^vmware(-[0-9]+)?\.log$", f)]
            if not logs:
                continue
            vus += 1
            for fichier in logs:                       # journal courant ET journaux des sessions precedentes
                with open(os.path.join(dossier, fichier), encoding="utf-8", errors="replace") as f:
                    for ligne in f:
                        m = RE_GEL.match(ligne)
                        if not m or int(m.group(2)) < GEL_MIN_S:
                            continue
                        t = datetime.strptime(m.group(1), "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
                        age = (maintenant - t).total_seconds()
                        if 0 <= age <= fenetre_s:
                            sortie.append((int(age), int(m.group(2)), vm))
        return sortie if vus else None
    except OSError:
        return None


def memoire_hote_mo():
    """RAM disponible de l'hote Windows en Mo (GlobalMemoryStatusEx) ; None si indisponible."""
    try:
        import ctypes

        class MS(ctypes.Structure):
            _fields_ = [("l", ctypes.c_ulong), ("u", ctypes.c_ulong), ("tp", ctypes.c_ulonglong), ("ap", ctypes.c_ulonglong),
                        ("tpf", ctypes.c_ulonglong), ("apf", ctypes.c_ulonglong), ("tv", ctypes.c_ulonglong),
                        ("av", ctypes.c_ulonglong), ("ae", ctypes.c_ulonglong)]
        m = MS()
        m.l = ctypes.sizeof(MS)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
        return int(m.ap / 1048576)
    except Exception:
        return None


def espace_libre_go(chemin):
    try:
        return shutil.disk_usage(chemin).free / float(GIO)
    except OSError:
        return None


# ------------------------------------------------------------------------------------------------ evaluation du pre-vol (pure)
def evaluer(f, cle, p, ack_cnpg=False, controle_drapeau=True):
    """Faits -> rapport. PURE : aucune E/S. Chaque raison = (niveau, constat, remede, code) ; `code` n'est renseigne que pour les
    ORANGE (CODES_ORANGE) : un ORANGE doit etre acquitte code par code pour qu'un lancement reel soit possible. Tout fait absent
    ou illisible = ROUGE. `controle_drapeau=False` : le drapeau d'origine (HOST_DISK_REMEDIATION_ENABLED) n'est PAS evalue ici ; la porte
    du scenario DISK_E2E_158 (helios_disk_e2e.py) evalue elle-meme, et plus strictement, les drapeaux du Backend."""
    nom, ip = CIBLES[cle]
    r = []

    def add(niveau, constat, remede="", code=None):
        if niveau == "ORANGE" and code not in CODES_ORANGE:
            raise AssertionError("raison ORANGE sans code connu : %r" % (constat,))
        r.append((niveau, constat, remede, code))
    plan = None
    n = f.get("noeud")
    if not n or not n.get("joignable"):
        add("ROUGE", "noeud .%s injoignable en ssh ou mesures impossibles" % cle, "aucune mesure = aucun test ; verifier la VM")
    else:
        fs = n.get("fs")
        if fs is None:
            add("ROUGE", "occupation du systeme de fichiers racine illisible", "aucun remplissage sans mesure")
        else:
            try:
                u = fs.usage
                if u >= p.urgence:
                    add("ROUGE", "occupation deja de %.2f %% (>= urgence %.1f %%)" % (u, p.urgence), "liberer de l'espace avant tout test")
                elif u >= DETECTION_PCT:
                    add("ROUGE", "occupation deja de %.2f %% (>= %.0f %%) : un incident disque existe ou existerait" % (u, DETECTION_PCT),
                        "traiter la cause avant le test")
                elif u > p.recuperation:
                    add("ROUGE", "occupation de base %.2f %% > recuperation %.1f %% : la recuperation serait impossible" % (u, p.recuperation),
                        "liberer de l'espace ou ne pas tester ce noeud")
                else:
                    besoin = octets_pour(fs, p.cible)
                    if besoin > MAX_TOTAL:
                        add("ROUGE", "remplissage de %d Mo > limite de %d Mo" % (besoin // MIO, MAX_TOTAL // MIO), "cible irrealiste pour ce noeud")
                    else:
                        plan = {"usage": u, "besoin": besoin, "blocs": len(planifier(fs, p)),
                                "pour_detection": octets_pour(fs, DETECTION_PCT + 0.01)}
                        add("INFO", "occupation %.2f %% ; +%d Mo pour atteindre %.1f %% (plafond %.1f %%, urgence %.1f %%)" % (
                            u, besoin // MIO, p.cible, p.plafond, p.urgence), "")
            except (ErreurNoeud, RefusSecurite) as e:
                add("ROUGE", "plan de remplissage impossible : %s" % e, "")
        ft = n.get("fstype")
        if ft not in TYPES_FS_OK:
            add("ROUGE", "type de systeme de fichiers %r non valide (ext4 ou xfs requis : fallocate sans ecriture de donnees)" % (ft,),
                "ne pas tester sur un autre type")
        if n.get("fallocate") is not True:
            add("ROUGE", "fallocate absent ou non verifie sur le noeud", "installer util-linux ou ne pas tester")
        d = n.get("dossier")
        if d == "inconnu":
            add("ROUGE", "etat du repertoire de test %s illisible" % DOSSIER, "")
        elif d:
            add("ROUGE", "%s contient deja %d fichier(s) : residu d'un test precedent (aucun nettoyage automatique)" % (DOSSIER, len(d)),
                "l'exploitant les examine et les retire lui-meme")
        else:
            add("INFO", "repertoire de test %s : %s" % (DOSSIER, "absent (sera cree, mode 0700)" if d is None else "vide"), "")
        racine, di, parent = n.get("racine"), n.get("dossier_info", "inconnu"), n.get("parent")
        if racine is None:
            add("ROUGE", "etat de / (peripherique, systeme de fichiers, montage) illisible", "refus (echec sur)")
        elif di == "inconnu":
            add("ROUGE", "etat reel (type, proprietaire, permissions, volume) du repertoire de test illisible", "refus (echec sur)")
        else:
            anomalies = anomalies_parent(parent, racine) if di is None else anomalies_dossier(di, racine)
            for msg in anomalies:
                add("ROUGE", "%s %s : %s" % ("repertoire parent" if di is None else "repertoire de test",
                                             PARENT_DOSSIER if di is None else DOSSIER, msg),
                    "les fichiers de test ne seraient pas sur le volume mesure par ssh et Prometheus ; l'exploitant corrige lui-meme")
            if not anomalies:
                add("INFO", "%s : meme volume que / (peripherique %s, %s), type et droits conformes" % (
                    "repertoire parent " + PARENT_DOSSIER if di is None else "repertoire de test", racine.get("dev"), racine.get("fstype")), "")
        hn = n.get("hostname")
        if hn is None:
            add("ROUGE", "nom d'hote repondu en ssh illisible", "refus (echec sur) : l'identite de la machine n'est pas prouvee")
        elif nom_court(hn) != nom_court(nom):
            add("ROUGE", "ssh sur %s repond le nom d'hote %r, le noeud Kubernetes attendu est %r : MAUVAISE MACHINE (IP reattribuee ?)" % (
                ip, hn, nom), "ne rien lancer ; verifier la correspondance IP / nom avant toute decision")
        k = n.get("kubelet")
        seuil = seuil_kubelet(k)
        if seuil is None:
            add("ROUGE", "seuils du kubelet inconnus : le ramasse-miettes d'images (air-gap) pourrait se declencher",
                "lire /var/lib/kubelet/config.yaml ; sans cela, pas de test")
        elif p.urgence > seuil - MARGE_KUBELET_PCT + 1e-9:
            add("ROUGE", "arret d'urgence %.1f %% trop proche du declenchement du kubelet (%.1f %%, marge exigee %.1f points)" % (
                p.urgence, seuil, MARGE_KUBELET_PCT), "resserrer les seuils (jamais les desserrer)")
        else:
            add("INFO", "kubelet : DiskPressure et ramasse-miettes d'images des %.1f %% ; marge de l'arret d'urgence %.1f points" % (
                seuil, seuil - p.urgence), "")
            add("INFO", "garde cote noeud : liberation des %.1f %% (urgence + %.1f point), a %.1f points du kubelet" % (
                seuil_garde_x100(p) / 100.0, MARGE_GARDE_PCT, seuil - seuil_garde_x100(p) / 100.0), "")
    pr = f.get("prom") or {}
    illisibles = pr.get("illisibles") or []
    if illisibles:
        add("ROUGE", "Prometheus illisible (requete(s) : %s)" % ", ".join(illisibles),
            "refus (echec sur) : la detection et la verification reposent sur Prometheus")
    if pr.get("series") != 1:
        add("ROUGE", "Prometheus : %s serie(s) pour la racine de .%s (exactement 1 attendue)" % (pr.get("series"), cle),
            "la detection et la verification reposent sur cette serie unique")
    else:
        u_prom = pr.get("usage")
        fs = (n or {}).get("fs")
        if u_prom is None:
            add("ROUGE", "occupation Prometheus illisible", "")
        elif fs is not None and abs(u_prom - fs.usage) > 1.0:
            add("ROUGE", "ecart de %.1f points entre Prometheus (%.2f %%) et le noeud (%.2f %%)" % (abs(u_prom - fs.usage), u_prom, fs.usage),
                "un des deux releves est faux : refus (echec sur)")
    if pr.get("incident_actif") is None:
        add("ROUGE", "helios_incident illisible dans Prometheus pour %s" % nom, "refus (echec sur)")
    elif pr["incident_actif"]:
        add("ROUGE", "helios_incident deja actif sur %s" % nom, "attendre sa fin")
    c = f.get("cluster")
    if c is None:
        add("ROUGE", "etat du cluster inconnu (kubectl muet)", "")
    elif c[0] < c[1]:
        add("ROUGE", "cluster degrade : %d/%d noeuds Ready" % (c[0], c[1]), "attendre le retour a %d/%d" % (c[1], c[1]))
    nk = f.get("noeud_k8s") or {}
    if nk.get("ready") is not True:
        add("ROUGE", "noeud %s non Ready ou etat inconnu" % nom, "")
    if nk.get("disk_pressure") is not False:
        add("ROUGE", "DiskPressure actif ou inconnu sur %s" % nom, "")
    ips = nk.get("internal_ips")
    nom_k8s = nk.get("nom_k8s")
    if not ips:
        add("ROUGE", "InternalIP du noeud Kubernetes %s illisible ou absente" % nom, "refus (echec sur) : l'identite de la machine n'est pas prouvee")
    elif ip not in ips:
        add("ROUGE", "InternalIP du noeud Kubernetes %s = %s mais la cible ssh est %s : MAUVAISE MACHINE" % (nom, ", ".join(ips), ip),
            "ne rien lancer ; verifier la correspondance IP / nom avant toute decision")
    if nom_k8s is not None and nom_k8s != nom:
        add("ROUGE", "le noeud Kubernetes lu s'appelle %r et non %r" % (nom_k8s, nom), "refus (echec sur)")
    hn_ok = (n or {}).get("hostname") is not None and nom_court((n or {}).get("hostname")) == nom_court(nom)
    if hn_ok and ips and ip in ips and (nom_k8s is None or nom_k8s == nom):
        add("INFO", "identite verifiee : nom d'hote ssh = noeud Kubernetes %s ; InternalIP %s = IP ssh" % (nom, ip), "")
    cn = f.get("cnpg")
    if cn is None or cn.get("primaire_sur_cible") is None or cn.get("sain") is None:
        add("ROUGE", "role CNPG de la cible ou sante de CNPG inconnus", "refus (echec sur)")
    else:
        if cn["sain"] is False:
            add("ROUGE", "CNPG non sain", "attendre le retour a la normale")
        if cn["primaire_sur_cible"]:
            if ack_cnpg:
                add("ORANGE", "%s HEBERGE LE PRIMAIRE CNPG : risque accepte explicitement (--accepte-risque-primaire-cnpg)" % nom,
                    "un depassement menacerait la base : plafond %.1f %%, urgence %.1f %%, garde et expiration armees" % (p.plafond, p.urgence),
                    CODE_CNPG_ACCEPTE)
            else:
                add("ROUGE", "%s HEBERGE LE PRIMAIRE CNPG : un depassement du remplissage menacerait la base ; risque NON accepte" % nom,
                    "choisir l'autre noeud, ou accepter le risque PAR ECRIT puis relancer avec --accepte-risque-primaire-cnpg")
    inc = f.get("incidents")
    if inc is None:
        add("ROUGE", "incidents illisibles (base de donnees)", "refus (echec sur)")
    else:
        if inc.get("disque_actifs"):
            add("ROUGE", "incident disque actif ou recent sur %s (%s) : le backend le CORRELERAIT, aucun nouvel incident" % (
                nom, ", ".join("#%s" % i for i in inc["disque_actifs"])), "attendre sa fin")
        ages = inc.get("notready_ages_s") or []
        if any(a < NOTREADY_ROUGE_S for a in ages):
            add("ROUGE", "NODE_NOT_READY il y a moins de %d min" % (NOTREADY_ROUGE_S // 60), "attendre la stabilite du cluster")
        elif any(a < NOTREADY_ORANGE_S for a in ages):
            add("ORANGE", "NODE_NOT_READY il y a moins de %d h" % (NOTREADY_ORANGE_S // 3600), "cluster peu stable", "NOTREADY_2H")
    dr = f.get("drapeau")
    if not controle_drapeau:
        pass
    elif dr == "true":
        add("INFO", "remediation disque activee (HOST_DISK_REMEDIATION_ENABLED=true)" + (
            "" if cle == CLE_E2E else " ; NB : le Backend ne remedie JAMAIS NODE_DISK_FULL sur %s : detection seule" % nom), "")
    elif dr in ("false", "absent"):
        add("ORANGE", "remediation disque NON activee (drapeau %s) : le test ne validera que la DETECTION" % dr,
            "les fichiers seront liberes par l'outil (expiration) ; la remediation exige le Bridge, le drapeau et un deploiement autorises",
            CODE_REMEDIATION_NON_ACTIVEE)
    else:
        add("ROUGE", "etat du drapeau de remediation disque illisible", "refus (echec sur)")
    h = f.get("hote") or {}
    ram = h.get("ram_mo")
    if ram is None:
        add("ROUGE", "RAM de l'hote illisible", "refus (echec sur)")
    elif ram < HOTE_RAM_MIN_MO:
        add("ROUGE", "RAM de l'hote basse (%d Mo < %d Mo) : gels de VM" % (ram, HOTE_RAM_MIN_MO), "liberer de la RAM de l'hote")
    cl = h.get("c_libre_go")
    if cl is None:
        add("ROUGE", "espace libre de C: illisible", "refus (echec sur)")
    elif cl < HOTE_C_LIBRE_MIN_GO:
        add("ROUGE", "C: n'a que %.1f Go libres (< %.0f Go)" % (cl, HOTE_C_LIBRE_MIN_GO), "liberer de l'espace sur l'hote")
    g = h.get("gels")
    if g is None:
        add("ROUGE", "journaux VMware illisibles : impossible d'exclure un gel recent de l'hote", "refus (echec sur)")
    else:
        recents = [x for x in g if x[0] < GEL_ROUGE_S]
        anciens = [x for x in g if GEL_ROUGE_S <= x[0] < GEL_ORANGE_S]
        if recents:
            add("ROUGE", "gel de l'hote il y a moins d'1 h (%s)" % ", ".join("%s %d s" % (x[2], x[1]) for x in recents), "attendre l'absence de gel")
        elif anciens:
            add("ORANGE", "gel de l'hote dans les 48 dernieres heures (%d)" % len(anciens), "hote peu stable", "GEL_HOTE_48H")
    v = h.get("vmdk")
    if v is None:
        add("ROUGE", "type du disque virtuel (VMDK) illisible", "refus (echec sur)")
    elif v.get("parent"):
        add("ROUGE", "VMDK avec parent (clone lie) : croissance non maitrisee", "ne pas tester")
    elif v.get("snapshots"):
        add("ROUGE", "VMDK avec %s snapshot(s) / delta : les ecritures iraient dans le delta" % v["snapshots"], "ne pas tester")
    elif v.get("croissant"):
        add("INFO", "VMDK %s croissant : alloue %.2f Go sur %.2f Go ; fallocate n'ecrit aucune donnee (croissance attendue ~0)" % (
            v.get("type"), v["alloue"] / float(GIO), v["capacite"] / float(GIO)), "")
    elif v.get("prealloue"):
        add("INFO", "VMDK preallocue : aucune croissance possible sur C:", "")
    else:
        add("ORANGE", "type de VMDK inattendu : %s" % v.get("type"), "", "VMDK_INATTENDU")
    verdict = "VERT"
    for niv in ("ORANGE", "ROUGE"):
        if any(x[0] == niv for x in r):
            verdict = niv
    return {"raisons": r, "verdict": verdict, "plan": plan}


def orange_non_acquittes(rapport, acquittes):
    """Raisons ORANGE qui ne sont PAS acquittees. Le risque CNPG n'est acquitte que par --accepte-risque-primaire-cnpg (sans lui
    la raison est ROUGE) ; tout autre ORANGE doit figurer, par son code, dans `acquittes` (--accepte-orange)."""
    return [x for x in rapport["raisons"] if x[0] == "ORANGE" and x[3] != CODE_CNPG_ACCEPTE and x[3] not in (acquittes or ())]


def analyser_acquittements(valeurs):
    """--accepte-orange (liste de textes 'A,B') -> (ensemble de codes, erreur | None). Code inconnu ou non acquittable = refus."""
    codes = set()
    for v in valeurs or []:
        for c in str(v).split(","):
            c = c.strip()
            if not c:
                continue
            if c not in CODES_ORANGE_ACQUITTABLES:
                return set(), "code ORANGE inconnu ou non acquittable par --accepte-orange : %r (valeurs : %s)" % (
                    c, ", ".join(CODES_ORANGE_ACQUITTABLES))
            codes.add(c)
    return codes, None


def afficher_rapport(rapport, cle, p, sortie=print, titre="PRE-VOL DU TEST DISQUE"):
    nom = CIBLES[cle][0]
    sortie("%s  .%s (%s)" % (titre, cle, nom))
    sortie("=" * 78)
    for niv, msg, remede, code in rapport["raisons"]:
        sortie("  [%s] %s" % (niv, msg))
        if remede:
            sortie("          -> %s" % remede)
        if niv == "ORANGE" and code != CODE_CNPG_ACCEPTE:
            sortie("          acquittement exige pour --go : --accepte-orange %s" % code)
    sortie("")
    sortie("VERDICT : %s" % rapport["verdict"])


# ------------------------------------------------------------------------------------------------ acces au noeud
class Noeud(object):
    """Interface des operations sur le noeud : TOUT acces au noeud passe par elle (jeu ferme de commandes)."""

    def stats(self): raise NotImplementedError
    def type_fs(self): raise NotImplementedError
    def fallocate_present(self): raise NotImplementedError
    def config_kubelet(self): raise NotImplementedError
    def contenu_dossier(self): raise NotImplementedError          # None = absent ; [] = vide ; [noms]
    def creer_dossier(self): raise NotImplementedError
    def supprimer_dossier_vide(self): raise NotImplementedError
    def allouer(self, nom, octets): raise NotImplementedError
    def liberer(self, noms): raise NotImplementedError
    def armer_expiration(self, jeton, noms, delai_s): raise NotImplementedError
    def expiration_active(self, jeton): raise NotImplementedError
    def desarmer_expiration(self, jeton): raise NotImplementedError
    def hostname(self): raise NotImplementedError
    def inspecter(self, chemin): raise NotImplementedError        # None = absent ; sinon type, uid, gid, mode, dev, fstype, montage
    def armer_garde(self, jeton, noms, seuil_x100, delai_s): raise NotImplementedError
    def garde_active(self, jeton): raise NotImplementedError
    def desarmer_garde(self, jeton): raise NotImplementedError


class NoeudSsh(Noeud):
    """Noeud reel, joint par ssh. Jeu FERME de commandes : stat, findmnt, hostname, test, ls, cat, mkdir, rmdir, fallocate, rm (noms
    exacts), systemd-run (minuteur d'expiration ET garde : la garde execute /usr/bin/awk avec un programme a MODELE FIXE, sans
    `sh -c`), systemctl (is-active / stop de CES deux unites). Aucun joker, aucune valeur libre dans une commande."""

    def __init__(self, exe, ip):
        self.exe, self.ip = exe, ip

    def _r(self, argv, delai=45, sudo=False):
        return self.exe.ssh(self.ip, (["sudo", "-n"] if sudo else []) + list(argv), delai)

    def hostname(self):
        r = self._r(["hostname"])
        if r.rc != 0:
            raise ErreurNoeud("hostname a echoue (rc=%s)" % r.rc)
        return parse_hostname(r.out)

    def inspecter(self, chemin):
        """Type, proprietaire, permissions, peripherique, systeme de fichiers et point de montage de `chemin` (jeu ferme : /,
        /var/lib, le repertoire de test) ; None s'il n'existe pas."""
        if chemin not in CHEMINS_INSPECTABLES:
            raise RefusSecurite("chemin non inspectable : %r" % (chemin,))
        r = self._r(["stat", "-c", "%F:%u:%g:%a:%d", "--", chemin])
        if r.rc != 0:
            if "No such file" in (r.out + r.err):
                return None
            raise ErreurNoeud("stat de %s impossible (rc=%s)" % (chemin, r.rc))
        info = parse_stat_chemin(r.out)
        m = self._r(["findmnt", "-n", "-o", "FSTYPE,TARGET", "-T", chemin])
        if m.rc != 0:
            raise ErreurNoeud("findmnt -T %s a echoue (rc=%s)" % (chemin, m.rc))
        info.update(parse_findmnt(m.out))
        return info

    def stats(self):
        r = self._r(["stat", "-f", "-c", "%S %b %a %f", "/"])
        if r.rc != 0:
            raise ErreurNoeud("stat -f a echoue (rc=%s)" % r.rc)
        return parse_stat_f(r.out)

    def type_fs(self):
        r = self._r(["findmnt", "-no", "FSTYPE", "/"])
        if r.rc != 0:
            raise ErreurNoeud("findmnt a echoue (rc=%s)" % r.rc)
        return r.out.strip()

    def fallocate_present(self):
        return self._r(["test", "-x", "/usr/bin/fallocate"]).rc == 0

    def config_kubelet(self):
        r = self._r(["cat", "/var/lib/kubelet/config.yaml"], sudo=True)
        if r.rc != 0:
            raise ErreurNoeud("lecture de la configuration du kubelet impossible (rc=%s)" % r.rc)
        return r.out

    def contenu_dossier(self):
        r = self._r(["ls", "-A", "--", DOSSIER], sudo=True)
        if r.rc == 0:
            return [l.strip() for l in r.out.splitlines() if l.strip()]
        if "No such file" in (r.out + r.err):
            return None
        raise ErreurNoeud("lecture de %s impossible (rc=%s)" % (DOSSIER, r.rc))

    def creer_dossier(self):
        r = self._r(["mkdir", "-m", "0700", "--", DOSSIER], sudo=True)
        if r.rc != 0:
            raise ErreurNoeud("creation de %s impossible (rc=%s)" % (DOSSIER, r.rc))

    def supprimer_dossier_vide(self):
        """rmdir : ne supprime QUE si le repertoire est vide."""
        r = self._r(["rmdir", "--", DOSSIER], sudo=True)
        if r.rc != 0:
            raise ErreurNoeud("rmdir de %s impossible (rc=%s)" % (DOSSIER, r.rc))

    def allouer(self, nom, octets):
        chemin = chemin_de(nom)
        if not isinstance(octets, int) or not 0 < octets <= BLOC_MAX:
            raise RefusSecurite("taille d'allocation hors limites : %r" % (octets,))
        if self._r(["test", "!", "-e", chemin], sudo=True).rc != 0:
            raise ErreurNoeud("le fichier %s existe deja : refus" % nom)
        r = self._r(["fallocate", "-l", str(octets), "--", chemin], sudo=True)
        if r.rc != 0:
            raise ErreurNoeud("fallocate a echoue (rc=%s)" % r.rc)

    def liberer(self, noms):
        chemins = [chemin_de(n) for n in noms]             # leve RefusSecurite si un nom sort du motif exact
        if not chemins:
            return
        r = self._r(["rm", "-f", "--"] + chemins, sudo=True)
        if r.rc != 0:
            raise ErreurNoeud("rm des fichiers de test a echoue (rc=%s)" % r.rc)

    def armer_expiration(self, jeton, noms, delai_s):
        unite = nom_unite(jeton)
        if not isinstance(delai_s, int) or not 60 <= delai_s <= EXPIRATION_MAX_S:
            raise RefusSecurite("delai d'expiration hors limites : %r" % (delai_s,))
        chemins = [chemin_de(n) for n in noms]
        if not chemins:
            raise RefusSecurite("aucun fichier a proteger par l'expiration")
        argv = ["systemd-run", "--quiet", "--collect", "--unit=%s" % unite, "--on-active=%ds" % delai_s,
                "--description=HELIOS test disque : liberation de securite", "/usr/bin/rm", "-f", "--"] + chemins
        r = self._r(argv, sudo=True)
        if r.rc != 0:
            raise ErreurNoeud("armement du minuteur d'expiration impossible (rc=%s)" % r.rc)

    def expiration_active(self, jeton):
        r = self._r(["systemctl", "is-active", nom_unite(jeton) + ".timer"])
        return r.rc == 0 and r.out.strip() == "active"

    def desarmer_expiration(self, jeton):
        self._r(["systemctl", "stop", nom_unite(jeton) + ".timer"], sudo=True)      # le minuteur peut deja avoir tire : rc ignore

    def armer_garde(self, jeton, noms, seuil_x100, delai_s):
        """Garde cote noeud : unite TRANSITOIRE (systemd-run) qui execute le programme awk a modele fixe `programme_garde`."""
        unite = nom_unite_garde(jeton)
        if not isinstance(delai_s, int) or isinstance(delai_s, bool) or not 60 <= delai_s <= EXPIRATION_MAX_S:
            raise RefusSecurite("duree de garde hors limites : %r" % (delai_s,))
        chemins = [chemin_de(n) for n in noms]
        programme = programme_garde(chemins, seuil_x100, delai_s // GARDE_PAS_S)
        argv = ["systemd-run", "--quiet", "--collect", "--unit=%s" % unite, "--property=RuntimeMaxSec=%d" % (delai_s + 60),
                "--description=HELIOS test disque : garde-fou avant le seuil du kubelet", "/usr/bin/awk", programme]
        r = self._r(argv, sudo=True)
        if r.rc != 0:
            raise ErreurNoeud("armement de la garde cote noeud impossible (rc=%s)" % r.rc)

    def garde_active(self, jeton):
        r = self._r(["systemctl", "is-active", nom_unite_garde(jeton) + ".service"])
        return r.rc == 0 and r.out.strip() == "active"

    def desarmer_garde(self, jeton):
        self._r(["systemctl", "stop", nom_unite_garde(jeton) + ".service"], sudo=True)      # peut deja s'etre arretee : rc ignore


# ------------------------------------------------------------------------------------------------ monde simule (aucune E/S)
class MondeSimule(object):
    """Noeud + HELIOS simules en memoire (horloge virtuelle). Sert a la SIMULATION par defaut et aux tests. Les pannes injectables
    (dict `pannes`) sont listees dans NoeudSimule et SondeSimulee."""

    def __init__(self, taille, dispo, helios="resolve", croissance_o_s=0.0, fstype="ext4", dossier=None, pannes=None,
                 delai_detection_s=105, delai_action_s=10, delai_verification_s=30, recuperation_pct=RECUPERATION_PCT,
                 hostname="k8s-worker1", dev_racine=64512):
        self.taille, self.base, self.t = int(taille), int(dispo), 0.0
        self.fichiers = {}
        self.ratio = {}                        # nom -> part de la taille VISIBLE dans la mesure (1.0 = fichier sur le volume mesure)
        self.dossier = dossier                 # None = absent ; dict nom -> octets
        if dossier:
            self.fichiers.update(dossier)
        self.fstype, self.helios = fstype, helios
        self.hostname, self.dev_racine = hostname, dev_racine
        self.croissance, self.organique = float(croissance_o_s), 0.0
        self.minuteurs = {}                    # jeton -> [echeance, noms, actif]
        self.gardes = {}                       # jeton -> [seuil (%), noms, actif, fin]
        self.pannes = dict(pannes or {})
        self.d_detection, self.d_action, self.d_verif = delai_detection_s, delai_action_s, delai_verification_s
        self.recuperation = recuperation_pct
        self.incident = None
        self.action = None
        self.t_depassement = None
        self.journal = []
        self.appels = []
        self.usage_max = 0.0                   # plus forte occupation MESUREE (par le noeud ou par la simulation)
        self.usage_vrai_max = 0.0              # plus forte occupation REELLE (chaque seconde simulee et chaque allocation)
        self.t_urgence = None                  # instant ou l'occupation reelle a atteint l'urgence (83 %) pour la 1re fois
        self.instants_stats = []               # instants (horloge simulee) de chaque mesure faite par l'outil : cadence de surveillance

    def dispo(self):
        visibles = sum(int(o * self.ratio.get(n, 1.0)) for n, o in self.fichiers.items())
        return max(0, self.base - visibles - int(self.organique))

    def fs(self):
        f = Fs(self.taille, self.dispo())
        self.usage_max = max(self.usage_max, f.usage)
        return f

    def retirer(self, nom):
        self.fichiers.pop(nom, None)
        self.ratio.pop(nom, None)

    def noter(self):
        u = Fs(self.taille, self.dispo()).usage
        self.usage_vrai_max = max(self.usage_vrai_max, u)
        if self.t_urgence is None and u >= URGENCE_PCT:
            self.t_urgence = self.t

    def avancer(self, secondes):
        fin = self.t + secondes
        while self.t < fin - 1e-9:
            pas = min(1.0, fin - self.t)
            self.t += pas
            self.organique += self.croissance * pas
            self.noter()
            for jeton, m in list(self.minuteurs.items()):
                if m[2] and self.t >= m[0]:
                    for nom in m[1]:
                        self.retirer(nom)
                    m[2] = False
                    self.journal.append((self.t, "expiration"))
            for jeton, g in list(self.gardes.items()):
                if not g[2]:
                    continue
                if self.t >= g[3]:
                    g[2] = False                                    # fin de vie de l'unite (RuntimeMaxSec)
                elif int(round(self.t)) % GARDE_PAS_S == 0 and Fs(self.taille, self.dispo()).usage >= g[0]:
                    for nom in g[1]:
                        self.retirer(nom)
                    g[2] = False
                    self.journal.append((self.t, "garde"))
            self._helios()

    def _helios(self):
        if self.helios == "absent":
            return
        u = self.fs().usage
        if u > DETECTION_PCT:
            if self.t_depassement is None:
                self.t_depassement = self.t
            if self.incident is None and self.t - self.t_depassement >= self.d_detection:
                self.incident = {"id": 90001, "statut": "OPEN", "cree": self.t}
                self.journal.append((self.t, "incident"))
        else:
            self.t_depassement = None
        if self.helios != "resolve" or self.incident is None:
            return
        i = self.incident
        if i["statut"] == "OPEN" and self.t - i["cree"] >= self.d_action:
            i["statut"] = "IN_PROGRESS"
            self.action = "HOST_RECLAIM_DISK:COMPLETED"
            for nom in [n for n in self.fichiers if RE_FICHIER.match(n)]:
                self.retirer(nom)
            self.journal.append((self.t, "remediation"))
        elif i["statut"] == "IN_PROGRESS" and self.t - i["cree"] >= self.d_action + self.d_verif:
            if self.fs().usage <= self.recuperation:
                i["statut"] = "RESOLVED"
                self.journal.append((self.t, "resolu"))


class NoeudSimule(Noeud):
    """Noeud simule. Pannes (cles de MondeSimule.pannes) : stats_apres (n), sans_fallocate, mkdir, allouer, liberer (nombre
    d'echecs), liberer_sans_effet, armer, expiration_inactive, armer_garde, garde_inactive, saut_apres (k, octets),
    effet_ratio (part de chaque allocation visible dans la mesure), liberation_externe (k, octets), latence_ssh_s (duree de
    CHAQUE appel), minuteur_avance_s, dossier_lien, dossier_proprio, dossier_mode, dossier_autre_fs, dossier_illisible,
    parent_autre_fs, racine_illisible, hostname_illisible."""

    def __init__(self, monde):
        self.m = monde
        self.n_stats = 0
        self._lib_attente = 0

    def _lat(self):
        """Latence ssh simulee : chaque appel fait avancer l'horloge du monde (minuteur, garde, HELIOS suivent)."""
        s = self.m.pannes.get("latence_ssh_s", 0)
        if s:
            self.m.avancer(s)

    def stats(self):
        self._lat()
        self.n_stats += 1
        self.m.instants_stats.append(self.m.t)
        a = self.m.pannes.get("stats_apres")
        if a is not None and self.n_stats > a:
            raise ErreurNoeud("panne simulee : stats illisibles")
        if self._lib_attente:
            self._lib_attente -= 1
            if self._lib_attente == 0:                       # un tiers libere de l'espace ENTRE deux blocs
                self.m.organique -= self.m.pannes["liberation_externe"][1]
        self.m.appels.append("stats")
        return self.m.fs()

    def hostname(self):
        self._lat()
        self.m.appels.append("hostname")
        if self.m.pannes.get("hostname_illisible"):
            raise ErreurNoeud("panne simulee : hostname")
        return self.m.hostname

    def inspecter(self, chemin):
        self._lat()
        self.m.appels.append("inspect")
        if chemin not in CHEMINS_INSPECTABLES:
            raise RefusSecurite("chemin non inspectable : %r" % (chemin,))
        p = self.m.pannes
        if chemin == "/" and p.get("racine_illisible"):
            raise ErreurNoeud("panne simulee : stat /")
        info = {"type": "directory", "uid": 0, "gid": 0, "mode": "755", "dev": self.m.dev_racine, "fstype": self.m.fstype, "montage": "/"}
        if chemin == PARENT_DOSSIER and p.get("parent_autre_fs"):
            info.update({"dev": self.m.dev_racine + 1, "montage": PARENT_DOSSIER})
        if chemin == DOSSIER:
            if p.get("dossier_illisible"):
                raise ErreurNoeud("panne simulee : stat du repertoire de test")
            if self.m.dossier is None:
                return None
            info["mode"] = "700"
            if p.get("dossier_lien"):
                info["type"] = "symbolic link"
            if p.get("dossier_proprio"):
                info["uid"] = 1000
            if p.get("dossier_mode"):
                info["mode"] = "777"
            if p.get("dossier_autre_fs"):
                info.update({"dev": self.m.dev_racine + 1, "montage": DOSSIER})
        return info

    def type_fs(self):
        return self.m.fstype

    def fallocate_present(self):
        return not self.m.pannes.get("sans_fallocate")

    def config_kubelet(self):
        return "kind: KubeletConfiguration\nevictionHard:\n    imagefs.available: \"15%\"\n    nodefs.available: \"10%\"\n"

    def contenu_dossier(self):
        self._lat()
        self.m.appels.append("ls")
        if self.m.dossier is None:
            return None
        return sorted(self.m.fichiers)

    def creer_dossier(self):
        self._lat()
        self.m.appels.append("mkdir")
        if self.m.pannes.get("mkdir"):
            raise ErreurNoeud("panne simulee : mkdir")
        self.m.dossier = {}

    def supprimer_dossier_vide(self):
        self._lat()
        self.m.appels.append("rmdir")
        if self.m.fichiers:
            raise ErreurNoeud("rmdir : repertoire non vide")
        self.m.dossier = None

    def allouer(self, nom, octets):
        self._lat()
        chemin_de(nom)
        if not isinstance(octets, int) or not 0 < octets <= BLOC_MAX:
            raise RefusSecurite("taille d'allocation hors limites : %r" % (octets,))
        if self.m.pannes.get("allouer"):
            raise ErreurNoeud("panne simulee : fallocate")
        if nom in self.m.fichiers:
            raise ErreurNoeud("fichier deja present")
        if octets > self.m.dispo():
            raise ErreurNoeud("fallocate : plus d'espace")
        self.m.appels.append("fallocate")
        self.m.fichiers[nom] = octets
        self.m.ratio[nom] = self.m.pannes.get("effet_ratio", 1.0)     # < 1 : les fichiers ne sont pas (tous) sur le volume mesure
        saut = self.m.pannes.get("saut_apres")                 # ecritures EXTERNES simulees (n-ieme bloc, octets)
        if saut and len(self.m.fichiers) == saut[0]:
            self.m.organique += saut[1]
        lib = self.m.pannes.get("liberation_externe")          # espace libere par un tiers (n-ieme bloc, octets)
        if lib and len(self.m.fichiers) == lib[0]:
            self._lib_attente = 2                              # applique a la 2e mesure suivante (juste avant le bloc suivant)
        self.m.noter()

    def liberer(self, noms):
        self._lat()
        for n in noms:
            chemin_de(n)
        self.m.appels.append("rm")
        pannes = self.m.pannes.get("liberer", 0)
        if pannes:
            self.m.pannes["liberer"] = pannes - 1
            raise ErreurNoeud("panne simulee : rm")
        if self.m.pannes.get("liberer_sans_effet"):
            return                                             # rm rend 0 mais les fichiers sont toujours la : seule la verification le voit
        for n in noms:
            self.m.retirer(n)
        self.m.journal.append((self.m.t, "rm"))

    def armer_expiration(self, jeton, noms, delai_s):
        self._lat()
        nom_unite(jeton)
        for n in noms:
            chemin_de(n)
        if self.m.pannes.get("armer"):
            raise ErreurNoeud("panne simulee : systemd-run")
        self.m.appels.append("systemd-run")
        self.m.minuteurs[jeton] = [self.m.t + delai_s - self.m.pannes.get("minuteur_avance_s", 0), list(noms), True]

    def expiration_active(self, jeton):
        self._lat()
        if self.m.pannes.get("expiration_inactive"):
            return False
        m = self.m.minuteurs.get(jeton)
        return bool(m and m[2])

    def desarmer_expiration(self, jeton):
        self._lat()
        self.m.appels.append("systemctl-stop")
        m = self.m.minuteurs.get(jeton)
        if m:
            m[2] = False

    def armer_garde(self, jeton, noms, seuil_x100, delai_s):
        self._lat()
        nom_unite_garde(jeton)
        for n in noms:
            chemin_de(n)
        if self.m.pannes.get("armer_garde"):
            raise ErreurNoeud("panne simulee : systemd-run (garde)")
        self.m.appels.append("systemd-run-garde")
        self.m.gardes[jeton] = [seuil_x100 / 100.0, list(noms), True, self.m.t + delai_s + 60]

    def garde_active(self, jeton):
        self._lat()
        if self.m.pannes.get("garde_inactive"):
            return False
        g = self.m.gardes.get(jeton)
        return bool(g and g[2])

    def desarmer_garde(self, jeton):
        self._lat()
        self.m.appels.append("systemctl-stop-garde")
        g = self.m.gardes.get(jeton)
        if g:
            g[2] = False


class HorlogeSimulee(object):
    def __init__(self, monde):
        self.m = monde

    def now(self):
        return self.m.t

    def dormir(self, s):
        self.m.avancer(s)


class HorlogeReelle(object):
    """Horloge MONOTONE : seules des durees sont calculees, un saut de l'horloge murale ne fausse ni le budget ni la fenetre."""

    def now(self):
        return time.monotonic()

    def dormir(self, s):
        time.sleep(s)


class SondeSimulee(object):
    """Sonde simulee de l'incident disque. Pannes : sonde_illisible (True = toujours ; n = a partir de la n-ieme lecture),
    latence_sonde_s (duree de chaque lecture)."""

    def __init__(self, monde):
        self.m = monde
        self.n = 0

    def lire(self):
        self.n += 1
        lat = self.m.pannes.get("latence_sonde_s", 0)
        if lat:
            self.m.avancer(lat)
        pa = self.m.pannes.get("sonde_illisible")
        if pa is True or (isinstance(pa, int) and not isinstance(pa, bool) and self.n >= pa):
            raise SondeIllisible("panne simulee : sonde de l'incident illisible")
        i = self.m.incident
        return {"id": i["id"], "statut": i["statut"], "action": self.m.action} if i else None


class SondeReelle(object):
    """Lit l'incident disque cree apres le lancement (SELECT seulement)."""

    def __init__(self, exe, nom, primaire, id0):
        self.exe, self.nom, self.primaire, self.id0 = exe, nom, primaire, int(id0)

    def lire(self):
        """Incident disque cree depuis le lancement, ou None s'il n'y en a PAS. Une lecture impossible (psql en echec apres
        SONDE_ESSAIS essais, reponse inattendue) leve SondeIllisible : elle n'est JAMAIS confondue avec "aucun incident"."""
        types = ",".join("'%s'" % t for t in TYPES_INCIDENT_DISQUE)
        sql = ("select i.id, i.status, coalesce((select a.type || ':' || a.status from incident_action a where a.incident_id = i.id "
               "order by a.id desc limit 1), '-') from incident i where i.id > %d and i.app = '%s' and i.incident_type in (%s) "
               "order by i.id desc limit 1;" % (self.id0, self.nom, types))
        lignes = None
        for _ in range(SONDE_ESSAIS):
            lignes = sql_local(self.exe, self.primaire, sql)
            if lignes is not None:
                break
        if lignes is None:
            raise SondeIllisible("lecture de l'incident impossible (psql en echec apres %d essais)" % SONDE_ESSAIS)
        if not lignes:
            return None
        if len(lignes[0]) < 3 or not lignes[0][0].isdigit():
            raise SondeIllisible("reponse inattendue de la base : %r" % ("|".join(lignes[0]))[:80])
        return {"id": int(lignes[0][0]), "statut": lignes[0][1], "action": None if lignes[0][2] == "-" else lignes[0][2]}


# ------------------------------------------------------------------------------------------------ execution du test
def liberer_tout(ops, jeton, tentes, cree_dossier, armee, sortie, tentatives=3, garde=False):
    """Retire par nom exact les fichiers de CE lancement ; ne desarme le minuteur d'expiration et la garde qu'une fois la
    liberation VERIFIEE (sinon ils restent armes et liberent eux-memes)."""
    if not tentes and not armee and not garde and not cree_dossier:
        return True                       # rien n'a ete cree : AUCUN acces au noeud
    ok = False
    for k in range(tentatives):
        try:
            if tentes:
                ops.liberer(list(tentes))
            reste = [n for n in (ops.contenu_dossier() or []) if n in tentes]
            if not reste:
                ok = True
                break
        except (ErreurNoeud, RefusSecurite) as e:
            sortie("   liberation : tentative %d/%d echouee (%s)" % (k + 1, tentatives, e))
    if ok:
        if armee:
            try:
                ops.desarmer_expiration(jeton)
            except (ErreurNoeud, RefusSecurite):
                pass
        if garde:
            try:
                ops.desarmer_garde(jeton)
            except (ErreurNoeud, RefusSecurite):
                pass
        if cree_dossier:
            try:
                ops.supprimer_dossier_vide()
            except (ErreurNoeud, RefusSecurite):
                pass
    else:
        sortie("!!! LIBERATION IMPOSSIBLE depuis l'outil : le minuteur d'expiration (unite %s.timer) et la garde (unite %s.service) "
               "RESTENT ARMES et liberent eux-memes les fichiers ; sinon les retirer a la main (noms exacts, repertoire %s)" % (
                   nom_unite(jeton), nom_unite_garde(jeton), DOSSIER))
    return ok


def attribuer_liberation(ops, jeton, maintenant, echeance_min):
    """Qui a retire les fichiers ? 'garde' (la garde cote noeud a tire : le seuil a ete franchi), 'expiration' (le minuteur, ou son
    echeance la plus proche est atteinte), 'helios' (aucun de NOS DEUX filets n'a tire et l'echeance n'est pas atteinte),
    'indeterminee' (etat des unites illisible, ou echeance inconnue). JAMAIS 'helios' par defaut."""
    try:
        if not ops.garde_active(jeton):
            return "garde"
        if not ops.expiration_active(jeton):
            return "expiration"
    except (ErreurNoeud, RefusSecurite):
        return "indeterminee"
    if echeance_min is None:
        return "indeterminee"
    if maintenant >= echeance_min:
        return "expiration"
    return "helios"


def suivre(ops, sonde, horloge, p, noms, sortie, jeton, echeance_min=None):
    """Suit la detection, la remediation et la liberation jusqu'a la resolution ou la fin de la fenetre d'observation, BORNEE par
    l'echeance la plus proche du minuteur (moins MARGE_FIN_S). L'occupation est mesuree toutes les PAS_MESURE_S s (urgence) et
    REMESUREE apres chaque appel lent ; l'incident n'est lu que toutes les PAS_SONDE_S s. Une sonde illisible leve SondeIllisible."""
    t0 = horloge.now()
    fin = t0 + p.observe
    if echeance_min is not None:
        fin = min(fin, echeance_min - MARGE_FIN_S)
    if fin - t0 < OBSERVE_MIN_S:
        raise RefusSecurite("fenetre d'observation insuffisante (%d s < %d s) avant l'echeance du minuteur" % (fin - t0, OBSERVE_MIN_S))
    ev = {"t_incident": None, "incident": None, "action": None, "t_liberation": None, "statut": None, "liberee_par": None}
    cycle, pas_sonde = 0, max(1, PAS_SONDE_S // PAS_MESURE_S)
    while horloge.now() < fin:
        horloge.dormir(PAS_MESURE_S)
        fs = ops.stats()
        if fs.usage >= p.urgence:
            raise ArretUrgence("occupation %.2f %% >= urgence %.1f %% pendant le suivi" % (fs.usage, p.urgence))
        cycle += 1
        if cycle % pas_sonde:
            continue
        etat = sonde.lire() if sonde is not None else None
        fs = ops.stats()                                     # nouvelle mesure APRES l'appel lent (psql) : on ne reste pas aveugle
        if fs.usage >= p.urgence:
            raise ArretUrgence("occupation %.2f %% >= urgence %.1f %% pendant le suivi" % (fs.usage, p.urgence))
        t = horloge.now() - t0
        if etat and ev["incident"] is None:
            ev["incident"], ev["t_incident"] = etat["id"], t
            sortie("T+%4ds  incident #%s detecte (%s)" % (t, etat["id"], etat["statut"]))
        if etat and etat.get("action") and ev["action"] is None:
            ev["action"] = etat["action"]
            sortie("T+%4ds  action %s" % (t, etat["action"]))
        presents = [n for n in (ops.contenu_dossier() or []) if n in noms]
        if not presents and ev["t_liberation"] is None:
            ev["t_liberation"] = t
            ev["liberee_par"] = attribuer_liberation(ops, jeton, horloge.now(), echeance_min)
            sortie("T+%4ds  fichiers de test liberes (occupation %.2f %%) par : %s" % (t, fs.usage, ev["liberee_par"]))
        if etat and etat["statut"] in ("RESOLVED", "RESOLUTION_FAILED"):
            ev["statut"] = etat["statut"]
            sortie("T+%4ds  incident #%s %s" % (t, etat["id"], etat["statut"]))
            if ev["t_liberation"] is not None:
                break
    return ev


def verifier_identite(ops, attendu):
    """Le nom d'hote repondu en ssh doit etre celui du noeud Kubernetes attendu : sinon c'est la MAUVAISE machine."""
    hn = ops.hostname()
    if nom_court(hn) != nom_court(attendu):
        raise RefusSecurite("ssh repond le nom d'hote %r, le noeud Kubernetes attendu est %r : mauvaise machine, rien n'est ecrit" % (
            hn, attendu))


def executer_test(ops, sonde, horloge, p, jeton, sortie=print, vmdk_avant=None, vmdk_apres=None, identite=None):
    """Remplit, suit, libere. Retourne un dict de resultats ; libere TOUJOURS ce qui a ete cree (finally). `identite` = nom du noeud
    Kubernetes attendu : le nom d'hote repondu en ssh doit lui correspondre (sinon refus avant toute ecriture)."""
    noms = noms_prevus(jeton)
    res = {"jeton": jeton, "verdict": "ERREUR", "raison": "", "alloues": [], "total_alloue": 0, "usage_initial": None,
           "usage_atteint": None, "usage_final": None, "detection_s": None, "action": None, "liberation_s": None,
           "liberee_par": None, "statut": None, "recupere": None, "liberation_ok": None, "croissance_vmdk": None,
           "unite": nom_unite(jeton), "unite_garde": nom_unite_garde(jeton)}
    tentes, armee, garde_armee, cree_dossier = [], False, False, False
    t_debut = horloge.now()
    try:
        if identite is not None:
            verifier_identite(ops, identite)
        contenu = ops.contenu_dossier()
        if contenu:
            raise RefusSecurite("%s n'est pas vide : residu d'un test precedent" % DOSSIER)
        fs = ops.stats()
        res["usage_initial"] = fs.usage
        planifier(fs, p)                                   # leve RefusSecurite si la base est hors limites
        sortie("T+%4ds  base : occupation %.2f %%, cible %.1f %%, plafond %.1f %%, urgence %.1f %%" % (
            0, fs.usage, p.cible, p.plafond, p.urgence))
        racine = ops.inspecter("/")
        existant = ops.inspecter(DOSSIER)
        if (contenu is None) != (existant is None):
            raise RefusSecurite("etat du repertoire %s incoherent entre ls et stat : refus" % DOSSIER)
        if existant is None:
            anomalies = anomalies_parent(ops.inspecter(PARENT_DOSSIER), racine)
            if anomalies:
                raise RefusSecurite("repertoire parent %s non conforme : %s" % (PARENT_DOSSIER, " ; ".join(anomalies)))
            ops.creer_dossier()
            cree_dossier = True
            existant = ops.inspecter(DOSSIER)                # on reverifie le repertoire REELLEMENT cree
        anomalies = anomalies_dossier(existant, racine)
        if anomalies:
            raise RefusSecurite("repertoire de test %s non conforme : %s" % (DOSSIER, " ; ".join(anomalies)))
        t_arm = horloge.now()                                # AVANT l'appel : borne conservatrice de l'instant REEL d'armement
        ops.armer_expiration(jeton, noms, p.expiration)      # les DEUX filets sont armes AVANT la premiere allocation
        armee = True                                         # a desarmer meme si la verification ci-dessous echoue
        if not ops.expiration_active(jeton):
            raise RefusSecurite("le minuteur d'expiration n'est pas actif : aucune allocation")
        ops.armer_garde(jeton, noms, seuil_garde_x100(p), p.expiration)
        garde_armee = True
        if not ops.garde_active(jeton):
            raise RefusSecurite("la garde cote noeud n'est pas active : aucune allocation")
        echeance_min = t_arm + p.expiration                  # le minuteur ne peut PAS tirer avant cet instant
        limite_alloc = t_arm + budget_allocation(p)          # au-dela, plus d'allocation : l'observation ne tiendrait plus
        sortie("T+%4ds  minuteur (%d s, unite %s.timer) et garde cote noeud (%.1f %%, unite %s.service) armes ; budget d'allocation %d s" % (
            horloge.now() - t_debut, p.expiration, nom_unite(jeton), seuil_garde_x100(p) / 100.0, nom_unite_garde(jeton),
            budget_allocation(p)))
        i, derniere = 0, 0.0
        while True:
            t_iter = horloge.now()
            fs = ops.stats()                                 # mesure FRAICHE avant chaque allocation
            if fs.usage >= p.urgence:
                raise ArretUrgence("occupation %.2f %% >= urgence %.1f %%" % (fs.usage, p.urgence))
            if fs.usage >= p.cible:
                break
            i += 1
            if i > MAX_FICHIERS:
                raise RefusSecurite("trop de blocs alloues sans atteindre la cible")
            bloc = min(arrondi_mio(octets_pour(fs, p.cible)), p.bloc)
            ok, raison = garde_allocation(fs, bloc, p, res["total_alloue"])
            if not ok:
                raise RefusSecurite("allocation refusee : %s" % raison)
            if horloge.now() + max(derniere, DUREE_BLOC_MIN_S) > limite_alloc:
                raise RefusSecurite("delai insuffisant pour poursuivre les allocations (%d s restantes sur un budget de %d s depuis "
                                    "l'armement, bloc estime a %d s) : l'observation et la liberation doivent tenir avant l'echeance "
                                    "du minuteur" % (max(0, limite_alloc - horloge.now()), budget_allocation(p), max(derniere, DUREE_BLOC_MIN_S)))
            nom = nom_fichier(jeton, i)
            tentes.append(nom)                               # note AVANT l'appel : une allocation partielle sera retiree
            ops.allouer(nom, bloc)
            res["alloues"].append(nom)
            res["total_alloue"] += bloc
            fs_apres = ops.stats()                           # EFFET de l'allocation sur le volume mesure
            if fs_apres.usage >= p.urgence:
                raise ArretUrgence("occupation %.2f %% >= urgence %.1f %% apres le bloc %d" % (fs_apres.usage, p.urgence, i))
            effet = fs.dispo - fs_apres.dispo
            if effet < EFFET_MIN * bloc:
                raise RefusSecurite("allocation sans effet mesure : l'espace disponible n'a baisse que de %d Mo pour %d Mo alloues "
                                    "(< %d %%) : les fichiers ne sont pas sur le volume mesure" % (
                                        max(effet, 0) // MIO, bloc // MIO, round(EFFET_MIN * 100)))
            derniere = horloge.now() - t_iter
            sortie("T+%4ds  bloc %d : +%d Mo alloues (fallocate), effet mesure +%d Mo, occupation %.2f %%" % (
                horloge.now() - t_debut, i, bloc // MIO, effet // MIO, fs_apres.usage))
        fs = ops.stats()
        res["usage_atteint"] = fs.usage
        if fs.usage > p.plafond:
            raise ArretUrgence("occupation %.2f %% > plafond %.1f %% apres allocation" % (fs.usage, p.plafond))
        sortie("T+%4ds  cible atteinte : occupation %.2f %% (detection attendue dans ~2 min)" % (horloge.now() - t_debut, fs.usage))
        ev = suivre(ops, sonde, horloge, p, set(noms), sortie, jeton, echeance_min)
        res["detection_s"], res["action"], res["liberation_s"], res["statut"] = ev["t_incident"], ev["action"], ev["t_liberation"], ev["statut"]
        res["liberee_par"] = ev["liberee_par"]
        if res["liberee_par"] == "garde":
            res["verdict"], res["raison"] = "URGENCE", ("la garde cote noeud a libere les fichiers (seuil %.1f %% atteint) sans que l'outil "
                                                        "ne reagisse a temps" % (seuil_garde_x100(p) / 100.0))
            sortie("!!! GARDE COTE NOEUD DECLENCHEE : %s" % res["raison"])
        elif res["liberee_par"] == "expiration":
            res["verdict"], res["raison"] = "ERREUR", "le minuteur d'expiration a libere les fichiers pendant l'observation : test invalide"
        elif ev["incident"] is None:
            res["verdict"], res["raison"] = "AUCUNE_DETECTION", "aucun incident disque cree dans la fenetre d'observation"
        elif ev["statut"] == "RESOLVED" and res["liberee_par"] == "helios":
            res["verdict"], res["raison"] = "OK", "detection, liberation par HELIOS et resolution"
        else:
            res["verdict"], res["raison"] = "DETECTION_SEULE", "incident detecte ; remediation par HELIOS non prouvee (liberation par l'outil)"
    except ArretUrgence as e:
        res["verdict"], res["raison"] = "URGENCE", str(e)
        sortie("!!! ARRET D'URGENCE : %s -> liberation immediate" % e)
    except RefusSecurite as e:
        res["verdict"], res["raison"] = "REFUS", str(e)
        sortie("REFUS DE SECURITE : %s" % e)
    except ErreurNoeud as e:
        res["verdict"], res["raison"] = "ERREUR", str(e)
        sortie("ERREUR : %s -> liberation et arret" % e)
    finally:
        res["liberation_ok"] = liberer_tout(ops, jeton, tentes, cree_dossier, armee, sortie, garde=garde_armee)
        try:
            fs = ops.stats()
            res["usage_final"] = fs.usage
            res["recupere"] = fs.usage <= p.recuperation
        except (ErreurNoeud, RefusSecurite):
            res["usage_final"], res["recupere"] = None, None
        if vmdk_avant is not None and vmdk_apres is not None:
            apres = vmdk_apres()
            if apres is not None:
                res["croissance_vmdk"] = apres["alloue"] - vmdk_avant["alloue"]
    if res["verdict"] == "OK" and not (res["recupere"] and res["liberation_ok"]):
        res["verdict"], res["raison"] = "ERREUR", "recuperation (<= %.1f %%) ou liberation non confirmee" % p.recuperation
    return res


def afficher_bilan(res, p, sortie=print):
    sortie("")
    sortie("BILAN : %s - %s" % (res["verdict"], res["raison"]))
    f = lambda v: "?" if v is None else "%.2f %%" % v
    sortie("  occupation : initiale %s | atteinte %s | finale %s (recuperation <= %.1f %% : %s)" % (
        f(res["usage_initial"]), f(res["usage_atteint"]), f(res["usage_final"]), p.recuperation,
        {True: "OUI", False: "NON", None: "inconnue"}[res["recupere"]]))
    sortie("  blocs alloues : %d (%d Mo cumules, plafond %d Mo) | detection : %s | action : %s | liberation : %s (%s) | statut : %s" % (
        len(res["alloues"]), res["total_alloue"] // MIO, MAX_TOTAL // MIO,
        "T+%ds" % res["detection_s"] if res["detection_s"] is not None else "aucune",
        res["action"] or "aucune", "T+%ds" % res["liberation_s"] if res["liberation_s"] is not None else "par l'outil",
        res["liberee_par"] or "outil", res["statut"] or "-"))
    sortie("  liberation verifiee : %s" % {True: "OUI", False: "NON (voir l'alerte ci-dessus)", None: "inconnue"}[res["liberation_ok"]])
    if res["croissance_vmdk"] is not None:
        sortie("  croissance des extents VMDK sur C: : %+d Mo%s" % (res["croissance_vmdk"] // MIO,
               "  !!! > %d Mo : a examiner" % (VMDK_CROISSANCE_ALERTE // MIO) if res["croissance_vmdk"] > VMDK_CROISSANCE_ALERTE else ""))


CODES_SORTIE = {"OK": 0, "DETECTION_SEULE": 3}


# ------------------------------------------------------------------------------------------------ collecte reelle (LECTURE SEULE)
class ExecReel(object):
    """Acces reels : ssh, kubectl local, HTTP. UNIQUE endroit du fichier qui appelle subprocess / urllib."""

    def ssh(self, ip, argv, delai=45):
        ligne = " ".join(shlex.quote(a) for a in argv)
        try:
            p = subprocess.run(SSH + ["kube@%s" % ip, ligne], capture_output=True, text=True, timeout=delai)
            return Res(p.returncode, p.stdout, p.stderr)
        except Exception as e:
            return Res(255, "", type(e).__name__)

    def local(self, argv, delai=40, entree=None):
        try:
            p = subprocess.run(list(argv), input=entree, capture_output=True, text=True, timeout=delai)
            return Res(p.returncode, p.stdout, p.stderr)
        except Exception as e:
            return Res(255, "", type(e).__name__)

    def http_json(self, url, delai=25):
        try:
            with urllib.request.urlopen(url, timeout=delai) as r:
                return json.load(r)
        except Exception:
            return None


class Dependances(object):
    """Acces a l'exterieur du processus. Les tests injectent un faux."""

    def executeur(self):
        return ExecReel()

    def horloge(self):
        return HorlogeReelle()

    def maintenant(self):
        return datetime.now(timezone.utc)

    def nouveau_jeton(self):
        return secrets.token_hex(16)

    def memoire_hote_mo(self):
        return memoire_hote_mo()

    def espace_libre_go(self, chemin):
        return espace_libre_go(chemin)


def sql_local(exe, primaire, requete):
    r = exe.local(["kubectl", "exec", "-i", "-n", "pfe", primaire, "-c", "postgres", "--",
                   "psql", "-U", "postgres", "-d", "pfe_db", "-At", "-F", "|"], entree=requete, delai=60)
    if r.rc != 0:
        return None
    return [l.split("|") for l in r.out.splitlines() if l.strip()]


def lire_noeud(exe, ip):
    n = NoeudSsh(exe, ip)
    d = {"joignable": False, "fs": None, "fstype": None, "fallocate": None, "dossier": "inconnu", "kubelet": None,
         "hostname": None, "racine": None, "dossier_info": "inconnu", "parent": None}
    try:
        d["fs"] = n.stats()
        d["joignable"] = True
    except (ErreurNoeud, RefusSecurite):
        return d
    for cle, fn in (("fstype", n.type_fs), ("fallocate", n.fallocate_present), ("dossier", n.contenu_dossier),
                    ("hostname", n.hostname), ("racine", lambda: n.inspecter("/")),
                    ("dossier_info", lambda: n.inspecter(DOSSIER)), ("parent", lambda: n.inspecter(PARENT_DOSSIER))):
        try:
            d[cle] = fn()
        except (ErreurNoeud, RefusSecurite):
            pass
    try:
        d["kubelet"] = parse_kubelet(n.config_kubelet())
    except (ErreurNoeud, RefusSecurite):
        pass
    return d


def _requete_prom(exe, requete):
    """Valeurs d'une requete Prometheus instantanee ; None si la reponse est ILLISIBLE (echec HTTP, JSON invalide, statut different
    de success, valeur non numerique). [] = reponse valide SANS echantillon."""
    obj = exe.http_json(PROM + urllib.parse.urlencode({"query": requete}))
    if (not isinstance(obj, dict) or obj.get("status") != "success" or not isinstance(obj.get("data"), dict)
            or not isinstance(obj["data"].get("result"), list)):
        return None
    vals = valeurs_prom(obj)
    return vals if len(vals) == len(obj["data"]["result"]) else None


def lire_prom(exe, ip, nom):
    inst = "%s:9100" % ip
    sel = 'instance="%s",mountpoint="/",fstype!~"tmpfs"' % inst
    nb = _requete_prom(exe, "count(node_filesystem_avail_bytes{%s})" % sel)
    u = _requete_prom(exe, "(1-(node_filesystem_avail_bytes{%s}/node_filesystem_size_bytes{%s}))*100" % (sel, sel))
    inc = _requete_prom(exe, 'count(helios_incident{cible="%s"}) or vector(0)' % nom)
    illisibles = [k for k, v in (("series", nb), ("usage", u), ("incident_actif", inc)) if v is None]
    return {"series": (int(nb[0]) if nb else 0) if nb is not None else None,
            "usage": u[0] if u is not None and len(u) == 1 else None,
            "incident_actif": int(inc[0]) if inc else None,
            "illisibles": illisibles}


def lire_noeud_k8s(exe, nom):
    """kubectl get node <nom> -o json -> {'disk_pressure', 'internal_ips', 'nom_k8s'} ; None si illisible."""
    r = exe.local(["kubectl", "get", "node", nom, "-o", "json"])
    if r.rc != 0:
        return None
    try:
        o = json.loads(r.out)
        conds = {c["type"]: c["status"] for c in o["status"]["conditions"]}
        ips = [a["address"] for a in o["status"]["addresses"] if a.get("type") == "InternalIP"]
        nom_k8s = o["metadata"]["name"]
    except (ValueError, KeyError, TypeError, AttributeError):
        return None
    return {"disk_pressure": {"True": True, "False": False}.get(conds.get("DiskPressure")), "internal_ips": ips, "nom_k8s": nom_k8s}


def lire_cluster(exe, nom):
    r = exe.local(["kubectl", "get", "nodes", "--no-headers"])
    if r.rc != 0:
        return None, {"ready": None, "disk_pressure": None, "internal_ips": None, "nom_k8s": None}
    prets, total, cible = parse_noeuds(r.out, nom)
    k = lire_noeud_k8s(exe, nom) or {"disk_pressure": None, "internal_ips": None, "nom_k8s": None}
    return (prets, total) if total else None, {"ready": cible, "disk_pressure": k["disk_pressure"],
                                               "internal_ips": k["internal_ips"], "nom_k8s": k["nom_k8s"]}


def lire_cnpg(exe, nom):
    r = exe.local(["kubectl", "-n", "pfe", "get", "cluster.postgresql.cnpg.io", "-o", "json"])
    if r.rc != 0:
        return None, None
    try:
        c = json.loads(r.out)["items"][0]
        primaire = c["status"]["currentPrimary"]
        sain = (c["status"].get("readyInstances") == c["spec"]["instances"]
                and str(c["status"].get("phase", "")).startswith("Cluster in healthy state"))
    except (ValueError, KeyError, IndexError, TypeError):
        return None, None
    r2 = exe.local(["kubectl", "-n", "pfe", "get", "pod", primaire, "-o", "jsonpath={.spec.nodeName}"])
    if r2.rc != 0 or not r2.out.strip():
        return None, primaire
    return {"primaire_sur_cible": r2.out.strip() == nom, "sain": bool(sain)}, primaire


def lire_incidents(exe, nom, primaire):
    if not primaire:
        return None, 0
    types = ",".join("'%s'" % t for t in TYPES_INCIDENT_DISQUE)
    actifs = ",".join("'%s'" % s for s in STATUTS_ACTIFS)
    maintenant = "(now() at time zone 'Africa/Tunis')"
    sql = ("select 'DISQUE', id, status from incident where incident_type in (%s) and app = '%s' and (status in (%s) or "
           "created_at > %s - interval '%d seconds');\n"
           "select 'NOTREADY', extract(epoch from (%s - created_at))::bigint from incident where incident_type = 'NODE_NOT_READY' "
           "and (status in (%s) or created_at > %s - interval '%d seconds');\n"
           "select 'MAXID', coalesce(max(id), 0) from incident;" % (types, nom, actifs, maintenant, DISQUE_RECENT_S,
                                                                      maintenant, actifs, maintenant, NOTREADY_ORANGE_S))
    lignes = sql_local(exe, primaire, sql)
    if lignes is None:
        return None, 0
    disque = [l[1] for l in lignes if l[0] == "DISQUE" and len(l) >= 3]
    ages = [int(l[1]) for l in lignes if l[0] == "NOTREADY" and len(l) >= 2 and l[1].lstrip("-").isdigit()]
    maxid = [int(l[1]) for l in lignes if l[0] == "MAXID" and len(l) >= 2 and l[1].isdigit()]
    return {"disque_actifs": disque, "notready_ages_s": ages}, (maxid[0] if maxid else 0)


def lire_drapeau(exe):
    r = exe.local(["kubectl", "-n", "pfe", "get", "configmap", "backend-config", "-o", "json"])
    if r.rc != 0:
        return None
    try:
        data = json.loads(r.out).get("data") or {}
    except ValueError:
        return None
    v = data.get("HOST_DISK_REMEDIATION_ENABLED")
    return "absent" if v is None else ("true" if str(v).strip().lower() == "true" else "false")


def lire_hote(deps, cle, vm_base):
    return {"ram_mo": deps.memoire_hote_mo(), "c_libre_go": deps.espace_libre_go(vm_base),
            "gels": gels_vmware(vm_base, deps.maintenant()), "vmdk": analyser_vmdk(os.path.join(vm_base, VM_DOSSIER[cle]))}


def collecter_reel(deps, cle, vm_base):
    """Releves REELS en LECTURE SEULE (ssh de lecture, Prometheus, kubectl get, SELECT, fichiers de l'hote). Retourne (faits, contexte)."""
    nom, ip = CIBLES[cle]
    exe = deps.executeur()
    cluster, nk = lire_cluster(exe, nom)
    cnpg, primaire = lire_cnpg(exe, nom)
    incidents, maxid = lire_incidents(exe, nom, primaire)
    faits = {"noeud": lire_noeud(exe, ip), "prom": lire_prom(exe, ip, nom), "cluster": cluster, "noeud_k8s": nk, "cnpg": cnpg,
             "incidents": incidents, "drapeau": lire_drapeau(exe), "hote": lire_hote(deps, cle, vm_base)}
    return faits, {"exe": exe, "primaire": primaire, "id0": maxid}


# ------------------------------------------------------------------------------------------------ simulation par defaut
SIM_DEFAUT = {"155": {"taille": 38121 * MIO, "dispo": 11259 * MIO, "primaire_cnpg": True, "vmdk_alloue": 35358900224},
              "158": {"taille": 38121 * MIO, "dispo": 12639 * MIO, "primaire_cnpg": False, "vmdk_alloue": 34909913088}}
VMDK_CAPACITE_SIM = 42949672960
INFO_RACINE_SIM = {"type": "directory", "uid": 0, "gid": 0, "mode": "755", "dev": 64512, "fstype": "ext4", "montage": "/"}


def faits_simules(cle, a):
    """Faits de SIMULATION (jamais une mesure ; surchargeables par --sim-*). .155 : valeurs relevees le 13 nov. 2026 (etape 1).
    .158 : occupation Prometheus du 13 nov. (66,86 %) et extents VMDK relevees, mais taille du systeme de fichiers SUPPOSEE
    identique a celle de .155 (meme gabarit, non mesuree). Le pre-vol reel mesure tout."""
    nom, ip = CIBLES[cle]
    d = SIM_DEFAUT[cle]
    taille = d["taille"] if a.sim_taille_mo is None else int(a.sim_taille_mo) * MIO
    if a.sim_usage is not None:
        dispo = int(round(taille * (1.0 - float(a.sim_usage) / 100.0)))
    else:
        dispo = d["dispo"] if a.sim_taille_mo is None else int(taille * d["dispo"] / d["taille"])
    fs = Fs(taille, dispo)
    cnpg = d["primaire_cnpg"] if a.sim_primaire_cnpg is None else (a.sim_primaire_cnpg == "oui")
    ram = 2500 if a.sim_ram_mo is None else int(a.sim_ram_mo)
    return fs, {
        "noeud": {"joignable": True, "fs": fs, "fstype": "ext4", "fallocate": True, "dossier": None,
                  "kubelet": parse_kubelet("kind: KubeletConfiguration\nevictionHard:\n    imagefs.available: \"15%\"\n    nodefs.available: \"10%\"\n"),
                  "hostname": nom, "racine": dict(INFO_RACINE_SIM), "dossier_info": None, "parent": dict(INFO_RACINE_SIM)},
        "prom": {"series": 1, "usage": fs.usage, "incident_actif": 0, "illisibles": []}, "cluster": (3, 3),
        "noeud_k8s": {"ready": True, "disk_pressure": False, "internal_ips": [ip], "nom_k8s": nom},
        "cnpg": {"primaire_sur_cible": cnpg, "sain": True},
        "incidents": {"disque_actifs": [], "notready_ages_s": []}, "drapeau": "absent" if a.sim_drapeau is None else a.sim_drapeau,
        "hote": {"ram_mo": ram, "c_libre_go": 36.1, "gels": [],
                 "vmdk": {"type": "twoGbMaxExtentSparse", "extents": 11, "capacite": VMDK_CAPACITE_SIM, "alloue": d["vmdk_alloue"],
                          "reste": VMDK_CAPACITE_SIM - d["vmdk_alloue"], "croissant": True, "prealloue": False, "parent": False,
                          "snapshots": 0}}}


# ------------------------------------------------------------------------------------------------ ligne de commande
def construire_parseur():
    # allow_abbrev=False : aucune option (surtout un acquittement de risque) ne peut etre activee par un prefixe
    ap = argparse.ArgumentParser(description="Test DISK HELIOS : remplissage controle (fallocate) d'un noeud. SIMULATION par defaut.",
                                 allow_abbrev=False)
    ap.add_argument("--target", default=None, help="cible EXPLICITE : 155 ou 158 (aucune valeur par defaut)")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--preflight", action="store_true", help="pre-vol REEL en lecture seule (ssh, Prometheus, kubectl)")
    g.add_argument("--go", action="store_true", help="lancement REEL (exige --confirm et un pre-vol sans ROUGE)")
    ap.add_argument("--confirm", default=None, help="texte exact affiche par la simulation (ex. DISK-155-81-82-83)")
    ap.add_argument("--accepte-risque-primaire-cnpg", action="store_true", dest="ack_cnpg",
                    help="accepte EXPLICITEMENT le risque si la cible heberge le primaire CNPG (jamais presume)")
    ap.add_argument("--accepte-orange", action="append", default=None, dest="accepte_orange", metavar="CODE[,CODE...]",
                    help="acquitte EXPLICITEMENT, code par code, les ORANGE du pre-vol (sans acquittement : --go refuse). Codes : "
                    + " ; ".join("%s = %s" % (c, CODES_ORANGE[c]) for c in CODES_ORANGE_ACQUITTABLES))
    ap.add_argument("--image-backend", default=None, dest="image_backend", metavar="TAG",
                    help="cible .158 (scenario DISK_E2E_158) : image du Backend qui DOIT tourner (ex. pfe-backend:0.2.165-disk-e2e) ; "
                    "obligatoire pour --preflight et --go, comparee a l'image reellement deployee (aucune valeur par defaut)")
    ap.add_argument("--target-pct", type=float, default=CIBLE_PCT, dest="cible_pct")
    ap.add_argument("--cap-pct", type=float, default=PLAFOND_PCT, dest="plafond_pct")
    ap.add_argument("--emergency-pct", type=float, default=URGENCE_PCT, dest="urgence_pct")
    ap.add_argument("--recovery-pct", type=float, default=RECUPERATION_PCT, dest="recuperation_pct")
    ap.add_argument("--chunk-mb", type=int, default=BLOC_DEFAUT // MIO, dest="bloc_mo")
    ap.add_argument("--observe-s", type=int, default=OBSERVE_DEFAUT_S, dest="observe_s")
    ap.add_argument("--expiry-s", type=int, default=None, dest="expiration_s")
    ap.add_argument("--vm-base", default=VM_BASE_DEFAUT, help="dossier des VM VMware sur l'hote")
    s = ap.add_argument_group("simulation (valeurs simulees, aucune mesure reelle)")
    s.add_argument("--sim-usage", type=float, default=None)
    s.add_argument("--sim-taille-mo", type=int, default=None)
    s.add_argument("--sim-helios", choices=("resolve", "detect", "absent"), default="resolve")
    s.add_argument("--sim-croissance-mo-s", type=float, default=0.0)
    s.add_argument("--sim-ram-mo", type=int, default=None)
    s.add_argument("--sim-primaire-cnpg", choices=("oui", "non"), default=None)
    s.add_argument("--sim-drapeau", choices=("true", "false", "absent"), default=None)
    return ap


def codes_a_acquitter(rapport):
    """Codes des ORANGE du rapport qui exigent --accepte-orange (le risque CNPG a son propre drapeau), sans doublon, dans l'ordre."""
    vus = []
    for x in rapport["raisons"]:
        if x[0] == "ORANGE" and x[3] != CODE_CNPG_ACCEPTE and x[3] not in vus:
            vus.append(x[3])
    return vus


def enregistrer_comme_outil():
    """Declare CE module sous le nom `helios_disk_test` (nom sous lequel la porte l'importe), pour qu'elle compose ce module et pas une
    seconde copie. Uniquement si `sys.modules[__name__]` est bien ce module (jamais un autre `__main__`)."""
    moi = sys.modules.get(__name__)
    if moi is not None and getattr(moi, "enregistrer_comme_outil", None) is enregistrer_comme_outil:
        sys.modules.setdefault("helios_disk_test", moi)


def module_porte_e2e():
    """Retourne LA PORTE du scenario DISK_E2E_158 (helios_disk_e2e.py, meme dossier), chargee au premier appel. Importation PARESSEUSE :
    la porte importe cet outil (elle le compose), l'inverse ne peut donc pas se faire au chargement. Le lancement reel de .158 ne s'en
    passe jamais. Un module deja charge est reutilise (jamais une seconde copie)."""
    module = sys.modules.get("helios_disk_e2e")
    if module is None:
        enregistrer_comme_outil()
        chemin = os.path.join(os.path.dirname(os.path.abspath(__file__)), "helios_disk_e2e.py")
        spec = importlib.util.spec_from_file_location("helios_disk_e2e", chemin)
        module = importlib.util.module_from_spec(spec)
        sys.modules["helios_disk_e2e"] = module
        try:
            spec.loader.exec_module(module)
        except BaseException:
            sys.modules.pop("helios_disk_e2e", None)               # jamais un module a moitie charge laisse en place
            raise
    return module


def principal(argv=None, deps=None, sortie=print):
    ap = construire_parseur()
    a = ap.parse_args(argv)
    try:
        cle = resoudre_cible(a.target)
        p = valider_parametres(a.cible_pct, a.plafond_pct, a.urgence_pct, a.recuperation_pct, a.bloc_mo * MIO, a.observe_s, a.expiration_s)
    except RefusSecurite as e:
        sortie("REFUS : %s" % e)
        return 2
    acquittes, erreur = analyser_acquittements(a.accepte_orange)
    if erreur:
        sortie("REFUS : %s" % erreur)
        return 2
    mode = "go" if a.go else ("preflight" if a.preflight else "simulation")
    attendu = jeton_confirmation(cle, p)
    if a.go and a.confirm != attendu:
        sortie("REFUS : --go exige --confirm %s (texte exact ; rien n'a ete lance)" % attendu)
        return 2
    nom = CIBLES[cle][0]
    e2e = cle == CLE_E2E
    if e2e and a.ack_cnpg:
        sortie("REFUS : --accepte-risque-primaire-cnpg n'est pas admis pour le scenario DISK_E2E_158 (.158 : le risque CNPG n'est jamais "
               "presume accepte) ; rien n'a ete lance")
        return 2
    if e2e and CODE_REMEDIATION_NON_ACTIVEE in acquittes:
        sortie("REFUS : --accepte-orange %s n'est pas admis pour le scenario DISK_E2E_158 (la remediation par le Bridge est obligatoire : "
               "aucun lancement en detection seule) ; rien n'a ete lance" % CODE_REMEDIATION_NON_ACTIVEE)
        return 2
    if mode == "simulation":
        sortie("SIMULATION : valeurs simulees, AUCUN acces reseau, ssh, VM ni fichier. Cible .%s (%s)." % (cle, nom))
        fs, faits = faits_simules(cle, a)
        rapport = evaluer(faits, cle, p, a.ack_cnpg, controle_drapeau=not e2e)
        afficher_rapport(rapport, cle, p, sortie, "PRE-VOL SIMULE")
        if rapport["verdict"] == "ROUGE":
            sortie("\nSIMULATION arretee : pre-vol ROUGE (le lancement reel serait refuse).")
            return 1
        try:
            planifier(fs, p)
        except RefusSecurite as e:
            sortie("\nSIMULATION arretee : %s" % e)
            return 1
        codes = codes_a_acquitter(rapport)
        if e2e:
            sortie("\nNOTE : le lancement REEL de .158 est le scenario DISK_E2E_158 : sa PORTE est obligatoire (Bridge, Backend et image, "
                   "drapeaux, protection systemd, cooldown, incidents disque ouverts, RAM et pages de l'hote ; collecte reelle en lecture "
                   "seule) et il n'existe AUCUN lancement en detection seule. Voir RUNBOOK-DISK-E2E-158.md.")
        if codes:
            sortie("\nNOTE : en lancement reel, --go REFUSERAIT ces ORANGE sans acquittement explicite : --accepte-orange %s" % ",".join(codes))
        sortie("")
        monde = MondeSimule(fs.taille, fs.dispo, helios=a.sim_helios, croissance_o_s=a.sim_croissance_mo_s * MIO,
                            recuperation_pct=p.recuperation, hostname=nom)
        res = executer_test(NoeudSimule(monde), SondeSimulee(monde), HorlogeSimulee(monde), p, "0" * 32, sortie, identite=nom)
        afficher_bilan(res, p, sortie)
        sortie("\nSIMULATION terminee : aucune commande reelle executee. Lancement reel (apres accord EXPLICITE) :")
        sortie("  python helios_disk_test.py --target %s --go --confirm %s%s%s%s" % (
            cle, attendu, " --image-backend <TAG>" if e2e else "",
            " --accepte-risque-primaire-cnpg" if faits["cnpg"]["primaire_sur_cible"] and not e2e else "",
            " --accepte-orange %s" % ",".join(codes) if codes else ""))
        return CODES_SORTIE.get(res["verdict"], 1)
    deps = deps or Dependances()
    faits, ctx = collecter_reel(deps, cle, a.vm_base)
    if e2e:
        # PORTE DU SCENARIO DISK_E2E_158 : obligatoire pour .158 (preflight ET go). Collecte reelle, en lecture seule, de ce que le Backend
        # et le Bridge DEPLOYES savent faire ; au moindre fait manquant, illisible ou ROUGE, ou ORANGE non acquitte : aucune charge.
        porte = module_porte_e2e()
        faits_porte = porte.collecter_faits_e2e(deps, ctx["exe"], ctx["primaire"])
        rapport = porte.evaluer_e2e(faits, faits_porte, p, cle, image_backend_attendue=a.image_backend, acquittes=acquittes)
        porte.afficher_rapport_e2e(rapport, sortie)
    else:
        rapport = evaluer(faits, cle, p, a.ack_cnpg)
        afficher_rapport(rapport, cle, p, sortie)
    codes = codes_a_acquitter(rapport)
    if mode == "preflight":
        sortie("\nPre-vol termine : RIEN n'a ete cree ni modifie. Confirmation d'un lancement reel : --go --confirm %s%s%s" % (
            attendu, " --image-backend %s" % (a.image_backend or "<TAG>") if e2e else "",
            " --accepte-orange %s" % ",".join(codes) if codes else ""))
        sortie("Un pre-vol reussi N'EST PAS une preuve de remediation : seule l'execution du scenario reel (--go) la produit, et seule la "
               "resolution verifiee de l'incident la prouve.")
        return 0 if rapport["verdict"] != "ROUGE" else 1
    if rapport["verdict"] == "ROUGE":
        sortie("\nREFUS : pre-vol ROUGE, rien n'a ete lance.")
        return 1
    manquants = orange_non_acquittes(rapport, acquittes)
    if manquants:
        sortie("\nREFUS : ORANGE non acquitte(s) : %s ; relire le pre-vol puis relancer avec --accepte-orange %s (rien n'a ete lance)" % (
            ", ".join(x[3] or "?" for x in manquants), ",".join(x[3] for x in manquants if x[3])))
        return 1
    if e2e and not rapport.get("go"):
        # Defense en profondeur : le lanceur honore AUSSI la decision propre de la porte (`go`), pas seulement sa lecture du verdict.
        sortie("\nREFUS : la porte du scenario DISK_E2E_158 n'autorise pas le lancement, rien n'a ete lance.")
        return 1
    horloge = deps.horloge()
    ops = NoeudSsh(ctx["exe"], CIBLES[cle][1])
    sonde = SondeReelle(ctx["exe"], nom, ctx["primaire"], ctx["id0"])
    dossier_vm = os.path.join(a.vm_base, VM_DOSSIER[cle])
    res = executer_test(ops, sonde, horloge, p, deps.nouveau_jeton(), sortie, faits["hote"]["vmdk"], lambda: analyser_vmdk(dossier_vm),
                        identite=nom)
    afficher_bilan(res, p, sortie)
    return CODES_SORTIE.get(res["verdict"], 1)


if __name__ == "__main__":
    enregistrer_comme_outil()                                              # la porte du scenario compose CE module (pas une 2e copie)
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    sys.exit(principal())
