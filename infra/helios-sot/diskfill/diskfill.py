#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""HELIOS - remplissage disque de LABORATOIRE du noeud .158 (k8s-worker1). HORS PRODUCTION.

SERVICE LOCAL du scenario DISK_E2E_158 : equivalent, SUR le noeud, de `helios_disk_test.py --target 158 --go`. Il remplit `/` par
`fallocate` jusqu'a 81 %, laisse HELIOS (detecteur -> incident -> HOST_RECLAIM_DISK par le Bridge -> verification) liberer les
fichiers, puis verifie le retour a 77 % ou moins. Il NE SUPPRIME RIEN dans le cas nominal : seul HELIOS libere. Il ne libere
lui-meme que par SECURITE : urgence (83 %), garde independante (83,5 %), fin d'observation (720 s) ou expiration (1 020 s).

Demarrage : `sudo systemctl start helios-diskfill.service` - UNE SEULE commande, directement dans la VM, COMME `helios-cpuload.service` : AUCUN
armement manuel et AUCUNE dependance de l'hote Windows. `ExecStartPre` execute AUTOMATIQUEMENT tous les controles du noeud et refuse de demarrer,
sans rien allouer, si l'un echoue. L'attestation de l'agent Windows DISK (`192.168.148.1:19158`, HTTP/HMAC : rapport authentique, frais, pour
DISK_E2E_158, VERT ou ORANGE explicitement autorise) est OPTIONNELLE : DESACTIVEE par defaut (ATTESTATION_HOTE_REQUISE = False), son code et ses
tests sont CONSERVES ; la rendre obligatoire demande une nouvelle version du programme (SHA256SUMS, reinstallation). Le watchdog VM_DOWN `:9099`
reste independant et inchange.
La garde independante est demarree AVANT par la dependance de l'unite (Requires= + After=). Chaque fin laisse UNE ligne finale dans le journal
systemd : `RESULTAT : verdict=<V> code=<n> ...` (REFUS par `verifier` ; verdict de `run` ; INTERROMPU par `nettoyer` si `run` n'a rien rendu,
par exemple apres un SIGKILL). Codes : voir CODES_SORTIE ; le code 6 (ARRET demande) laisse l'unite propre.

