#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""HELIOS - scenario DISK E2E deterministe `DISK_E2E_158` (cible .158 = k8s-worker1).

    python helios_disk_e2e.py --coherence   # verifie, sans reseau, que Bridge / Backend / outil / detecteur partagent les memes constantes
    python helios_disk_e2e.py --demo        # demonstration SIMULEE du chemin nominal (vrai code du Bridge, noeud et Backend simules)
    python helios_disk_e2e.py --refus       # idem avec un prerequis manquant : aucune charge, aucun incident

Lancement REEL (seul chemin) : python helios_disk_test.py --target 158 --go --confirm DISK-158-81-82-83 --image-backend TAG
Le lanceur charge ce module et lui fait evaluer la PORTE ci-dessous AVANT toute injection (aucun contournement possible pour .158).

Chemin nominal obligatoire (jamais une escalade) :  DETECTION -> INCIDENT -> HOST_RECLAIM_DISK -> BRIDGE -> VERIFY -> RESOLVED

Ce module ne fait LUI-MEME aucune entree/sortie reelle : ni ssh, ni kubectl, ni sous-processus (verifie par test). La collecte reelle des
faits (`collecter_faits_e2e`, LECTURE SEULE) passe par l'executeur du lanceur ; aucun remplissage, aucun deploiement, aucune ecriture.
Il COMPOSE l'outil d'injection existant (helios_disk_test.py) avec :
  1. des PARAMETRES FIGES (cible 81 %, plafond 82 %, urgence 83 %, garde 83,5 %, detection > 80 %, recuperation <= 77 %, 6 Gio) ;
  2. une GARDE de prerequis plus stricte que le pre-vol de l'outil (Bridge et Backend prets et a la bonne version, drapeau DEDIE du
     scenario actif et ancien drapeau ferme, protection systemd, cooldown du Backend, aucun incident disque concurrent, RAM et pages de
     l'hote) : au moindre doute, AUCUNE charge n'est injectee et AUCUN incident n'est cree ;
  3. une SIMULATION dans laquelle le VRAI code du Bridge (infra/helios-sot/bridge/bridge.py) s'execute derriere son vrai gestionnaire HTTP,
     contre un noeud simule, pilote par un modele du Backend dont chaque constante est verifiee contre les sources Java (--coherence).
Un echec reel de mesure ou de verification n'est JAMAIS transforme en RESOLVED.
"""
import argparse
import hashlib
import importlib.util
import json
import os
import re
import secrets
import shlex
import sys
import threading
import urllib.error
import urllib.request
from collections import namedtuple
from http.server import HTTPServer
from types import SimpleNamespace

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import helios_disk_test as H  # noqa: E402

# ------------------------------------------------------------------------------------------------ parametres FIGES du scenario
SCENARIO = "DISK_E2E_158"
CLE = "158"
NOEUD_K8S = "k8s-worker1"
NOEUD_BRIDGE = "worker1"
IP = "192.168.148.158"
NS = "k8s-nodes"
TYPE_INCIDENT = "NODE_DISK_FULL"
ACTION = "HOST_RECLAIM_DISK"
CHEMIN_NOMINAL = ("DETECTION", "INCIDENT", "HOST_RECLAIM_DISK", "BRIDGE", "VERIFY", "RESOLVED")

DETECTION_PCT = 80.0          # la regle NODE_DISK_FULL declenche pour une occupation STRICTEMENT superieure
CIBLE_PCT = 81.0              # injection
PLAFOND_PCT = 82.0
URGENCE_PCT = 83.0            # arret d'urgence de l'outil
GARDE_PCT = 83.5              # garde systeme cote noeud (urgence + 0,5 point)
RECUPERATION_PCT = 77.0       # recuperation verifiee : occupation <= 77 %
DOSSIER = "/var/lib/helios-diskfill"
MAX_TOTAL_OCTETS = 6 * 1024 ** 3
MAX_FICHIERS = 16
BLOC_MO = 512
OBSERVE_S = 720
EXPIRATION_S = 1020
COOLDOWN_DEFAUT_MIN = 30      # remediation.cooldown-minutes (REMEDIATION_COOLDOWN_MINUTES) : defaut de RemediationProperties si la cle est absente
COOLDOWN_BACKEND_S = COOLDOWN_DEFAUT_MIN * 60
MARGE_COOLDOWN_S = 120
ECHANTILLONS_SAINS = 2
HOTE_RAM_MIN_MO = 2500        # seuil de travail : RAM disponible de l'hote Windows avant tout test (memoire du projet, 11 nov. 2026)
HOTE_PAGES_MAX_S = 5000       # pages lues sur disque par seconde (pagination) : au-dela, l'hote gele les VM

# Drapeaux du Backend (ConfigMap backend-config) : le scenario a SON drapeau ; l'ancien drapeau global doit rester ferme.
DRAPEAU_SCENARIO = "HOST_DISK_E2E_158_ENABLED"
DRAPEAU_ANCIEN = "HOST_DISK_REMEDIATION_ENABLED"
DRAPEAU_BRIDGE = "HELIOS_BRIDGE_ENABLED"
CLE_COOLDOWN = "REMEDIATION_COOLDOWN_MINUTES"
NS_PFE = "pfe"
DEPLOIEMENT_BACKEND = "backend"
CONTENEUR_BACKEND = "backend"
SELECTEUR_BACKEND = "app=backend"
IP_BRIDGE = "192.168.148.161"
BRIDGE_DEPLOYE = "/opt/helios-ssh-bridge/bridge.py"
URL_ACTIONS_BRIDGE = "http://127.0.0.1:8099/actions"
URL_ACTION_BRIDGE = "http://127.0.0.1:8099/action"
FICHIER_ENV_BRIDGE = "/etc/helios/bridge.env"            # lu SUR .161 (sudo) pour la sonde ; la valeur ne quitte jamais .161
CODE_SONDE = "E2E_BRIDGE_SONDE"
CODE_SONDE_MESURE = "E2E_BRIDGE_MESURE"
ECART_MESURE_MAX_PTS = 3.0     # ecart max entre l'occupation de / lue par le Bridge (sa liaison ssh) et celle lue par le lanceur
TYPES_DISQUE = ("NODE_DISK_FULL", "NODE_DISK_PRESSURE", "NODE_INODES_FULL")
CODE_INCIDENTS_ANCIENS = "E2E_INCIDENTS_DISQUE_ANCIENS"
AGE_INCIDENT_DISQUE_ANCIEN_S = 48 * 3600
NOEUDS_DISQUE_E2E = (NOEUD_K8S, "k8s-worker2")       # ORANGE acquittable (declare dans helios_disk_test.CODES_ORANGE)

BRIDGE_SOT = os.path.join(REPO, "infra", "helios-sot", "bridge", "bridge.py")
_JAVA = os.path.join(REPO, "backend", "backend", "src", "main", "java", "com", "orange", "pfe", "backend")
_REM = os.path.join(_JAVA, "service", "remediation")
SOURCES = {
    "bridge": BRIDGE_SOT,
    "java_executeur": os.path.join(_REM, "KubernetesRemediationExecutor.java"),
    "java_client": os.path.join(_REM, "RestTemplateBridgeClient.java"),
    "java_verification": os.path.join(_REM, "HostDiskVerificationService.java"),
    "java_politique": os.path.join(_REM, "RemediationPolicyService.java"),
    "java_scenario": os.path.join(_REM, "HostDiskScenario.java"),
    "java_orchestrateur": os.path.join(_JAVA, "service", "HostDiskIncidentRemediationOrchestrator.java"),
    "java_worker": os.path.join(_JAVA, "service", "CamundaJobWorkerLifecycle.java"),
    "java_proprietes": os.path.join(_JAVA, "config", "RemediationProperties.java"),
    "application_properties": os.path.join(REPO, "backend", "backend", "src", "main", "resources", "application.properties"),
    "detecteur": os.path.join(HERE, "detecter.candidate.py"),
}


def parametres():
    """Parametres FIGES du scenario -> helios_disk_test.Params (valides par l'outil lui-meme). Aucune surcharge n'existe."""
    return H.valider_parametres(cible=CIBLE_PCT, plafond=PLAFOND_PCT, urgence=URGENCE_PCT, recuperation=RECUPERATION_PCT,
                                bloc=BLOC_MO * H.MIO, observe_s=OBSERVE_S, expiration_s=EXPIRATION_S)


def sha256_fichier(chemin):
    with open(chemin, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def sha256_bridge_sot():
    return sha256_fichier(BRIDGE_SOT)


# ------------------------------------------------------------------------------------------------ coherence inter-composants
def lire_sources():
    out = {}
    for cle, chemin in SOURCES.items():
        with open(chemin, encoding="utf-8") as f:
            out[cle] = f.read()
    return out


def _valeur(texte, motif, flags=re.M):
    m = re.search(motif, texte, flags)
    return m.group(1) if m else None


def verifier_coherence(sources=None):
    """Liste de (nom, ok, detail). Chaque constante partagee entre l'outil, le Bridge, le Backend Java et le detecteur doit etre
    IDENTIQUE : une derive (ex. seuil 85 d'un cote, 77 de l'autre) fait echouer cette verification. PURE si `sources` est fourni."""
    s = lire_sources() if sources is None else sources
    r = []

    def ajouter(nom, ok, detail=""):
        r.append((nom, bool(ok), detail))

    def egal(nom, texte, motif, attendu, flags=re.M):
        v = _valeur(texte, motif, flags)
        ajouter(nom, v is not None and v == attendu, "lu %r, attendu %r" % (v, attendu))

    p = parametres()
    ajouter("outil : cible/plafond/urgence/recuperation = 81/82/83/77",
            (p.cible, p.plafond, p.urgence, p.recuperation) == (CIBLE_PCT, PLAFOND_PCT, URGENCE_PCT, RECUPERATION_PCT), repr(tuple(p)[:4]))
    ajouter("outil : detection > 80", H.DETECTION_PCT == DETECTION_PCT, "H.DETECTION_PCT=%r" % H.DETECTION_PCT)
    ajouter("outil : garde = urgence + 0,5 = 83,5 %", H.seuil_garde_x100(p) == int(round(GARDE_PCT * 100)), "x100=%r" % H.seuil_garde_x100(p))
    ajouter("outil : 6 Gio et 16 fichiers maximum", H.MAX_TOTAL == MAX_TOTAL_OCTETS and H.MAX_FICHIERS == MAX_FICHIERS,
            "%r / %r" % (H.MAX_TOTAL, H.MAX_FICHIERS))
    ajouter("outil : repertoire exclusif", H.DOSSIER == DOSSIER, repr(H.DOSSIER))
    ajouter("outil : cible 158 = k8s-worker1 / 192.168.148.158", H.CIBLES.get(CLE) == (NOEUD_K8S, IP), repr(H.CIBLES.get(CLE)))

    b = s["bridge"]
    egal("Bridge : action", b, r'^ACTION_DISQUE = "([A-Z_]+)"', ACTION)
    egal("Bridge : scenario", b, r'^DISQUE_SCENARIO = "([A-Z0-9_]+)"', SCENARIO)
    egal("Bridge : noeud unique", b, r'^DISQUE_NOEUD = "([a-z0-9]+)"', NOEUD_BRIDGE)
    egal("Bridge : cible Kubernetes unique", b, r'^DISQUE_CIBLE_K8S = "([a-z0-9-]+)"', NOEUD_K8S)
    egal("Bridge : type d'incident unique", b, r'^DISQUE_TYPE = "([A-Z_]+)"', TYPE_INCIDENT)
    egal("Bridge : namespace", b, r'^DISQUE_NS = "([a-z0-9-]+)"', NS)
    egal("Bridge : repertoire exclusif", b, r'^DISQUE_DOSSIER = "([^"]+)"', DOSSIER)
    egal("Bridge : recuperation <= 77 %", b, r"^DISQUE_RECUPERATION_PCT = ([0-9.]+)", "77.0")
    egal("Bridge : 16 fichiers maximum", b, r"^DISQUE_MAX_FICHIERS = ([0-9]+)", str(MAX_FICHIERS))
    ajouter("Bridge : 6 Gio maximum", re.search(r"^DISQUE_MAX_TOTAL = 6 \* 1024 \* 1024 \* 1024", b, re.M) is not None)
    ajouter("Bridge : la politique par type est INCHANGEE (NODE_DISK_FULL -> HOST_IMAGE_GC : le Bridge ne CHOISIT pas)",
            re.search(r'"NODE_DISK_FULL":\s+"HOST_IMAGE_GC"', b) is not None)

    sc = s["java_scenario"]
    egal("Java scenario : nom", sc, r'String NAME = "([A-Z0-9_]+)"', SCENARIO)
    egal("Java scenario : namespace", sc, r'String NAMESPACE = "([a-z0-9-]+)"', NS)
    egal("Java scenario : cible unique", sc, r'String TARGET = "([a-z0-9-]+)"', NOEUD_K8S)
    egal("Java scenario : type d'incident unique", sc, r'String INCIDENT_TYPE = "([A-Z_]+)"', TYPE_INCIDENT)
    egal("Java scenario : recuperation <= 77 %", sc, r"RECOVERY_THRESHOLD_PERCENT = ([0-9.]+);", "77.0")
    v = s["java_verification"]
    ajouter("Java verification : seuil d'origine INCHANGE hors scenario (defaut 85.0)", "recovery-threshold-percent:85.0}" in v)
    ajouter("Java verification : 77 % lu dans le scenario, applique SEULEMENT si le drapeau du scenario est actif",
            "HostDiskScenario.RECOVERY_THRESHOLD_PERCENT" in v and "scenarioEnabled && HostDiskScenario.matchesTargetAndType(target, type)" in v
            and "remediation.host-disk-e2e158-enabled:false}" in v)
    egal("Java verification : seuil des inodes inchange", v, r"INODES_RECOVERY_THRESHOLD_PERCENT = ([0-9.]+);", "85.0")
    e = s["java_executeur"]
    egal("Java executeur : scenario", e, r'DISK_E2E_SCENARIO = "([A-Z0-9_]+)"', SCENARIO)
    egal("Java executeur : cible", e, r'DISK_E2E_TARGET = "([a-z0-9-]+)"', NOEUD_K8S)
    egal("Java executeur : type", e, r'DISK_E2E_TYPE = "([A-Z_]+)"', TYPE_INCIDENT)
    m_exec = re.search(r"RemediationExecutionResult executeHostDiskReclaim\((.*?)RemediationExecutionResult executeLegacyHostReclaim\(", e, re.S)
    methode = m_exec.group(1) if m_exec else ""
    ajouter("Java executeur : NODE_DISK_FULL hors scenario refuse (.155 jamais) ; PRESSURE / INODES gardent la precedence d'origine "
            "(ancien drapeau en tete, memes cibles)",
            bool(methode) and "if (DISK_E2E_TYPE.equals(type)) {" in methode and "k8s-worker2/.155 jamais" in methode
            and "(!scenario && !properties.isHostDiskRemediationEnabled())" in methode
            and 'Set.of("k8s-worker1", "k8s-worker2")' in methode,
            "methode executeHostDiskReclaim %s" % ("lue" if methode else "INTROUVABLE"))
    bloc = re.search(r"if \(DISK_E2E_TARGET\.equals\(canonicalTarget\) && DISK_E2E_TYPE\.equals\(type\)\) \{(.*?)\n        \}\n", e, re.S)
    corps = bloc.group(1) if bloc else ""
    ajouter("Java executeur : le scenario passe par le Bridge, sous son drapeau dedie, SANS repli vers le pod hote prive",
            bool(corps) and "properties.isHostDiskE2e158Enabled()" in corps and "executeDiskReclaimThroughBridge(" in corps
            and "executeLegacyHostReclaim(" not in corps and "runHostCommand(" not in corps,
            "bloc du scenario %s" % ("lu" if corps else "INTROUVABLE"))
    c = s["java_client"]
    egal("Java client : action", c, r'DISK_RECLAIM_ACTION = "([A-Z_]+)"', ACTION)
    egal("Java client : type", c, r'DISK_INCIDENT_TYPE = "([A-Z_]+)"', TYPE_INCIDENT)
    egal("Java client : namespace", c, r'DISK_NAMESPACE = "([a-z0-9-]+)"', NS)
    egal("Java client : cible", c, r'DISK_TARGET = "([a-z0-9-]+)"', NOEUD_K8S)
    egal("Java client : scenario", c, r'DISK_SCENARIO = "([A-Z0-9_]+)"', SCENARIO)
    ajouter("Java client : l'envoi au Bridge est gouverne par le drapeau dedie du scenario (jamais l'ancien)",
            "properties.isHostDiskE2e158Enabled()" in c and "isHostDiskRemediationEnabled" not in c)
    # Uniquement le CORPS de verifyDiskRecovery : les verifications CPU et RAM portent les memes expressions (un controle sur tout le
    # fichier laisserait passer une derive de la verification DISK).
    m = re.search(r"RecoveryVerificationResult verifyDiskRecovery\((.*?)RecoveryVerificationResult verifyMemoryRecovery\(", s["java_worker"], re.S)
    w = m.group(1) if m else ""
    ajouter("Java verification Camunda : tentatives bornees 2..8, pas <= 30 s, 2 echantillons sains consecutifs",
            bool(w) and "Math.max(2, Math.min(8, configuredAttempts))" in w and "Math.max(1, Math.min(30, configuredDelay))" in w
            and "consecutiveHealthy >= 2" in w and "hostDiskVerificationService.verify(" in w, "corps de verifyDiskRecovery %s" % ("lu" if w else "INTROUVABLE"))
    pr = s["java_proprietes"]
    ajouter("Java proprietes : cooldown 30 min, 10 tentatives, pas 30 s, listes blanches '*'",
            "private int cooldownMinutes = 30;" in pr and "private int verificationRetryAttempts = 10;" in pr
            and "private int verificationRetryDelaySeconds = 30;" in pr and 'allowlistedApps = new LinkedHashSet<>(Set.of("*"))' in pr)
    ajouter("Java proprietes : drapeau du scenario DESACTIVE par defaut, ancien drapeau inchange (desactive)",
            "private boolean hostDiskE2e158Enabled = false;" in pr and "private boolean hostDiskRemediationEnabled = false;" in pr)
    ap = s["application_properties"]
    ajouter("application.properties : HOST_DISK_E2E_158_ENABLED (defaut false) distinct de HOST_DISK_REMEDIATION_ENABLED (defaut false)",
            "remediation.host-disk-e2e158-enabled=${HOST_DISK_E2E_158_ENABLED:false}" in ap
            and "remediation.host-disk-remediation-enabled=${HOST_DISK_REMEDIATION_ENABLED:false}" in ap)
    po = s["java_politique"]
    ajouter("Java politique : NODE_DISK_FULL -> HOST_RECLAIM_DISK",
            '"NODE_DISK_FULL", "NODE_DISK_PRESSURE", "NODE_INODES_FULL" ->' in po and "actionType = ActionType.HOST_RECLAIM_DISK;" in po)
    m_pol = re.search(r"boolean isDiskAutoRemediationEligible\((.*?)private boolean isSafeAction\(", po, re.S)
    eligibilite = m_pol.group(1) if m_pol else ""
    ajouter("Java politique : le scenario n'est eligible QUE par son drapeau dedie ; NODE_DISK_FULL hors scenario (.155) jamais ; "
            "PRESSURE/INODES STRICTEMENT inchanges (ancien drapeau, memes cibles)",
            bool(eligibilite) and "HostDiskScenario.matches(namespace, target, incidentType)" in eligibilite
            and "return properties.isHostDiskE2e158Enabled();" in eligibilite
            and 'if ("NODE_DISK_FULL".equals(normalizedType)) {' in eligibilite
            and "properties.isHostDiskRemediationEnabled()" in eligibilite
            and 'Set.of("k8s-worker1", "k8s-worker2").contains(normalizedTarget)' in eligibilite
            and 'Set.of("NODE_DISK_PRESSURE", "NODE_INODES_FULL").contains(normalizedType)' in eligibilite,
            "methode isDiskAutoRemediationEligible %s" % ("lue" if eligibilite else "INTROUVABLE"))
    o = s["java_orchestrateur"]
    ajouter("Java orchestrateur : allowlist d'origine INCHANGEE ; NODE_DISK_FULL hors scenario (.155) refuse ; drapeau du scenario "
            "verifie avant toute action ; ancien drapeau pour PRESSURE/INODES",
            'Set.of("k8s-worker1", "k8s-worker2");' in o and "HostDiskScenario.matches(" in o and 'if (!scenario && "NODE_DISK_FULL".equals(type)) {' in o
            and "properties.isHostDiskE2e158Enabled()" in o and "properties.isHostDiskRemediationEnabled()" in o)
    d = _valeur(s["detecteur"], r'\("NODE_DISK_FULL", \'\(1-\(node_filesystem_avail_bytes[^\']*\)\)\*100 > ([0-9.]+)\'')
    ajouter("detecteur : NODE_DISK_FULL > 80", d == "80", "lu %r" % d)

    # Noms de fichiers : tout nom produit par l'outil doit etre reconnu par le Bridge, et seulement ceux-la.
    try:
        regexp = re.compile(_valeur(b, r'^DISQUE_RE_FICHIER = re\.compile\(r"([^"]+)"\)') or "(?!)")
        jeton = "0123456789abcdef0123456789abcdef"
        noms = [H.nom_fichier(jeton, i) for i in range(1, MAX_FICHIERS + 1)]
        reconnus = all(regexp.fullmatch(n) for n in noms)
        refuses = not any(regexp.fullmatch(n) for n in ("README", noms[0] + "\n", noms[0][:-4] + ".bak", "helios-diskfill-ABC-001.bin"))
        ajouter("noms de fichiers : outil et Bridge reconnaissent exactement le meme motif", reconnus and refuses,
                "reconnus=%s refuses=%s" % (reconnus, refuses))
    except Exception as ex:  # noqa: BLE001 - toute erreur de lecture = incoherence
        ajouter("noms de fichiers : outil et Bridge reconnaissent exactement le meme motif", False, "%s: %s" % (type(ex).__name__, ex))
    return r


def coherence_ok(sources=None):
    return all(ok for _, ok, _ in verifier_coherence(sources))


# ------------------------------------------------------------------------------------------------ garde des prerequis (PURE)
def autotester_garde(p=None):
    """Controle STATIQUE du programme de la garde cote noeud, construit avec les parametres figes : modele fixe, aucun `$`, `%` ni
    `${`, seuil 83,5 % (8350), chemins exacts du repertoire exclusif, `rm -f --` absolu. (L'execution reelle d'awk sur le noeud
    appartient au lancement autorise : elle n'est pas faite ici.)"""
    p = p or parametres()
    try:
        chemins = [H.chemin_de(n) for n in H.noms_prevus("0123456789abcdef0123456789abcdef")[:3]]
        prog = H.programme_garde(chemins, H.seuil_garde_x100(p), p.expiration // H.GARDE_PAS_S)
    except H.RefusSecurite:
        return False
    return bool("$" not in prog and "%" not in prog and "${" not in prog and "/usr/bin/rm -f -- " in prog
                and ">= %d" % int(round(GARDE_PCT * 100)) in prog and all(c in prog for c in chemins)
                and prog.count("system(") == 2 and "sh -c" not in prog)


def faits_e2e_simules(sha_bridge=None, image="pfe-backend:sim-disk158", usage_pct=None):
    """Faits E2E de SIMULATION (jamais une mesure) : tout est pret. `usage_pct` = occupation de / que le Bridge simule lirait (defaut : celle
    du noeud simule par defaut de l'outil)."""
    sim = H.SIM_DEFAUT[CLE]
    if usage_pct is None:
        usage_pct = (1.0 - float(sim["dispo"]) / float(sim["taille"])) * 100.0
    return {
        "drapeaux": {DRAPEAU_SCENARIO: "true", DRAPEAU_ANCIEN: "absent", DRAPEAU_BRIDGE: "true"},
        "bridge": {"joignable": True, "capacites": sorted(["HOST_IMAGE_GC", "HOST_RECLAIM_DISK", "RESTART_POD"]),
                   "sha256": sha_bridge or sha256_bridge_sot()},
        "bridge_sonde": {"tentee": True, "ok": True, "execute": False, "validation": "ACCEPTED", "mode": "RECOMMEND_ONLY",
                         "usage": round(usage_pct, 2), "fichiers": 0, "rejet": None, "stderr": ""},
        "backend": {"pret": True, "image": image, "images_pods": [image, image], "env_a_jour": True},
        "systemd": {"systemd_run": True, "awk": True, "unites_residuelles": [], "garde_autotest": autotester_garde()},
        "cooldown_minutes": COOLDOWN_DEFAUT_MIN,
        "actions_disque_recentes_s": [],
        "incidents_disque_ouverts": [],
        "hote": {"pages_s": 120.0},
    }


def _rapport_e2e(r, base, acquittes):
    """Assemble le rapport : verdict, `go` (aucun ROUGE ET aucun ORANGE non acquitte) et ORANGE non acquittes (code par code)."""
    verdict = "ROUGE" if any(x[0] == "ROUGE" for x in r) else ("ORANGE" if any(x[0] == "ORANGE" for x in r) else "VERT")
    non_acquittes = [x for x in r if x[0] == "ORANGE" and x[3] != H.CODE_CNPG_ACCEPTE and x[3] not in acquittes]
    return {"raisons": r, "verdict": verdict, "go": verdict != "ROUGE" and not non_acquittes, "base": base,
            "plan": base["plan"] if base else None, "orange_non_acquittes": non_acquittes, "acquittes": sorted(acquittes)}


def _nombre(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def noeud_incident_disque(app):
    """Normalise uniquement les noms connus du detecteur DISK ; aucune heuristique large."""
    valeur = str(app or "")
    for noeud in NOEUDS_DISQUE_E2E:
        if valeur in (noeud, noeud + "-DiskPressure"):
            return noeud
    return None


def evaluer_e2e(faits, fe, p=None, cle=CLE, sha_bridge_attendu=None, image_backend_attendue="pfe-backend:sim-disk158", acquittes=()):
    """Faits -> rapport. PURE : aucune E/S. Tout fait absent ou illisible est ROUGE. Un ORANGE ne s'acquitte que CODE PAR CODE (`acquittes`,
    qui vient de --accepte-orange) ; jamais REMEDIATION_NON_ACTIVEE (le chemin nominal exige la remediation par le Bridge : aucun lancement
    en detection seule) ni le risque CNPG (jamais presume accepte). `go` n'est vrai que sans ROUGE et sans ORANGE non acquitte.
    Refus => l'appelant n'injecte RIEN et ne cree AUCUN incident."""
    p = p or parametres()
    acquittes = set(acquittes or ()) - {H.CODE_REMEDIATION_NON_ACTIVEE, H.CODE_CNPG_ACCEPTE}
    r = []

    def add(niveau, constat, remede="", code=None):
        r.append((niveau, constat, remede, code))

    if cle != CLE:
        add("ROUGE", "le scenario %s ne vise que .%s (recu : %r)" % (SCENARIO, CLE, cle), "aucune autre cible (.155 : jamais)", "E2E_CIBLE")
        return _rapport_e2e(r, None, acquittes)
    if tuple(p) != tuple(parametres()):
        add("ROUGE", "parametres differents des parametres figes du scenario", "aucune surcharge n'est permise", "E2E_PARAMETRES")

    # Base : le pre-vol de l'outil SANS son controle d'ancien drapeau (evalue ci-dessous, plus strictement). Le risque CNPG n'est JAMAIS
    # presume accepte ; ses ORANGE (gel d'hote, NODE_NOT_READY, VMDK) restent ORANGE : acquittables code par code.
    base = H.evaluer(faits, CLE, p, ack_cnpg=False, controle_drapeau=False)
    r.extend(base["raisons"])
    fe = fe or {}

    ram = ((faits or {}).get("hote") or {}).get("ram_mo")
    if _nombre(ram) and ram < HOTE_RAM_MIN_MO:
        add("ROUGE", "RAM de l'hote %d Mo < %d Mo (seuil de travail du scenario) : gels de VM probables" % (ram, HOTE_RAM_MIN_MO),
            "liberer la RAM de l'hote (fermer Chrome, arreter le harnais de sondes) puis relancer", "E2E_HOTE_RAM")
    ho = fe.get("hote")
    pages = ho.get("pages_s") if isinstance(ho, dict) else None
    if not _nombre(pages):
        add("ROUGE", "pages lues/s de l'hote illisibles (pagination non mesurable)", "refus (echec sur)", "E2E_HOTE_PAGES_ILLISIBLES")
    elif pages >= HOTE_PAGES_MAX_S:
        add("ROUGE", "l'hote relit %.0f pages/s sur disque (>= %d) : pagination intense, gels de VM probables" % (pages, HOTE_PAGES_MAX_S),
            "attendre le retour au calme de l'hote", "E2E_HOTE_PAGES")

    dr = fe.get("drapeaux")
    if not isinstance(dr, dict):
        for code in ("E2E_DRAPEAU_DISQUE", "E2E_DRAPEAU_LEGACY", "E2E_DRAPEAU_BRIDGE"):
            add("ROUGE", "drapeaux du Backend (backend-config / env du deploiement) illisibles", "refus (echec sur)", code)
    else:
        sc, lg = dr.get(DRAPEAU_SCENARIO), dr.get(DRAPEAU_ANCIEN)
        if sc == "true":
            add("INFO", "%s=true : le Backend ouvre la remediation du scenario %s (et d'elle seule)" % (DRAPEAU_SCENARIO, SCENARIO), "")
        else:
            add("ROUGE", "%s non active explicitement (valeur lue : %s) : le Backend ne remedierait pas le scenario" % (DRAPEAU_SCENARIO, sc),
                "activer ce drapeau PAR UNE DECISION DISTINCTE et autorisee, puis redemarrer le Backend", "E2E_DRAPEAU_DISQUE")
        if lg in ("false", "absent"):
            add("INFO", "ancien drapeau %s=%s : le pod hote prive historique reste ferme pendant le test" % (DRAPEAU_ANCIEN, lg), "")
        else:
            add("ROUGE", "ancien drapeau %s=%s : il ouvrirait le pod hote prive historique (NODE_DISK_PRESSURE / NODE_INODES_FULL) pendant "
                "le test" % (DRAPEAU_ANCIEN, lg), "le laisser ferme (absent ou false)", "E2E_DRAPEAU_LEGACY")
        if dr.get(DRAPEAU_BRIDGE) != "true":
            add("ROUGE", "%s non active explicitement (le Backend n'enverrait rien au Bridge)" % DRAPEAU_BRIDGE, "refus (echec sur)",
                "E2E_DRAPEAU_BRIDGE")

    br = fe.get("bridge")
    if not isinstance(br, dict) or br.get("joignable") is not True:
        add("ROUGE", "Bridge injoignable ou etat illisible", "refus (echec sur)", "E2E_BRIDGE_INJOIGNABLE")
    else:
        caps = br.get("capacites")
        if not isinstance(caps, (list, tuple)) or ACTION not in caps:
            add("ROUGE", "le Bridge deploye n'annonce pas %s (/actions) : capacite non deployee" % ACTION,
                "deploiement du Bridge SoT par une decision distincte et autorisee", "E2E_BRIDGE_CAPACITE_ABSENTE")
        attendu = sha_bridge_attendu or sha256_bridge_sot()
        deploye = br.get("sha256")
        if not deploye:
            add("ROUGE", "empreinte du Bridge deploye illisible", "refus (echec sur)", "E2E_BRIDGE_VERSION_ILLISIBLE")
        elif deploye != attendu:
            add("ROUGE", "le Bridge deploye n'est pas la source de verite (empreinte %s... != %s...)" % (str(deploye)[:12], attendu[:12]),
                "redeployer la source de verite (decision distincte)", "E2E_BRIDGE_VERSION")

    # SONDE du Bridge : la liaison ssh qui servira a la remediation est Bridge -> .158 (pas Windows -> .158). Elle n'est tentee que si le Bridge est
    # EXACTEMENT la source de verite et annonce la capacite (un ancien Bridge remplacerait la demande par HOST_IMAGE_GC) ; sinon les ROUGE ci-dessus suffisent.
    if bridge_conforme(br, sha_bridge_attendu or sha256_bridge_sot()):
        so = fe.get("bridge_sonde")
        fs = ((faits or {}).get("noeud") or {}).get("fs")
        usage_lanceur = getattr(fs, "usage", None)
        if not isinstance(so, dict) or so.get("tentee") is not True:
            add("ROUGE", "sonde du Bridge non effectuee ou illisible (validation seule de %s vers .%s)" % (ACTION, CLE), "refus (echec sur)", CODE_SONDE)
        elif (so.get("ok") is not True or so.get("execute") is not False or so.get("validation") != "ACCEPTED"
              or so.get("mode") != "RECOMMEND_ONLY"):
            add("ROUGE", "le Bridge deploye n'execute pas la validation seule de %s vers .%s (ok=%r execute=%r validation=%r mode=%r rejet=%r : %s)" % (
                ACTION, CLE, so.get("ok"), so.get("execute"), so.get("validation"), so.get("mode"), so.get("rejet"),
                str(so.get("erreur") or so.get("stderr") or "")[:100]),
                "verifier la liaison ssh Bridge -> .%s et le sudo du noeud (decision distincte)" % CLE, CODE_SONDE)
        elif not _nombre(so.get("usage")):
            add("ROUGE", "occupation de / lue par le Bridge illisible", "refus (echec sur)", CODE_SONDE)
        elif _nombre(usage_lanceur) and abs(float(so["usage"]) - float(usage_lanceur)) > ECART_MESURE_MAX_PTS:
            add("ROUGE", "occupation de / : %.2f %% selon le Bridge mais %.2f %% selon le lanceur (ecart > %.0f points) : mesures incoherentes" % (
                float(so["usage"]), float(usage_lanceur), ECART_MESURE_MAX_PTS), "ne rien injecter avant d'avoir compris l'ecart", CODE_SONDE_MESURE)
        else:
            add("INFO", "liaison ssh Bridge -> .%s verifiee : validation seule de %s acceptee par le Bridge deploye (occupation de / lue par le Bridge : "
                "%.2f %%%s ; aucune suppression, aucune commande d'ecriture)" % (
                    CLE, ACTION, float(so["usage"]),
                    " ; ecart avec le lanceur %.2f pt" % abs(float(so["usage"]) - float(usage_lanceur)) if _nombre(usage_lanceur) else ""), "")

    bk = fe.get("backend")
    if not isinstance(bk, dict) or bk.get("pret") is not True:
        add("ROUGE", "Backend non pret ou etat illisible", "refus (echec sur)", "E2E_BACKEND_NON_PRET")
    elif not image_backend_attendue:
        add("ROUGE", "aucune image de Backend declaree (--image-backend) capable de %s" % SCENARIO,
            "declarer l'image deployee apres autorisation", "E2E_BACKEND_IMAGE_NON_DECLAREE")
    else:
        if bk.get("image") != image_backend_attendue:
            add("ROUGE", "image du Backend %r != image attendue %r : le routage DISK vers le Bridge n'est pas prouve deploye" % (
                bk.get("image"), image_backend_attendue), "deploiement du Backend par une decision distincte", "E2E_BACKEND_IMAGE")
        pods = bk.get("images_pods")
        if not isinstance(pods, (list, tuple)) or not pods or not all(isinstance(x, str) for x in pods):
            add("ROUGE", "images des pods Backend illisibles", "refus (echec sur)", "E2E_BACKEND_PODS_ILLISIBLES")
        elif any(x != image_backend_attendue for x in pods):
            add("ROUGE", "un pod Backend au moins n'a pas l'image attendue %r (%s) : un incident pourrait etre traite par l'ancien code" % (
                image_backend_attendue, ", ".join(sorted(set(pods)))), "attendre la fin du deploiement", "E2E_BACKEND_IMAGE")
        if bk.get("env_a_jour") is not True:
            add("ROUGE", "pods Backend demarres AVANT la derniere modification de backend-config (ou date illisible) : leur environnement "
                "(drapeaux) n'est pas prouve a jour", "redemarrer le Backend apres la modification (decision distincte)",
                "E2E_BACKEND_ENV_PERIME")

    sy = fe.get("systemd")
    if not isinstance(sy, dict):
        add("ROUGE", "protection systemd illisible", "refus (echec sur)", "E2E_SYSTEMD_ILLISIBLE")
    else:
        if sy.get("systemd_run") is not True:
            add("ROUGE", "systemd-run absent ou non verifie sur le noeud : ni minuteur d'expiration ni garde", "refus", "E2E_SYSTEMD_RUN")
        if sy.get("awk") is not True:
            add("ROUGE", "awk absent ou non verifie sur le noeud : la garde ne peut pas tourner", "refus", "E2E_AWK")
        if sy.get("unites_residuelles") != []:
            add("ROUGE", "unites helios-diskfill-* residuelles ou illisibles : %r" % (sy.get("unites_residuelles"),),
                "l'exploitant les examine lui-meme", "E2E_UNITES_RESIDUELLES")
        if sy.get("garde_autotest") is not True:
            add("ROUGE", "auto-test du programme de garde non reussi", "refus", "E2E_GARDE_AUTOTEST")

    ages = fe.get("actions_disque_recentes_s")
    minutes = fe.get("cooldown_minutes", COOLDOWN_DEFAUT_MIN)
    if not isinstance(ages, (list, tuple)) or not all(_nombre(a) for a in ages):
        add("ROUGE", "historique des actions HOST_RECLAIM_DISK illisible (cooldown du Backend)", "refus (echec sur)", "E2E_COOLDOWN_ILLISIBLE")
    elif not isinstance(minutes, int) or isinstance(minutes, bool) or minutes < 0:
        add("ROUGE", "REMEDIATION_COOLDOWN_MINUTES illisible (cooldown du Backend)", "refus (echec sur)", "E2E_COOLDOWN_ILLISIBLE")
    elif any(a < minutes * 60 + MARGE_COOLDOWN_S for a in ages):
        add("ROUGE", "action HOST_RECLAIM_DISK sur %s il y a moins de %d min : le Backend bloquerait la remediation (cooldown)" % (
            NOEUD_K8S, (minutes * 60 + MARGE_COOLDOWN_S + 59) // 60), "attendre la fin du cooldown", "E2E_COOLDOWN_BACKEND")

    ouverts = fe.get("incidents_disque_ouverts")
    if not isinstance(ouverts, (list, tuple)):
        add("ROUGE", "incidents disque ouverts illisibles", "refus (echec sur)", "E2E_INCIDENTS_ILLISIBLES")
    else:
        diskpressure = fe.get("diskpressure")
        for inc in ouverts:
            if not isinstance(inc, dict):
                add("ROUGE", "ligne d'incident disque mal formee", "refus (echec sur)", "E2E_INCIDENTS_ILLISIBLES")
                continue
            app = str(inc.get("app") or "")
            if app == "monitoring":
                add("INFO", "incident disque de fond sur monitoring (.160) tolere : la regle DETECTOR-PRIORITY donne la priorite au worker", "")
                continue
            noeud = noeud_incident_disque(app)
            age = inc.get("age_s")
            etat = diskpressure.get(noeud) if isinstance(diskpressure, dict) and noeud else None
            ancien_confirme = (noeud in NOEUDS_DISQUE_E2E
                               and isinstance(age, int) and not isinstance(age, bool)
                               and age >= AGE_INCIDENT_DISQUE_ANCIEN_S
                               and etat == "False")
            if ancien_confirme:
                add("ORANGE", "incident disque historique #%s sur %s (age %dh, DiskPressure=False confirme par Kubernetes) : laisse ouvert, non modifie" % (
                    inc.get("id"), noeud, age // 3600),
                    "incident historique conserve ; acquittement limite au preflight DISK", CODE_INCIDENTS_ANCIENS)
            elif noeud == NOEUD_K8S:
                add("ROUGE", "incident disque concurrent #%s sur %s (age ou DiskPressure non confirme)" % (
                    inc.get("id"), app), "refus ; ne pas injecter tant que la condition n'est pas resolue et lue", "E2E_INCIDENT_DISQUE_CONCURRENT")
            else:
                add("ROUGE", "incident disque non historique, non classe ou etat DiskPressure illisible #%s sur %s" % (
                    inc.get("id"), app), "refus ferme ; verifier le noeud et son etat Kubernetes", "E2E_INCIDENT_DISQUE_NON_CLASSE")

    return _rapport_e2e(r, base, acquittes)


def afficher_rapport_e2e(rapport, sortie=print):
    sortie("PRE-VOL DU SCENARIO %s  .%s (%s)" % (SCENARIO, CLE, NOEUD_K8S))
    sortie("=" * 78)
    acquittes = set(rapport.get("acquittes") or ())
    for niveau, constat, remede, code in rapport["raisons"]:
        sortie("  [%s] %s%s" % (niveau, constat, "  <%s>" % code if code and niveau == "ROUGE" else ""))
        if remede and niveau != "INFO":
            sortie("          -> %s" % remede)
        if niveau == "ORANGE" and code != H.CODE_CNPG_ACCEPTE:
            sortie("          acquitte par --accepte-orange %s" % code if code in acquittes
                   else "          acquittement exige pour --go : --accepte-orange %s" % code)
    sortie("")
    sortie("VERDICT : %s%s" % (rapport["verdict"], "" if rapport["go"] else "  =>  AUCUNE charge injectee, AUCUN incident cree"))


# ------------------------------------------------------------------------------------------------ collecte REELLE (LECTURE SEULE)
# Tout passe par `exe` (l'executeur du lanceur : ssh de lecture, kubectl get, SELECT, powershell de lecture) : ce module n'ouvre AUCUN
# sous-processus lui-meme. Une lecture impossible, partielle ou incoherente donne None => ROUGE dans evaluer_e2e : jamais une valeur devinee.
PS_PAGES = "1..3 | ForEach-Object { (Get-CimInstance Win32_PerfFormattedData_PerfOS_Memory).PagesInputPersec; Start-Sleep -Seconds 1 }"


def _json(texte):
    try:
        return json.loads(texte)
    except (ValueError, TypeError):
        return None


def _epoch(texte):
    """Horodatage RFC 3339 UTC de Kubernetes (2026-11-14T01:02:03Z) -> secondes ; None si illisible."""
    try:
        return H.datetime.strptime(texte, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=H.timezone.utc).timestamp()
    except (ValueError, TypeError):
        return None


def _etat_drapeau(valeur):
    return "absent" if valeur is None else ("true" if str(valeur).strip().lower() == "true" else "false")


def lire_configmap(exe):
    """ConfigMap backend-config -> (data, date de derniere modification en secondes | None) ; None si illisible."""
    r = exe.local(["kubectl", "-n", NS_PFE, "get", "configmap", "backend-config", "--show-managed-fields", "-o", "json"])
    if r.rc != 0:
        return None
    o = _json(r.out)
    if not isinstance(o, dict) or not isinstance(o.get("data") or {}, dict):
        return None
    temps = [_epoch(m.get("time")) for m in ((o.get("metadata") or {}).get("managedFields") or []) if isinstance(m, dict)]
    return dict(o.get("data") or {}), (max(temps) if temps and all(t is not None for t in temps) else None)


def lire_backend(exe, t_config):
    """Deploiement `backend` et ses pods -> (etat, env directe du conteneur) ; (None, {}) si illisible ou incoherent."""
    rd = exe.local(["kubectl", "-n", NS_PFE, "get", "deployment", DEPLOIEMENT_BACKEND, "-o", "json"])
    rp = exe.local(["kubectl", "-n", NS_PFE, "get", "pods", "-l", SELECTEUR_BACKEND, "-o", "json"])
    if rd.rc != 0 or rp.rc != 0:
        return None, {}
    d, p = _json(rd.out), _json(rp.out)
    try:
        voulu, st = d["spec"]["replicas"], d["status"]
        conteneur = [c for c in d["spec"]["template"]["spec"]["containers"] if c.get("name") == CONTENEUR_BACKEND][0]
        env = dict((e["name"], e["value"] if "value" in e else None) for e in (conteneur.get("env") or []))
        pret = (isinstance(voulu, int) and not isinstance(voulu, bool) and voulu >= 1
                and all(st.get(k) == voulu for k in ("replicas", "updatedReplicas", "readyReplicas", "availableReplicas"))
                and st.get("observedGeneration") == d["metadata"]["generation"])
        images, demarrages = [], []
        for pod in p["items"]:
            if pod["metadata"].get("deletionTimestamp") or pod["status"].get("phase") not in ("Pending", "Running"):
                continue
            images.append([c["image"] for c in pod["spec"]["containers"] if c.get("name") == CONTENEUR_BACKEND][0])
            demarrages.append(_epoch(pod["status"].get("startTime")))
    except (KeyError, IndexError, TypeError, AttributeError):
        return None, {}
    frais = None
    if t_config is not None and demarrages and all(t is not None for t in demarrages):
        frais = all(t > t_config for t in demarrages)
    return {"pret": bool(pret and images), "image": conteneur.get("image"), "images_pods": sorted(images), "env_a_jour": frais}, env


def drapeaux_effectifs(data, env):
    """Etat de chaque drapeau tel que le Backend le lit : l'env DIRECTE du conteneur l'emporte sur la ConfigMap (envFrom). Une env qui
    pointe vers un secret ou une autre ressource (valueFrom) est 'illisible' : on ne la lit pas."""
    out = {}
    for nom in (DRAPEAU_SCENARIO, DRAPEAU_ANCIEN, DRAPEAU_BRIDGE):
        out[nom] = ("illisible" if env[nom] is None else _etat_drapeau(env[nom])) if nom in env else _etat_drapeau(data.get(nom))
    return out


def cooldown_effectif(data, env):
    brut = env[CLE_COOLDOWN] if CLE_COOLDOWN in env else data.get(CLE_COOLDOWN)
    if brut is None and CLE_COOLDOWN not in env:
        return COOLDOWN_DEFAUT_MIN
    texte = str(brut).strip() if brut is not None else ""
    return int(texte) if texte.isdigit() else None


def lire_bridge(exe):
    """Bridge DEPLOYE sur .161 : /actions (catalogue du processus en cours) et empreinte du fichier. Lecture seule, par ssh."""
    d = {"joignable": False, "capacites": None, "sha256": None}
    r = exe.ssh(IP_BRIDGE, ["curl", "-sS", "--max-time", "8", URL_ACTIONS_BRIDGE], 30)
    o = _json(r.out) if r.rc == 0 else None
    if isinstance(o, dict):
        d["joignable"] = True
        cat = o.get("catalogue")
        if isinstance(cat, list) and cat and all(isinstance(x, str) for x in cat):
            d["capacites"] = sorted(cat)
    h = exe.ssh(IP_BRIDGE, ["sha256sum", BRIDGE_DEPLOYE], 30)
    m = re.match(r"([0-9a-f]{64})\s", h.out or "") if h.rc == 0 else None
    d["sha256"] = m.group(1) if m else None
    return d


def bridge_conforme(bridge, sha_attendu):
    """Vrai si le Bridge est joignable, annonce HOST_RECLAIM_DISK ET est EXACTEMENT la source de verite (empreinte identique)."""
    return (isinstance(bridge, dict) and bridge.get("joignable") is True and isinstance(bridge.get("capacites"), (list, tuple))
            and ACTION in bridge["capacites"] and bool(sha_attendu) and bridge.get("sha256") == sha_attendu)


def corps_sonde_bridge():
    """Corps JSON de la requete de VALIDATION SEULE : memes champs que le client Java du Backend, plus `recommend_only` (le Bridge valide, LIT
    le repertoire exclusif et l'occupation de /, et ne supprime RIEN)."""
    return json.dumps({"action": ACTION, "type": TYPE_INCIDENT, "ns": NS, "cible": NOEUD_K8S, "noeud": NOEUD_K8S, "scenario": SCENARIO,
                       "recommend_only": True}, sort_keys=True, separators=(",", ":"))


def script_sonde_bridge():
    """Script bash execute SUR .161 : lit le jeton du Bridge dans son fichier d'environnement (sudo), le transmet a `curl` par un TUBE (jamais en argument,
    jamais affiche) et poste la requete de validation seule au Bridge local. Rend une ligne JSON."""
    return ("set -u; T=$(sudo -n sed -n 's/^HELIOS_BRIDGE_TOKEN=//p' %s | head -1); "
            "[ -n \"$T\" ] || { echo '{\"sonde_erreur\":\"jeton_illisible\"}'; exit 0; }; "
            "printf 'X-Helios-Token: %%s\\n' \"$T\" | curl -sS --max-time 60 -H @- -H 'Content-Type: application/json' -d '%s' %s | head -c 3000"
            % (FICHIER_ENV_BRIDGE, corps_sonde_bridge(), URL_ACTION_BRIDGE))


def lire_sonde_bridge(exe, bridge, sha_attendu=None):
    """Sonde du Bridge DEPLOYE : validation seule (`recommend_only`) de HOST_RECLAIM_DISK vers .158, par SA liaison ssh. Jamais tentee si le Bridge n'est pas
    EXACTEMENT la source de verite et n'annonce pas la capacite. {'tentee': False} si non tentee ; sinon les champs utiles de la reponse."""
    if not bridge_conforme(bridge, sha_attendu or sha256_bridge_sot()):
        return {"tentee": False}
    r = exe.ssh(IP_BRIDGE, ["bash", "-c", script_sonde_bridge()], 90)
    lignes = [l for l in (r.out or "").splitlines() if l.strip()]
    if r.rc != 0 or not lignes:
        return {"tentee": True, "ok": None, "erreur": "aucune reponse (rc=%s)" % r.rc}
    j = _json(lignes[-1])
    if not isinstance(j, dict):
        return {"tentee": True, "ok": None, "erreur": "reponse illisible"}
    if "sonde_erreur" in j:
        return {"tentee": True, "ok": None, "erreur": str(j["sonde_erreur"])[:60]}
    return {"tentee": True, "ok": j.get("ok"), "execute": j.get("execute"), "validation": j.get("validation"), "mode": j.get("execution_mode"),
            "usage": j.get("usage_avant_pct"), "fichiers": j.get("fichiers"), "rejet": j.get("rejet"), "stderr": str(j.get("stderr") or "")[:120]}


def lire_systemd(exe, ip):
    """Protection systemd de .158 : systemd-run et awk presents, aucune unite helios-diskfill-* residuelle. Lecture seule."""
    def present(chemin):
        rc = exe.ssh(ip, ["test", "-x", chemin], 20).rc
        return True if rc == 0 else (False if rc == 1 else None)        # 255 (ssh en echec) = illisible, jamais 'absent'
    u = exe.ssh(ip, ["systemctl", "list-units", "--all", "--no-legend", "--plain", "helios-diskfill-*"], 30)
    unites = [l.split()[0] for l in (u.out or "").splitlines() if l.strip()] if u.rc == 0 else None
    return {"systemd_run": present("/usr/bin/systemd-run"), "awk": present("/usr/bin/awk"), "unites_residuelles": unites,
            "garde_autotest": autotester_garde()}


def lire_incidents_e2e(exe, primaire):
    """(ages en secondes des actions HOST_RECLAIM_DISK recentes sur k8s-worker1, incidents disque ouverts) par SELECT ; (None, None) si illisible."""
    if not primaire:
        return None, None
    types = ",".join("'%s'" % t for t in TYPES_DISQUE)
    actifs = ",".join("'%s'" % s for s in H.STATUTS_ACTIFS)
    sql = ("select 'AGE', extract(epoch from ((now() at time zone 'Africa/Tunis') - a.created_at))::bigint from incident_action a "
           "where a.type = '%s' and a.target_namespace = '%s' and a.target_name = '%s' order by a.created_at desc limit 20;\n"
           "select 'OUVERT', i.id, i.app, i.status, extract(epoch from ((now() at time zone 'Africa/Tunis') - i.created_at))::bigint "
           "from incident i where i.incident_type in (%s) and i.status in (%s) order by i.id desc limit 50;" % (ACTION, NS, NOEUD_K8S, types, actifs))
    lignes = H.sql_local(exe, primaire, sql)
    if lignes is None:
        return None, None
    ages, ouverts = [], []
    for l in lignes:
        if l[0] == "AGE" and len(l) >= 2 and l[1].lstrip("-").isdigit():
            ages.append(int(l[1]))
        elif l[0] == "OUVERT" and len(l) >= 5 and l[1].isdigit() and l[4].isdigit():
            ouverts.append({"id": int(l[1]), "app": l[2], "statut": l[3], "age_s": int(l[4])})
        else:
            return None, None                      # ligne inattendue : on ne devine pas
    return ages, ouverts


def lire_diskpressure(exe):
    """Lit les conditions DiskPressure des deux workers ; None si une lecture est incomplete ou ambigue."""
    resultats = {}
    for noeud in NOEUDS_DISQUE_E2E:
        r = exe.local(["kubectl", "get", "node", noeud, "-o", "json"], 30)
        if r.rc != 0:
            return None
        objet = _json(r.out)
        if not isinstance(objet, dict) or not isinstance(objet.get("metadata"), dict):
            return None
        if objet["metadata"].get("name") != noeud:
            return None
        statut = objet.get("status")
        conditions = statut.get("conditions") if isinstance(statut, dict) else None
        if not isinstance(conditions, list):
            return None
        trouvees = [x for x in conditions if isinstance(x, dict) and x.get("type") == "DiskPressure"]
        if len(trouvees) != 1 or trouvees[0].get("status") not in ("True", "False"):
            return None
        resultats[noeud] = trouvees[0]["status"]
    return resultats


def lire_pages_s(exe):
    """Pages lues sur disque par seconde sur l'hote Windows (moyenne de 3 echantillons, lecture CIM) ; None si illisible."""
    r = exe.local(["powershell", "-NoProfile", "-NonInteractive", "-Command", PS_PAGES], 40)
    if r.rc != 0:
        return None
    valeurs = []
    for l in (r.out or "").splitlines():
        if l.strip():
            try:
                valeurs.append(float(l.strip()))
            except ValueError:
                return None
    return sum(valeurs) / len(valeurs) if len(valeurs) >= 2 else None


def _lire(fonction, *a):
    """Une lecture qui echoue de maniere inattendue est ILLISIBLE (None), jamais une exception qui laisserait croire a un refus propre."""
    try:
        return fonction(*a)
    except Exception:          # noqa: BLE001 - echec sur : tout est ramene a 'illisible' => ROUGE
        return None


def collecter_faits_e2e(deps, exe, primaire):
    """Faits E2E REELS en LECTURE SEULE (ssh de lecture, kubectl get, SELECT, lecture CIM) : drapeaux EFFECTIFS du Backend, Bridge deploye
    (catalogue + empreinte), SONDE du Bridge (validation seule `recommend_only` vers .158 : liaison ssh du Bridge, sudo du noeud, mesure de /,
    aucune suppression ; jamais tentee si le Bridge n'est pas la source de verite), Backend (etat, image, pods, environnement a jour),
    protection systemd de .158, cooldown, incidents disque ouverts, pagination de l'hote. `deps` n'est pas utilise ici (la RAM de l'hote est
    deja lue par le lanceur) : parametre conserve pour que la porte puisse evoluer sans changer l'appel."""
    ip = H.CIBLES[CLE][1]
    config = _lire(lire_configmap, exe)
    data, t_config = config if config else (None, None)
    backend, env = _lire(lire_backend, exe, t_config) or (None, {})
    ages, ouverts = _lire(lire_incidents_e2e, exe, primaire) or (None, None)
    incident_worker = (ouverts is not None and any(
        isinstance(x, dict) and x.get("app") != "monitoring" for x in ouverts))
    diskpressure = _lire(lire_diskpressure, exe) if incident_worker else ({} if ouverts is not None else None)
    bridge = _lire(lire_bridge, exe)
    return {
        "drapeaux": drapeaux_effectifs(data, env) if data is not None else None,
        "cooldown_minutes": cooldown_effectif(data, env) if data is not None else None,
        "bridge": bridge,
        "bridge_sonde": _lire(lire_sonde_bridge, exe, bridge),
        "backend": backend,
        "systemd": _lire(lire_systemd, exe, ip),
        "actions_disque_recentes_s": ages,
        "incidents_disque_ouverts": ouverts,
        "diskpressure": diskpressure,
        "hote": {"pages_s": _lire(lire_pages_s, exe)},
    }


# ------------------------------------------------------------------------------------------------ le VRAI Bridge, en processus
_BRIDGE = None


def bridge_module():
    """Charge le Bridge SoT (cible de deploiement). Importer ce module n'a aucun effet de bord (verifie par les tests)."""
    global _BRIDGE
    if _BRIDGE is None:
        spec = importlib.util.spec_from_file_location("bridge_cible_e2e", BRIDGE_SOT)
        mod = importlib.util.module_from_spec(spec)
        sys.modules["bridge_cible_e2e"] = mod
        spec.loader.exec_module(mod)
        _BRIDGE = mod
    return _BRIDGE


CMD_STAT_DIR = "sudo -n /usr/bin/stat -c '%F|%U|%a' " + DOSSIER
CMD_FIND = "sudo -n /usr/bin/find " + DOSSIER + " -maxdepth 1 -mindepth 1 -printf '%y|%u|%m|%n|%s|%f\\n'"
CMD_STAT_FS = "sudo -n /usr/bin/stat -f -c '%S|%b|%a' /"
PREFIXE_RM = "sudo -n /usr/bin/rm -f -- "


class ExecuterSurMonde(object):
    """Remplace `bridge.executer` (le SEUL point d'acces SSH du Bridge) : traduit les QUATRE commandes que le Bridge peut emettre pour
    HOST_RECLAIM_DISK en operations sur le monde simule. Toute autre commande leve une AssertionError (jamais acceptee en silence).
    Pannes (monde.pannes) : bridge_ssh_ko, bridge_rm_echoue, bridge_rm_sans_effet, bridge_croissance_apres_rm (fraction de la taille),
    dossier_lien, dossier_proprio, dossier_mode."""
    BS = 4096

    def __init__(self, monde):
        self.m = monde
        self.commandes = []
        self.rm = []

    def __call__(self, noeud, commande):
        self.commandes.append(commande)
        m = self.m
        assert noeud == NOEUD_BRIDGE, "le Bridge ne doit viser que %s (recu %r)" % (NOEUD_BRIDGE, noeud)

        def r(out="", rc=0, err=""):
            return {"host": noeud, "ip": IP, "command": commande, "exitCode": rc, "stdout": out.strip()[:4000], "stderr": err,
                    "ok": rc == 0, "resume": (out or err or "")[:80]}

        if m.pannes.get("bridge_ssh_ko"):
            return r(rc=255, err="ssh: connect to host: Connection refused")
        if commande == CMD_STAT_DIR:
            if m.dossier is None:
                return r(rc=1, err="stat: cannot statx '%s': No such file or directory" % DOSSIER)
            genre = "symbolic link" if m.pannes.get("dossier_lien") else "directory"
            proprio = "kube" if m.pannes.get("dossier_proprio") else "root"
            mode = "777" if m.pannes.get("dossier_mode") else "700"
            return r("%s|%s|%s" % (genre, proprio, mode))
        if commande == CMD_FIND:
            lignes = ["f|root|644|1|%d|%s" % (octets, nom) for nom, octets in sorted(m.fichiers.items())]
            return r("\n".join(lignes))
        if commande == CMD_STAT_FS:
            return r("%d|%d|%d" % (self.BS, m.taille // self.BS, m.dispo() // self.BS))
        if commande.startswith(PREFIXE_RM):
            argv = shlex.split(commande)
            assert argv[:5] == ["sudo", "-n", "/usr/bin/rm", "-f", "--"] and len(argv) > 5, "forme de rm inattendue : %r" % argv[:6]
            noms = []
            for c in argv[5:]:
                assert c.startswith(DOSSIER + "/"), "chemin hors du repertoire exclusif : %r" % c
                nom = c[len(DOSSIER) + 1:]
                assert H.RE_FICHIER.match(nom) and "/" not in nom and not any(ch in c for ch in "*?[]{}~$`\\"), "chemin refuse : %r" % c
                noms.append(nom)
            self.rm.append(list(noms))
            if m.pannes.get("bridge_rm_echoue"):
                return r(rc=1, err="rm: erreur")
            if not m.pannes.get("bridge_rm_sans_effet"):
                for nom in noms:
                    m.retirer(nom)
                m.journal.append((m.t, "rm-bridge"))
                croissance = m.pannes.get("bridge_croissance_apres_rm")       # un tiers ecrit juste apres la suppression
                if croissance:
                    m.organique += croissance * m.taille
            return r()
        raise AssertionError("COMMANDE NON AUTORISEE emise par le Bridge : %r" % (commande,))


TOKEN_BRIDGE_SIM = "jeton-bridge-simule"
_ATTRIBUTS_BRIDGE = ("executer", "tracer", "_tele", "JETON", "noeud_kubectl")


class BridgeEnProcessus(object):
    """Serveur HTTP local (127.0.0.1, port ephemere) qui execute le VRAI gestionnaire du Bridge ; seul `executer` est remplace par
    ExecuterSurMonde. Le jeton `X-Helios-Token` est exige comme en production. Restaure le module a la sortie."""

    def __init__(self, monde=None):
        self.monde = None
        self.exec = None
        self.traces = []
        self.requetes = []
        self.reponses = []
        if monde is not None:
            self.attacher(monde)

    def attacher(self, monde):
        """Relie le Bridge au monde simule (cree apres lui : le monde a besoin du Backend, qui a besoin du Bridge)."""
        self.monde = monde
        self.exec = ExecuterSurMonde(monde)

    def __enter__(self):
        assert self.exec is not None, "BridgeEnProcessus : aucun monde attache"
        b = bridge_module()
        self._sauve = dict((k, getattr(b, k)) for k in _ATTRIBUTS_BRIDGE)
        b.executer = self.exec
        b.tracer = lambda *parts: self.traces.append(" ".join(str(x) for x in parts))
        b._tele = None
        b.JETON = TOKEN_BRIDGE_SIM
        b.noeud_kubectl = lambda force=False: "master"
        b._DISQUE_LIAISONS.clear()
        self.srv = HTTPServer(("127.0.0.1", 0), b.Handler)
        self.fil = threading.Thread(target=self.srv.serve_forever, daemon=True)
        self.fil.start()
        return self

    def __exit__(self, *a):
        self.srv.shutdown()
        self.srv.server_close()
        self.fil.join(5)
        b = bridge_module()
        for k, v in self._sauve.items():
            setattr(b, k, v)
        b._DISQUE_LIAISONS.clear()

    def poster(self, chemin, corps, jeton=TOKEN_BRIDGE_SIM):
        """POST JSON -> (statut HTTP, corps JSON). Boucle locale, jamais de proxy."""
        self.requetes.append((chemin, dict(corps)))
        ouvreur = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        req = urllib.request.Request("http://127.0.0.1:%d%s" % (self.srv.server_address[1], chemin),
                                     data=json.dumps(corps).encode("utf-8"),
                                     headers={"Content-Type": "application/json", "X-Helios-Token": jeton})
        try:
            with ouvreur.open(req, timeout=20) as rep:
                resultat = rep.status, json.loads(rep.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            try:
                resultat = e.code, json.loads(e.read().decode("utf-8"))
            except ValueError:
                resultat = e.code, {}
        self.reponses.append(resultat)
        return resultat

    def get(self, chemin):
        ouvreur = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with ouvreur.open("http://127.0.0.1:%d%s" % (self.srv.server_address[1], chemin), timeout=20) as rep:
            return rep.status, json.loads(rep.read().decode("utf-8"))


# ------------------------------------------------------------------------------------------------ modele du Backend (fidele aux sources Java)
Reponse = namedtuple("Reponse", "ok execute exit_code message")


def _echec(message):
    return Reponse(False, False, -1, message)


class ClientBridgeModele(object):
    """Miroir de RestTemplateBridgeClient.sendDiskReclaimAction : memes refus (sans requete), memes valeurs canoniques envoyees,
    meme lecture de la reponse (ok = ok && exitCode == 0). Les constantes sont verifiees contre le Java par verifier_coherence."""

    def __init__(self, bridge, drapeau_scenario=True, bridge_actif=True, url_configuree=True, jeton=TOKEN_BRIDGE_SIM):
        self.bridge, self.drapeau_scenario, self.bridge_actif = bridge, drapeau_scenario, bridge_actif
        self.url_configuree, self.jeton = url_configuree, jeton
        self.envois = []

    def envoyer_disque(self, action, type_incident, ns, cible, noeud, scenario, incident_id):
        if not self.drapeau_scenario:                      # drapeau DEDIE du scenario (HOST_DISK_E2E_158_ENABLED), jamais l'ancien
            return _echec("Scenario DISK_E2E_158 desactivee (HOST_DISK_E2E_158_ENABLED).")
        if not self.bridge_actif:
            return _echec("Bridge desactive.")
        if not self.url_configuree or not self.jeton:
            return _echec("Configuration du Bridge incomplete.")
        if action != ACTION:
            return _echec("Action disque non autorisee.")
        if str(type_incident).lower() != TYPE_INCIDENT.lower():
            return _echec("Type incident disque non autorise.")
        if str(ns).lower() != NS.lower():
            return _echec("Namespace cible disque non autorise.")
        if str(cible).lower() != NOEUD_K8S.lower() or str(noeud).lower() != NOEUD_K8S.lower():
            return _echec("Le nettoyage disque est limite au worker laboratoire k8s-worker1.")
        if scenario != SCENARIO:
            return _echec("Scenario disque non autorise.")
        if not isinstance(incident_id, int) or isinstance(incident_id, bool) or incident_id <= 0:
            return _echec("Identifiant d'incident disque requis.")
        charge = {"action": ACTION, "type": TYPE_INCIDENT, "ns": NS, "cible": NOEUD_K8S, "noeud": NOEUD_K8S,
                  "scenario": SCENARIO, "incident_id": str(incident_id)}
        self.envois.append(dict(charge))
        try:
            statut, corps = self.bridge.poster("/action", charge, self.jeton)
        except OSError:
            return _echec("Erreur de communication avec le Bridge.")
        if not 200 <= statut < 300:
            return _echec("Erreur de communication avec le Bridge.")
        ok, execute, code = corps.get("ok"), corps.get("execute"), corps.get("exitCode")
        if not isinstance(ok, bool) or not isinstance(execute, bool) or isinstance(code, bool) or not isinstance(code, (int, float)):
            return _echec("Format de reponse Bridge invalide.")
        message = next((str(corps.get(k)) for k in ("resume", "stderr", "detail", "stdout") if corps.get(k)), "")
        return Reponse(ok and int(code) == 0, execute, int(code), message)


class BackendModele(object):
    """Modele du Backend pour le chemin DISK : HostDiskIncidentRemediationOrchestrator.startIfEligible -> RemediationPolicyService.buildPlan
    -> KubernetesRemediationExecutor.executeHostDiskReclaim -> CamundaJobWorkerLifecycle.verifyDiskRecovery. Ce n'est PAS le code Java :
    c'est un MODELE dont chaque constante est verifiee contre les sources (verifier_coherence).

    Deux drapeaux INDEPENDANTS : `drapeau_scenario` (HOST_DISK_E2E_158_ENABLED) gouverne SEUL k8s-worker1 + NODE_DISK_FULL (Bridge, jamais le
    pod hote) ; `drapeau_legacy` (HOST_DISK_REMEDIATION_ENABLED) ne gouverne que NODE_DISK_PRESSURE / NODE_INODES_FULL (pod hote d'origine,
    STRICTEMENT inchange : memes cibles qu'avant). NODE_DISK_FULL hors scenario, donc sur k8s-worker2 (.155), n'est JAMAIS eligible."""
    TYPES_DISQUE = ("NODE_DISK_FULL", "NODE_DISK_PRESSURE", "NODE_INODES_FULL")
    TYPES_LEGACY = ("NODE_DISK_PRESSURE", "NODE_INODES_FULL")
    CIBLES_ADMISES = ("k8s-worker1", "k8s-worker2")       # allowlist d'origine (PRESSURE / INODES) ; NODE_DISK_FULL : voir ci-dessous
    COOLDOWN_S = COOLDOWN_BACKEND_S
    CONFIANCE_MIN = 0.45
    TENTATIVES_MAX = 8                 # Math.max(2, Math.min(8, 10))
    PAS_VERIFICATION_S = 30            # Math.max(1, Math.min(30, 30))
    SEUIL_DISQUE_PCT = RECUPERATION_PCT
    SEUIL_INODES_PCT = 85.0

    def __init__(self, client, drapeau_scenario=True, drapeau_legacy=False, bridge_actif=True, automation=True, confiance=1.0,
                 historique=None, legacy_succes=False):
        self.client, self.drapeau_scenario, self.drapeau_legacy = client, drapeau_scenario, drapeau_legacy
        self.bridge_actif, self.automation, self.confiance, self.legacy_succes = bridge_actif, automation, confiance, legacy_succes
        self.historique = list(historique or [])      # (t, cible, type_action, type_incident)
        self.historique_incident = []                 # messages de l'historique de l'incident
        self.appels_legacy = []

    @staticmethod
    def _noeud_k8s(app):
        t = (app or "").strip().lower()
        return t.startswith("k8s-master") or t.startswith("k8s-worker")

    @staticmethod
    def est_scenario(ns, cible, type_inc):
        """HostDiskScenario.matches : EXACTEMENT (k8s-nodes, k8s-worker1, NODE_DISK_FULL)."""
        return ((ns or "").strip().lower() == NS and (cible or "").strip().lower() == NOEUD_K8S
                and (type_inc or "").strip().upper() == TYPE_INCIDENT)

    def drapeau_ouvert(self, ns, cible, type_inc):
        """isDiskAutoRemediationEligible (partie drapeau) : le scenario n'est gouverne QUE par son drapeau dedie ; NODE_DISK_FULL hors
        scenario (k8s-worker2 = .155) : JAMAIS ; PRESSURE / INODES : l'ancien drapeau, memes cibles qu'avant (les deux workers)."""
        if (ns or "").strip().lower() != NS or not cible or not type_inc:
            return False
        if self.est_scenario(ns, cible, type_inc):
            return self.drapeau_scenario
        if type_inc.strip().upper() == TYPE_INCIDENT:
            return False
        return self.drapeau_legacy and cible.strip().lower() in self.CIBLES_ADMISES and type_inc.strip().upper() in self.TYPES_LEGACY

    def cooldown_bloque(self, t, cible, type_action, type_incident):
        """isCooldownBlocked : une action du meme type sur la meme cible dans les 30 dernieres minutes bloque, SAUF si le type d'incident
        a change depuis la derniere action sur cette cible (le contournement intelligent du Backend)."""
        sur_cible = [h for h in self.historique if h[1] == cible]
        if not [h for h in sur_cible if h[2] == type_action and t - h[0] < self.COOLDOWN_S]:
            return False
        derniere = sur_cible[-1][3] if sur_cible else None
        return not (derniere and type_incident and derniere.lower() != type_incident.lower())

    def construire_plan(self, incident, t):
        type_inc = (incident["type"] or "").strip().upper()
        cible = incident["app"]
        noeud = incident["ns"].lower() == NS and self._noeud_k8s(cible)
        action = ACTION if (noeud and type_inc in self.TYPES_DISQUE) else None
        eligible_disque = action == ACTION and self.drapeau_ouvert(incident["ns"], cible, type_inc)
        cooldown = action is not None and self.cooldown_bloque(t, cible, action, type_inc)
        db = any(mot in (type_inc + " " + (action or "")).upper() for mot in ("DB", "DATABASE", "POSTGRES"))
        auto = (self.automation and action is not None and eligible_disque and self.confiance >= self.CONFIANCE_MIN
                and not db and not cooldown)
        return {"action": action, "auto": auto, "cooldown": cooldown, "approbation": not auto,
                "manuel": action is None or not auto, "cible": cible, "ns": incident["ns"], "type": type_inc,
                "incident_id": incident["id"]}

    def demarrer_si_eligible(self, incident, t):
        """startIfEligible : refus (incident laisse OPEN, aucune action) ou plan AUTO. Retourne (plan | None, raison)."""
        type_inc = (incident["type"] or "").strip().upper()
        if type_inc not in self.TYPES_DISQUE:
            return None, "ignore : type hors DISK"
        if incident["ns"].strip().lower() != NS:
            self.historique_incident.append("DISK remediation refused: namespace outside the approved scope.")
            return None, "namespace hors perimetre"
        if (incident["app"] or "").strip().lower() not in self.CIBLES_ADMISES:
            self.historique_incident.append("DISK remediation refused: target outside the worker allowlist.")
            return None, "cible hors allowlist"
        est_scn = self.est_scenario(incident["ns"], incident["app"], type_inc)
        if not est_scn and type_inc == TYPE_INCIDENT:
            self.historique_incident.append("DISK remediation refused: NODE_DISK_FULL is eligible only for the DISK_E2E_158 scenario "
                                            "(k8s-worker1); k8s-worker2 never.")
            return None, "NODE_DISK_FULL hors scenario : jamais"
        if est_scn:
            if not self.drapeau_scenario:
                self.historique_incident.append("DISK remediation not started: HOST_DISK_E2E_158_ENABLED=false "
                                                "(HOST_DISK_REMEDIATION_ENABLED does not enable the DISK_E2E_158 scenario).")
                return None, "drapeau du scenario desactive"
        elif not self.drapeau_legacy:
            self.historique_incident.append("DISK remediation not started: HOST_DISK_REMEDIATION_ENABLED=false.")
            return None, "drapeau disque d'origine desactive"
        plan = self.construire_plan(incident, t)
        if plan["action"] != ACTION or not plan["auto"] or plan["approbation"] or plan["manuel"]:
            self.historique_incident.append("DISK remediation not started by policy.")
            return None, "politique : %s" % ("cooldown actif" if plan["cooldown"] else "plan non automatique")
        incident["statut"] = "IN_PROGRESS"
        self.historique.append((t, plan["cible"], ACTION, type_inc))
        return plan, "plan automatique"

    def executer(self, plan):
        """executeHostDiskReclaim : worker1 + NODE_DISK_FULL = scenario -> Bridge (drapeau du scenario, AUCUN repli vers le pod hote) ;
        NODE_DISK_FULL ailleurs (.155) -> refus quels que soient les drapeaux ; PRESSURE / INODES -> chemin d'origine (ancien drapeau,
        controle EN TETE pour tout ce qui n'est pas le scenario, memes cibles qu'avant)."""
        cible = (plan["cible"] or "").strip().lower()
        type_inc = plan["type"]
        est_scn = self.est_scenario(plan["ns"], cible, type_inc)
        if not est_scn and not self.drapeau_legacy:
            return {"voie": "refus", "succes": False, "message": "HOST_RECLAIM_DISK refuse : fonctionnalite DISK desactivee."}
        if plan["ns"].lower() != NS:
            return {"voie": "refus", "succes": False, "message": "HOST_RECLAIM_DISK refuse : namespace cible invalide."}
        if cible not in self.CIBLES_ADMISES:
            return {"voie": "refus", "succes": False, "message": "HOST_RECLAIM_DISK refuse : cible hors allowlist des workers."}
        if type_inc not in self.TYPES_DISQUE:
            return {"voie": "refus", "succes": False, "message": "HOST_RECLAIM_DISK refuse : type d'incident non autorise."}
        if est_scn:
            if not self.drapeau_scenario:
                return {"voie": "refus", "succes": False, "message": "HOST_RECLAIM_DISK refuse : scenario DISK_E2E_158 desactive "
                        "(HOST_DISK_E2E_158_ENABLED) ; l'ancien drapeau ne l'active pas et aucun repli vers le pod hote n'existe."}
            if not self.bridge_actif:
                return {"voie": "bridge", "succes": False, "message": "Bridge desactive ; aucune requete DISK envoyee."}
            iid = plan.get("incident_id")
            if not isinstance(iid, int) or iid <= 0:
                return {"voie": "bridge", "succes": False, "message": "Nettoyage DISK refuse : incident source inconnu."}
            rep = self.client.envoyer_disque(ACTION, TYPE_INCIDENT, NS, cible, cible, SCENARIO, iid)
            succes = rep.ok and rep.execute and rep.exit_code == 0
            message = rep.message or ("Bridge a termine le nettoyage DISK." if succes else "Bridge n'a pas confirme le nettoyage DISK.")
            return {"voie": "bridge", "succes": succes, "message": message, "reponse": rep}
        if type_inc == TYPE_INCIDENT:
            return {"voie": "refus", "succes": False, "message": "HOST_RECLAIM_DISK refuse : NODE_DISK_FULL n'a que la voie du scenario "
                    "DISK_E2E_158 (k8s-worker1) ; k8s-worker2/.155 jamais."}
        self.appels_legacy.append((cible, type_inc))
        return {"voie": "pod_hote", "succes": self.legacy_succes,
                "message": "commande hote bornee (journal 200 Mo) sur %s ; aucun fichier du scenario n'est vise" % cible}

    def nouvelle_verification(self, t, delai_s):
        return {"i": 0, "sains": 0, "prochain": t + delai_s}

    def echantillon(self, etat, prom_usage, t, type_incident=TYPE_INCIDENT):
        """Un tour de verifyDiskRecovery : (CONTINUE | RESOLU | ECHEC, message). Une mesure absente n'est JAMAIS saine."""
        seuil = self.SEUIL_INODES_PCT if type_incident == "NODE_INODES_FULL" else self.SEUIL_DISQUE_PCT
        etat["i"] += 1
        sain = prom_usage is not None and 0.0 <= prom_usage <= 100.0 and prom_usage <= seuil
        etat["sains"] = etat["sains"] + 1 if sain else 0
        msg = "echantillon %d/%d : Prometheus %s, seuil %.1f %% -> %s (sains consecutifs %d/%d)" % (
            etat["i"], self.TENTATIVES_MAX, "illisible" if prom_usage is None else "%.2f %%" % prom_usage, seuil,
            "sain" if sain else "NON sain", etat["sains"], ECHANTILLONS_SAINS)
        if etat["sains"] >= ECHANTILLONS_SAINS:
            return "RESOLU", msg
        if etat["i"] >= self.TENTATIVES_MAX:
            return "ECHEC", msg
        etat["prochain"] = t + self.PAS_VERIFICATION_S
        return "CONTINUE", msg


Delais = namedtuple("Delais", "detection orchestration verification scrape")
DELAIS_DEFAUT = Delais(detection=105, orchestration=10, verification=30, scrape=15)


class MondeE2E(H.MondeSimule):
    """Monde simule (noeud + horloge virtuelle de l'outil) dont la partie « HELIOS » n'est PLUS un modele canne : detection -> incident ->
    politique -> executeur -> VRAI Bridge (HTTP) -> verification Prometheus a 2 echantillons. Les delais sont des PARAMETRES du modele
    (detection ~105 s, orchestration 10 s, 1er echantillon 30 s apres l'action, scrape 15 s), pas des mesures de ce scenario."""

    def __init__(self, taille, dispo, backend, bridge, delais=DELAIS_DEFAUT, incident_id=90001, **kw):
        kw.setdefault("hostname", NOEUD_K8S)
        super(MondeE2E, self).__init__(taille, dispo, helios="e2e", **kw)
        self.backend, self.bridge, self.delais = backend, bridge, delais
        self.chaine = []                 # [(t, etape, detail)]
        self._iid = incident_id
        self._t_scrape = None
        self.prom_usage = None
        self._tentative_faite = False
        self.verif = None
        self.reponse_bridge = None

    def noter_etape(self, etape, detail=""):
        self.chaine.append((self.t, etape, detail))

    def _scrape(self):
        if self._t_scrape is None or self.t - self._t_scrape >= self.delais.scrape:
            self._t_scrape = self.t
            self.prom_usage = H.Fs(self.taille, self.dispo()).usage

    def _helios(self):
        self._scrape()
        u = self.fs().usage
        if self.incident is None:
            if u > H.DETECTION_PCT:
                if self.t_depassement is None:
                    self.t_depassement = self.t
                if self.t - self.t_depassement >= self.delais.detection:
                    self.incident = {"id": self._iid, "statut": "OPEN", "cree": self.t, "type": TYPE_INCIDENT, "ns": NS, "app": NOEUD_K8S}
                    self.noter_etape("DETECTION", "%s sur %s : occupation %.2f %% > %.0f %%" % (TYPE_INCIDENT, NOEUD_K8S, u, H.DETECTION_PCT))
                    self.noter_etape("INCIDENT", "#%s OPEN" % self._iid)
                    self.journal.append((self.t, "incident"))
            else:
                self.t_depassement = None
            return
        i = self.incident
        if i["statut"] == "OPEN" and not self._tentative_faite and self.t - i["cree"] >= self.delais.orchestration:
            self._tentative_faite = True
            self._remedier()
        elif i["statut"] == "IN_PROGRESS" and self.verif is not None and self.t >= self.verif["prochain"]:
            self._verifier()

    def _remedier(self):
        i = self.incident
        plan, raison = self.backend.demarrer_si_eligible(i, self.t)
        if plan is None:
            self.noter_etape("REFUS_POLITIQUE", raison)
            return
        self.action = "%s:IN_PROGRESS" % ACTION
        self.noter_etape("HOST_RECLAIM_DISK", "politique : action %s AUTO sur %s" % (ACTION, plan["cible"]))
        res = self.backend.executer(plan)
        self.reponse_bridge = res.get("reponse")
        self.noter_etape("BRIDGE", "voie=%s succes=%s | %s" % (res["voie"], res["succes"], res["message"]))
        self.journal.append((self.t, "remediation"))
        if res["succes"]:
            self.action = "%s:COMPLETED" % ACTION
            self.verif = self.backend.nouvelle_verification(self.t, self.delais.verification)
        else:
            self.action = "%s:FAILED" % ACTION
            self._echec("remediation non confirmee : %s" % res["message"])

    def _verifier(self):
        statut, msg = self.backend.echantillon(self.verif, self.prom_usage, self.t)
        self.noter_etape("VERIFY", msg)
        if statut == "RESOLU":
            self.incident["statut"] = "RESOLVED"
            self.verif = None
            self.noter_etape("RESOLVED", "incident #%s RESOLVED" % self.incident["id"])
            self.journal.append((self.t, "resolu"))
        elif statut == "ECHEC":
            self._echec("verification non confirmee apres %d echantillons" % self.backend.TENTATIVES_MAX)

    def _echec(self, raison):
        self.incident["statut"] = "RESOLUTION_FAILED"
        self.verif = None
        self.noter_etape("ESCALATE", "RESOLUTION_FAILED : %s (ECHEC du scenario, jamais un resultat nominal)" % raison)
        self.journal.append((self.t, "echec"))


# ------------------------------------------------------------------------------------------------ lancement du scenario
SIM_ARGS = SimpleNamespace(sim_taille_mo=None, sim_usage=None, sim_primaire_cnpg=None, sim_ram_mo=None, sim_drapeau="absent")


def monde_nominal(pannes=None, drapeau_scenario=True, drapeau_legacy=False, bridge_actif=True, automation=True, historique=None,
                  legacy_succes=False, delais=DELAIS_DEFAUT, usage=None, base_bridge=None):
    """(contexte, faits, fe) du scenario nominal sur .158 (valeurs simulees : H.SIM_DEFAUT['158']). `contexte` doit etre utilise avec
    `with` : il demarre/arrete le Bridge en processus. Retourne un objet avec .monde, .bridge, .backend, .client."""
    args = SimpleNamespace(**vars(SIM_ARGS))
    if usage is not None:
        args.sim_usage = usage
    fs, faits = H.faits_simules(CLE, args)
    ctx = SimpleNamespace(faits=faits, fe=faits_e2e_simules(usage_pct=fs.usage), fs=fs)
    ctx.bridge = BridgeEnProcessus()
    ctx.client = ClientBridgeModele(ctx.bridge, drapeau_scenario=drapeau_scenario, bridge_actif=bridge_actif)
    ctx.backend = BackendModele(ctx.client, drapeau_scenario=drapeau_scenario, drapeau_legacy=drapeau_legacy, bridge_actif=bridge_actif,
                                automation=automation, historique=historique, legacy_succes=legacy_succes)
    ctx.monde = MondeE2E(fs.taille, fs.dispo, ctx.backend, ctx.bridge, delais=delais, pannes=pannes, hostname=NOEUD_K8S)
    ctx.bridge.attacher(ctx.monde)
    return ctx


def conclure(res, monde, p=None):
    """Verdict du scenario. RESOLVED_E2E uniquement si TOUT est prouve ; sinon ECHEC_E2E avec la cause. Jamais d'optimisme."""
    p = p or parametres()
    etapes = []
    for _, etape, _ in monde.chaine:
        if etape in CHEMIN_NOMINAL and (not etapes or etapes[-1] != etape):
            etapes.append(etape)
    escalade = any(e == "ESCALATE" for _, e, _ in monde.chaine)
    preuves = {
        "outil_ok": res["verdict"] == "OK",
        "incident_resolu": res["statut"] == "RESOLVED" and monde.incident is not None and monde.incident["statut"] == "RESOLVED",
        "action_completee": monde.action == "%s:COMPLETED" % ACTION,
        "liberee_par_helios": res["liberee_par"] == "helios",
        "recupere": res["recupere"] is True and res["usage_final"] is not None and res["usage_final"] <= RECUPERATION_PCT,
        "liberation_verifiee": res["liberation_ok"] is True,
        "chemin_nominal_complet": tuple(etapes) == CHEMIN_NOMINAL,
        "aucune_escalade": not escalade,
        "plafond_respecte": monde.usage_vrai_max <= PLAFOND_PCT and monde.t_urgence is None,
    }
    manque = [k for k, v in preuves.items() if not v]
    return {"verdict": "RESOLVED_E2E" if not manque else "ECHEC_E2E", "preuves": preuves, "manque": manque, "etapes": etapes,
            "res": res, "injection": bool(res["alloues"]), "incident": monde.incident is not None}


def lancer_scenario(ctx, sortie=print, jeton=None, sha_bridge_attendu=None, image_backend_attendue="pfe-backend:sim-disk158",
                    acquittes=()):
    """Garde des prerequis puis injection + suivi par l'outil (SIMULATION). Prerequis refuses => AUCUN appel au noeud, AUCUN incident.
    Le lancement REEL de .158 applique la meme garde, avec des faits reels : helios_disk_test.principal."""
    p = parametres()
    rapport = evaluer_e2e(ctx.faits, ctx.fe, p, CLE, sha_bridge_attendu, image_backend_attendue, acquittes)
    afficher_rapport_e2e(rapport, sortie)
    if not rapport["go"]:
        return {"verdict": "REFUS_PREREQUIS", "injection": False, "incident": False, "rapport": rapport, "res": None,
                "codes": [x[3] for x in rapport["raisons"] if x[0] == "ROUGE"]}
    jeton = jeton or secrets.token_hex(16)
    ops, sonde, horloge = H.NoeudSimule(ctx.monde), H.SondeSimulee(ctx.monde), H.HorlogeSimulee(ctx.monde)
    with ctx.bridge:
        res = H.executer_test(ops, sonde, horloge, p, jeton, sortie=sortie, identite=NOEUD_K8S)
    out = conclure(res, ctx.monde, p)
    out["rapport"], out["jeton"] = rapport, jeton
    return out


def afficher_chaine(monde, sortie=print):
    sortie("")
    sortie("CHAINE HELIOS (horloge simulee, T+ en secondes depuis le debut du test) :")
    for t, etape, detail in monde.chaine:
        sortie("  T+%5.0fs  %-18s %s" % (t, etape, detail))


def demonstration(sortie=print, **kw):
    """Demonstration SIMULEE complete du chemin nominal. Retourne le dict de lancer_scenario."""
    ctx = monde_nominal(**kw)
    out = lancer_scenario(ctx, sortie)
    if out["verdict"] != "REFUS_PREREQUIS":
        afficher_chaine(ctx.monde, sortie)
        sortie("")
        sortie("SCENARIO %s : %s" % (SCENARIO, out["verdict"]))
        sortie("  etapes : %s" % " -> ".join(out["etapes"]))
        if out["manque"]:
            sortie("  preuves manquantes : %s" % ", ".join(out["manque"]))
        else:
            sortie("  preuves : %s" % ", ".join(sorted(out["preuves"])))
        sortie("  Bridge : %d suppression(s) groupee(s) ; HTTP /action = %d requete(s)" % (
            len(ctx.bridge.exec.rm), sum(1 for c, _ in ctx.bridge.requetes if c == "/action")))
    out["ctx"] = ctx
    return out


def principal(argv=None, sortie=print):
    ap = argparse.ArgumentParser(description="Scenario DISK E2E deterministe DISK_E2E_158 : coherence et simulation (le lancement "
                                 "reel est helios_disk_test.py --target 158 --go).", allow_abbrev=False)
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--coherence", action="store_true", help="verifie la coherence des constantes entre composants (lecture de fichiers)")
    g.add_argument("--demo", action="store_true", help="demonstration simulee du chemin nominal (defaut)")
    g.add_argument("--refus", action="store_true", help="simulation avec un prerequis manquant (drapeau du scenario absent)")
    ap.add_argument("--go", action="store_true", help="NON DISPONIBLE ICI : le lancement reel est helios_disk_test.py --target 158 --go")
    a = ap.parse_args(argv)
    if a.go:
        sortie("REFUS : ce module ne lance rien (ni ssh, ni kubectl, ni remplissage, ni deploiement). Le lancement reel est "
               "`python helios_disk_test.py --target 158 --go --confirm DISK-158-81-82-83 --image-backend TAG` : il applique OBLIGATOIREMENT "
               "la porte de ce module. Voir infra/helios-sot/RUNBOOK-DISK-E2E-158.md : deploiement et autorisation nominative distincts.")
        return 2
    if a.coherence:
        res = verifier_coherence()
        for nom, ok, detail in res:
            sortie("  [%s] %s%s" % ("OK" if ok else "ECART", nom, "" if ok else "  (%s)" % detail))
        sortie("COHERENCE : %s (%d/%d)" % ("OK" if all(o for _, o, _ in res) else "ECART", sum(1 for _, o, _ in res if o), len(res)))
        return 0 if all(o for _, o, _ in res) else 1
    if a.refus:
        ctx = monde_nominal()
        ctx.fe["drapeaux"][DRAPEAU_SCENARIO] = "absent"
        out = lancer_scenario(ctx, sortie)
        sortie("SCENARIO %s : %s | injection=%s incident=%s" % (SCENARIO, out["verdict"], out["injection"], out["incident"]))
        return 0 if out["verdict"] == "REFUS_PREREQUIS" and not ctx.monde.appels else 1
    out = demonstration(sortie)
    return 0 if out["verdict"] == "RESOLVED_E2E" else 1


if __name__ == "__main__":
    sys.exit(principal())