Sous-commandes (une seule entree, aucune option de seuil) :
  verifier [--a-blanc]   ExecStartPre : tous les controles internes, puis jeton, etat et cooldown. --a-blanc : aucun effet.
  run                    ExecStart : remplissage controle, observation, verification du retour <= 77 %.
  garde                  unite helios-diskguard.service : filet independant (83,5 % ou echeance), noms exacts seulement.
  nettoyer               ExecStopPost : libere par noms exacts les fichiers de CE lancement (jamais ceux d'un lancement precedent).
  etat [--json]          lecture seule : faits du noeud (installation, unites, repertoires, etat, cooldown).
  nettoyer-orphelins     MANUEL uniquement : plan par defaut ; suppression avec --confirmer. Jamais automatique.

Contrat du Bridge (HOST_RECLAIM_DISK) respecte a l'identique : repertoire /var/lib/helios-diskfill en `directory|root|700`, fichiers
helios-diskfill-<32 hex>-<001..016>.bin reguliers, root, un seul lien, un seul jeton, <= 16 fichiers, <= 6 Gio. Les parametres sont
CODES EN DUR (ni argument, ni variable d'environnement, ni fichier a deposer) : rien ne peut les modifier avant le demarrage.

ASCII pur, bibliotheque standard seule. Tous les acces au systeme sont dans MondeReel : le reste est pur et teste sur un monde emule.
"""
import argparse
import hashlib
import hmac
import json
import math
import os
import re
import secrets
import socket
import stat
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from collections import namedtuple

VERSION = 1
NOM_CIBLE = "k8s-worker1"
IP_CIBLE = "192.168.148.158"

# ------------------------------------------------------------------------------------------------ chemins (figes)
CHEMIN_SCRIPT = "/opt/helios-diskfill/diskfill.py"
UNITE_PRINCIPALE = "helios-diskfill.service"
UNITE_GARDE = "helios-diskguard.service"
CHEMIN_UNITE = {UNITE_PRINCIPALE: "/etc/systemd/system/helios-diskfill.service",
                UNITE_GARDE: "/etc/systemd/system/helios-diskguard.service"}
NOMS_SOT = ("diskfill.py", "helios-diskfill.service", "helios-diskguard.service")      # fichiers du service (empreintes affichees)
# Empreintes SHA-256 (contenu LF) des DEUX unites VALIDEES. Elles remplacent, SUR LE NOEUD, la liaison que l'armement faisait avec la source de
# verite : `verifier` refuse de demarrer si une unite installee differe (limites RuntimeMaxSec/MemoryMax/TasksMax, durcissement, ExecStart*,
# dependances : tout le contenu). Le programme ne peut pas se verifier lui-meme : son empreinte est controlee par l'installateur et par le
# diagnostic manuel, et par l'agent Windows authentifie quand l'attestation est requise (ATTESTATION_HOTE_REQUISE). Un test de coherence verrouille
# ces valeurs sur les fichiers du depot (toute modification d'une unite exige de les mettre a jour ici, puis de regenerer SHA256SUMS).
SHA_UNITES = {"helios-diskfill.service": "6cd2ae205d0e3a99114dfa06656507d48a1378c4f3f00e17ee18e180ce5169b9",
              "helios-diskguard.service": "0f89c28ce69b4145e506d3b55da3a5e60dbec27f7c92571f637f47b708e1eaa1"}
DOSSIER = "/var/lib/helios-diskfill"              # repertoire EXCLUSIF du Bridge (StateDirectory, mode 0700 explicite)
DOSSIER_ETAT = "/var/lib/helios-diskfill-etat"    # horodatage du cooldown local (hors du repertoire du Bridge)
RUN_DIR = "/run/helios-diskfill"                  # volatil (tmpfs) : etat, battement, marqueurs
F_ETAT = RUN_DIR + "/etat.json"
F_BATTEMENT = RUN_DIR + "/garde.battement.json"
F_MARQUEUR_GARDE = RUN_DIR + "/garde.declenchee"
F_MARQUEUR_EXPIRATION = RUN_DIR + "/expiration.declenchee"
F_MARQUEUR_ARRET_GARDE = RUN_DIR + "/garde.arret"  # pose par `nettoyer` quand le demarrage a ete REFUSE : une garde OISIVE sort aussitot
F_COOLDOWN = DOSSIER_ETAT + "/derniere-execution.json"
URL_PROM = "http://192.168.148.160:9090/api/v1/query"
# Agent Windows DISK independant du watchdog VM_DOWN. Adresse et contrat figes : aucun endpoint arbitraire.
URL_AGENT_HOTE = "http://192.168.148.1:19158/v1/preflight"
F_CLE_AGENT_HOTE = "/etc/helios-diskfill/host-agent.key"
AGENT_HOTE_CONTRAT = 1
AGENT_HOTE_CIBLE = "158"
AGENT_HOTE_SCENARIO = "DISK_E2E_158"
AGENT_HOTE_IMAGE = "pfe-backend:0.2.165-disk-e2e"
AGENT_HOTE_FRAICHEUR_S = 30
AGENT_HOTE_TIMEOUT_S = 55
AGENT_HOTE_CODES_ORANGE_AUTORISES = frozenset(("GEL_HOTE_48H", "E2E_INCIDENTS_DISQUE_ANCIENS"))
# DECISION DE L'EXPLOITANT (17 nov. 2026) : `sudo systemctl start helios-diskfill.service` doit suffire DIRECTEMENT DANS LA VM, comme
# helios-cpuload.service, sans solliciter l'hote Windows. L'attestation de l'agent est donc DESACTIVEE par defaut : agent absent ou arrete =
# AUCUN effet, AUCUNE requete vers Windows. Le code de l'attestation (HMAC, fraicheur, liste ORANGE autorises) est CONSERVE et teste : True
# (nouvelle version du programme, SHA256SUMS regenere, reinstallation) la rend de nouveau OBLIGATOIRE, en echec ferme.
ATTESTATION_HOTE_REQUISE = False


def agent_hote_preflight_autorisable(verdict, go, raisons):
    """Politique locale identique a l'agent : ORANGE uniquement sur allowlist et sans aucun ROUGE."""
    if go is not True or not isinstance(raisons, list) or not all(isinstance(x, dict) for x in raisons):
        return False
    niveaux = [x.get("level") for x in raisons]
    if any(niveau not in ("INFO", "ORANGE") for niveau in niveaux):
        return False
    oranges = [x for x in raisons if x.get("level") == "ORANGE"]
    if verdict == "VERT":
        return not oranges and all(niveau == "INFO" for niveau in niveaux)
    if verdict == "ORANGE":
        return bool(oranges) and all(x.get("code") in AGENT_HOTE_CODES_ORANGE_AUTORISES for x in oranges)
    return False


CONFIG_KUBELET = "/var/lib/kubelet/config.yaml"
PROPRIETES_UNITE = ("LoadState", "FragmentPath", "DropInPaths", "UnitFileState", "StateDirectoryMode", "Restart", "NeedDaemonReload")

# ------------------------------------------------------------------------------------------------ parametres FIGES (= lanceur valide)
MIO = 1024 * 1024
GIO = 1024 * MIO
DETECTION_PCT = 80.0                 # regle NODE_DISK_FULL : > 80
CIBLE_PCT, PLAFOND_PCT, URGENCE_PCT, GARDE_PCT, RECUPERATION_PCT = 81.0, 82.0, 83.0, 83.5, 77.0
MARGE_KUBELET_PCT = 2.0              # l'urgence doit rester a >= 2 points sous le declenchement du kubelet
BLOC = 512 * MIO
BLOC_MAX = 1024 * MIO
MAX_FICHIERS = 16
MAX_TOTAL = 6 * GIO
OBSERVE_S = 720
OBSERVE_MIN_S = 60
EXPIRATION_S = 1020
MARGE_FIN_S = 120
BUDGET_ALLOC_S = EXPIRATION_S - OBSERVE_S - MARGE_FIN_S      # 180 s, comptes depuis la creation de l'etat du lancement (fin de verifier)
DUREE_BLOC_MIN_S = 5
EFFET_MIN = 0.90
PAS_S = 2                            # mesure de l'occupation toutes les 2 s (le lanceur : 5 s)
PAS_SONDE_S = 10                     # Prometheus (informatif) toutes les 10 s
GARDE_PAS_S = 2
GARDE_ATTENTE_ETAT_S = 120           # garde inactive : sort si aucun lancement ne s'annonce
BATTEMENT_MAX_S = 10                 # un battement plus vieux = garde perdue
ATTENTE_GARDE_S = 15
CONFIRMATIONS_RETOUR = 2             # mesures consecutives <= 77 % apres liberation
# Cooldown local : 5 min depuis le DEBUT du dernier lancement (17 nov. 2026 ; avant : 30 min, qui refusait la commande unique pendant une demi-heure
# apres chaque essai). Invariant verrouille par test (ConfigMap du depot) : cooldown local >= cooldown du Backend (REMEDIATION_COOLDOWN_MINUTES, 2 min)
# + un lancement complet jusqu'a RESOLVED (~2 min, mesure 78-119 s) + 1 min de marge ; sinon un 2e lancement rapproche ne serait pas remedie par HELIOS
# (cooldown du Backend) et seul le filet du service le liberait. Les limites de chaque lancement (plafonds, garde, duree) sont independantes du cooldown.
COOLDOWN_LOCAL_S = 5 * 60
ECART_PROM_MAX_PTS = 1.0
# Inodes de / : refus a partir de 85 % (regle NODE_INODES_FULL du detecteur : `> 85`, comme DETECTION_PCT reprend NODE_DISK_FULL) : un incident
# d'inodes existerait ou serait imminent et fausserait le scenario ; refus aussi sous INODES_LIBRES_MIN inodes libres (le plan en cree <= 16).
INODES_MAX_PCT = 85.0
INODES_LIBRES_MIN = 1024
TYPES_FS_OK = ("ext4", "xfs")
PHASES = ("pret", "remplissage", "observation", "liberation", "terminee", "liberation_incomplete")
PHASES_ACTIVES = ("pret", "remplissage", "observation", "liberation", "liberation_incomplete")

RE_JETON = re.compile(r"[0-9a-f]{32}")
RE_FICHIER = re.compile(r"helios-diskfill-([0-9a-f]{32})-([0-9]{3})\.bin")      # fullmatch UNIQUEMENT (= motif du Bridge)
CODES_SORTIE = {"OK": 0, "ERREUR": 1, "REFUS": 2, "DETECTION_SEULE": 3, "AUCUNE_DETECTION": 4, "URGENCE": 5, "ARRET": 6}


class RefusSecurite(Exception):
    """Une condition de securite n'est pas remplie (ou n'a pas pu etre verifiee) : on s'arrete SANS rien faire."""


class ArretUrgence(Exception):
    """L'occupation a atteint le seuil d'urgence : liberation immediate."""


class ArretManuel(Exception):
    """SIGTERM / SIGINT : l'exploitant (ou systemd) demande l'arret : liberation immediate."""


class ErreurNoeud(Exception):
    """Mesure ou commande impossible sur le noeud."""


# ------------------------------------------------------------------------------------------------ calculs (purs)
def usage_pct(taille, dispo):
    """Occupation en % = (1 - disponible / taille) x 100 : MEME FORMULE que la regle NODE_DISK_FULL et que le Bridge."""
    if (not isinstance(taille, int) or not isinstance(dispo, int) or isinstance(taille, bool) or isinstance(dispo, bool)
            or taille <= 0 or dispo < 0 or dispo > taille):
        raise ErreurNoeud("mesures incoherentes (taille=%r, disponible=%r)" % (taille, dispo))
    return (1.0 - float(dispo) / float(taille)) * 100.0


class Fs(namedtuple("Fs", "taille dispo")):
    """Systeme de fichiers racine : taille et espace disponible (hors reserve root), en octets."""
    __slots__ = ()

    @property
    def usage(self):
        return usage_pct(self.taille, self.dispo)


def usage_inodes_pct(total, libres):
    """Occupation des inodes en % = (1 - libres / total) x 100 : MEME FORMULE que la regle NODE_INODES_FULL du detecteur. Une mesure
    incoherente (total nul, negatif, libres > total, types inattendus) est une ERREUR, jamais une valeur devinee (echec sur)."""
    if (not isinstance(total, int) or not isinstance(libres, int) or isinstance(total, bool) or isinstance(libres, bool)
            or total <= 0 or libres < 0 or libres > total):
        raise ErreurNoeud("mesures d'inodes incoherentes (total=%r, libres=%r)" % (total, libres))
    return (1.0 - float(libres) / float(total)) * 100.0


def octets_pour(fs, pct):
    """Octets a ajouter pour que l'occupation atteigne `pct` (0 si elle l'est deja)."""
    return max(0, int(math.ceil(pct / 100.0 * fs.taille)) - (fs.taille - fs.dispo))


def arrondi_mio(n):
    return ((int(n) + MIO - 1) // MIO) * MIO


def nom_court(nom):
    return (nom or "").strip().lower().split(".")[0]


def nom_fichier(jeton, i):
    if not isinstance(jeton, str) or not RE_JETON.fullmatch(jeton) or not isinstance(i, int) or isinstance(i, bool) \
            or not 1 <= i <= MAX_FICHIERS:
        raise RefusSecurite("nom de fichier de test invalide (jeton=%r, index=%r)" % (jeton, i))
    return "helios-diskfill-%s-%03d.bin" % (jeton, i)


def noms_prevus(jeton):
    return [nom_fichier(jeton, i) for i in range(1, MAX_FICHIERS + 1)]


def chemin_de(nom):
    """Chemin complet d'un fichier de test ; refuse tout nom qui n'est pas EXACTEMENT de la forme attendue (aucun joker)."""
    m = RE_FICHIER.fullmatch(nom) if isinstance(nom, str) else None
    if m is None or not 1 <= int(m.group(2)) <= MAX_FICHIERS:
        raise RefusSecurite("nom de fichier refuse (hors du motif du repertoire de test) : %r" % (nom,))
    return DOSSIER + "/" + nom


def planifier(fs):
    """Plan theorique (pur) : tailles des blocs pour atteindre la cible. Refuse tout plan qui depasserait une limite."""
    u = fs.usage
    if u >= URGENCE_PCT:
        raise RefusSecurite("occupation %.2f %% >= seuil d'urgence %.1f %%" % (u, URGENCE_PCT))
    if u >= DETECTION_PCT:
        raise RefusSecurite("occupation %.2f %% >= seuil de detection (%.0f %%) : un incident naturel existerait" % (u, DETECTION_PCT))
    if u > RECUPERATION_PCT:
        raise RefusSecurite("occupation de base %.2f %% > seuil de recuperation %.1f %% : recuperation impossible" % (u, RECUPERATION_PCT))
    reste = octets_pour(fs, CIBLE_PCT)
    if reste <= 0:
        raise RefusSecurite("cible deja atteinte")
    if reste > MAX_TOTAL:
        raise RefusSecurite("remplissage de %d Mo > limite de %d Mo" % (reste // MIO, MAX_TOTAL // MIO))
    blocs, cumul = [], 0
    while reste > 0:
        b = min(arrondi_mio(reste), BLOC)
        blocs.append(b)
        reste -= b
        cumul += b
        if Fs(fs.taille, fs.dispo - cumul).usage > PLAFOND_PCT:
            raise RefusSecurite("le plan depasserait le plafond de %.1f %%" % PLAFOND_PCT)
    if len(blocs) > MAX_FICHIERS:
        raise RefusSecurite("trop de blocs (%d > %d)" % (len(blocs), MAX_FICHIERS))
    return blocs


def garde_allocation(fs, octets, deja_alloue=0):
    """(ok, raison) a evaluer AVANT chaque allocation, sur des mesures FRAICHES."""
    if not isinstance(octets, int) or octets <= 0 or octets > BLOC:
        return False, "taille de bloc hors limites (%r)" % (octets,)
    if not isinstance(deja_alloue, int) or deja_alloue < 0 or deja_alloue + octets > MAX_TOTAL:
        return False, "plafond cumule de %d Mo depasse (deja %s Mo + bloc de %d Mo)" % (
            MAX_TOTAL // MIO, deja_alloue // MIO if isinstance(deja_alloue, int) else deja_alloue, octets // MIO)
    u = fs.usage
    if u >= URGENCE_PCT:
        return False, "occupation %.2f %% >= seuil d'urgence %.1f %%" % (u, URGENCE_PCT)
    if u >= CIBLE_PCT:
        return False, "cible deja atteinte (%.2f %%)" % u
    if octets >= fs.dispo:
        return False, "le bloc depasserait l'espace disponible"
    apres = Fs(fs.taille, fs.dispo - octets).usage
    if apres > PLAFOND_PCT:
        return False, "occupation apres allocation %.2f %% > plafond %.1f %%" % (apres, PLAFOND_PCT)
    return True, "ok"


def parametres():
    """Parametres figes du scenario (aucune surcharge possible : ni argument, ni variable d'environnement, ni fichier). Source unique des tests de
    coherence avec le lanceur, la porte E2E et le Bridge."""
    return {"cible_pct": CIBLE_PCT, "plafond_pct": PLAFOND_PCT, "urgence_pct": URGENCE_PCT, "garde_pct": GARDE_PCT,
            "recuperation_pct": RECUPERATION_PCT, "bloc_mo": BLOC // MIO, "max_fichiers": MAX_FICHIERS, "max_total_mo": MAX_TOTAL // MIO,
            "observe_s": OBSERVE_S, "expiration_s": EXPIRATION_S, "cooldown_local_min": COOLDOWN_LOCAL_S // 60}


# ------------------------------------------------------------------------------------------------ parsing (pur)
def _bloc_yaml(texte, nom):
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
    """config.yaml du kubelet -> seuils d'eviction / ramasse-miettes (meme logique que le lanceur), ou None si illisible."""
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
    """Occupation (%) a partir de laquelle le kubelet agit ; None si inconnue."""
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


def parse_fib_trie(texte):
    """/proc/net/fib_trie -> ensemble des adresses IPv4 LOCALES du noeud."""
    ips, courante = set(), None
    for ligne in (texte or "").splitlines():
        m = re.match(r"^\s*(?:\|--|\+--)\s+(\d+\.\d+\.\d+\.\d+)(?:/\d+)?\b", ligne)
        if m:
            courante = m.group(1) if ligne.strip().startswith("|--") else None
            continue
        if courante and re.match(r"^\s*/32\s+host\s+LOCAL\s*$", ligne):
            ips.add(courante)
    return ips


def _anomalies_volume(info, racine, chemin=None):
    a = []
    if info.get("dev") != racine.get("dev"):
        a.append("peripherique %s != celui de / (%s) : autre systeme de fichiers que celui mesure" % (info.get("dev"), racine.get("dev")))
    if info.get("fstype") not in TYPES_FS_OK:
        a.append("systeme de fichiers %s (ext4 ou xfs requis)" % info.get("fstype"))
    if info.get("montage") not in ("/", chemin):
        a.append("point de montage %s au lieu de /" % info.get("montage"))
    return a


def anomalies_dossier(info, racine):
    """Anomalies d'un repertoire de test EXISTANT ; [] = conforme AU CONTRAT DU BRIDGE (directory|root|700, volume de /).
    Dans l'espace de noms d'une unite systemd, un StateDirectory= est un BIND MOUNT de lui-meme (constate sur .158, systemd 255, y compris pour
    ExecStartPre=+) : `findmnt` y rapporte le repertoire comme point de montage. Seul CE repertoire est admis comme point de montage, et le
    peripherique doit rester celui de / (ext4 ou xfs) : un autre volume monte dessus reste refuse."""
    if not info or not racine:
        return ["etat du repertoire ou de / illisible"]
    a = []
    if info.get("type") != "directory":
        a.append("n'est pas un repertoire (type : %s)" % info.get("type"))
    if info.get("uid") != 0 or info.get("gid") != 0:
        a.append("proprietaire %s:%s au lieu de root:root" % (info.get("uid"), info.get("gid")))
    if info.get("mode") != "700":
        a.append("permissions %s au lieu de 700 (le Bridge exige directory|root|700)" % info.get("mode"))
    a.extend(_anomalies_volume(info, racine, DOSSIER))
    return a


def anomalies_prive(info, nom):
    """Repertoire d'etat (cooldown, /run) : vrai repertoire root:root 0700."""
    if not info:
        return ["%s illisible ou absent" % nom]
    a = []
    if info.get("type") != "directory":
        a.append("%s n'est pas un repertoire" % nom)
    if info.get("uid") != 0 or info.get("gid") != 0:
        a.append("%s : proprietaire %s:%s au lieu de root:root" % (nom, info.get("uid"), info.get("gid")))
    if info.get("mode") != "700":
        a.append("%s : permissions %s au lieu de 700" % (nom, info.get("mode")))
    return a


def entree_supprimable(e):
    """Entree d'un repertoire de test (dict nom/type/uid/nlink/taille) : fichier regulier, root, un seul lien, taille > 0."""
    return e.get("type") == "f" and e.get("uid") == 0 and e.get("nlink") == 1 and isinstance(e.get("taille"), int) and e["taille"] > 0


def plan_orphelins(entrees):
    """Plan PUR du nettoyage MANUEL des orphelins (memes regles que disque_plan du Bridge) : {refus, jeton, fichiers, taille, etrangers}."""
    plan = {"refus": "", "jeton": None, "fichiers": [], "taille": 0, "etrangers": 0}
    jetons, fichiers, taille = set(), [], 0
    for e in entrees:
        m = RE_FICHIER.fullmatch(e.get("nom", ""))
        if not m:
            plan["etrangers"] += 1
            continue
        if not entree_supprimable(e) or not 1 <= int(m.group(2)) <= MAX_FICHIERS:
            plan.update(refus="entree homonyme non conforme : %s" % e.get("nom", "")[:60], jeton=None, fichiers=[], taille=0)
            return plan
        jetons.add(m.group(1))
        fichiers.append(e["nom"])
        taille += e["taille"]
    if len(jetons) > 1:
        plan.update(refus="%d jetons presents : on ne devine pas lequel est le bon" % len(jetons), jeton=None, fichiers=[], taille=0)
    elif len(fichiers) > MAX_FICHIERS:
        plan.update(refus="%d fichiers > %d" % (len(fichiers), MAX_FICHIERS), jeton=None, fichiers=[], taille=0)
    elif taille > MAX_TOTAL:
        plan.update(refus="%d Mo > limite de %d Mo" % (taille // MIO, MAX_TOTAL // MIO), jeton=None, fichiers=[], taille=0)
    else:
        plan["jeton"] = next(iter(jetons)) if jetons else None
        plan["fichiers"] = sorted(fichiers)
        plan["taille"] = taille
    return plan


# ------------------------------------------------------------------------------------------------ etat (pur)
def _nombre(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and v == v and v not in (float("inf"), float("-inf"))


def valider_etat(e):
    """Etat d'un lancement (fichier etat.json) -> bool. Le jeton doit etre un jeton valide ; la phase, connue."""
    return (isinstance(e, dict) and isinstance(e.get("jeton"), str) and RE_JETON.fullmatch(e["jeton"]) is not None
            and e.get("phase") in PHASES and isinstance(e.get("invocation_id"), str) and e["invocation_id"] != ""
            and _nombre(e.get("cree_epoch")) and _nombre(e.get("debut_epoch")) and _nombre(e.get("echeance_epoch")))


def valider_battement(b):
    return isinstance(b, dict) and isinstance(b.get("pid"), int) and _nombre(b.get("epoch")) and (b.get("jeton") is None or (
        isinstance(b.get("jeton"), str) and RE_JETON.fullmatch(b["jeton"]) is not None))


def _valeur_resultat(v):
    """Valeur d'un champ de la ligne RESULTAT : ASCII, sans espace (un champ = un mot), jamais de saut de ligne."""
    if v is None:
        return "inconnu"
    if v is True:
        return "oui"
    if v is False:
        return "non"
    if isinstance(v, float):
        return "%.2f" % v
    return re.sub(r"[^A-Za-z0-9_.,:/+-]", "_", str(v))[:80] or "-"


def ligne_resultat(verdict, code, **champs):
    """LIGNE FINALE du journal systemd : une seule ligne, `cle=valeur` separes par des espaces, facile a retrouver :
    `journalctl -u helios-diskfill.service | grep RESULTAT`. Le verdict et le code sont ceux du service (voir CODES_SORTIE)."""
    mots = ["verdict=%s" % _valeur_resultat(verdict), "code=%d" % code]
    mots.extend("%s=%s" % (k, _valeur_resultat(v)) for k, v in champs.items())
    return "helios-diskfill : RESULTAT : " + " ".join(mots)


# ------------------------------------------------------------------------------------------------ monde reel (LINUX)
class MondeReel(object):
    """Acces reels : UNIQUE endroit du fichier qui touche au systeme (os, subprocess, urllib, /proc, signaux). Tout le reste est pur."""

    def __init__(self):
        self._arret = False

    # --- signaux : un gestionnaire ne fait QU'UNE chose, poser un drapeau (lecon de cpuload.py v2.1)
    def installer_signaux(self):
        import signal

        def _poser(_signum, _cadre):
            self._arret = True
        signal.signal(signal.SIGTERM, _poser)
        signal.signal(signal.SIGINT, _poser)

    def arret_demande(self):
        return self._arret

    # --- temps, identite
    def maintenant(self):
        return time.time()

    def dormir(self, secondes):
        time.sleep(secondes)

    def hostname(self):
        return socket.gethostname()

    def ips_locales(self):
        texte = self.lire_texte("/proc/net/fib_trie", 1048576)
        if texte is None:
            raise ErreurNoeud("/proc/net/fib_trie illisible")
        return parse_fib_trie(texte)

    def boot_id(self):
        t = self.lire_texte("/proc/sys/kernel/random/boot_id", 256)
        if not t or not t.strip():
            raise ErreurNoeud("boot_id illisible")
        return t.strip()

    def invocation_id(self):
        return os.environ.get("INVOCATION_ID") or None

    def pid(self):
        return os.getpid()

    def pid_vivant(self, pid):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True

    def chemin_script(self):
        return os.path.realpath(os.path.abspath(__file__))

    def nouveau_jeton(self):
        return secrets.token_hex(16)

    def preflight_hote(self):
        """Interroge l'agent Windows DISK ; authentification HMAC bidirectionnelle et attestation strictement fraiche. Echec ferme."""
        # Le repertoire et la cle sont provisionnes une seule fois par l'installateur Windows ; pas de secret en argument/env.
        try:
            st_dir = os.lstat(os.path.dirname(F_CLE_AGENT_HOTE))
            if (not stat.S_ISDIR(st_dir.st_mode) or st_dir.st_uid != 0 or
                    stat.S_IMODE(st_dir.st_mode) != 0o700):
                raise ErreurNoeud("repertoire de cle agent Windows non conforme (root:root 0700 exige)")
            fd = os.open(F_CLE_AGENT_HOTE, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            try:
                st = os.fstat(fd)
                if (not stat.S_ISREG(st.st_mode) or st.st_uid != 0 or st.st_nlink != 1 or
                        stat.S_IMODE(st.st_mode) != 0o600):
                    raise ErreurNoeud("cle agent Windows non conforme (fichier root:root 0600, lien unique exige)")
                brut = os.read(fd, 256)
            finally:
                os.close(fd)
            texte = brut.decode("ascii").strip()
            if not re.fullmatch(r"[0-9a-f]{64}", texte):
                raise ErreurNoeud("cle agent Windows illisible ou format invalide")
            cle = bytes.fromhex(texte)
        except ErreurNoeud:
            raise
        except Exception as e:
            raise ErreurNoeud("cle agent Windows inaccessible (%s)" % e.__class__.__name__)

        horodatage = int(self.maintenant())
        nonce = secrets.token_hex(16)
        requete = {"contract": AGENT_HOTE_CONTRAT, "target": AGENT_HOTE_CIBLE,
                   "scenario": AGENT_HOTE_SCENARIO, "image_backend": AGENT_HOTE_IMAGE,
                   "timestamp": horodatage, "nonce": nonce}
        corps = json.dumps(requete, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")
        prefixe = ("REQ\n%d\n%s\n" % (horodatage, nonce)).encode("ascii")
        signature = hmac.new(cle, prefixe + corps, hashlib.sha256).hexdigest()
        req = urllib.request.Request(URL_AGENT_HOTE, data=corps, method="POST", headers={
            "Content-Type": "application/json", "Accept": "application/json",
            "Cache-Control": "no-store", "Connection": "close",
            "X-Helios-Time": str(horodatage), "X-Helios-Nonce": nonce,
            "X-Helios-Signature": signature})
        try:
            # Proxy systeme ignore : connexion directe a l'adresse fixe du reseau HELIOS.
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(req, timeout=AGENT_HOTE_TIMEOUT_S) as reponse:
                if getattr(reponse, "status", 200) != 200:
                    raise ErreurNoeud("agent Windows a repondu HTTP %s" % getattr(reponse, "status", "?"))
                brut_rep = reponse.read(65537)
                if len(brut_rep) > 65536:
                    raise ErreurNoeud("attestation Windows trop volumineuse")
                sig_rep = reponse.headers.get("X-Helios-Signature", "")
        except ErreurNoeud:
            raise
        except Exception as e:
            raise ErreurNoeud("agent Windows DISK injoignable ou delai depasse (%s)" % e.__class__.__name__)
        try:
            objet = json.loads(brut_rep.decode("ascii"))
        except Exception:
            raise ErreurNoeud("attestation Windows invalide (JSON/ASCII)")
        attendu = hmac.new(cle, ("RESP\n%d\n%s\n" % (horodatage, nonce)).encode("ascii") + brut_rep,
                           hashlib.sha256).hexdigest()
        if not sig_rep or not hmac.compare_digest(sig_rep, attendu):
            raise ErreurNoeud("signature de l'agent Windows invalide")
        maintenant = self.maintenant()
        if not isinstance(objet, dict):
            raise ErreurNoeud("attestation Windows non structuree")
        if (objet.get("contract") != AGENT_HOTE_CONTRAT or objet.get("target") != AGENT_HOTE_CIBLE or
                objet.get("scenario") != AGENT_HOTE_SCENARIO or objet.get("image_backend") != AGENT_HOTE_IMAGE or
                objet.get("request_timestamp") != horodatage or objet.get("request_nonce") != nonce):
            raise ErreurNoeud("attestation Windows hors contrat, cible, scenario ou requete")
        genere = objet.get("generated_epoch")
        if (not isinstance(genere, (int, float)) or isinstance(genere, bool) or genere > maintenant + 5 or
                maintenant - genere > AGENT_HOTE_FRAICHEUR_S):
            raise ErreurNoeud("attestation Windows perimee ou horloge incoherente")
        raisons = objet.get("reasons")
        if not isinstance(raisons, list) or not all(isinstance(x, dict) for x in raisons):
            raise ErreurNoeud("raisons du pre-vol Windows absentes ou illisibles")
        verdict = objet.get("verdict")
        go = objet.get("go")
        if not agent_hote_preflight_autorisable(verdict, go, raisons):
            details = ["%s:%s" % (str(x.get("level", "?")), str(x.get("code", "?"))) for x in raisons
                       if x.get("level") in ("ROUGE", "ORANGE")]
            raise RefusSecurite("pre-vol Windows refuse (verdict=%s, go=%r%s)" % (
                verdict, go, "; " + ", ".join(details) if details else ""))
        return objet

    # --- systeme de fichiers
    def stats(self):
        st = os.statvfs("/")
        return Fs(int(st.f_frsize) * int(st.f_blocks), int(st.f_frsize) * int(st.f_bavail))

    def inodes(self):
        """(inodes au total, inodes libres) de / : les memes grandeurs que node_filesystem_files / node_filesystem_files_free."""
        st = os.statvfs("/")
        return int(st.f_files), int(st.f_ffree)

    def _cmd(self, argv, delai=10):
        try:
            r = subprocess.run(argv, capture_output=True, text=True, timeout=delai)
        except (OSError, subprocess.TimeoutExpired) as e:
            raise ErreurNoeud("%s impossible (%s)" % (os.path.basename(argv[0]), e.__class__.__name__))
        return r

    def info_chemin(self, chemin):
        try:
            st = os.lstat(chemin)
        except FileNotFoundError:
            return None
        except OSError as e:
            raise ErreurNoeud("stat de %s impossible (%s)" % (chemin, e.__class__.__name__))
        genre = ("directory" if stat.S_ISDIR(st.st_mode) else "symbolic link" if stat.S_ISLNK(st.st_mode)
                 else "regular file" if stat.S_ISREG(st.st_mode) else "autre")
        info = {"type": genre, "uid": st.st_uid, "gid": st.st_gid, "mode": "%o" % (st.st_mode & 0o7777), "dev": st.st_dev}
        r = self._cmd(["/usr/bin/findmnt", "-n", "-o", "FSTYPE,TARGET", "-T", chemin])
        mots = (r.stdout or "").split()
        if r.returncode != 0 or len(mots) != 2:
            raise ErreurNoeud("findmnt -T %s a echoue" % chemin)
        info["fstype"], info["montage"] = mots
        return info

    def contenu_dossier(self):
        try:
            return sorted(os.listdir(DOSSIER))
        except FileNotFoundError:
            return None
        except OSError as e:
            raise ErreurNoeud("lecture de %s impossible (%s)" % (DOSSIER, e.__class__.__name__))

    def entrees_dossier(self):
        try:
            noms = sorted(os.listdir(DOSSIER))
        except FileNotFoundError:
            return None
        except OSError as e:
            raise ErreurNoeud("lecture de %s impossible (%s)" % (DOSSIER, e.__class__.__name__))
        sortie = []
        for n in noms:
            st = os.lstat(DOSSIER + "/" + n)
            genre = "f" if stat.S_ISREG(st.st_mode) else "d" if stat.S_ISDIR(st.st_mode) else "l" if stat.S_ISLNK(st.st_mode) else "?"
            sortie.append({"nom": n, "type": genre, "uid": st.st_uid, "nlink": st.st_nlink, "taille": st.st_size})
        return sortie

    def fallocate_present(self):
        return os.access("/usr/bin/fallocate", os.X_OK)

    def allouer(self, nom, octets):
        chemin = chemin_de(nom)
        if not isinstance(octets, int) or isinstance(octets, bool) or not 0 < octets <= BLOC_MAX:
            raise RefusSecurite("taille d'allocation hors limites : %r" % (octets,))
        if os.path.lexists(chemin):
            raise ErreurNoeud("le fichier %s existe deja : refus" % nom)
        r = self._cmd(["/usr/bin/fallocate", "-l", str(octets), "--", chemin], 60)         # AUCUN repli (dd, posix_fallocate) : jamais d'ecriture
        if r.returncode != 0:
            raise ErreurNoeud("fallocate a echoue (rc=%s)" % r.returncode)
        st = os.lstat(chemin)
        if not stat.S_ISREG(st.st_mode) or st.st_uid != 0 or st.st_nlink != 1 or st.st_size != octets:
            raise ErreurNoeud("fichier alloue non conforme (type, proprietaire, liens ou taille)")

    def liberer(self, noms):
        """Supprime par NOM EXACT (descripteur du repertoire, sans suivre de lien) les seuls fichiers conformes ; retourne les noms supprimes."""
        for n in noms:
            chemin_de(n)                                                           # RefusSecurite si un nom sort du motif exact
        drapeaux = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
        try:
            fd = os.open(DOSSIER, drapeaux)
        except FileNotFoundError:
            return []
        supprimes = []
        try:
            for n in noms:
                try:
                    st = os.stat(n, dir_fd=fd, follow_symlinks=False)
                except FileNotFoundError:
                    continue
                if not stat.S_ISREG(st.st_mode) or st.st_uid != 0 or st.st_nlink != 1:
                    raise RefusSecurite("entree homonyme non conforme (refus de la supprimer) : %s" % n)
                os.unlink(n, dir_fd=fd)
                supprimes.append(n)
        finally:
            os.close(fd)
        return supprimes

    # --- fichiers d'etat
    def lire_texte(self, chemin, max_octets=65536):
        try:
            with open(chemin, "rb") as f:
                data = f.read(max_octets + 1)
        except FileNotFoundError:
            return None
        except OSError as e:
            raise ErreurNoeud("lecture de %s impossible (%s)" % (chemin, e.__class__.__name__))
        if len(data) > max_octets:
            raise ErreurNoeud("%s trop volumineux" % chemin)
        return data.decode("utf-8", "replace")

    def lire_json(self, chemin):
        t = self.lire_texte(chemin)
        if t is None:
            return None
        try:
            return json.loads(t)
        except ValueError:
            raise ErreurNoeud("%s illisible (JSON invalide)" % chemin)

    def ecrire_json(self, chemin, obj):
        tmp = chemin + ".tmp"
        data = (json.dumps(obj, sort_keys=True, indent=1) + "\n").encode("ascii")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, data)
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(tmp, chemin)

    def supprimer(self, chemin):
        try:
            os.unlink(chemin)
        except FileNotFoundError:
            pass

    def sha256(self, chemin):
        h = hashlib.sha256()
        try:
            with open(chemin, "rb") as f:
                for bloc in iter(lambda: f.read(65536), b""):
                    h.update(bloc)
        except OSError:
            return None
        return h.hexdigest()

    # --- systemd
    def unite_proprietes(self, unite, proprietes):
        argv = ["/usr/bin/systemctl", "show", "--no-pager"]
        for p in proprietes:
            argv += ["-p", p]
        r = self._cmd(argv + [unite])
        if r.returncode != 0:
            raise ErreurNoeud("systemctl show %s a echoue" % unite)
        sortie = {}
        for ligne in (r.stdout or "").splitlines():
            k, sep, v = ligne.partition("=")
            if sep:
                sortie[k] = v
        return sortie

    def unite_occupee(self, unite):
        r = self._cmd(["/usr/bin/systemctl", "is-active", unite])
        return (r.stdout or "").strip() in ("active", "activating", "deactivating", "reloading")

    def unites_transitoires(self):
        """Unites transitoires du LANCEUR (helios-diskfill-*) : None si illisible (jamais 'aucune' par defaut)."""
        try:
            r = subprocess.run(["/usr/bin/systemctl", "list-units", "--all", "--no-legend", "--plain", "helios-diskfill-*"],
                               capture_output=True, text=True, timeout=15)
        except (OSError, subprocess.TimeoutExpired):
            return None
        if r.returncode != 0:
            return None
        return [l.split()[0] for l in (r.stdout or "").splitlines() if l.strip()]

    # --- kubelet, Prometheus
    def config_kubelet(self):
        return self.lire_texte(CONFIG_KUBELET, 262144)

    def prom(self, requete):
        """Valeurs d'une requete Prometheus instantanee ; None si ILLISIBLE ; [] = reponse valide sans echantillon."""
        try:
            with urllib.request.urlopen(URL_PROM + "?" + urllib.parse.urlencode({"query": requete}), timeout=4) as r:
                obj = json.load(r)
        except Exception:                                                           # noqa: BLE001 - toute lecture ratee = illisible
            return None
        if (not isinstance(obj, dict) or obj.get("status") != "success" or not isinstance(obj.get("data"), dict)
                or not isinstance(obj["data"].get("result"), list)):
            return None
        vals = valeurs_prom(obj)
        return vals if len(vals) == len(obj["data"]["result"]) else None


# ------------------------------------------------------------------------------------------------ lectures composees (pures, sur un monde)
def lire_prom_noeud(monde):
    """Faits Prometheus de .158 (memes requetes que le pre-vol du lanceur)."""
    sel = 'instance="%s:9100",mountpoint="/",fstype!~"tmpfs"' % IP_CIBLE
    nb = monde.prom("count(node_filesystem_avail_bytes{%s})" % sel)
    u = monde.prom("(1-(node_filesystem_avail_bytes{%s}/node_filesystem_size_bytes{%s}))*100" % (sel, sel))
    inc = monde.prom('count(helios_incident{cible="%s"}) or vector(0)' % NOM_CIBLE)
    illisibles = [k for k, v in (("series", nb), ("usage", u), ("incident_actif", inc)) if v is None]
    return {"series": (int(nb[0]) if nb else 0) if nb is not None else None, "usage": u[0] if u is not None and len(u) == 1 else None,
            "incident_actif": int(inc[0]) if inc else None, "illisibles": illisibles}


def shas_noeud(monde):
    return {"diskfill.py": monde.sha256(CHEMIN_SCRIPT), "helios-diskfill.service": monde.sha256(CHEMIN_UNITE[UNITE_PRINCIPALE]),
            "helios-diskguard.service": monde.sha256(CHEMIN_UNITE[UNITE_GARDE])}


def cooldown_restant_s(monde, maintenant):
    """(restant en s, erreur) : None/None si aucune execution locale ; restant 0 = ecoule ; erreur = horodatage illisible (echec sur)."""
    try:
        stamp = monde.lire_json(F_COOLDOWN)
    except ErreurNoeud as e:
        return None, str(e)
    if stamp is None:
        return None, None
    if not isinstance(stamp, dict) or not _nombre(stamp.get("debut_epoch")):
        return None, "horodatage du cooldown local illisible (fichier altere) : l'exploitant le supprime lui-meme"
    age = maintenant - stamp["debut_epoch"]
    if age < 0:
        return None, "horodatage du cooldown local dans le futur (horloge reculee ?) : refus"
    return max(0, int(COOLDOWN_LOCAL_S - age)), None


def _sortie_verif(entrees, sortie):
    for etat, code, texte in entrees:
        sortie("   [%s] %s : %s" % (("OK" if etat is True else "KO" if etat is False else ".."), code, texte))


def verifier(monde, a_blanc=False, sortie=print):
    """ExecStartPre (ou a blanc) : TOUS les controles internes ; en lancement reel, et seulement s'ils sont TOUS satisfaits, cree le jeton, l'etat
    et l'horodatage du cooldown. AUCUN armement externe n'est lu ni exige : la verification est faite ICI, a chaque demarrage.
    Retourne le code de sortie (0 = accepte, 2 = refus). `a_blanc` : aucun effet (rien n'est ecrit ni cree, rien n'est alloue)."""
    maintenant = monde.maintenant()
    entrees, ctx = [], {}

    def tester(code, fonction):
        try:
            for etat, texte in fonction():
                entrees.append((etat, code, texte))
        except (ErreurNoeud, RefusSecurite) as e:
            entrees.append((False, code, str(e)))
        except Exception as e:                                                       # noqa: BLE001 - echec sur
            entrees.append((False, code, "lecture impossible (%s)" % e.__class__.__name__))

    def absent_ok(etat_reel):
        return None if a_blanc else etat_reel                                         # a blanc : une absence attendue n'est qu'informative

    def c_invocation():
        if a_blanc:
            return [(None, "a blanc : aucun effet (rien ecrit, aucun fichier cree, aucune allocation)")]
        iid = monde.invocation_id()
        return [(bool(iid), "execute par une unite systemd (INVOCATION_ID %s)" % ("present" if iid else "ABSENT : refus de tourner hors de l'unite"))]

    def c_identite():
        hn, ips = nom_court(monde.hostname()), monde.ips_locales()
        return [(hn == NOM_CIBLE, "nom d'hote %r (attendu %r)" % (hn, NOM_CIBLE)),
                (IP_CIBLE in ips, "IP %s %s sur ce noeud" % (IP_CIBLE, "presente" if IP_CIBLE in ips else "ABSENTE"))]

    def c_installation():
        chemin = monde.chemin_script()
        sorties = [(chemin == CHEMIN_SCRIPT, "script execute depuis %s (attendu %s)" % (chemin, CHEMIN_SCRIPT))]
        ctx["shas"] = shas_noeud(monde)
        for nom in NOMS_SOT:
            sha = ctx["shas"].get(nom)
            sorties.append((bool(sha), "SHA-256 %s : %s" % (nom, sha[:16] + "..." if sha else "ILLISIBLE")))
        for unite in (UNITE_PRINCIPALE, UNITE_GARDE):
            sha, valide = ctx["shas"].get(unite), SHA_UNITES[unite]
            sorties.append((sha == valide, "%s : %s" % (unite, "identique a l'unite validee (%s...) : limites, durcissement, ExecStart*, dependances" % valide[:16]
                                                         if sha == valide else "EMPREINTE DIFFERENTE de l'unite validee (%s... attendu, %s lu) : fichier modifie"
                                                         % (valide[:16], (sha or "illisible")[:16]))))
        for unite in (UNITE_PRINCIPALE, UNITE_GARDE):
            p = monde.unite_proprietes(unite, PROPRIETES_UNITE)
            sorties.append((p.get("LoadState") == "loaded", "%s : LoadState=%s" % (unite, p.get("LoadState"))))
            sorties.append((p.get("FragmentPath") == CHEMIN_UNITE[unite], "%s : FragmentPath=%s" % (unite, p.get("FragmentPath"))))
            sorties.append(((p.get("DropInPaths") or "") == "", "%s : aucun drop-in (DropInPaths=%r)" % (unite, p.get("DropInPaths") or "")))
            sorties.append((p.get("UnitFileState") == "static", "%s : UnitFileState=%s (static : jamais enable)" % (unite, p.get("UnitFileState"))))
            mode = (p.get("StateDirectoryMode") or "").lstrip("0")
            sorties.append((mode == "700", "%s : StateDirectoryMode=%s (0700 EXIGE : contrat du Bridge directory|root|700)" % (
                unite, p.get("StateDirectoryMode"))))
            sorties.append((p.get("Restart") == "no", "%s : Restart=%s" % (unite, p.get("Restart"))))
            sorties.append((p.get("NeedDaemonReload") == "no", "%s : NeedDaemonReload=%s (la configuration CHARGEE doit etre celle du fichier valide)" % (
                unite, p.get("NeedDaemonReload"))))
        return sorties

    def c_dossiers():
        racine = monde.info_chemin("/")
        sorties = []
        d = monde.info_chemin(DOSSIER)
        if d is None:
            sorties.append((absent_ok(False), "%s absent (systemd StateDirectory le cree au demarrage de l'unite)" % DOSSIER))
        else:
            an = anomalies_dossier(d, racine)
            sorties.append((not an, "%s : %s" % (DOSSIER, "conforme au contrat du Bridge (directory, root:root, 0700, volume de /)" if not an
                                                  else " ; ".join(an))))
        for chemin in (DOSSIER_ETAT, RUN_DIR):
            info = monde.info_chemin(chemin)
            if info is None:
                sorties.append((absent_ok(False), "%s absent (cree par systemd au demarrage de l'unite)" % chemin))
            else:
                an = anomalies_prive(info, chemin)
                sorties.append((not an, "%s : %s" % (chemin, "root:root 0700" if not an else " ; ".join(an))))
        contenu = monde.contenu_dossier()
        if contenu is None:
            sorties.append((None, "%s absent : aucun orphelin possible" % DOSSIER))
        elif contenu:
            sorties.append((False, "%s contient %d entree(s) : ORPHELINS d'un lancement precedent (aucun nettoyage automatique) ; l'exploitant "
                                   "les examine puis : sudo /usr/bin/python3 -I -B %s nettoyer-orphelins" % (DOSSIER, len(contenu), CHEMIN_SCRIPT)))
        else:
            sorties.append((True, "%s vide" % DOSSIER))
        return sorties

    def c_residus():
        u = monde.unites_transitoires()
        if u is None:
            return [(False, "unites transitoires helios-diskfill-* illisibles")]
        return [(u == [], "aucune unite transitoire du lanceur (helios-diskfill-*)" if not u else "unites du lanceur presentes : %s" % ", ".join(u))]

    def c_systeme():
        fs = monde.stats()
        ctx["fs"] = fs
        racine = monde.info_chemin("/")
        sorties = [((racine or {}).get("fstype") in TYPES_FS_OK, "systeme de fichiers de / : %s (ext4 ou xfs requis : fallocate sans ecriture)" % (
            (racine or {}).get("fstype"),)),
            (monde.fallocate_present(), "fallocate present")]
        try:
            blocs = planifier(fs)
            sorties.append((True, "occupation %.2f %% ; +%d Mo en %d bloc(s) pour atteindre %.1f %% (plafond %.1f, urgence %.1f)" % (
                fs.usage, sum(blocs) // MIO, len(blocs), CIBLE_PCT, PLAFOND_PCT, URGENCE_PCT)))
        except RefusSecurite as e:
            sorties.append((False, "plan de remplissage impossible : %s" % e))
        seuil = seuil_kubelet(parse_kubelet(monde.config_kubelet()))
        if seuil is None:
            sorties.append((False, "seuils du kubelet inconnus : le ramasse-miettes d'images (air-gap) pourrait se declencher"))
        else:
            sorties.append((URGENCE_PCT <= seuil - MARGE_KUBELET_PCT + 1e-9, "kubelet : action a %.1f %% ; urgence %.1f %% (marge exigee %.1f points)" % (
                seuil, URGENCE_PCT, MARGE_KUBELET_PCT)))
        return sorties

    def c_inodes():
        total, libres = monde.inodes()
        usage = usage_inodes_pct(total, libres)
        return [(usage < INODES_MAX_PCT and libres >= INODES_LIBRES_MIN,
                 "inodes de / : %.1f %% utilises (%d libres sur %d) ; refus des %.0f %% (regle NODE_INODES_FULL) ou sous %d inodes libres" % (
                     usage, libres, total, INODES_MAX_PCT, INODES_LIBRES_MIN))]

    def c_cooldown():
        restant, erreur = cooldown_restant_s(monde, maintenant)
        if erreur:
            return [(False, erreur)]
        if restant is None:
            return [(True, "aucune execution locale precedente")]
        return [(restant == 0, "cooldown local de %d min %s" % (COOLDOWN_LOCAL_S // 60, "ecoule" if restant == 0 else
                                                                  "NON ecoule : encore %d min %d s (relancer la meme commande ensuite : un 2e lancement plus "
                                                                  "rapproche ne serait pas remedie par HELIOS)" % (restant // 60, restant % 60)))]

    def c_prometheus():
        p = lire_prom_noeud(monde)
        if p["illisibles"]:
            return [(False, "Prometheus illisible (requete(s) : %s) : la detection et la verification en dependent" % ", ".join(p["illisibles"]))]
        sorties = [(p["series"] == 1, "Prometheus : %s serie(s) pour la racine de .158 (exactement 1 attendue)" % p["series"])]
        fs = ctx.get("fs")
        if p["usage"] is None or fs is None:
            sorties.append((False, "occupation Prometheus illisible"))
        else:
            ecart = abs(p["usage"] - fs.usage)
            sorties.append((ecart <= ECART_PROM_MAX_PTS, "occupation : Prometheus %.2f %% / noeud %.2f %% (ecart %.2f pt, max %.1f)" % (
                p["usage"], fs.usage, ecart, ECART_PROM_MAX_PTS)))
        sorties.append((p["incident_actif"] == 0, "helios_incident{cible=%s} : %s" % (NOM_CIBLE, "aucun incident actif" if p["incident_actif"] == 0
                                                                                      else "ACTIF ou illisible (%r)" % (p["incident_actif"],))))
        return sorties

    def c_garde():
        essais = 1 if a_blanc else ATTENTE_GARDE_S
        for k in range(essais):
            b = monde.lire_json(F_BATTEMENT)
            if valider_battement(b) and monde.maintenant() - b["epoch"] <= BATTEMENT_MAX_S and monde.pid_vivant(b["pid"]):
                return [(b["jeton"] is None, "garde independante armee et libre (pid %d)" % b["pid"] if b["jeton"] is None
                         else "garde deja affectee a un autre lancement (jeton %s) : refus" % b["jeton"][:8])]
            if k < essais - 1:
                monde.dormir(1)
        return [(absent_ok(False), "garde independante (%s) non armee : aucun remplissage sans elle" % UNITE_GARDE)]

    def c_hote_windows():
        if not ATTESTATION_HOTE_REQUISE:
            return [(None, "attestation de l'hote Windows NON requise (demarrage autonome, comme helios-cpuload.service) : controles du noeud seuls, "
                           "aucune requete vers Windows ; diagnostic facultatif : helios_diskfill_service.py --preflight")]
        if a_blanc:
            return [(None, "a blanc : consultation de l'agent Windows non effectuee (aucun effet)")]
        att = monde.preflight_hote()
        raisons = att.get("reasons") if isinstance(att, dict) else None
        autorise = agent_hote_preflight_autorisable(att.get("verdict"), att.get("go"), raisons)
        return [(autorise, "agent Windows DISK authentifie, attestation fraiche : VERT ou ORANGE explicitement autorise (aucun ROUGE ; ORANGE limites a GEL_HOTE_48H et E2E_INCIDENTS_DISQUE_ANCIENS)")]

    sortie("helios-diskfill : VERIFICATION %s (version %d)" % ("A BLANC" if a_blanc else "DE DEMARRAGE", VERSION))
    for code, fonction in (("INVOCATION", c_invocation), ("IDENTITE", c_identite), ("INSTALLATION", c_installation), ("DOSSIERS", c_dossiers),
                           ("RESIDUS", c_residus), ("SYSTEME", c_systeme), ("INODES", c_inodes), ("COOLDOWN", c_cooldown),
                           ("PROMETHEUS", c_prometheus), ("GARDE", c_garde), ("HOTE_WINDOWS", c_hote_windows)):
        tester(code, fonction)
    _sortie_verif(entrees, sortie)
    refus = [e for e in entrees if e[0] is False]
    if refus:
        if not a_blanc:
            sortie(ligne_resultat("REFUS", CODES_SORTIE["REFUS"], etape="verifier", controles_ko=",".join(sorted(set(e[1] for e in refus))),
                                  alloue=False))
        sortie("helios-diskfill : REFUS : %d controle(s) en echec (%s) ; rien n'a ete cree ni alloue" % (
            len(refus), ", ".join(sorted(set(e[1] for e in refus)))))
        return CODES_SORTIE["REFUS"]
    if a_blanc:
        sortie("helios-diskfill : A BLANC : controles locaux acceptes ; aucun acces reseau Windows, aucune ecriture, aucun fichier cree, aucune allocation")
        return 0
    jeton = monde.nouveau_jeton()
    debut = monde.maintenant()
    etat = {"version": VERSION, "jeton": jeton, "invocation_id": monde.invocation_id(), "cree_epoch": debut, "debut_epoch": debut,
            "echeance_epoch": debut + EXPIRATION_S, "phase": "pret", "noms": noms_prevus(jeton), "verdict": None}
    try:
        for marqueur in (F_MARQUEUR_GARDE, F_MARQUEUR_EXPIRATION, F_MARQUEUR_ARRET_GARDE):
            monde.supprimer(marqueur)                                                # aucun marqueur d'un lancement precedent ne survit
        monde.ecrire_json(F_ETAT, etat)
        monde.ecrire_json(F_COOLDOWN, {"debut_epoch": debut, "jeton8": jeton[:8], "invocation_id": monde.invocation_id()})
    except (ErreurNoeud, OSError) as e:
        sortie(ligne_resultat("REFUS", CODES_SORTIE["REFUS"], etape="verifier", controles_ko="ETAT", alloue=False))
        sortie("helios-diskfill : REFUS : ecriture de l'etat impossible (%s) ; aucun remplissage" % e.__class__.__name__)
        return CODES_SORTIE["REFUS"]
    sortie("helios-diskfill : VERIFICATION OK : demarrage direct ; %s ; "
           "aucun armement requis (aucun armement manuel) ; jeton %s... ; "
           "echeance %s UTC" % ("controles locaux + attestation Windows authentifiee (VERT ou ORANGE autorise)" if ATTESTATION_HOTE_REQUISE
                                else "controles du noeud tous satisfaits (attestation Windows non requise)", jeton[:8], _utc(etat["echeance_epoch"])))
    return 0


def _utc(t):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))


# ------------------------------------------------------------------------------------------------ liberation (noms exacts)
def liberer_tout(monde, noms, sortie=print, tentatives=3):
    """Retire par NOM EXACT ce qui est encore present parmi `noms`. N'appelle AUCUNE suppression si rien ne reste (cas nominal : HELIOS a
    libere). Retourne True si plus aucun des noms n'est present (relecture, pas le code retour d'une commande)."""
    for k in range(tentatives):
        try:
            presents = [n for n in (monde.contenu_dossier() or []) if n in noms]
            if not presents:
                return True
            monde.liberer(presents)
        except (ErreurNoeud, RefusSecurite) as e:
            sortie("helios-diskfill :    liberation : tentative %d/%d echouee (%s)" % (k + 1, tentatives, e))
    try:
        return not [n for n in (monde.contenu_dossier() or []) if n in noms]
    except ErreurNoeud:
        return False


def _ecrire_phase(monde, etat, **champs):
    """Met a jour l'etat de CE lancement (ecriture atomique) ; un echec d'ecriture n'empeche jamais la liberation."""
    etat.update(champs)
    try:
        monde.ecrire_json(F_ETAT, etat)
    except (ErreurNoeud, OSError):
        pass


def attribuer_liberation(monde, etat, maintenant):
    """Qui a retire les fichiers ? 'garde' (marqueur de la garde), 'expiration' (marqueur ou echeance atteinte), sinon 'helios' = AUCUN de nos filets
    n'a tire (HELIOS attendu ; la preuve complete est la base et le journal du Bridge : un tiers resterait indiscernable ici)."""
    try:
        if monde.lire_texte(F_MARQUEUR_GARDE) is not None:
            return "garde"
        if monde.lire_texte(F_MARQUEUR_EXPIRATION) is not None or maintenant >= etat["echeance_epoch"]:
            return "expiration"
    except ErreurNoeud:
        return "indeterminee"
    return "helios"


def garde_vivante(monde, jeton):
    b = monde.lire_json(F_BATTEMENT)
    return (valider_battement(b) and b["jeton"] == jeton and monde.maintenant() - b["epoch"] <= BATTEMENT_MAX_S and monde.pid_vivant(b["pid"]))


def attendre_garde(monde, jeton):
    """La garde doit etre armee POUR CE JETON avant la premiere allocation (sinon rien n'est alloue)."""
    for _ in range(ATTENTE_GARDE_S):
        if monde.arret_demande():
            raise ArretManuel("arret demande avant la premiere allocation")
        if garde_vivante(monde, jeton):
            return
        monde.dormir(1)
    raise RefusSecurite("la garde independante (%s) n'a pas confirme son armement pour ce lancement : aucune allocation" % UNITE_GARDE)


# ------------------------------------------------------------------------------------------------ run
def executer(monde, sortie=print):
    """ExecStart : remplit, observe, verifie, libere TOUJOURS ce qu'il a cree (finally). Retourne le code de sortie du verdict."""
    res = {"verdict": "ERREUR", "raison": "", "alloues": [], "total_alloue": 0, "usage_initial": None, "usage_atteint": None,
           "usage_final": None, "incident_vu": False, "liberee_par": None, "liberation_ok": None, "recupere": None}
    tentes, etat = [], None
    t_debut = monde.maintenant()

    def j(message):
        sortie("helios-diskfill : T+%4ds  %s" % (monde.maintenant() - t_debut, message))
    try:
        etat = monde.lire_json(F_ETAT)
        iid = monde.invocation_id()
        if not valider_etat(etat) or not iid or etat["invocation_id"] != iid or etat["phase"] != "pret":
            etat = None
            raise RefusSecurite("aucun etat de verification pour CE lancement : le programme ne tourne que par l'unite systemd, apres `verifier`")
        jeton, noms = etat["jeton"], noms_prevus(etat["jeton"])
        echeance = etat["echeance_epoch"]
        sortie("helios-diskfill : DEBUT %s UTC ; jeton %s... ; echeance %s UTC" % (_utc(t_debut), jeton[:8], _utc(echeance)))
        sortie("helios-diskfill : pour la preuve complete (hote Windows, lecture seule) : preuves_injection_reelle.py --depuis \"<heure LOCALE du "
               "debut ci-dessus : UTC + 1 h>\"")
        contenu = monde.contenu_dossier()
        if contenu:
            raise RefusSecurite("%s n'est pas vide : residu d'un test precedent" % DOSSIER)
        fs = monde.stats()
        res["usage_initial"] = fs.usage
        planifier(fs)
        j("base : occupation %.2f %%, cible %.1f %%, plafond %.1f %%, urgence %.1f %%, garde %.1f %%" % (fs.usage, CIBLE_PCT, PLAFOND_PCT,
                                                                                                          URGENCE_PCT, GARDE_PCT))
        attendre_garde(monde, jeton)
        j("garde independante armee pour ce jeton (%s) ; echeance dans %d s" % (UNITE_GARDE, int(echeance - monde.maintenant())))
        limite_alloc = etat["debut_epoch"] + BUDGET_ALLOC_S
        _ecrire_phase(monde, etat, phase="remplissage")
        i, derniere = 0, 0.0
        while True:
            if monde.arret_demande():
                raise ArretManuel("arret demande pendant le remplissage")
            t_iter = monde.maintenant()
            fs = monde.stats()                                                      # mesure FRAICHE avant chaque allocation
            if fs.usage >= URGENCE_PCT:
                raise ArretUrgence("occupation %.2f %% >= urgence %.1f %%" % (fs.usage, URGENCE_PCT))
            if fs.usage >= CIBLE_PCT:
                break
            i += 1
            if i > MAX_FICHIERS:
                raise RefusSecurite("trop de blocs alloues sans atteindre la cible")
            bloc = min(arrondi_mio(octets_pour(fs, CIBLE_PCT)), BLOC)
            ok, raison = garde_allocation(fs, bloc, res["total_alloue"])
            if not ok:
                raise RefusSecurite("allocation refusee : %s" % raison)
            if monde.maintenant() + max(derniere, DUREE_BLOC_MIN_S) > limite_alloc:
                raise RefusSecurite("delai insuffisant pour poursuivre les allocations (budget de %d s depuis le demarrage du lancement)" % BUDGET_ALLOC_S)
            nom = nom_fichier(jeton, i)
            tentes.append(nom)                                                      # note AVANT l'appel : une allocation partielle sera retiree
            monde.allouer(nom, bloc)
            res["alloues"].append(nom)
            res["total_alloue"] += bloc
            fs_apres = monde.stats()
            if fs_apres.usage >= URGENCE_PCT:
                raise ArretUrgence("occupation %.2f %% >= urgence %.1f %% apres le bloc %d" % (fs_apres.usage, URGENCE_PCT, i))
            effet = fs.dispo - fs_apres.dispo
            if effet < EFFET_MIN * bloc:
                raise RefusSecurite("allocation sans effet mesure : l'espace disponible n'a baisse que de %d Mo pour %d Mo alloues (< %d %%) : "
                                    "les fichiers ne sont pas sur le volume mesure" % (max(effet, 0) // MIO, bloc // MIO, round(EFFET_MIN * 100)))
            derniere = monde.maintenant() - t_iter
            j("bloc %d : +%d Mo alloues (fallocate), effet mesure +%d Mo, occupation %.2f %%" % (i, bloc // MIO, effet // MIO, fs_apres.usage))
        fs = monde.stats()
        res["usage_atteint"] = fs.usage
        if fs.usage > PLAFOND_PCT:
            raise ArretUrgence("occupation %.2f %% > plafond %.1f %% apres allocation" % (fs.usage, PLAFOND_PCT))
        j("cible atteinte : occupation %.2f %% (detection attendue au prochain cycle du detecteur, jusqu'a ~5 min)" % fs.usage)
        _ecrire_phase(monde, etat, phase="observation")
        ev = suivre(monde, etat, set(noms), j, res)
        res["liberee_par"] = ev["liberee_par"]
        if ev["liberee_par"] == "garde":
            res["verdict"], res["raison"] = "URGENCE", "la garde independante a libere les fichiers (seuil %.1f %% atteint)" % GARDE_PCT
        elif ev["liberee_par"] == "expiration":
            res["verdict"], res["raison"] = "ERREUR", "l'echeance a libere les fichiers pendant l'observation : test invalide"
        elif ev["liberee_par"] == "indeterminee":
            res["verdict"], res["raison"] = "ERREUR", "attribution de la liberation impossible (marqueurs illisibles) : test invalide"
        elif ev["liberee_par"] is None:
            if res["incident_vu"]:
                res["verdict"], res["raison"] = ("DETECTION_SEULE", "incident visible dans Prometheus mais fichiers non liberes dans la fenetre : "
                                                                    "remediation par HELIOS non prouvee (liberation par le service)")
            else:
                res["verdict"], res["raison"] = ("AUCUNE_DETECTION", "aucun incident visible dans Prometheus pendant la fenetre d'observation "
                                                                     "(liberation par le service)")
        elif not ev["retour_confirme"]:
            res["verdict"], res["raison"] = "ERREUR", "fichiers liberes mais occupation non revenue a %.1f %% ou moins dans la fenetre" % RECUPERATION_PCT
        else:
            res["verdict"], res["raison"] = "OK", ("fichiers liberes sans declenchement de la garde ni de l'echeance, occupation <= %.1f %% (attribution a HELIOS "
                                                    "a confirmer par la base et le journal du Bridge)" % RECUPERATION_PCT)
    except ArretUrgence as e:
        res["verdict"], res["raison"] = "URGENCE", str(e)
        sortie("helios-diskfill : !!! ARRET D'URGENCE : %s -> liberation immediate" % e)
    except ArretManuel as e:
        if etat is not None and monde.maintenant() >= etat["echeance_epoch"]:
            # Le signal arrive APRES l'echeance : ce n'est plus un arret de l'exploitant mais un depassement (systemd : RuntimeMaxSec).
            res["verdict"], res["raison"] = "ERREUR", "echeance du lancement atteinte avant la fin (%s) : arret par systemd" % e
            sortie("helios-diskfill : !!! ECHEANCE ATTEINTE : %s -> liberation immediate" % e)
        else:
            res["verdict"], res["raison"] = "ARRET", str(e)
            sortie("helios-diskfill : ARRET DEMANDE : %s -> liberation immediate" % e)
    except RefusSecurite as e:
        res["verdict"], res["raison"] = "REFUS", str(e)
        sortie("helios-diskfill : REFUS DE SECURITE : %s" % e)
    except ErreurNoeud as e:
        res["verdict"], res["raison"] = "ERREUR", str(e)
        sortie("helios-diskfill : ERREUR : %s -> liberation et arret" % e)
    finally:
        if tentes:
            res["liberation_ok"] = liberer_tout(monde, set(tentes), sortie)
        else:
            res["liberation_ok"] = True
        try:
            fs = monde.stats()
            res["usage_final"] = fs.usage
            res["recupere"] = fs.usage <= RECUPERATION_PCT
        except ErreurNoeud:
            res["usage_final"], res["recupere"] = None, None
        if etat is not None:
            _ecrire_phase(monde, etat, phase="terminee" if res["liberation_ok"] else "liberation_incomplete", verdict=res["verdict"])
    if res["verdict"] == "OK" and not (res["recupere"] and res["liberation_ok"]):
        res["verdict"], res["raison"] = "ERREUR", "recuperation (<= %.1f %%) ou liberation non confirmee" % RECUPERATION_PCT
    if etat is not None:
        _ecrire_phase(monde, etat, verdict=res["verdict"])                           # verdict FINAL (apres la derniere correction)
    sortie("helios-diskfill : BILAN : %s - %s" % (res["verdict"], res["raison"]))
    f = lambda v: "?" if v is None else "%.2f %%" % v
    sortie("helios-diskfill :   occupation : initiale %s | atteinte %s | finale %s (retour <= %.1f %% : %s)" % (
        f(res["usage_initial"]), f(res["usage_atteint"]), f(res["usage_final"]), RECUPERATION_PCT,
        {True: "OUI", False: "NON", None: "inconnu"}[res["recupere"]]))
    sortie("helios-diskfill :   blocs alloues : %d (%d Mo cumules, plafond %d Mo) | incident vu dans Prometheus : %s | liberation par : %s | "
           "liberation verifiee : %s" % (len(res["alloues"]), res["total_alloue"] // MIO, MAX_TOTAL // MIO, "oui" if res["incident_vu"] else "non",
                                         res["liberee_par"] or "le service (securite)",
                                         {True: "OUI", False: "NON", None: "inconnue"}[res["liberation_ok"]]))
    code = CODES_SORTIE.get(res["verdict"], 1)
    # LIGNE FINALE, unique et greppable, du journal systemd (la liberation est verifiee par relecture AVANT cette ligne).
    sortie(ligne_resultat(res["verdict"], code, etape="run", jeton=etat["jeton"][:8] if etat is not None else None, incident_vu=res["incident_vu"],
                          liberee_par="aucune" if not tentes else "helios_infere" if res["liberee_par"] == "helios" else res["liberee_par"] or "service",
                          usage_initial=res["usage_initial"],
                          usage_atteint=res["usage_atteint"], usage_final=res["usage_final"], recupere=res["recupere"],
                          liberation_verifiee=res["liberation_ok"], blocs=len(res["alloues"]), alloue_mo=res["total_alloue"] // MIO,
                          duree_s=int(monde.maintenant() - t_debut)))
    return code


def suivre(monde, etat, noms, j, res):
    """Observation : occupation toutes les PAS_S s (urgence), Prometheus (informatif) toutes les PAS_SONDE_S s, battement de la garde. Se termine
    quand les fichiers ont disparu ET que l'occupation est confirmee <= 77 % (2 mesures consecutives), ou a la fin de la fenetre.
    Retourne {liberee_par, retour_confirme}."""
    t0 = monde.maintenant()
    fin = min(t0 + OBSERVE_S, etat["echeance_epoch"] - MARGE_FIN_S)
    if fin - t0 < OBSERVE_MIN_S:
        raise RefusSecurite("fenetre d'observation insuffisante (%d s < %d s) avant l'echeance" % (fin - t0, OBSERVE_MIN_S))
    ev = {"liberee_par": None, "retour_confirme": False}
    sains, dernier_prom, perdue_depuis = 0, t0 - PAS_SONDE_S, None
    while monde.maintenant() < fin:
        if monde.arret_demande():
            raise ArretManuel("arret demande pendant l'observation")
        monde.dormir(PAS_S)
        fs = monde.stats()
        if fs.usage >= URGENCE_PCT:
            raise ArretUrgence("occupation %.2f %% >= urgence %.1f %% pendant le suivi" % (fs.usage, URGENCE_PCT))
        presents = [n for n in (monde.contenu_dossier() or []) if n in noms]
        maintenant = monde.maintenant()
        if maintenant - dernier_prom >= PAS_SONDE_S:
            dernier_prom = maintenant
            try:
                inc = lire_prom_noeud(monde)["incident_actif"]
            except (ErreurNoeud, KeyError):
                inc = None
            if inc and not res["incident_vu"]:
                res["incident_vu"] = True
                j("incident HELIOS visible dans Prometheus (helios_incident{cible=%s} = %d)" % (NOM_CIBLE, inc))
            elif inc is None and not res.get("prom_signale"):
                res["prom_signale"] = True
                j("Prometheus illisible (informatif : la remediation ne depend pas de cette lecture)")
        if presents:
            if not garde_vivante(monde, etat["jeton"]):
                perdue_depuis = perdue_depuis or maintenant
                if maintenant - perdue_depuis > BATTEMENT_MAX_S:
                    raise ErreurNoeud("garde independante PERDUE (plus de battement valide depuis %d s) : filet de securite absent" % BATTEMENT_MAX_S)
            else:
                perdue_depuis = None
            continue
        if ev["liberee_par"] is None:
            ev["liberee_par"] = attribuer_liberation(monde, etat, maintenant)
            res["liberee_par"] = ev["liberee_par"]                                   # visible au bilan meme si un arret survient avant le retour
            j("fichiers de test liberes (occupation %.2f %%) par : %s%s" % (fs.usage, ev["liberee_par"],
                                                                         " (aucun de nos filets n'a tire : HELIOS attendu)" if ev["liberee_par"] == "helios" else ""))
            if ev["liberee_par"] != "helios":
                return ev
        sains = sains + 1 if fs.usage <= RECUPERATION_PCT else 0
        if sains >= CONFIRMATIONS_RETOUR:
            ev["retour_confirme"] = True
            j("retour a %.2f %% confirme (%d mesures consecutives <= %.1f %%)" % (fs.usage, sains, RECUPERATION_PCT))
            return ev
    return ev


# ------------------------------------------------------------------------------------------------ garde (unite independante)
def _arret_oisif_demande(monde, t0):
    """Garde OISIVE (aucun lancement annonce) : `nettoyer` a-t-il signale, APRES notre demarrage, que le demarrage du service a ete refuse ?
    Le marqueur est ignore s'il est plus ancien que la garde, et tant que le service principal est occupe (un nouveau `systemctl start` peut deja
    etre en cours : la garde reste alors armee pour lui). Lecture impossible = on reste (le delai d'oisivete borne l'attente). Ne concerne JAMAIS
    une garde qui garde un lancement : l'appelant ne l'interroge que sans jeton."""
    try:
        marque = monde.lire_json(F_MARQUEUR_ARRET_GARDE)
        if not (isinstance(marque, dict) and _nombre(marque.get("epoch")) and marque["epoch"] >= t0):
            return False
        return not monde.unite_occupee(UNITE_PRINCIPALE)
    except (ErreurNoeud, OSError):
        return False


def garde_tour(monde, t0, sortie=print):
    """UN tour de la garde (pur sur un monde : l'emulateur des tests l'appelle a chaque pas de temps simule). Retourne None (continuer) ou le code
    de sortie (fin). Bat (battement), puis libere par noms exacts si l'occupation atteint GARDE_PCT ou si l'echeance du lancement est atteinte.
    N'adopte QUE l'etat d'un lancement cree APRES son propre demarrage `t0` : jamais celui d'un lancement precedent (aucun nettoyage automatique
    au demarrage)."""
    maintenant = monde.maintenant()
    etat, jeton = None, None
    try:
        lu = monde.lire_json(F_ETAT)
        if valider_etat(lu) and lu["cree_epoch"] >= t0 - 5:
            etat, jeton = lu, lu["jeton"]
    except ErreurNoeud:
        pass
    try:
        monde.ecrire_json(F_BATTEMENT, {"pid": monde.pid(), "jeton": jeton, "epoch": maintenant, "version": VERSION})
    except (ErreurNoeud, OSError) as e:
        sortie("helios-diskfill : garde : ecriture du battement impossible (%s)" % e.__class__.__name__)
    if jeton is None:
        if _arret_oisif_demande(monde, t0):
            sortie("helios-diskfill : garde : demarrage du service refuse, aucun lancement : fin (rien a garder)")
            return 0
        if maintenant - t0 > GARDE_ATTENTE_ETAT_S:
            sortie("helios-diskfill : garde : aucun lancement annonce depuis %d s : fin (rien a garder)" % GARDE_ATTENTE_ETAT_S)
            return 0
        return None
    if etat["phase"] == "terminee":
        sortie("helios-diskfill : garde : lancement termine et libere : fin du filet")
        return 0
    noms = set(noms_prevus(jeton))
    try:
        presents = [n for n in (monde.contenu_dossier() or []) if n in noms]
        usage = monde.stats().usage
    except ErreurNoeud as e:
        sortie("helios-diskfill : garde : mesure impossible (%s)" % e)
        presents, usage = [], 0.0
    if presents and usage >= GARDE_PCT:
        sortie("helios-diskfill : garde : !!! occupation %.2f %% >= %.1f %% : liberation par noms exacts (%d fichier(s))" % (usage, GARDE_PCT, len(presents)))
        _declencher(monde, F_MARQUEUR_GARDE, usage, presents, sortie)
        return 0
    if maintenant >= etat["echeance_epoch"]:
        if presents:
            sortie("helios-diskfill : garde : !!! echeance du lancement atteinte : liberation par noms exacts (%d fichier(s))" % len(presents))
            _declencher(monde, F_MARQUEUR_EXPIRATION, usage, presents, sortie)
        else:
            sortie("helios-diskfill : garde : echeance atteinte, aucun fichier restant : fin")
        return 0
    return None


def garde(monde, sortie=print):
    """Filet INDEPENDANT du service principal : un tour toutes les GARDE_PAS_S s (voir garde_tour)."""
    if not monde.invocation_id():
        sortie("helios-diskfill : garde : REFUS de tourner hors d'une unite systemd")
        return CODES_SORTIE["REFUS"]
    t0 = monde.maintenant()
    sortie("helios-diskfill : garde : demarree %s UTC (seuil %.1f %%, pas %d s)" % (_utc(t0), GARDE_PCT, GARDE_PAS_S))
    while True:
        if monde.arret_demande():
            sortie("helios-diskfill : garde : arret demande")
            return 0
        code = garde_tour(monde, t0, sortie)
        if code is not None:
            return code
        monde.dormir(GARDE_PAS_S)

def _declencher(monde, marqueur, usage, presents, sortie):
    try:
        monde.ecrire_json(marqueur, {"epoch": monde.maintenant(), "usage": round(usage, 2), "fichiers": len(presents)})
    except (ErreurNoeud, OSError):
        pass
    liberer_tout(monde, set(presents), sortie)


# ------------------------------------------------------------------------------------------------ nettoyer (ExecStopPost)
def _signaler_refus_a_la_garde(monde):
    """Demarrage REFUSE (aucun etat de CE lancement) : previent la garde OISIVE qu'elle peut sortir (sinon elle resterait active jusqu'a
    GARDE_ATTENTE_ETAT_S et le pre-vol suivant verrait une unite active). Sans effet sur une garde qui garde un lancement. Echec d'ecriture : ignore."""
    try:
        monde.ecrire_json(F_MARQUEUR_ARRET_GARDE, {"epoch": monde.maintenant(), "invocation_id": monde.invocation_id()})
    except (ErreurNoeud, OSError):
        pass


def nettoyer(monde, sortie=print):
    """ExecStopPost : libere par noms exacts les fichiers de CE lancement. Ne touche JAMAIS ce qui appartient a un lancement precedent : sans etat
    portant l'INVOCATION_ID courant, il ne fait rien (aucun nettoyage automatique des orphelins) ; il signale seulement a la garde oisive que le
    demarrage a ete refuse."""
    iid = monde.invocation_id()
    try:
        etat = monde.lire_json(F_ETAT)
    except ErreurNoeud:
        etat = None
    if not iid or not valider_etat(etat) or etat["invocation_id"] != iid:
        if iid:
            _signaler_refus_a_la_garde(monde)
        sortie("helios-diskfill : nettoyer : aucun etat de CE lancement : rien a faire (aucun nettoyage automatique d'un lancement precedent)")
        return 0
    if etat["phase"] == "terminee":
        sortie("helios-diskfill : nettoyer : lancement deja termine et libere")
        return 0
    noms = set(noms_prevus(etat["jeton"]))
    ok = liberer_tout(monde, noms, sortie)
    _ecrire_phase(monde, etat, phase="terminee" if ok else "liberation_incomplete")
    if etat.get("verdict") is None:
        # `run` n'a rendu AUCUN verdict (processus tue, plante ou arrete avant son bilan) : cette ligne est alors la seule trace finale du journal.
        # Elle precede le message a l'exploitant ci-dessous, qui reste la DERNIERE ligne (la consigne en cas de liberation non verifiee).
        try:
            usage = monde.stats().usage
        except ErreurNoeud:
            usage = None
        sortie(ligne_resultat("INTERROMPU", CODES_SORTIE["ERREUR"], etape="nettoyer", jeton=etat["jeton"][:8], liberation_verifiee=ok,
                              usage_final=usage, recupere=None if usage is None else usage <= RECUPERATION_PCT))
    if ok:
        sortie("helios-diskfill : nettoyer : liberation verifiee (plus aucun fichier du jeton %s...)" % etat["jeton"][:8])
    else:
        sortie("helios-diskfill : nettoyer : !!! LIBERATION NON VERIFIEE : la garde (%s) reste armee et liberera a son seuil ou a l'echeance ; "
               "sinon : sudo /usr/bin/python3 -I -B %s nettoyer-orphelins" % (UNITE_GARDE, CHEMIN_SCRIPT))
    return 0 if ok else 1


# ------------------------------------------------------------------------------------------------ etat (lecture seule)
def faits_noeud(monde):
    """Faits du noeud, en LECTURE SEULE (aucun effet). Une lecture impossible = None (jamais une valeur devinee)."""
    def lire(f):
        try:
            return f()
        except Exception:                                                            # noqa: BLE001
            return None
    maintenant = monde.maintenant()
    faits = {"version": VERSION, "epoch": maintenant, "hostname": lire(lambda: nom_court(monde.hostname())),
             "ip_presente": lire(lambda: IP_CIBLE in monde.ips_locales()), "boot_id": lire(monde.boot_id),
             "chemin_script": lire(monde.chemin_script), "sha256": lire(lambda: shas_noeud(monde)),
             "unites": dict((u, lire(lambda u=u: monde.unite_proprietes(u, PROPRIETES_UNITE))) for u in (UNITE_PRINCIPALE, UNITE_GARDE)),
             "unites_occupees": dict((u, lire(lambda u=u: monde.unite_occupee(u))) for u in (UNITE_PRINCIPALE, UNITE_GARDE)),
             "unites_transitoires": lire(monde.unites_transitoires), "contenu_dossier": lire(monde.contenu_dossier),
             "usage_pct": lire(lambda: round(monde.stats().usage, 2)),
             "inodes_pct": lire(lambda: round(usage_inodes_pct(*monde.inodes()), 1)),
             "attestation_hote_requise": bool(ATTESTATION_HOTE_REQUISE)}
    racine = lire(lambda: monde.info_chemin("/"))
    d = lire(lambda: monde.info_chemin(DOSSIER))
    faits["dossier"] = {"existe": d is not None, "anomalies": anomalies_dossier(d, racine) if d is not None and racine else None, "info": d}
    faits["dossier_etat"] = lire(lambda: monde.info_chemin(DOSSIER_ETAT))
    faits["run_dir"] = lire(lambda: monde.info_chemin(RUN_DIR))
    etat = lire(lambda: monde.lire_json(F_ETAT))
    faits["etat"] = ({"phase": etat.get("phase"), "jeton8": str(etat.get("jeton"))[:8], "verdict": etat.get("verdict"),
                      "echeance_epoch": etat.get("echeance_epoch")} if valider_etat(etat) else None)
    bat = lire(lambda: monde.lire_json(F_BATTEMENT))
    faits["battement"] = ({"age_s": round(maintenant - bat["epoch"], 1), "jeton8": str(bat.get("jeton"))[:8] if bat.get("jeton") else None,
                           "pid": bat["pid"]} if valider_battement(bat) else None)
    restant, erreur = cooldown_restant_s(monde, maintenant)
    faits["cooldown"] = {"restant_s": restant, "erreur": erreur, "local_min": COOLDOWN_LOCAL_S // 60}
    return faits


def etat_cmd(monde, en_json, sortie=print):
    f = faits_noeud(monde)
    if en_json:
        sortie(json.dumps(f, sort_keys=True))
        return 0
    sortie("helios-diskfill : ETAT (lecture seule) version %d, noeud %s, %s UTC" % (VERSION, f["hostname"], _utc(f["epoch"])))
    sortie("   occupation de / : %s %% ; repertoire de test : %s" % (f["usage_pct"], "absent" if not f["dossier"]["existe"] else
                                                                     "%d entree(s)" % len(f["contenu_dossier"] or [])))
    sortie("   inodes de / : %s %%" % f["inodes_pct"])
    sortie("   attestation de l'hote Windows : %s" % ("REQUISE (echec ferme si l'agent est absent)" if f["attestation_hote_requise"]
                                                     else "non requise (demarrage autonome, comme helios-cpuload.service)"))
    sortie("   dernier lancement : %s ; cooldown local : %s" % (f["etat"], "ecoule" if not f["cooldown"]["restant_s"] else "encore %d s" % f["cooldown"]["restant_s"]))
    sortie("   unites occupees : %s ; garde : %s" % (f["unites_occupees"], f["battement"]))
    return 0


# ------------------------------------------------------------------------------------------------ nettoyer-orphelins (MANUEL)
def orphelins(monde, confirmer=None, sortie=print):
    """Nettoyage MANUEL des fichiers orphelins (jamais automatique). Plan par defaut ; suppression seulement avec --confirmer <texte exact>."""
    for u in (UNITE_PRINCIPALE, UNITE_GARDE):
        try:
            if monde.unite_occupee(u):
                sortie("helios-diskfill : REFUS : %s est active : arreter le lancement en cours d'abord (sudo systemctl stop %s)" % (u, UNITE_PRINCIPALE))
                return CODES_SORTIE["REFUS"]
        except ErreurNoeud as e:
            sortie("helios-diskfill : REFUS : etat de %s illisible (%s)" % (u, e))
            return CODES_SORTIE["REFUS"]
    info, racine = monde.info_chemin(DOSSIER), monde.info_chemin("/")
    if info is None:
        sortie("helios-diskfill : %s absent : aucun orphelin" % DOSSIER)
        return 0
    anomalies = anomalies_dossier(info, racine)
    if anomalies:
        sortie("helios-diskfill : REFUS : repertoire non conforme : %s" % " ; ".join(anomalies))
        return CODES_SORTIE["REFUS"]
    entrees = monde.entrees_dossier() or []
    plan = plan_orphelins(entrees)
    if plan["refus"]:
        sortie("helios-diskfill : REFUS : %s ; rien n'est supprime" % plan["refus"])
        return CODES_SORTIE["REFUS"]
    if not plan["fichiers"]:
        sortie("helios-diskfill : aucun fichier du scenario a supprimer (%d entree(s) etrangere(s), jamais touchees)" % plan["etrangers"])
        return 0
    texte = "SUPPRIMER-ORPHELINS-%s" % plan["jeton"][:8]
    sortie("helios-diskfill : PLAN : %d fichier(s) du jeton %s... (%d Mo), %d entree(s) etrangere(s) intacte(s)" % (
        len(plan["fichiers"]), plan["jeton"][:8], plan["taille"] // MIO, plan["etrangers"]))
    if confirmer != texte:
        sortie("helios-diskfill : rien n'a ete supprime. Pour les supprimer (noms exacts, un par un) : ... nettoyer-orphelins --confirmer %s" % texte)
        return 0
    ok = liberer_tout(monde, set(plan["fichiers"]), sortie)
    fs = monde.stats()
    sortie("helios-diskfill : %s ; occupation de / : %.2f %%" % ("liberation verifiee" if ok else "LIBERATION NON VERIFIEE", fs.usage))
    return 0 if ok else 1


# ------------------------------------------------------------------------------------------------ ligne de commande
def construire_parseur():
    ap = argparse.ArgumentParser(description="HELIOS - remplissage disque de laboratoire (.158). Aucune option de seuil.", allow_abbrev=False)
    sp = ap.add_subparsers(dest="commande")
    v = sp.add_parser("verifier", allow_abbrev=False)
    v.add_argument("--a-blanc", action="store_true", dest="a_blanc")
    sp.add_parser("run", allow_abbrev=False)
    sp.add_parser("garde", allow_abbrev=False)
    sp.add_parser("nettoyer", allow_abbrev=False)
    e = sp.add_parser("etat", allow_abbrev=False)
    e.add_argument("--json", action="store_true", dest="en_json")
    o = sp.add_parser("nettoyer-orphelins", allow_abbrev=False)
    o.add_argument("--confirmer", default=None)
    return ap


def principal(argv=None, monde=None, sortie=print):
    ap = construire_parseur()
    a = ap.parse_args(argv)
    if not a.commande:
        ap.print_usage()
        return CODES_SORTIE["REFUS"]
    monde = monde or MondeReel()
    if a.commande in ("run", "garde"):
        installer = getattr(monde, "installer_signaux", None)
        if installer:
            installer()
    if a.commande == "verifier":
        return verifier(monde, a.a_blanc, sortie)
    if a.commande == "run":
        return executer(monde, sortie)
    if a.commande == "garde":
        return garde(monde, sortie)
    if a.commande == "nettoyer":
        return nettoyer(monde, sortie)
    if a.commande == "etat":
        return etat_cmd(monde, a.en_json, sortie)
    return orphelins(monde, a.confirmer, sortie)


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except Exception:                                                                # noqa: BLE001
        pass
    sys.exit(principal())
