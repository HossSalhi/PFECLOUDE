#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""helios-ssh-bridge — Camunda execute dans le cluster ET sur les VMs, par SSH.

Le bundle de connecteurs Camunda n'a pas de connecteur SSH : ce service expose
l'execution SSH en HTTP, que le connecteur io.camunda:http-json:1 sait appeler.

Deux endpoints :
  POST /exec    {host, command}            -> commande libre sur une VM
  POST /action  {action, ns, cible, noeud} -> traduit l'action de l'IA en commande
                                              reelle et l'execute

Pourquoi le master : `kubectl` y tourne avec le kubeconfig admin, donc SANS AUCUNE
limite RBAC. C'est la voie "tout sans exception" demandee.

Perimetre de securite :
  - ECOUTE UNIQUEMENT SUR 127.0.0.1 : injoignable depuis le reseau, seul un
    processus de la VM .161 (donc Camunda) peut l'appeler.
  - jeton partage obligatoire (en-tete X-Helios-Token).
  - cibles limitees a une table fixe (master / worker1 / worker2).
  - toute commande est journalisee dans /var/log/helios-ssh-bridge.log AVANT execution.
"""
import base64, json, os, re, shlex, smtplib, subprocess, threading, time, uuid
from email.message import EmailMessage
from http.server import BaseHTTPRequestHandler, HTTPServer

# AI-HOST-DECISION-TELEMETRY-01 : persistance decisionnelle, en AJOUT PUR.
# Import defensif : si le module est absent ou casse, _tele vaut None et
# chaque appel est saute. Le Bridge se comporte alors exactement comme avant.
try:
    import sys as _sys
    _sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import telemetrie as _tele
except Exception:
    _tele = None

NOEUDS = {"master": "192.168.148.153", "worker1": "192.168.148.158", "worker2": "192.168.148.155",
          "monitoring": "192.168.148.160",   # .160 : detection (Prometheus/Loki/Grafana en local)
          "iacamunda": "192.168.148.161",    # .161 : remediation host possible (RAM Ollama, etc.)
          # SESSION9/S9C3 - anomalie C16 : .162 (hostname reel k8s-lb2, HAProxy + membre etcd)
          # etait ABSENTE. Consequence mesuree : le detecteur emettait l IP nue comme cible et,
          # depuis S3C1-E, le Bridge refusait explicitement au lieu de se replier sur master.
          # La detection et la verification marchaient, la remediation etait impossible.
          # SYNCHRONISATION OBLIGATOIRE avec IP2NOM dans detecter.py : le filtre de cible lit
          # NOMS_PAR_IP, lui-meme derive de ce dictionnaire. Corriger un seul des deux fichiers
          # ferait REJETER la cible par le filtre et rendrait un FAUX RESOLVED.
          "lb2": "192.168.148.162"}
UTIL = os.environ.get("HELIOS_SSH_USER", "kube")
# SESSION9/S9C5 : date de demarrage, exposee par /metrics pour distinguer un Bridge
# redemarre d un Bridge continu.
_DEMARRAGE = time.time()
CLE = os.environ.get("HELIOS_SSH_KEY", "/etc/helios/bridge_id_ed25519")
HOTES = os.environ.get("HELIOS_KNOWN_HOSTS", "/etc/helios/known_hosts")
# LOT1/P0-7 : identite Kubernetes restreinte (plus de kubeconfig admin).
KUBECONFIG_BRIDGE = os.environ.get("HELIOS_KUBECONFIG", "/etc/helios/bridge.kubeconfig")
PREFIXE_KUBECTL = "KUBECONFIG=" + KUBECONFIG_BRIDGE + " "
JETON = os.environ.get("HELIOS_BRIDGE_TOKEN", "")
# VMDOWN-POLICY-20261105 : le Bridge n'a plus AUCUNE capacite de mise sous tension. Le jeton du watchdog hote
# (HELIOS_WATCHDOG_TOKEN) n'est plus lu ici : le BACKEND est le seul appelant autorise de POST /restart.
# LOT2/PHASE 20 : l orchestration du cycle (re-verification, comptage d echecs,
# declenchement d email) appartient a CAMUNDA depuis helios-camunda-full-cycle v17.
# Le Bridge reste un EXECUTEUR et un CAPTEUR : il ne decide plus du workflow.
# Mettre HELIOS_BRIDGE_ORCHESTRATION=on pour retablir l ancien comportement.
ORCHESTRATION_BRIDGE = os.environ.get("HELIOS_BRIDGE_ORCHESTRATION", "off").lower() == "on"
# VMDOWN-POLICY-20261105 : plus d'URL de watchdog hote dans le Bridge (aucun appel sortant vers l'hyperviseur).
JOURNAL = "/var/log/helios-ssh-bridge.log"
DELAI = 90

# --- Verification post-remediation + rapport email ------------------------------
# La solution a-t-elle resolu ? Le cycle de detection continu sert lui-meme de verif :
# a chaque /detecter, on re-teste la sante des remediations recentes. Si l'app est
# saine -> succes (l'alarme se clarifie). Si elle reste cassee -> EMAIL rapport complet.
# Email : Gmail REEL via le relais sur l'hote (le TCP direct .161->Gmail est casse par
# le NAT), avec repli MailHog pour que la demo ne rate jamais.
RELAY_HOST = os.environ.get("HELIOS_RELAY_HOST", "192.168.148.1")   # relais hote -> Gmail
RELAY_PORT = int(os.environ.get("HELIOS_RELAY_PORT", "2526"))
MAILHOG_HOST = os.environ.get("HELIOS_MAILHOG_HOST", "127.0.0.1")
MAILHOG_PORT = int(os.environ.get("HELIOS_MAILHOG_PORT", "1025"))
EMAIL_FROM = os.environ.get("HELIOS_EMAIL_FROM", "helios-aiops@orange.tn")
EMAIL_TO = os.environ.get("HELIOS_EMAIL_TO", "houssamsalhi972@gmail.com")
GRACE_VERIF = int(os.environ.get("HELIOS_GRACE_VERIF", "40"))   # s : laisser la remediation agir
SUIVI = {}                       # (ns,cible) -> {type,action,resultat,ok,ts,emailed,echecs}
_SUIVI_LOCK = threading.Lock()

# --------------------------------------------- POD_CRASH_LOOP-FIX-01 : rollback CIBLE
# POURQUOI. Toutes les variantes d action ROLLBACK produisaient
# `kubectl rollout undo deployment/<c> -n <ns>` SANS --to-revision, donc un retour a la
# revision N-1. Sur un historique a deux templates (sain / crash) cela ALTERNE. Mesure du
# lot POD_CRASH_LOOP-E2E-01 : revisions 3 -> 9, six rollbacks, oscillation exacte, et le
# deploiement laisse sur la revision qui crashe. `ROLLBACK_TO_LAST_HEALTHY_REVISION`
# nommait une semantique que la commande n implementait pas.
#
# CE QUI CHANGE. Le rollback vise desormais une revision EXPLICITE, prouvee saine a partir
# de l etat Kubernetes reel, et REFUSE d agir s il ne peut pas la prouver. Un verrou
# d idempotence empeche deux instances Camunda successives d agir sur le meme etat logique.

# Fenetre couvrant la convergence du rollout PUIS la stabilisation observee. Le lot E2E-01
# a mesure un cycle de detection de 60 s et une fenetre de detection de 3 min
# (max_over_time[3m]) : en deca de 180 s le verrou laisserait passer une seconde action sur
# le meme incident. 300 s ajoute une marge et reste inferieur au delai au-dela duquel un
# nouveau CrashLoop est legitimement un nouvel incident.
COOLDOWN_ROLLBACK = 300
_ROLLBACKS = {}                  # cle idempotence -> {revision, ts, etat}
_ROLLBACKS_LOCK = threading.Lock()

# `waiting.reason` consideres comme FAUTIFS pour juger la sante d une revision.
ETATS_FAUTIFS = ("CrashLoopBackOff", "CreateContainerError", "CreateContainerConfigError",
                 "ImagePullBackOff", "ErrImagePull", "RunContainerError")


def cle_idempotence(ns, cible, typ, action):
    """Cle DETERMINISTE d une occurrence logique.

    Volontairement SANS horodatage : deux instances Camunda successives traitant le meme
    incident doivent produire la MEME cle, sinon le verrou ne sert a rien. Les champs
    `incident_id` et `correlation_id` emis par le BPMN portent l heure de l instance et
    sont donc inutilisables comme cle. Toutes les variantes ROLLBACK partagent la meme
    famille : changer de libelle ne doit pas contourner le verrou.
    """
    fam = "ROLLBACK" if "ROLLBACK" in (action or "").upper() else (action or "").upper()
    return "%s|%s|%s|%s" % (ns or "", cible or "", (typ or "").upper(), fam)


def revision_saine(ns, cible):
    """Derniere revision PROUVEE saine d un Deployment, depuis l etat Kubernetes reel.

    CONTRAT DE SANTE, sans heuristique : une revision est saine si le ReplicaSet qui la
    porte a `status.readyReplicas >= 1` ET que ses Pods sont Ready sans `waiting.reason`
    fautif. `revision < revision courante` n est PAS une preuve de sante et n est jamais
    utilise. Une revision historique deja ramenee a zero par Kubernetes n est donc pas
    prouvable : la fonction REFUSE alors, elle n invente aucun repli.

    LECTURE COMPACTE : `executer()` tronque stdout a 4000 caracteres. On ne demande donc
    JAMAIS de JSON complet, seulement des projections jsonpath de quelques dizaines
    d octets par objet.

    Rend {"revision": int, "courante": int, "preuve": str} ou {"refus": str}.
    """
    fmt_rs = (r'{range .items[*]}'
              r'{.metadata.annotations.deployment\.kubernetes\.io/revision}|'
              r'{.metadata.labels.pod-template-hash}|'
              r'{.status.readyReplicas}' + r'{"\n"}{end}')
    fmt_pod = (r'{range .items[*]}{.metadata.labels.pod-template-hash}|'
               r'{range .status.containerStatuses[*]}{.ready},{.state.waiting.reason};{end}'
               + r'{"\n"}{end}')

    r1 = executer_kubectl("kubectl get rs -n %s -l app=%s -o jsonpath='%s'"
                          % (ns, cible, fmt_rs))
    if not r1.get("ok"):
        return {"refus": "lecture des ReplicaSets impossible : %s" % (r1.get("resume") or "")[:110]}
    r2 = executer_kubectl("kubectl get pods -n %s -l app=%s -o jsonpath='%s'"
                          % (ns, cible, fmt_pod))
    if not r2.get("ok"):
        return {"refus": "lecture des Pods impossible : %s" % (r2.get("resume") or "")[:110]}

    # etat des Pods, par pod-template-hash
    fautifs, prets = {}, {}
    for ligne in (r2.get("stdout") or "").splitlines():
        if "|" not in ligne:
            continue
        h, conteneurs = ligne.split("|", 1)
        for c in conteneurs.split(";"):
            if not c.strip():
                continue
            bouts = c.split(",")
            pret = bouts[0].strip().lower() == "true"
            raison = bouts[1].strip() if len(bouts) > 1 else ""
            if raison in ETATS_FAUTIFS:
                fautifs[h] = raison
            if pret:
                prets[h] = prets.get(h, 0) + 1

    # revisions, par ReplicaSet
    revisions = []
    for ligne in (r1.get("stdout") or "").splitlines():
        bouts = ligne.split("|")
        if len(bouts) < 3:
            continue
        try:
            rev = int(bouts[0].strip() or "0")
        except (TypeError, ValueError):
            continue
        if rev <= 0:
            continue
        h = bouts[1].strip()
        try:
            ready = int(bouts[2].strip() or "0")
        except (TypeError, ValueError):
            ready = 0
        revisions.append({"rev": rev, "hash": h, "ready": ready, "fautif": fautifs.get(h, "")})
    if not revisions:
        return {"refus": "aucune annotation de revision exploitable pour %s/%s" % (ns, cible)}

    courante = max(x["rev"] for x in revisions)
    saines = [x for x in revisions
              if x["ready"] >= 1 and not x["fautif"] and prets.get(x["hash"], 0) >= 1]
    if not saines:
        return {"refus": "aucune revision prouvee saine : aucun ReplicaSet ne porte de Pod Ready "
                         "(revision courante %d)" % courante, "courante": courante}
    meilleure = max(saines, key=lambda x: x["rev"])
    return {"revision": meilleure["rev"], "courante": courante,
            "preuve": "revision %d : ReplicaSet %s, readyReplicas=%d, aucun etat fautif"
                      % (meilleure["rev"], meilleure["hash"] or "?", meilleure["ready"])}

def preparer_rollback(action, typ, ns, cible, revision_demandee=None, maintenant=None):
    """Decide d un rollback CIBLE. N execute rien.

    Rend l un de :
      {"refus": raison}                        -> ne rien executer
      {"noop": raison, "revision": N, "cle":}  -> deja dans l etat vise, passer en verification
      {"cmd": commande, "revision": N, ...}    -> executer exactement cette commande
    """
    maintenant = time.time() if maintenant is None else maintenant
    cle = cle_idempotence(ns, cible, typ, action)

    # 1. VERROU INTER-INSTANCES. Une action de la meme famille sur la meme cible a-t-elle
    #    deja ete executee dans la fenetre ? Si oui, aucune seconde action : on laisse la
    #    verification de la premiere conclure.
    with _ROLLBACKS_LOCK:
        precedent = _ROLLBACKS.get(cle)
    if precedent and (maintenant - precedent["ts"]) < COOLDOWN_ROLLBACK:
        return {"noop": "rollback deja execute il y a %d s vers la revision %s "
                        "(cooldown %d s) : aucune seconde action"
                        % (int(maintenant - precedent["ts"]), precedent["revision"],
                           COOLDOWN_ROLLBACK),
                "revision": precedent["revision"], "cle": cle}

    # 2. REVISION CIBLE. Prouvee depuis Kubernetes ; si l appelant en fournit une, elle est
    #    VERIFIEE contre cette preuve, jamais acceptee sur parole.
    etat = revision_saine(ns, cible)
    if "refus" in etat:
        return {"refus": etat["refus"]}
    revision = etat["revision"]
    origine = "prouvee par le Bridge"
    if revision_demandee not in (None, ""):
        try:
            demandee = int(revision_demandee)
        except (TypeError, ValueError):
            return {"refus": "revision invalide : %r" % (revision_demandee,)}
        if demandee != revision:
            return {"refus": "revision %d demandee mais non prouvee saine "
                             "(seule la revision %d l est)" % (demandee, revision)}
        origine = "fournie par l appelant et verifiee"

    # 3. DEJA DANS L ETAT VISE. On ne rejoue pas un rollback vers la revision courante.
    if etat["courante"] == revision:
        return {"noop": "revision courante %d deja la derniere prouvee saine : aucune action"
                        % revision, "revision": revision, "cle": cle}

    return {"cmd": "kubectl rollout undo deployment/%s -n %s --to-revision=%d"
                   % (cible, ns, revision),
            "revision": revision, "cle": cle, "origine": origine,
            "preuve": etat.get("preuve", "")}

SUR = re.compile(r"^[a-z0-9][a-z0-9.-]{0,62}$")   # noms K8s : pas d'injection shell


def tracer(*parts):
    ligne = time.strftime("%Y-%m-%dT%H:%M:%S") + " " + " ".join(str(p) for p in parts)
    try:
        with open(JOURNAL, "a", encoding="utf-8") as f:
            f.write(ligne + "\n")
    except Exception:
        pass
    print(ligne, flush=True)


def executer(noeud, commande):
    ip = NOEUDS[noeud]
    # HOST-ACTION-SAFETY-HARDENING-01 - garde air-gap au point de passage UNIQUE
    # des commandes hote. L interdit ne repose donc pas sur l absence d une entree
    # de table : meme une commande construite ailleurs est refusee ici, avant SSH.
    _interdit = commande_interdite(commande)
    if _interdit:
        tracer("COMMANDE REFUSEE", noeud, "|", _interdit, "|", str(commande)[:120])
        return {"host": noeud, "ip": ip, "command": commande, "exitCode": -1,
                "stdout": "", "stderr": _interdit, "ok": False,
                "refus": "COMMAND_FORBIDDEN", "resume": "REFUS " + _interdit}
    tracer("EXEC", noeud, ip, "|", commande)
    # LOT1/P0-4 : authentification par CLE dediee (plus de mot de passe), et les cles
    # d hote sont VERIFIEES contre un known_hosts epingle (plus de MITM silencieux).
    argv = ["ssh", "-i", CLE,
            "-o", "IdentitiesOnly=yes", "-o", "BatchMode=yes",
            "-o", "StrictHostKeyChecking=yes", "-o", "UserKnownHostsFile=" + HOTES,
            "-o", "ConnectTimeout=8", "-o", "LogLevel=ERROR",
            "%s@%s" % (UTIL, ip), commande]
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=DELAI)
        res = {"host": noeud, "ip": ip, "command": commande, "exitCode": p.returncode,
               "stdout": p.stdout.strip()[:4000], "stderr": p.stderr.strip()[:2000],
               "ok": p.returncode == 0}
    except subprocess.TimeoutExpired:
        res = {"host": noeud, "ip": ip, "command": commande, "exitCode": 124,
               "stdout": "", "stderr": "timeout apres %ds" % DELAI, "ok": False}
    # Resume HONNETE : en cas d'echec on remonte stderr, jamais une chaine vide qui
    # se lirait comme un succes dans la trace.
    if res["ok"]:
        res["resume"] = res["stdout"][:300].replace("\n", " ") or "commande executee"
    else:
        res["resume"] = "ECHEC(exit=%s) %s" % (res["exitCode"],
                        (res["stderr"] or res["stdout"] or "aucune sortie")[:250].replace("\n", " "))
    tracer("RES ", noeud, "exit=%s" % res["exitCode"], "|", res["resume"][:130])
    return res


# ============================================================================
# CPU-HOST-ACTION-INTEGRATION-01 — CAPACITE D EXECUTION : HOST_SERVICE_CPU_QUOTA
# ----------------------------------------------------------------------------
# CE QUE CE BLOC AJOUTE : la capacite d APPLIQUER, de VERIFIER, de RETIRER et de
# faire EXPIRER une limite CPU systemd sur une unite host explicitement
# autorisee.
#
# CE QU IL N AJOUTE PAS : aucune regle "NODE_HIGH_CPU implique cette action".
# ACTION_PAR_TYPE_MESURE n est PAS modifiee. Le Bridge n execute cette action
# que si une couche amont la demande NOMMEMENT, avec ses parametres.
#
# Fondement experimental (lot CPU-HOST-ACTION-UNBLOCK-01, 3 repetitions) :
#   .161, 3 coeurs, ollama.service a 94 % du CPU machine
#   CPUQuota=150%  ->  ollama 50,0 %  et  CPU hote 98 % -> 54 %
#   0 requete perdue, rollback par `systemctl revert` verifie 3 fois sur 3.
# ============================================================================

ACTION_QUOTA = "HOST_SERVICE_CPU_QUOTA"
ACTION_QUOTA_RETRAIT = "HOST_SERVICE_CPU_QUOTA_REMOVE"
ACTION_QUOTA_ETAT = "HOST_SERVICE_CPU_QUOTA_STATUS"
ACTIONS_QUOTA = (ACTION_QUOTA, ACTION_QUOTA_RETRAIT, ACTION_QUOTA_ETAT)

# ALLOWLIST. Cle = nom de noeud du Bridge, valeur = unites systemd autorisees.
# N y figure que ce qui a ete valide EXPERIMENTALEMENT. Toute extension exige
# une validation dediee (nombre de coeurs, part du consommateur, effet mesure).
QUOTA_CIBLES_AUTORISEES = {
    "iacamunda": {"ollama.service"},          # .161, 3 coeurs — valide par mesure
    # HOST-CPU-HOSTLOAD-CAPABILITY-IMPLEMENTATION-01 (§21). UNE unite par worker,
    # nommee explicitement. Le §22 interdit toute forme generique : ni "*", ni
    # "*.service", ni kubepods.slice. helios-cpuload.service est un service de
    # LABORATOIRE, sans fonction de production, installe par ce lot.
    "worker1": {"helios-cpuload.service"},    # .158, 2 coeurs — cible de validation
    "worker2": {"helios-cpuload.service"},    # .155, 2 coeurs — cible de secours
    "master": {"helios-cpuload.service"},     # .153 CPU-MASTER-20261029 (quota labo uniquement)
}

# ============================================================================
# HOST-CPU-HOSTLOAD-CAPABILITY-IMPLEMENTATION-01 — ARRET DE LA SOURCE DE CHARGE
#
# CE QUE CE BLOC AJOUTE : la capacite d ARRETER et de REDEMARRER une unite
# systemd de LABORATOIRE, strictement limitee a une allowlist (noeud, unite).
# C est la seconde des deux remediations host-level retenues par
# HOST-CPU-HOSTLOAD-CAPABILITY-01, l autre etant HOST_SERVICE_CPU_QUOTA.
#
# CE QUE CE BLOC N AJOUTE PAS : aucune regle « NODE_HIGH_CPU implique cette
# action ». ACTION_PAR_TYPE_MESURE est INCHANGEE et continue d escalader.
# La selection reste le fait d une couche amont, jamais du Bridge.
#
# POURQUOI UNE ACTION DEDIEE ET NON UN `STOP_SERVICE` GENERIQUE : un parametre
# de service libre permettrait, par erreur d appel ou par proposition d un
# modele, un `systemctl stop kubelet`. La cible est donc resolue depuis
# l ALLOWLIST a partir du seul nom de noeud, et une cible fournie dans la
# requete n est acceptee que si elle est EGALE a celle de l allowlist.
# ============================================================================

ACTION_CPULOAD_STOP = "STOP_CPU_LOAD_SERVICE"
ACTION_CPULOAD_START = "START_CPU_LOAD_SERVICE"    # rollback explicite du precedent
ACTION_CPULOAD_ETAT = "CPU_LOAD_SERVICE_STATUS"    # lecture seule
ACTIONS_CPULOAD = (ACTION_CPULOAD_STOP, ACTION_CPULOAD_START, ACTION_CPULOAD_ETAT)

# ALLOWLIST. Cle = nom de noeud du Bridge, valeur = L UNIQUE unite autorisee.
# Une seule unite par noeud, volontairement : il n y a aucune raison qu une
# action de laboratoire puisse viser plusieurs cibles.
ALLOWED_CPU_LOAD_TARGETS = {
    "worker1": "helios-cpuload.service",
    "worker2": "helios-cpuload.service",
    "master": "helios-cpuload.service",     # CPU-MASTER-20261029
}

# INTERDITS ABSOLUS, meme si un jour ils entraient dans l allowlist par erreur.
# Ces services portent la DETECTION, la DECISION, l EXECUTION ou la
# DISPONIBILITE du cluster. Les plafonner degraderait la capacite meme de
# constater l incident, de decider, d agir, ou de tenir le quorum.
QUOTA_SERVICES_INTERDITS = frozenset({
    "helios-etcd.service", "kubelet.service", "containerd.service",
    "prometheus.service", "loki.service", "grafana-server.service",
    "node_exporter.service", "prometheus-node-exporter.service",
    "helios-cpu-host-context.service",        # l instrument de mesure
    "helios-ssh-bridge.service",              # le bras d execution
    "pfe-ai-service.service",                 # le decideur
    "helios-quorum-guard.service",            # la protection du quorum
    "sshd.service", "ssh.service", "systemd-journald.service",
})

# Bornes. 20 % = un cinquieme de coeur, en dessous un service utile s etrangle.
# 400 % = quatre coeurs, au-dela le plafond ne borne plus rien sur ce parc
# (la VM la plus large en a trois).
QUOTA_MIN_PCT = 20
QUOTA_MAX_PCT = 400
QUOTA_DUREE_DEFAUT_S = 900                    # 15 min
QUOTA_DUREE_MAX_S = 3600                      # 1 h : un plafond n est pas un reglage

# Unite systemd : minuscules, chiffres, . _ @ - et suffixe .service obligatoire.
# Interdit donc tout fragment de shell, nom de pod, de deploiement ou de process.
UNITE_SYSTEMD = re.compile(r"^[a-z0-9][a-z0-9._@-]{0,62}\.service$")

# ============================================================================
# DISK-E2E-158-01 - CAPACITE D EXECUTION : HOST_RECLAIM_DISK (scenario DISK_E2E_158)
# ----------------------------------------------------------------------------
# CE QUE CE BLOC AJOUTE : la capacite de LIBERER, sur UN SEUL noeud (.158 = worker1), les
# fichiers de remplissage d UN scenario de test identifie par son JETON, puis de MESURER
# l occupation REELLE de / avant de rendre un verdict.
#
# CE QU IL N AJOUTE PAS : aucune regle « NODE_DISK_FULL implique cette action ».
# ACTION_PAR_TYPE_MESURE est INCHANGEE ; catalogue() est INCHANGE ; le Bridge n execute cette
# action que si l amont (le backend) la demande NOMMEMENT, avec le nom du scenario.
#
# GARANTIES (chacune est verrouillee par test_bridge_disk_reclaim.py) :
#   - une seule cible (worker1), un seul type (NODE_DISK_FULL), un seul scenario (DISK_E2E_158) ;
#   - les SEULS fichiers supprimables sont ceux du repertoire exclusif /var/lib/helios-diskfill
#     (racine, 0700) dont le nom est EXACTEMENT helios-diskfill-<jeton 32 hex>-<NNN>.bin, fichier
#     regulier, proprietaire root, un seul lien, UN SEUL jeton present ;
#   - suppression par `rm -f --` sur des chemins EXACTS : aucun joker, aucun -r, aucun find -delete ;
#   - toute anomalie (repertoire non conforme, deux jetons, homonyme non regulier, liste suspecte)
#     = REFUS : rien n est touche ;
#   - IDEMPOTENT : sans fichier a supprimer, rien n est supprime ; ce n est un succes que si
#     l occupation mesuree est DEJA <= 77 % ;
#   - JAMAIS de succes sans mesure : ok = (aucun fichier du jeton restant) ET (occupation reelle
#     de / <= 77 % apres liberation) ; le code retour de `rm` n est PAS une preuve ;
#   - un incident dont un premier jeton a ete lie ne peut pas faire supprimer un AUTRE jeton ;
#   - le minuteur d expiration et la garde du scenario ne sont PAS touches (ils appartiennent a
#     l outil de lancement, qui les desarme lui-meme apres verification).
# ============================================================================

ACTION_DISQUE = "HOST_RECLAIM_DISK"
ACTIONS_DISQUE = (ACTION_DISQUE,)
DISQUE_SCENARIO = "DISK_E2E_158"
DISQUE_NOEUD = "worker1"                      # .158 : l UNIQUE noeud autorise
DISQUE_CIBLE_K8S = "k8s-worker1"
DISQUE_TYPE = "NODE_DISK_FULL"
DISQUE_NS = "k8s-nodes"
DISQUE_DOSSIER = "/var/lib/helios-diskfill"   # repertoire EXCLUSIF du scenario (racine, 0700)
# Motifs SANS ancres : ils ne servent QU AVEC fullmatch() (un `$` accepterait un saut de ligne final).
DISQUE_RE_FICHIER = re.compile(r"helios-diskfill-([0-9a-f]{32})-([0-9]{3})\.bin")
DISQUE_RE_JETON = re.compile(r"[0-9a-f]{32}")
DISQUE_RE_INCIDENT = re.compile(r"[0-9]{1,12}")
DISQUE_MAX_FICHIERS = 16
DISQUE_MAX_TOTAL = 6 * 1024 * 1024 * 1024     # jamais plus de 6 Gio a liberer
DISQUE_RECUPERATION_PCT = 77.0                # recupere <=> occupation de / <= 77 %
DISQUE_LISTE_MAX = 3900                       # executer() tronque stdout a 4000 : au-dela, la liste est suspecte
DISQUE_LIAISONS_MAX = 64
_DISQUE_LIAISONS = {}                         # incident_id -> jeton lie au premier passage
_DISQUE_VERROU = threading.RLock()


def disque_usage_pct(total_blocs, dispo_blocs):
    """Meme formule que la detection : (1 - disponible / taille) * 100, sur f_bavail / f_blocks."""
    if total_blocs <= 0 or dispo_blocs < 0 or dispo_blocs > total_blocs:
        return None
    return (1.0 - float(dispo_blocs) / float(total_blocs)) * 100.0


def disque_valider_requete(d, noeud):
    """(ok, rejet, motif). AUCUNE commande n est lancee ici."""
    action = str(d.get("action", "")).strip().upper()
    typ = str(d.get("type", "") or d.get("incident_type", "")).strip().upper()
    ns = str(d.get("ns", "")).strip()
    cible = str(d.get("cible", "")).strip()
    scenario = str(d.get("scenario", "")).strip()
    incident = str(d.get("incident_id", "") or "").strip()
    if action != ACTION_DISQUE:
        return False, "REJECTED_ACTION", "action %r non prise en charge" % action[:40]
    if noeud != DISQUE_NOEUD or cible != DISQUE_CIBLE_K8S:
        return False, "REJECTED_TARGET_NOT_ALLOWLISTED", "cible %r / noeud %r hors allowlist (%s)" % (
            cible[:40], str(noeud)[:40], DISQUE_NOEUD)
    if typ != DISQUE_TYPE:
        return False, "REJECTED_TYPE", "type d incident %r non autorise (%s)" % (typ[:40], DISQUE_TYPE)
    if ns != DISQUE_NS:
        return False, "REJECTED_NAMESPACE", "namespace %r non autorise (%s)" % (ns[:40], DISQUE_NS)
    if scenario != DISQUE_SCENARIO:
        return False, "REJECTED_SCENARIO", "scenario %r inconnu (%s exige)" % (scenario[:40], DISQUE_SCENARIO)
    if incident and not DISQUE_RE_INCIDENT.fullmatch(incident):
        return False, "REJECTED_INCIDENT_ID", "identifiant d incident invalide"
    return True, None, ""


def disque_parse_find(sortie):
    """Sortie de `find -printf '%y|%u|%m|%n|%s|%f\n'` -> (entrees, illisibles). Le NOM est le DERNIER champ."""
    entrees, illisibles = [], 0
    for ligne in str(sortie or "").split("\n"):
        if not ligne:
            continue
        p = ligne.split("|", 5)
        if len(p) != 6 or len(p[0]) != 1 or not p[3].isdigit() or not p[4].isdigit() or not p[5]:
            illisibles += 1
            continue
        entrees.append({"type": p[0], "proprio": p[1], "mode": p[2], "liens": int(p[3]), "taille": int(p[4]), "nom": p[5]})
    return entrees, illisibles


def disque_plan(entrees, illisibles=0):
    """Plan PUR (aucune E/S) de la liberation : {refus, rejet, jeton, fichiers, taille, etrangers}.
    `refus` n est non vide que si RIEN ne doit etre supprime. Une entree dont le nom n est PAS exactement celui
    d un fichier du scenario est ETRANGERE : comptee, JAMAIS touchee."""
    plan = {"refus": "", "rejet": "", "jeton": None, "fichiers": [], "taille": 0, "etrangers": 0}

    def refuser(rejet, motif):
        plan["rejet"], plan["refus"] = rejet, motif
        plan["jeton"], plan["fichiers"], plan["taille"] = None, [], 0
        return plan

    if illisibles:
        return refuser("UNREADABLE_LISTING", "%d ligne(s) du repertoire illisible(s) : aucune suppression" % illisibles)
    jetons, fichiers, taille = set(), [], 0
    for e in entrees:
        m = DISQUE_RE_FICHIER.fullmatch(e["nom"])
        if not m:
            plan["etrangers"] += 1
            continue
        index = int(m.group(2))
        if (e["type"] != "f" or e["proprio"] != "root" or e["liens"] != 1 or e["taille"] <= 0
                or not 1 <= index <= DISQUE_MAX_FICHIERS):
            return refuser("ANOMALOUS_ENTRY", "entree homonyme non conforme : %s (type=%s proprietaire=%s liens=%s taille=%s)" % (
                e["nom"][:60], e["type"], e["proprio"], e["liens"], e["taille"]))
        jetons.add(m.group(1))
        fichiers.append(e["nom"])
        taille += e["taille"]
    if len(jetons) > 1:
        return refuser("AMBIGUOUS_TOKENS", "%d jetons presents : le Bridge ne devine pas lequel est le bon" % len(jetons))
    if len(fichiers) > DISQUE_MAX_FICHIERS:
        return refuser("TOO_MANY_FILES", "%d fichiers > %d" % (len(fichiers), DISQUE_MAX_FICHIERS))
    if taille > DISQUE_MAX_TOTAL:
        return refuser("TOO_LARGE", "%d Mo a liberer > limite de %d Mo" % (taille // 1048576, DISQUE_MAX_TOTAL // 1048576))
    plan["jeton"] = next(iter(jetons)) if jetons else None
    plan["fichiers"] = sorted(fichiers)
    plan["taille"] = taille
    return plan


def disque_lire_dossier(noeud):
    """Etat du repertoire exclusif : {present, entrees, illisibles} ou {erreur, rejet}. LECTURE SEULE."""
    r = executer(noeud, "sudo -n /usr/bin/stat -c '%F|%U|%a' " + DISQUE_DOSSIER)
    if not r.get("ok"):
        texte = ((r.get("stderr") or "") + " " + (r.get("stdout") or "")).lower()
        if "no such file" in texte or "aucun fichier" in texte:
            return {"present": False, "entrees": [], "illisibles": 0}
        return {"erreur": "stat du repertoire impossible : %s" % str(r.get("resume") or "echec")[:120],
                "rejet": "DIRECTORY_UNREADABLE"}
    lu = (r.get("stdout") or "").strip()
    if lu != "directory|root|700":
        return {"erreur": "repertoire %s non conforme (type|proprietaire|mode attendu directory|root|700, lu %s)" % (
            DISQUE_DOSSIER, lu[:60]), "rejet": "UNSAFE_DIRECTORY"}
    liste = executer(noeud, "sudo -n /usr/bin/find " + DISQUE_DOSSIER
                     + " -maxdepth 1 -mindepth 1 -printf '%y|%u|%m|%n|%s|%f\\n'")
    if not liste.get("ok"):
        return {"erreur": "liste du repertoire impossible : %s" % str(liste.get("resume") or "echec")[:120],
                "rejet": "DIRECTORY_UNREADABLE"}
    sortie = liste.get("stdout") or ""
    if len(sortie) >= DISQUE_LISTE_MAX:
        return {"erreur": "liste du repertoire trop longue (%d caracteres) : suspecte" % len(sortie),
                "rejet": "LISTING_TRUNCATED"}
    entrees, illisibles = disque_parse_find(sortie)
    return {"present": True, "entrees": entrees, "illisibles": illisibles}


def disque_mesurer(noeud):
    """Occupation REELLE de / : {ok, usage, taille, dispo} ou {ok: False, erreur}. LECTURE SEULE (stat -f)."""
    r = executer(noeud, "sudo -n /usr/bin/stat -f -c '%S|%b|%a' /")
    if not r.get("ok"):
        return {"ok": False, "erreur": "mesure impossible : %s" % str(r.get("resume") or "echec")[:120]}
    p = (r.get("stdout") or "").strip().split("|")
    try:
        taille_bloc, total, dispo = int(p[0]), int(p[1]), int(p[2])
    except (ValueError, IndexError):
        return {"ok": False, "erreur": "mesure illisible : %r" % (r.get("stdout") or "")[:60]}
    usage = disque_usage_pct(total, dispo)
    if usage is None or taille_bloc <= 0:
        return {"ok": False, "erreur": "mesure incoherente (%s)" % "|".join(p)[:60]}
    return {"ok": True, "usage": usage, "taille": total * taille_bloc, "dispo": dispo * taille_bloc}


def _disque_lier(incident, jeton):
    with _DISQUE_VERROU:
        if incident not in _DISQUE_LIAISONS and len(_DISQUE_LIAISONS) >= DISQUE_LIAISONS_MAX:
            _DISQUE_LIAISONS.pop(next(iter(_DISQUE_LIAISONS)))
        _DISQUE_LIAISONS.setdefault(incident, jeton)


def _disque_reponse(noeud, ok, execute, stdout="", stderr="", **plus):
    r = {"action": ACTION_DISQUE, "scenario": DISQUE_SCENARIO, "noeud": noeud, "execute": bool(execute), "ok": bool(ok),
         "exitCode": 0 if ok else -1, "stdout": stdout, "stderr": stderr,
         "seuil_recuperation_pct": DISQUE_RECUPERATION_PCT}
    r.update(plus)
    return r


def disque_reclamer(noeud, d):
    """Valide, lit, supprime les SEULS fichiers du jeton, relit, MESURE, conclut. Rend le corps de la reponse /action.
    `recommend_only` rend la validation et le plan SANS rien supprimer."""
    valide, rejet, motif = disque_valider_requete(d, noeud)
    if not valide:
        return _disque_reponse(noeud, False, False, stderr="requete refusee : " + motif, rejet=rejet)
    incident = str(d.get("incident_id", "") or "").strip()
    simulation = str(d.get("recommend_only", "")).lower() in ("1", "true", "yes", "oui")
    with _DISQUE_VERROU:
        lec = disque_lire_dossier(noeud)
        if "erreur" in lec:
            return _disque_reponse(noeud, False, False, stderr=lec["erreur"], rejet=lec["rejet"])
        avant = disque_mesurer(noeud)
        if not avant["ok"]:
            return _disque_reponse(noeud, False, False, stderr=avant["erreur"], rejet="MEASURE_FAILED")
        plan = disque_plan(lec["entrees"], lec["illisibles"])
        usage_avant = round(avant["usage"], 2)
        if plan["refus"]:
            return _disque_reponse(noeud, False, False, stderr=plan["refus"], rejet=plan["rejet"],
                                   etrangers=plan["etrangers"], usage_avant_pct=usage_avant)
        lie = _DISQUE_LIAISONS.get(incident) if incident else None
        if plan["jeton"] and lie and lie != plan["jeton"]:
            return _disque_reponse(noeud, False, False, rejet="TOKEN_CHANGED", etrangers=plan["etrangers"],
                                   stderr="l incident %s est lie au jeton %s : un autre jeton est present, rien n est supprime" % (
                                       incident, lie[:8]), usage_avant_pct=usage_avant)
        if simulation:
            return _disque_reponse(noeud, True, False, validation="ACCEPTED", execution_mode="RECOMMEND_ONLY",
                                   stdout="validation seule : %d fichier(s) a supprimer (%d Mo), %d entree(s) etrangere(s) intacte(s)" % (
                                       len(plan["fichiers"]), plan["taille"] // 1048576, plan["etrangers"]),
                                   jeton=plan["jeton"], fichiers=len(plan["fichiers"]), etrangers=plan["etrangers"],
                                   usage_avant_pct=usage_avant)
        tailles = dict((e["nom"], e["taille"]) for e in lec["entrees"])
        restants = []
        if plan["fichiers"]:
            if incident:
                _disque_lier(incident, plan["jeton"])
            commande = "sudo -n /usr/bin/rm -f -- " + " ".join(shlex.quote(DISQUE_DOSSIER + "/" + n) for n in plan["fichiers"])
            tracer("DISQUE SUPPRESSION", noeud, "jeton", plan["jeton"][:8], "|", len(plan["fichiers"]), "fichier(s) exact(s)")
            executer(noeud, commande)          # le code retour n est PAS une preuve : la relecture tranche
            apres = disque_lire_dossier(noeud)
            if "erreur" in apres:
                return _disque_reponse(noeud, False, True, rejet=apres["rejet"], jeton=plan["jeton"],
                                       stderr="suppression non verifiee : " + apres["erreur"], usage_avant_pct=usage_avant)
            presents = set(e["nom"] for e in apres["entrees"])
            restants = [n for n in plan["fichiers"] if n in presents]
        mesure = disque_mesurer(noeud)
        supprimes = len(plan["fichiers"]) - len(restants)
        octets = sum(tailles.get(n, 0) for n in plan["fichiers"] if n not in restants)
        commun = {"jeton": plan["jeton"], "supprimes": supprimes, "restants": restants, "octets_liberes": octets,
                  "etrangers": plan["etrangers"], "usage_avant_pct": usage_avant, "noop": not plan["fichiers"]}
        if not mesure["ok"]:
            return _disque_reponse(noeud, False, bool(plan["fichiers"]), stderr=mesure["erreur"], rejet="MEASURE_FAILED",
                                   recupere=False, **commun)
        usage_apres = round(mesure["usage"], 2)
        recupere = (not restants) and mesure["usage"] <= DISQUE_RECUPERATION_PCT
        commun["usage_apres_pct"], commun["recupere"] = usage_apres, recupere
        if recupere:
            if not plan["fichiers"]:
                texte = "aucun fichier du scenario a supprimer ; occupation %.2f %% deja <= %.0f %% (idempotent)" % (
                    usage_apres, DISQUE_RECUPERATION_PCT)
            else:
                texte = "%d fichier(s) du jeton %s supprime(s) (%d Mo) ; occupation %.2f %% -> %.2f %% (<= %.0f %%)" % (
                    supprimes, plan["jeton"][:8], octets // 1048576, usage_avant, usage_apres, DISQUE_RECUPERATION_PCT)
            return _disque_reponse(noeud, True, True, stdout=texte, **commun)
        if restants:
            return _disque_reponse(noeud, False, True, rejet="DELETION_INCOMPLETE", **commun,
                                   stderr="suppression incomplete : %d fichier(s) du jeton %s toujours presents" % (
                                       len(restants), plan["jeton"][:8]))
        if not plan["fichiers"]:
            return _disque_reponse(noeud, False, False, rejet="NO_SCENARIO_FILES", **commun,
                                   stderr="aucun fichier du scenario %s ; occupation %.2f %% > %.0f %% : incident etranger, "
                                          "RIEN n a ete touche" % (DISQUE_SCENARIO, usage_apres, DISQUE_RECUPERATION_PCT))
        return _disque_reponse(noeud, False, True, rejet="NOT_RECOVERED", **commun,
                               stderr="fichiers supprimes mais occupation %.2f %% > %.0f %% : recuperation NON confirmee" % (
                                   usage_apres, DISQUE_RECUPERATION_PCT))


# ============================================================================
# ACTION-CAPABILITY-EXTENSION-01 (2026-09-30)
#
# POURQUOI. Jusqu ici, pour un type MESURE par le detecteur, l action etait
# fixee trois fois : INCIDENT_ACTIONS (annote dict[str, str]), la politique
# deterministe du moteur, puis ACTION_PAR_TYPE_MESURE ici meme. Une couche
# amont ne pouvait donc jamais faire valoir un choix, et la valeur d une
# selection contextuelle etait impossible a mesurer.
#
# CE QUI EST AJOUTE. Un contrat explicite, PUREMENT ADDITIF :
#     selected_action_id    l action choisie par l amont
#     selection_source      qui l a choisie
#     candidate_action_ids  les actions ADMISSIBLES ici, calculees et non classees
#
# CE QUI N EST PAS AJOUTE. Aucune regle de choix. Ce bloc repond seulement a
# « cette action est-elle admissible et executable ici ? ». Il ne repond
# JAMAIS a « laquelle faut-il choisir ? ». VALIDATION n est pas SELECTION.
#
# CE QUI N EST PAS TOUCHE. ACTION_PAR_TYPE_MESURE est INCHANGEE. En l absence
# de selected_action_id, le comportement est exactement celui d avant.
# ============================================================================

# Actions dont le NOM ne decrit pas l EFFET REEL, constate sur catalogue().
# Elles restent utilisables par le chemin historique, mais sont refusees au
# contrat explicite tant qu elles ne sont pas renommees.
ACTIONS_SEMANTIQUE_BLOQUEE = {
    "RESTART_POD_TO_REBALANCE_LOAD":
        "ne redemarre aucun pod : execute pkill -9 sur yes, stress, cpuburn et dd",
    "HOST_IMAGE_GC":
        "ne declenche pas le garbage collector d images : vacuum de journaux et "
        "suppression de logs tournes (crictl rmi --prune retire, cluster air-gap)",
}

# Allowlist par mecanisme. UN SEUL mecanisme est ouvert a ce stade.
#   portee "noeud"  : agit sur le noeud, cible = nom de noeud Kubernetes
#   portee "charge" : agit sur une charge, exige ns + cible de deploiement
ACTIONS_ADMISSIBLES_PAR_MECANISME = {
    "NODE_HIGH_CPU": {
        "CORDON_NODE":                 {"portee": "noeud",  "destructive": False, "rollback": "UNCORDON_NODE"},
        "DRAIN_NODE":                  {"portee": "noeud",  "destructive": True,  "rollback": "UNCORDON_NODE"},
        "SCALE_UP_AND_REBALANCE_LOAD": {"portee": "charge", "destructive": False, "rollback": "SCALE_DOWN"},
        "RESTART_POD":                 {"portee": "charge", "destructive": True,  "rollback": None},
        "INCREASE_CPU":                {"portee": "charge", "destructive": False, "rollback": None},
    },
    # HOST-ACTION-SAFETY-HARDENING-01 - ID-1. Le contrat EXPLICITE separe
    # strictement le type d incident du nom d action :
    #     NODE_NOT_READY  n admet QUE RESTART_KUBELET
    #     VM_POWERED_OFF  n admet QUE RESTART_NODE_VM
    # Une demande croisee est donc REFUSEE, ce qui rend l ambiguite impossible.
    #
    # ACTION_PAR_TYPE_MESURE reste INCHANGEE, et c est deliberé : le detecteur ne
    # distingue pas une VM eteinte d un kubelet casse - les deux rendent
    # `kube_node_status_condition{condition="Ready"} == 0` - et seul le rallumage
    # traite les DEUX cas, un `systemctl restart kubelet` etant impossible sur une
    # VM eteinte (pas de SSH). Le chemin automatique continue donc de repondre
    # RESTART_NODE_VM sur NODE_NOT_READY. Ce qui est corrige, c est l ambiguite du
    # nom d ACTION, pas la politique de remediation.
    "NODE_NOT_READY": {
        "RESTART_KUBELET": {"portee": "noeud", "destructive": False, "rollback": None},
    },
    "VM_POWERED_OFF": {
        "RESTART_NODE_VM": {"portee": "noeud", "destructive": False, "rollback": None},
    },
}

# Namespaces dont les charges ne doivent jamais etre touchees par une remediation
# CPU : elles portent la detection, la decision, le reseau ou le stockage.
NS_CHARGES_INTERDITS = frozenset({
    "kube-system", "kube-public", "kube-node-lease", "local-path-storage",
    "metallb-system", "ingress-nginx", "falco", "cnpg-system", "k8s-nodes",
})

# Garde d execution. Tant qu elle vaut 0, une action explicitement selectionnee
# est VALIDEE et RAPPORTEE mais jamais executee : c est le RECOMMEND_ONLY exige
# par le lot. La passer a 1 est une decision d exploitation, pas de code.
EXEC_ACTION_EXPLICITE = os.environ.get("HELIOS_EXEC_ACTION_EXPLICITE", "0") == "1"


# ============================================================================
# HOST-ACTION-SAFETY-HARDENING-01 - IDENTITE ET SURETE DES CHEMINS D EXECUTION
# ----------------------------------------------------------------------------
# Trois defauts critiques constates par HOST-REMEDIATION-CAPABILITY-01 :
#   ID-1 NODE_NOT_READY servait de TYPE D INCIDENT et de NOM D ACTION, avec deux
#        effets incomparables : `vmrun start` d un cote (via ACTION_PAR_TYPE_MESURE
#        -> RESTART_NODE_VM), `systemctl restart kubelet` de l autre (entree de
#        `table`). Le Bridge devait donc DEVINER le sens voulu.
#   ID-2 ESCALATE_NODE_OUTAGE_AND_REBALANCE etait presente dans `table`, laquelle
#        est testee AVANT le repli `__escalade__` : une escalade EXECUTAIT
#        `systemctl restart kubelet`. inference.py l emet en 4 endroits (l. 757,
#        1644, 1955, 2455), le chemin etait donc bien atteignable.
#   ID-3 l action NODE_DISK_PRESSURE portait `crictl rmi --prune`, precisement ce
#        qui avait ete retire de _DISK parce que ce cluster est AIR-GAP.
#
# Ce bloc est ADDITIF : il ne remplace aucune politique deterministe existante et
# ne selectionne aucune action. Il REFUSE, il TRADUIT, il JOURNALISE.
# ============================================================================

# Un type d incident n est JAMAIS un nom d action. Ces identifiants decrivent un
# ETAT OBSERVE. Les accepter comme `selected_action_id` obligerait le Bridge a
# deviner l intention : il refuse explicitement (REJECTED_INVALID_ACTION_ID).
# Ils restent utilisables par le CHEMIN HISTORIQUE, ou ACTION_PAR_TYPE_MESURE
# fournit deja l action sans ambiguite.
IDENTIFIANTS_NON_ACTIONS = frozenset({
    "NODE_NOT_READY", "NODE_DISK_PRESSURE", "NODE_DISK_FULL", "NODE_HIGH_CPU",
    "NODE_HIGH_MEMORY", "NODE_INODES_FULL", "NODE_TIME_DRIFT", "NODE_CORDONED",
    "NODE_CPU_SATURATION", "NODE_MEMORY_PRESSURE", "PVC_NOT_BOUND",
    "NODE_LOAD_SATURATION", "NODE_OUTAGE", "VM_DOWN", "VM_POWERED_OFF",
    # ID-10, decouvert PAR LE TEST UNITAIRE de ce lot : ces dix types n etaient
    # couverts par aucune garde d identite, et deux d entre eux sont DANGEREUX.
    # `NODE_NETWORK_UNAVAILABLE` et `NODE_NETWORK_ERRORS` contiennent la
    # sous-chaine "NETWORK" : passes comme NOM D ACTION, ils tombaient dans le
    # repli par mot-cle de catalogue() et resolvaient vers
    #     kubectl delete networkpolicy -n <ns> --all --ignore-not-found
    # soit la SUPPRESSION DE TOUTES LES POLITIQUES RESEAU du namespace, alors que
    # ACTION_PAR_TYPE_MESURE les destine a une ESCALADE. Meme famille que ID-1 :
    # un nom d etat observe atteignait une commande destructive.
    "ETCD_NO_LEADER", "ETCD_SLOW_FSYNC", "ETCD_LEADER_FLAPPING",
    "NODE_FD_EXHAUSTED", "NODE_PID_PRESSURE", "NODE_NETWORK_UNAVAILABLE",
    "NODE_NETWORK_ERRORS", "NODE_CPU_STEAL", "NODE_DISK_SLOW", "NODE_OOM_KILL",
})

# Alias historiques : TRADUITS vers l identite canonique et JOURNALISES, jamais
# reinterpretes comme type d incident. RESTART_VM est un synonyme exact de
# RESTART_NODE_VM dans catalogue() depuis l origine ; on le nomme ici plutot que
# de le laisser dependre d un test d appartenance a un tuple.
ALIAS_ACTIONS_LEGACY = {
    "RESTART_VM": "RESTART_NODE_VM",
}

# AIR_GAP_IMAGE_PRUNE = FORBIDDEN. Ce cluster n a pas de registre : une image
# supprimee ne se retelecharge pas. La garde porte sur la COMMANDE, au point de
# passage unique de l execution hote, pour que l interdit ne repose pas sur
# l ABSENCE d une entree de table - qu une edition future pourrait reintroduire.
AIR_GAP_IMAGE_PRUNE = "FORBIDDEN"
COMMANDES_INTERDITES = (
    (re.compile(r"crictl\s+rmi", re.I),
     "crictl rmi interdit : cluster air-gap, une image supprimee est irrecuperable"),
    (re.compile(r"--prune\b", re.I),
     "--prune interdit : cluster air-gap"),
    (re.compile(r"\brm\s+-[a-zA-Z]*r[a-zA-Z]*\s+/var/lib/containerd", re.I),
     "suppression recursive de /var/lib/containerd interdite"),
    (re.compile(r"\brm\s+-[a-zA-Z]*r[a-zA-Z]*\s+/var/lib/etcd", re.I),
     "suppression recursive de /var/lib/etcd interdite"),
)


def commande_interdite(commande):
    """Rend le MOTIF d interdiction, ou None si la commande est admise."""
    txt = str(commande or "")
    for motif_re, motif in COMMANDES_INTERDITES:
        if motif_re.search(txt):
            return motif
    return None


# Valeurs de semantique, nommees pour ne PAS apparaitre comme litteraux cites dans
# catalogue() : _NOMS y est construit par regex sur `"([A-Z][A-Z_]+)":` et un
# `== "ESCALATION":` inline ferait annoncer une action inexistante par /actions.
SEM_ESCALATION = "ESCALATION"
SEM_NON_ACTION = "NON_ACTION"
SEM_REMEDIATION = "REMEDIATION"
SEM_ABSENT = "ABSENT"


def semantique_action(action_id):
    """ESCALATION | NON_ACTION | REMEDIATION | ABSENT.

    Ne consulte JAMAIS le type d incident : la semantique est une propriete du
    nom d action seul.
    """
    a = str(action_id or "").strip().upper()
    if not a:
        return "ABSENT"
    if "ESCALATE" in a or "HUMAN" in a or "MANUAL" in a:
        return "ESCALATION"
    if a in IDENTIFIANTS_NON_ACTIONS:
        return "NON_ACTION"
    return "REMEDIATION"


def resolve_action_id(action_id):
    """Identite canonique d un action_id, sans jamais consulter le type d incident.

    Rend {action_id, canonical_action_id, semantic_type, execution_allowed,
          legacy, motif}.
    """
    brut = str(action_id or "").strip()
    a = brut.upper()
    legacy = a in ALIAS_ACTIONS_LEGACY
    canon = ALIAS_ACTIONS_LEGACY.get(a, a)
    sem = semantique_action(canon)
    if sem == "ABSENT":
        autorise, motif = False, "aucun action_id fourni"
    elif sem == "ESCALATION":
        autorise, motif = False, "escalade : aucune commande de remediation n est executee"
    elif sem == "NON_ACTION":
        autorise, motif = False, ("'%s' est un TYPE D INCIDENT, pas un action_id : le "
                                 "Bridge refuse de deviner l intention" % canon)
    elif canon in ACTIONS_SEMANTIQUE_BLOQUEE:
        autorise, motif = False, ACTIONS_SEMANTIQUE_BLOQUEE[canon]
    else:
        autorise, motif = True, "action de remediation identifiee"
    return {"action_id": brut, "canonical_action_id": canon, "semantic_type": sem,
            "execution_allowed": autorise, "legacy": legacy, "motif": motif}


def _noeud_cluster_connu(nom):
    """Vrai si `nom` designe un noeud Kubernetes du cluster."""
    return str(nom or "").strip().replace("k8s-", "") in NOEUDS_CLUSTER


def _deploiement_etat(ns, nom):
    """Lecture seule. Rend (existe, replicas_desires, replicas_disponibles)."""
    r = executer_kubectl(
        "kubectl get deployment %s -n %s -o "
        "jsonpath='{.spec.replicas}|{.status.availableReplicas}'" % (nom, ns))
    if not r.get("ok"):
        return False, None, None
    txt = str(r.get("stdout", "")).strip().strip("'")
    if "|" not in txt:
        return False, None, None
    a, b = txt.split("|", 1)
    try:
        return True, int(a or 0), int(b or 0)
    except ValueError:
        return False, None, None


def _capacite_accueil(noeud_exclu):
    """Lecture seule. Reste-t-il un autre noeud Ready pour accueillir des pods ?"""
    r = executer_kubectl("kubectl get nodes --no-headers")
    if not r.get("ok"):
        return False, "etat des noeuds illisible"
    autres = 0
    for ligne in str(r.get("stdout", "")).splitlines():
        ch = ligne.split()
        if len(ch) >= 2 and ch[1].strip() == "Ready" and ch[0].strip() != str(noeud_exclu).strip():
            autres += 1
    if autres <= 0:
        return False, "aucun autre noeud Ready pour accueillir les charges"
    return True, "%d autre(s) noeud Ready disponible(s)" % autres


def precondition_action(action_id, spec, ns, cible, noeud):
    """Eligibilite TECHNIQUE d une action ici. Ne dit rien de sa pertinence metier."""
    portee = spec.get("portee")
    if portee == "noeud":
        if not _noeud_cluster_connu(cible):
            return False, "cible '%s' n est pas un noeud Kubernetes du cluster" % cible
        if action_id == "DRAIN_NODE":
            return _capacite_accueil(cible)
        return True, "cible de noeud valide"
    if portee == "charge":
        if not ns or ns in NS_CHARGES_INTERDITS:
            return False, ("aucune charge cible exploitable : ns='%s' — le detecteur "
                           "fournit ns=k8s-nodes pour un incident hote, il faut donc "
                           "que l amont designe explicitement un deploiement" % ns)
        if not cible:
            return False, "aucun deploiement cible fourni"
        existe, desires, dispo = _deploiement_etat(ns, cible)
        if not existe:
            return False, "deploiement %s/%s introuvable" % (ns, cible)
        if action_id == "SCALE_UP_AND_REBALANCE_LOAD":
            ok2, motif2 = _capacite_accueil("")
            if not ok2:
                return False, motif2
        return True, ("deploiement %s/%s present (%s desires, %s disponibles)"
                      % (ns, cible, desires, dispo))
    return False, "portee d action inconnue : %r" % portee


def valider_action_explicite(mecanisme, action_id, ns, cible, noeud):
    """VALIDATION, jamais SELECTION.

    Verdicts possibles :
        ACCEPTED
        ACCEPTED_ESCALATION               (HOST-ACTION-SAFETY-HARDENING-01)
        ABSENT
        REJECTED_INVALID_ACTION_ID        (HOST-ACTION-SAFETY-HARDENING-01)
        REJECTED_ACTION_SEMANTICS_BLOCKED
        REJECTED_MECHANISM_NOT_OPENED
        REJECTED_INVALID_ACTION_FOR_MECHANISM
        REJECTED_PRECONDITION_NOT_MET

    Ne propose JAMAIS d action de remplacement.
    """
    a = str(action_id or "").strip().upper()
    m = str(mecanisme or "").strip().upper()
    if not a:
        return {"verdict": "ABSENT", "motif": "aucune action explicite fournie"}
    # HOST-ACTION-SAFETY-HARDENING-01 - les gardes d IDENTITE passent AVANT toute
    # recherche par mecanisme : ni une escalade ni un type d incident ne dependent
    # du mecanisme pour etre reconnus comme tels.
    _id = resolve_action_id(a)
    if _id["legacy"]:
        tracer("ALIAS LEGACY", a, "->", _id["canonical_action_id"], "| traduction journalisee")
        a = _id["canonical_action_id"]
    if _id["semantic_type"] == "NON_ACTION":
        return {"verdict": "REJECTED_INVALID_ACTION_ID", "action": a,
                "semantic_type": "NON_ACTION", "motif": _id["motif"]}
    if _id["semantic_type"] == "ESCALATION":
        return {"verdict": "ACCEPTED_ESCALATION", "action": a,
                "semantic_type": "ESCALATION", "executable": False,
                "motif": _id["motif"],
                "spec": {"portee": "aucune", "destructive": False, "rollback": None}}
    if a in ACTIONS_SEMANTIQUE_BLOQUEE:
        return {"verdict": "REJECTED_ACTION_SEMANTICS_BLOCKED", "action": a,
                "motif": ACTIONS_SEMANTIQUE_BLOQUEE[a]}
    admissibles = ACTIONS_ADMISSIBLES_PAR_MECANISME.get(m)
    if admissibles is None:
        return {"verdict": "REJECTED_MECHANISM_NOT_OPENED", "action": a,
                "motif": "mecanisme '%s' non ouvert au contrat explicite" % m,
                "mecanismes_ouverts": sorted(ACTIONS_ADMISSIBLES_PAR_MECANISME)}
    if a not in admissibles:
        return {"verdict": "REJECTED_INVALID_ACTION_FOR_MECHANISM", "action": a,
                "motif": "action '%s' non admissible pour le mecanisme '%s'" % (a, m),
                "admissibles": sorted(admissibles)}
    ok, motif = precondition_action(a, admissibles[a], ns, cible, noeud)
    if not ok:
        return {"verdict": "REJECTED_PRECONDITION_NOT_MET", "action": a, "motif": motif}
    return {"verdict": "ACCEPTED", "action": a, "motif": motif, "spec": admissibles[a]}


def actions_candidates(mecanisme, ns, cible, noeud):
    """Les actions ADMISSIBLES dont les preconditions sont satisfaites ici.

    Ce n est ni un classement ni une recommandation : c est l ensemble des
    possibles. Le choix appartient entierement a la couche amont.
    """
    m = str(mecanisme or "").strip().upper()
    admissibles = ACTIONS_ADMISSIBLES_PAR_MECANISME.get(m) or {}
    eligibles, ecartees = [], []
    for a in sorted(admissibles):
        ok, motif = precondition_action(a, admissibles[a], ns, cible, noeud)
        cible_liste = eligibles if ok else ecartees
        cible_liste.append({"action_id": a, "motif": motif,
                            "destructive": bool(admissibles[a].get("destructive")),
                            "rollback": admissibles[a].get("rollback")})
    return {"mechanism": m,
            "target": {"ns": ns, "cible": cible, "noeud": noeud},
            "candidate_action_ids": [x["action_id"] for x in eligibles],
            "eligibles": eligibles,
            "ecartees": ecartees,
            "semantique_bloquee": sorted(ACTIONS_SEMANTIQUE_BLOQUEE),
            "execution_action_explicite": EXEC_ACTION_EXPLICITE,
            "note": "candidate_action_ids n est pas un classement"}


_QUOTAS = {}                                  # (noeud, service) -> etat du bail
_QUOTAS_LOCK = threading.Lock()


def _duree_systemd_us(txt):
    """Convertit une duree systemd en microsecondes. Rend None pour infinity.

    Formats observes : "infinity", "1s", "1.500000s", "400ms", "2.500000s".
    """
    if txt is None:
        return None
    t = str(txt).strip().lower()
    if not t or t in ("infinity", "[not set]"):
        return None
    total = 0.0
    trouve = False
    for val, unite in re.findall(r"([0-9]+(?:\.[0-9]+)?)\s*(min|ms|us|s)", t):
        f = {"min": 60_000_000.0, "s": 1_000_000.0, "ms": 1000.0, "us": 1.0}[unite]
        total += float(val) * f
        trouve = True
    return total if trouve else None


def quota_valider(noeud, service, quota_pct, duree_s):
    """Valide les parametres AVANT toute commande. Rend (ok, raison)."""
    if not noeud or noeud not in NOEUDS:
        return False, "noeud inconnu du Bridge : %s" % noeud
    if not service:
        return False, "parametre 'service' manquant"
    if not UNITE_SYSTEMD.match(service):
        return False, ("cible invalide : %s — une unite systemd .service est "
                       "exigee (ni pod, ni deploiement, ni processus)" % service[:60])
    if service in QUOTA_SERVICES_INTERDITS:
        return False, ("service interdit de plafonnement : %s (detection, "
                       "decision, execution ou disponibilite du cluster)" % service)
    autorises = QUOTA_CIBLES_AUTORISEES.get(noeud) or set()
    if service not in autorises:
        return False, ("service hors allowlist pour %s : %s (autorises : %s)"
                       % (noeud, service, ", ".join(sorted(autorises)) or "aucun"))
    if quota_pct is None:
        return False, "parametre 'quota_pct' manquant"
    try:
        q = int(quota_pct)
    except (TypeError, ValueError):
        return False, "quota_pct non entier : %r" % (quota_pct,)
    if q < QUOTA_MIN_PCT or q > QUOTA_MAX_PCT:
        return False, ("quota_pct hors bornes : %d (autorise %d a %d)"
                       % (q, QUOTA_MIN_PCT, QUOTA_MAX_PCT))
    try:
        d = int(duree_s) if duree_s is not None else QUOTA_DUREE_DEFAUT_S
    except (TypeError, ValueError):
        return False, "duree_s non entier : %r" % (duree_s,)
    if d <= 0 or d > QUOTA_DUREE_MAX_S:
        return False, ("duree_s hors bornes : %d (autorise 1 a %d)"
                       % (d, QUOTA_DUREE_MAX_S))
    return True, {"quota_pct": q, "duree_s": d}


def quota_cible_saine(noeud, service):
    """Verifie SUR LA VM que l unite existe, est une unite systemd reelle et
    est active. Rend (ok, details)."""
    cmd = ("systemctl show %s -p Id -p LoadState -p ActiveState -p Type "
           "-p FragmentPath -p CPUQuotaPerSecUSec --no-pager" % service)
    r = executer(noeud, cmd)
    if not r.get("ok"):
        return False, {"erreur": "systemctl show a echoue : %s" % (r.get("stderr") or "")[:200]}
    champs = {}
    for l in (r.get("stdout") or "").splitlines():
        if "=" in l:
            k, _, v = l.partition("=")
            champs[k.strip()] = v.strip()
    det = {
        "id": champs.get("Id", ""),
        "load_state": champs.get("LoadState", ""),
        "active_state": champs.get("ActiveState", ""),
        "type": champs.get("Type", ""),
        "fragment_path": champs.get("FragmentPath", ""),
        "quota_actuel": champs.get("CPUQuotaPerSecUSec", ""),
    }
    if det["id"] != service:
        return False, dict(det, erreur="unite rendue (%s) differente de la demandee (%s)"
                           % (det["id"], service))
    if det["load_state"] != "loaded":
        return False, dict(det, erreur="unite non chargee : LoadState=%s" % det["load_state"])
    if det["active_state"] != "active":
        return False, dict(det, erreur="unite non active : ActiveState=%s" % det["active_state"])
    if not det["fragment_path"]:
        return False, dict(det, erreur="aucun fichier d unite : cible non systemd")
    return True, det


_QUOTA_DROPIN_NAME = "zzzzzzzz-helios-temporary-cpu-quota.conf"

_QUOTA_STATE_DIR = "/run/helios-quota-expiry"

_QUOTA_LOCK_DIR = _QUOTA_STATE_DIR

_QUOTA_BASELINE_RE = re.compile(r"^(?:infinity|[0-9]+(?:\.[0-9]+)?(?:min|ms|us|s))$")

_QUOTA_TOKEN_RE = re.compile(r"^[0-9a-f]{32}$")

_QUOTA_OPERATION_LOCK = threading.RLock()

def _quota_target_valider(noeud, service):
    """Valide le couple nœud/unité via la politique d'allowlist existante."""
    try:
        ok, detail = quota_valider(noeud, service, QUOTA_MIN_PCT, 1)
    except Exception as exc:
        return False, "validation de cible impossible: %s" % (exc,)
    if not ok:
        return False, detail
    return True, None

def _quota_paths(noeud, service, token=None):
    """Construit des chemins uniquement à partir de valeurs déjà en allowlist."""
    valide, raison = _quota_target_valider(noeud, service)
    if not valide:
        raise ValueError("cible quota non autorisée: %s" % raison)
    base = service[:-8] if service.endswith(".service") else service
    stem = "%s-%s" % (noeud, base)
    lock = "%s/helios-quota-%s.lock" % (_QUOTA_LOCK_DIR, stem)
    dropin_dir = "/run/systemd/system/%s.d" % service
    dropin = "%s/%s" % (dropin_dir, _QUOTA_DROPIN_NAME)
    if token is None:
        helper = None
        timer = None
    else:
        if not _QUOTA_TOKEN_RE.fullmatch(str(token)):
            raise ValueError("identifiant de bail invalide")
        timer = "helios-quota-expire-%s-%s-%s" % (noeud, base, token)
        helper = "%s/%s-%s.sh" % (_QUOTA_STATE_DIR, stem, token)
    return {
        "lock": lock, "dropin_dir": dropin_dir, "dropin": dropin,
        "helper": helper, "timer": timer, "stem": stem,
    }

def _quota_remote_script(noeud, service, script):
    """Exécute un script root encodé, sous flock distant, sans quoting multi-shell."""
    # Le corps est encodé dans la commande SSH; appliquer aussi le filtre d'interdits
    # au corps décodé, faute de quoi le filtre central ne verrait que du base64.
    bloque = commande_interdite(script)
    if bloque:
        return {"host": noeud, "command": "<script quota protégé>", "exitCode": -1,
                "stdout": "", "stderr": bloque, "ok": False,
                "refus": "COMMAND_FORBIDDEN", "resume": "REFUS " + bloque}
    valide, raison = _quota_target_valider(noeud, service)
    if not valide:
        return {"host": noeud, "command": "<script quota protégé>", "exitCode": -1,
                "stdout": "", "stderr": str(raison), "ok": False,
                "refus": "QUOTA_TARGET_REJECTED", "resume": "REFUS " + str(raison)}
    paths = _quota_paths(noeud, service)
    payload = base64.b64encode(script.encode("utf-8")).decode("ascii")
    wrapper = (
        "STATE_DIR=%s; "
        "if [ -L \"$STATE_DIR\" ]; then exit 98; fi; "
        "if [ -e \"$STATE_DIR\" ] && [ ! -d \"$STATE_DIR\" ]; then exit 98; fi; "
        "if [ ! -e \"$STATE_DIR\" ]; then install -d -o root -g root -m 0755 \"$STATE_DIR\" || exit 98; fi; "
        "if [ \"$(stat -c '%%u:%%a' \"$STATE_DIR\")\" != '0:755' ]; then exit 98; fi; "
        "exec /usr/bin/flock -x %s /bin/sh -s"
    ) % (shlex.quote(_QUOTA_STATE_DIR), shlex.quote(paths["lock"]))
    cmd = "printf '%%s' %s | /usr/bin/base64 -d | sudo -n /bin/sh -c %s" % (
        shlex.quote(payload), shlex.quote(wrapper)
    )
    return executer(noeud, cmd)

def _quota_parse_kv(result):
    values = {}
    for line in (result.get("stdout") or "").splitlines():
        if line.startswith("HELIOS_") and "=" in line:
            key, _, value = line.partition("=")
            values[key] = value
    return values

def _quota_lire_info(noeud, service):
    """Lecture root, sérialisée avec les opérations de bail, sans mutation."""
    valide, raison = _quota_target_valider(noeud, service)
    if not valide:
        return False, {"erreur": "cible quota non autorisée", "detail": raison}
    p = _quota_paths(noeud, service)
    q = shlex.quote
    script = """#!/bin/sh
set -eu
SERVICE=%s
DROPIN=%s
LEGACY_TIMER=%s
CURRENT=$(systemctl show "$SERVICE" -p CPUQuotaPerSecUSec --value)
ACTIVE=$(systemctl show "$SERVICE" -p ActiveState --value)
LOAD=$(systemctl show "$SERVICE" -p LoadState --value)
OWNER=absent
TOKEN=
BASELINE=
TIMER=
PCT=
if [ -L "$DROPIN" ]; then
  OWNER=foreign
elif [ -e "$DROPIN" ]; then
  if [ ! -f "$DROPIN" ]; then
    OWNER=foreign
  elif grep -Fxq '# HELIOS_QUOTA_MANAGED_V1' "$DROPIN"; then
    OWNER=helios
    TOKEN=$(sed -n 's/^# HELIOS_QUOTA_TOKEN=//p' "$DROPIN")
    BASELINE=$(sed -n 's/^# HELIOS_QUOTA_BASELINE=//p' "$DROPIN")
    TIMER=$(sed -n 's/^# HELIOS_QUOTA_TIMER_UNIT=//p' "$DROPIN")
    PCT=$(sed -n 's/^CPUQuota=//p' "$DROPIN" | tr -d '%%')
  else
    OWNER=foreign
  fi
fi
LEGACY_TIMER_ACTIVE=0
for U in "$LEGACY_TIMER.timer" "$LEGACY_TIMER.service"; do
  if STATE=$(systemctl is-active "$U" 2>/dev/null); then :; else :; fi
  case "$STATE" in
    active|activating|reloading|deactivating) LEGACY_TIMER_ACTIVE=1 ;;
    inactive|failed|unknown) : ;;
    *) echo 'HELIOS_ERROR=état ancien timer illisible'; exit 23 ;;
  esac
done
TOKEN_TIMER_ACTIVE=0
if [ "$OWNER" = helios ]; then
  case "$TIMER" in
    helios-quota-expire-*) : ;;
    *) echo 'HELIOS_ERROR=identifiant de timer du bail invalide'; exit 24 ;;
  esac
  for U in "$TIMER.timer" "$TIMER.service"; do
    if STATE=$(systemctl is-active "$U" 2>/dev/null); then :; else :; fi
    case "$STATE" in
      active|activating|reloading|deactivating) TOKEN_TIMER_ACTIVE=1 ;;
      inactive|failed|unknown) : ;;
      *) echo 'HELIOS_ERROR=état du timer du bail illisible'; exit 25 ;;
    esac
  done
fi
CONTROL_NONEMPTY=0
for D in /etc/systemd/system.control/${SERVICE}.d /run/systemd/system.control/${SERVICE}.d; do
  if [ -L "$D" ]; then echo 'HELIOS_ERROR=systemd-control symlink'; exit 21; fi
  if [ -d "$D" ]; then
    FOUND=$(find "$D" -mindepth 1 -maxdepth 1 -print -quit) || { echo 'HELIOS_ERROR=control directory read failed'; exit 22; }
    if [ -n "$FOUND" ]; then CONTROL_NONEMPTY=$((CONTROL_NONEMPTY + 1)); fi
  fi
done
printf 'HELIOS_CURRENT=%%s\\nHELIOS_ACTIVE=%%s\\nHELIOS_LOAD=%%s\\nHELIOS_OWNER=%%s\\n' "$CURRENT" "$ACTIVE" "$LOAD" "$OWNER"
printf 'HELIOS_TOKEN=%%s\\nHELIOS_BASELINE=%%s\\nHELIOS_TIMER_UNIT=%%s\\nHELIOS_PCT=%%s\\nHELIOS_CONTROL_NONEMPTY=%%s\\nHELIOS_LEGACY_TIMER_ACTIVE=%%s\\nHELIOS_TOKEN_TIMER_ACTIVE=%%s\\n' "$TOKEN" "$BASELINE" "$TIMER" "$PCT" "$CONTROL_NONEMPTY" "$LEGACY_TIMER_ACTIVE" "$TOKEN_TIMER_ACTIVE"
if [ "$OWNER" = helios ]; then
  LINES=$(wc -l < "$DROPIN")
  printf 'HELIOS_DROPIN_LINES=%%s\\n' "$LINES"
  DROPIN_B64=$(base64 -w0 "$DROPIN")
  printf 'HELIOS_DROPIN_B64=%%s\\n' "$DROPIN_B64"
fi
""" % (q(service), q(p["dropin"]), q("helios-quota-expire-%s-%s" % (noeud, service[:-8])))
    r = _quota_remote_script(noeud, service, script)
    if not r.get("ok"):
        return False, {"erreur": "lecture de l'état du bail impossible", "detail": (r.get("stderr") or r.get("stdout") or "")[:300]}
    values = _quota_parse_kv(r)
    info = {
        "current": values.get("HELIOS_CURRENT", ""),
        "active": values.get("HELIOS_ACTIVE", ""),
        "load": values.get("HELIOS_LOAD", ""),
        "owner": values.get("HELIOS_OWNER", ""),
        "token": values.get("HELIOS_TOKEN", ""),
        "baseline": values.get("HELIOS_BASELINE", ""),
        "timer": values.get("HELIOS_TIMER_UNIT", ""),
        "pct": values.get("HELIOS_PCT", ""),
        "control_nonempty": values.get("HELIOS_CONTROL_NONEMPTY", ""),
        "legacy_timer_active": values.get("HELIOS_LEGACY_TIMER_ACTIVE", "0") == "1",
        "token_timer_active": values.get("HELIOS_TOKEN_TIMER_ACTIVE", "0") == "1",
    }
    if info["load"] != "loaded":
        return False, dict(info, erreur="la cible n'est pas chargée")
    if info["owner"] == "helios":
        expected_timer = _quota_paths(noeud, service, info["token"])["timer"] if _QUOTA_TOKEN_RE.fullmatch(info["token"]) else ""
        valid_pct = info["pct"].isdigit() and QUOTA_MIN_PCT <= int(info["pct"]) <= QUOTA_MAX_PCT
        try:
            decoded = base64.b64decode(values.get("HELIOS_DROPIN_B64", ""), validate=True).decode("utf-8")
        except Exception:
            decoded = ""
        expected_content = "\n".join([
            "# HELIOS_QUOTA_MANAGED_V1",
            "# HELIOS_QUOTA_TOKEN=" + info["token"],
            "# HELIOS_QUOTA_BASELINE=" + info["baseline"],
            "# HELIOS_QUOTA_TIMER_UNIT=" + info["timer"],
            "[Service]", "CPUQuota=" + info["pct"] + "%", "",
        ])
        if (
            values.get("HELIOS_DROPIN_LINES") != "6"
            or not _QUOTA_TOKEN_RE.fullmatch(info["token"])
            or not _QUOTA_BASELINE_RE.fullmatch(info["baseline"])
            or info["timer"] != expected_timer
            or not valid_pct
            or decoded != expected_content
        ):
            return False, dict(info, erreur="métadonnées ou contenu du drop-in HELIOS invalides; aucune mutation autorisée")
    elif info["owner"] == "foreign":
        return False, dict(info, erreur="le chemin du drop-in réservé existe mais n'appartient pas à HELIOS")
    return True, info

def _quota_helper_source(noeud, service, token, baseline, quota_pct):
    """Produit l'expiration autonome. Elle attend le token, puis verrouille et vérifie."""
    p = _quota_paths(noeud, service, token)
    q = shlex.quote
    content = "\n".join([
        "# HELIOS_QUOTA_MANAGED_V1",
        "# HELIOS_QUOTA_TOKEN=" + token,
        "# HELIOS_QUOTA_BASELINE=" + baseline,
        "# HELIOS_QUOTA_TIMER_UNIT=" + p["timer"],
        "[Service]", "CPUQuota=%d%%" % int(quota_pct), "",
    ])
    content_b64 = base64.b64encode(content.encode("utf-8")).decode("ascii")
    inner = """set -eu
DROPIN=%s
TOKEN=%s
BASELINE=%s
SERVICE=%s
HELPER=%s
EXPECTED_CONTENT_B64=%s
if [ -L "$DROPIN" ]; then
  logger -p user.err -t helios-quota-expiry 'CRITIQUE: drop-in symbolique; helper conserve'
  exit 35
fi
if [ ! -e "$DROPIN" ]; then
  CURRENT=$(systemctl show "$SERVICE" -p CPUQuotaPerSecUSec --value) || CURRENT=
  if [ "$CURRENT" = "$BASELINE" ]; then
    logger -t helios-quota-expiry "expiration d?j? termin?e pour $SERVICE; baseline confirm?e"
    rm -f -- "$HELPER"
    exit 0
  fi
  logger -p user.err -t helios-quota-expiry 'CRITIQUE: drop-in absent et baseline non confirm?e; nouvelle tentative requise'
  exit 35
fi
if [ ! -f "$DROPIN" ]; then
  logger -p user.err -t helios-quota-expiry 'CRITIQUE: chemin drop-in non r?gulier; helper conserv?'
  exit 35
fi
ACTIVE_TOKEN=$(sed -n 's/^# HELIOS_QUOTA_TOKEN=//p' "$DROPIN")
if [ "$ACTIVE_TOKEN" != "$TOKEN" ]; then
  logger -t helios-quota-expiry 'expiration obsolète ignorée; token différent'
  rm -f -- "$HELPER"
  exit 0
fi
ACTUAL_CONTENT_B64=$(base64 -w0 "$DROPIN")
if [ "$ACTUAL_CONTENT_B64" != "$EXPECTED_CONTENT_B64" ]; then
  logger -p user.err -t helios-quota-expiry 'expiration refusée: contenu drop-in modifié'
  exit 31
fi
if ! rm -- "$DROPIN"; then
  logger -p user.err -t helios-quota-expiry 'échec retrait drop-in HELIOS'
  exit 32
fi
RESTORED=0
ATTEMPT=1
while [ "$ATTEMPT" -le 3 ]; do
  if systemctl daemon-reload; then
    ACTUAL=$(systemctl show "$SERVICE" -p CPUQuotaPerSecUSec --value) || ACTUAL=
    if [ "$ACTUAL" = "$BASELINE" ]; then RESTORED=1; break; fi
  fi
  sleep 1
  ATTEMPT=$((ATTEMPT + 1))
done
if [ "$RESTORED" -eq 1 ]; then
  logger -t helios-quota-expiry "quota restauré pour $SERVICE; baseline=$BASELINE"
  rm -f -- "$HELPER"
  exit 0
fi
# Échec de reload/restauration : remettre le drop-in exact de HELIOS sur disque
# pour ne pas laisser le gestionnaire systemd en désaccord avec la configuration.
RESTORE_TMP="${DROPIN}.recovery.${TOKEN}"
PRESERVED=0
if printf '%%s' "$EXPECTED_CONTENT_B64" | base64 -d > "$RESTORE_TMP" &&
   chmod 0644 "$RESTORE_TMP" && mv -f -- "$RESTORE_TMP" "$DROPIN"; then
  PCT=$(sed -n 's/^CPUQuota=//p' "$DROPIN" | tr -d '%%')
  case "$PCT" in ''|*[!0-9]*) PCT=0 ;; esac
  if [ "$PCT" -gt 0 ]; then
    EXPECTED_ACTIVE_US=$((PCT * 10000))
    ATTEMPT=1
    while [ "$ATTEMPT" -le 3 ]; do
      if systemctl daemon-reload; then
        CHECK=$(systemctl show "$SERVICE" -p CPUQuotaPerSecUSec --value) || CHECK=
        CHECK_US=$(/usr/bin/python3 -c 'import re,sys; s=sys.argv[1].strip().lower(); m=re.fullmatch(r"([0-9]+(?:\\.[0-9]+)?)(min|ms|us|s)",s); print(round(float(m.group(1))*{"min":60000000,"s":1000000,"ms":1000,"us":1}[m.group(2)]) if m else -1)' "$CHECK") || CHECK_US=-999
        if [ "$CHECK_US" -eq "$EXPECTED_ACTIVE_US" ]; then PRESERVED=1; break; fi
      fi
      sleep 1
      ATTEMPT=$((ATTEMPT + 1))
    done
  fi
fi
if [ "$PRESERVED" -eq 1 ]; then
  logger -p user.err -t helios-quota-expiry "ÉCHEC expiration $SERVICE: drop-in HELIOS préservé et quota encore actif; intervention requise"
  exit 34
fi
logger -p user.err -t helios-quota-expiry "CRITIQUE expiration $SERVICE: restauration non confirmée et récupération du drop-in non vérifiée"
exit 35
""" % (q(p["dropin"]), q(token), q(baseline), q(service), q(p["helper"]), q(content_b64))
    return """#!/bin/sh
set -eu
LOCK=%s
DROPIN=%s
TOKEN=%s
HELPER=%s
TIMER=%s
SERVICE=%s
BASELINE=%s
# Ne pas garder le verrou pendant l'attente: le Bridge doit pouvoir installer le bail.
TRIES=0
while [ "$TRIES" -lt 30 ]; do
  if [ -f "$DROPIN" ] && [ ! -L "$DROPIN" ]; then
    SEEN=$(sed -n 's/^# HELIOS_QUOTA_TOKEN=//p' "$DROPIN")
    if [ "$SEEN" = "$TOKEN" ]; then
      exec /usr/bin/flock -x "$LOCK" /bin/sh -s <<'HELIOS_LOCKED_BODY'
%s
HELIOS_LOCKED_BODY
    elif [ -n "$SEEN" ]; then
      NEXT_TOKEN=$(sed -n 's/^# HELIOS_QUOTA_TOKEN=//p' "$DROPIN")
      NEXT_BASELINE=$(sed -n 's/^# HELIOS_QUOTA_BASELINE=//p' "$DROPIN")
      NEXT_TIMER=$(sed -n 's/^# HELIOS_QUOTA_TIMER_UNIT=//p' "$DROPIN")
      NEXT_QUOTA=$(sed -n 's/^CPUQuota=//p' "$DROPIN")
      NEXT_PCT=$(printf '%%s' "$NEXT_QUOTA" | sed 's/%%$//')
      NEXT_LINES=$(wc -l < "$DROPIN" | tr -d '[:space:]')
      ACTUAL_NEXT_CONTENT_B64=$(base64 -w0 "$DROPIN")
      EXPECTED_NEXT_CONTENT_B64=$(printf '%%s\\n' \\
        '# HELIOS_QUOTA_MANAGED_V1' \\
        "# HELIOS_QUOTA_TOKEN=$SEEN" \\
        "# HELIOS_QUOTA_BASELINE=$NEXT_BASELINE" \\
        "# HELIOS_QUOTA_TIMER_UNIT=$NEXT_TIMER" \\
        '[Service]' \\
        "CPUQuota=$NEXT_QUOTA" | base64 -w0)
      EXPECTED_NEXT_TIMER="${TIMER%%$TOKEN}${SEEN}"
      TOKEN_FORMAT_OK=0
      BASELINE_FORMAT_OK=0
      PCT_FORMAT_OK=0
      NEXT_TIMER_ACTIVE=0
      case "$SEEN" in
        *[!0-9a-f]*|'') TOKEN_FORMAT_OK=0 ;;
        *) if [ "${#SEEN}" -eq 32 ]; then TOKEN_FORMAT_OK=1; fi ;;
      esac
      if printf '%%s\n' "$NEXT_BASELINE" | grep -Eq '^(infinity|[0-9]+([.][0-9]+)?(min|ms|us|s))$'; then
        BASELINE_FORMAT_OK=1
      fi
      case "$NEXT_PCT" in
        ''|*[!0-9]*) PCT_FORMAT_OK=0 ;;
        *) if [ "$NEXT_PCT" -ge 20 ] && [ "$NEXT_PCT" -le 400 ]; then PCT_FORMAT_OK=1; fi ;;
      esac
      if [ "$NEXT_TIMER" = "$EXPECTED_NEXT_TIMER" ] &&
         NEXT_STATE=$(systemctl is-active "${NEXT_TIMER}.timer" 2>/dev/null) &&
         [ "$NEXT_STATE" = active ]; then
        NEXT_TIMER_ACTIVE=1
      fi
      if grep -Fxq '# HELIOS_QUOTA_MANAGED_V1' "$DROPIN" &&
         [ "$NEXT_TOKEN" = "$SEEN" ] &&
         [ "$NEXT_BASELINE" = "$BASELINE" ] &&
         [ "$ACTUAL_NEXT_CONTENT_B64" = "$EXPECTED_NEXT_CONTENT_B64" ] &&
         [ "$NEXT_LINES" = 6 ] &&
         [ "$TOKEN_FORMAT_OK" -eq 1 ] &&
         [ "$BASELINE_FORMAT_OK" -eq 1 ] &&
         [ "$PCT_FORMAT_OK" -eq 1 ] &&
         [ "$NEXT_TIMER_ACTIVE" -eq 1 ]; then
        logger -t helios-quota-expiry 'ancien timer HELIOS remplace par un bail valide; nettoyage du helper'
        rm -f -- "$HELPER"
        exit 0
      fi
      logger -p user.err -t helios-quota-expiry 'token different mais nouveau bail non confirme; helper conserve pour nouvelle tentative'
      exit 35
    fi
  fi
  sleep 1
  TRIES=$((TRIES + 1))
done
CURRENT=$(systemctl show "$SERVICE" -p CPUQuotaPerSecUSec --value) || CURRENT=
if [ "$CURRENT" = "$BASELINE" ]; then
  logger -t helios-quota-expiry 'expiration termin?e; baseline confirm?e'
  rm -f -- "$HELPER"
  exit 0
fi
logger -p user.err -t helios-quota-expiry 'CRITIQUE: aucun bail correspondant et quota diff?rent de la baseline; helper conserv? pour nouvelle tentative'
exit 35
""" % (q(p["lock"]), q(p["dropin"]), q(token), q(p["helper"]), q(p["timer"]), q(service), q(baseline), inner)

def _quota_stop_timer_script(noeud, service, timer, helper=None):
    p = _quota_paths(noeud, service)
    q = shlex.quote
    helper_clause = ""
    if helper:
        helper_clause = "rm -f -- %s\\n" % q(helper)
    return """#!/bin/sh
set -eu
TIMER=%s
HELPER=%s
STOP_RC=0
systemctl stop "$TIMER.timer" "$TIMER.service" >/dev/null 2>&1 || STOP_RC=$?
check_inactive() {
  U=$1
  if STATE=$(systemctl is-active "$U" 2>/dev/null); then RC=0; else RC=$?; fi
  case "$STATE" in
    active|activating|reloading|deactivating) return 1 ;;
    inactive|failed|unknown) return 0 ;;
    *) return 1 ;;
  esac
}
if ! check_inactive "$TIMER.timer" || ! check_inactive "$TIMER.service"; then
  echo 'HELIOS_ERROR=timer ou service expiration toujours actif'
  exit 41
fi
if [ -n "$HELPER" ]; then rm -f -- "$HELPER"; fi
printf 'HELIOS_TIMER_CANCELLED=1\\nHELIOS_STOP_COMMAND_RC=%%s\\n' "$STOP_RC"
""" % (q(timer), q(helper or ""))

def quota_timer_annuler(noeud, service):
    """Refuse l'annulation isolée si le drop-in est encore appliqué: utiliser quota_retirer."""
    valide, raison = _quota_target_valider(noeud, service)
    if not valide:
        return {"ok": False, "unit": None, "detail": raison}
    ok, info = _quota_lire_info(noeud, service)
    if not ok:
        return {"ok": False, "unit": None, "detail": info}
    if info["legacy_timer_active"]:
        return {"ok": False, "unit": None, "detail": "ancien timer Bridge non tokenisé actif; annulation refusée"}
    if info["owner"] == "absent":
        return {"ok": True, "unit": None, "already_absent": True}
    return {
        "ok": False, "unit": info.get("timer"),
        "detail": "annulation isolée refusée: le bail possède encore un drop-in actif; utiliser quota_retirer pour retirer timer et quota atomiquement",
    }

def quota_programmer_expiration(noeud, service, duree_s, token=None, baseline=None, quota_pct=None):
    """Crée un timer systemd tokenisé qui survit au redémarrage du processus Bridge."""
    valide, raison = _quota_target_valider(noeud, service)
    if not valide:
        return False, {"etape": "validation_cible", "detail": raison}
    try:
        duree_s = int(duree_s)
    except (TypeError, ValueError):
        return False, {"etape": "validation_duree", "detail": "durée non entière"}
    if duree_s <= 0 or duree_s > QUOTA_DUREE_MAX_S:
        return False, {"etape": "validation_duree", "detail": "durée hors bornes autorisées"}
    token = token or uuid.uuid4().hex
    if not _QUOTA_TOKEN_RE.fullmatch(str(token)):
        return False, {"etape": "validation_token", "detail": "token invalide"}
    if baseline is None:
        ok, det = quota_cible_saine(noeud, service)
        if not ok:
            return False, {"etape": "verification_cible", "detail": det}
        baseline = det.get("quota_actuel", "")
    baseline = str(baseline).strip()
    if not _QUOTA_BASELINE_RE.fullmatch(baseline):
        return False, {"etape": "validation_baseline", "detail": "format de quota initial non reconnu"}
    if quota_pct is None or not isinstance(quota_pct, int) or not (QUOTA_MIN_PCT <= quota_pct <= QUOTA_MAX_PCT):
        return False, {"etape": "validation_quota", "detail": "quota_pct entier autorisé requis pour l'expiration tokenisée"}
    p = _quota_paths(noeud, service, token)
    helper = _quota_helper_source(noeud, service, token, baseline, quota_pct)
    bloque = commande_interdite(helper)
    if bloque:
        return False, {"etape": "validation_helper", "detail": bloque}
    helper_b64 = base64.b64encode(helper.encode("utf-8")).decode("ascii")
    q = shlex.quote
    script = """#!/bin/sh
set -eu
STATE_DIR=%s
HELPER=%s
HELPER_TMP=\"${HELPER}.tmp\"
TIMER=%s
DURATION=%s
if [ -L \"$STATE_DIR\" ]; then echo 'HELIOS_ERROR=répertoire état symlink'; exit 51; fi
if [ -e \"$STATE_DIR\" ] && [ ! -d \"$STATE_DIR\" ]; then echo 'HELIOS_ERROR=répertoire état invalide'; exit 52; fi
install -d -o root -g root -m 0755 \"$STATE_DIR\"
DIR_META=$(stat -c '%%u:%%a' \"$STATE_DIR\")
if [ \"$DIR_META\" != '0:755' ]; then echo 'HELIOS_ERROR=permissions répertoire état inattendues'; exit 53; fi
printf '%%s' %s | base64 -d > \"$HELPER_TMP\"
chmod 0750 \"$HELPER_TMP\"
mv -f -- \"$HELPER_TMP\" \"$HELPER\"
if ! systemd-run --quiet --unit=\"$TIMER\" --on-active=\"${DURATION}s\" --timer-property=AccuracySec=1s --property=Restart=on-failure --property=RestartSec=30s --property=RestartMode=direct --collect /bin/sh \"$HELPER\"; then
  rm -f -- \"$HELPER\"
  echo 'HELIOS_ERROR=systemd-run a échoué'
  exit 54
fi
if STATE=$(systemctl is-active \"$TIMER.timer\" 2>/dev/null); then :; else STATE=unknown; fi
if [ \"$STATE\" != active ]; then
  STOP_RC=0
  systemctl stop \"$TIMER.timer\" \"$TIMER.service\" >/dev/null 2>&1 || STOP_RC=$?
  rm -f -- \"$HELPER\"
  echo 'HELIOS_ERROR=timer non actif après création'
  exit 55
fi
printf 'HELIOS_TIMER_UNIT=%%s\\nHELIOS_TIMER_STATE=%%s\\n' \"$TIMER\" \"$STATE\"
""" % (
        q(_QUOTA_STATE_DIR), q(p["helper"]), q(p["timer"]), int(duree_s), q(helper_b64)
    )
    r = _quota_remote_script(noeud, service, script)
    values = _quota_parse_kv(r)
    if not r.get("ok") or values.get("HELIOS_TIMER_STATE") != "active":
        cleanup = _quota_cancel_specific(noeud, service, p["timer"], p["helper"])
        return False, {
            "unit": p["timer"], "token": token, "etape": "creation_timer",
            "detail": (r.get("stderr") or r.get("stdout") or "timer non confirmé actif")[:300],
            "nettoyage": cleanup,
        }
    return True, {"unit": p["timer"], "token": token, "helper": p["helper"], "duree_s": int(duree_s), "etat": "active"}

def _quota_make_apply_script(noeud, service, quota_pct, baseline, token, timer, expected_owner, expected_token, expected_current, expected_prior_content_b64=""):
    p = _quota_paths(noeud, service, token)
    q = shlex.quote
    content = "\n".join([
        "# HELIOS_QUOTA_MANAGED_V1",
        "# HELIOS_QUOTA_TOKEN=" + token,
        "# HELIOS_QUOTA_BASELINE=" + baseline,
        "# HELIOS_QUOTA_TIMER_UNIT=" + timer,
        "[Service]",
        "CPUQuota=%d%%" % int(quota_pct),
        "",
    ])
    content_b64 = base64.b64encode(content.encode("utf-8")).decode("ascii")
    expected_us = int(quota_pct) * 10000
    expected_token_shell = q(expected_token or "")
    return """#!/bin/sh
set -eu
SERVICE=%s
DROPIN_DIR=%s
DROPIN=%s
LEGACY_TIMER=%s
STATE_DIR=%s
TOKEN=%s
BASELINE=%s
TIMER=%s
EXPECTED_OWNER=%s
EXPECTED_TOKEN=%s
EXPECTED_CURRENT=%s
EXPECTED_PRIOR_B64=%s
QUOTA_PCT=%s
EXPECTED_US=%s
HELPER=%s
HELPER_TMP=\"${DROPIN}.tmp.${TOKEN}\"
BACKUP=%s/%s-prior.conf
if [ -L \"$DROPIN_DIR\" ]; then echo 'HELIOS_ERROR=répertoire drop-in est un lien'; exit 61; fi
if [ -e \"$DROPIN_DIR\" ] && [ ! -d \"$DROPIN_DIR\" ]; then echo 'HELIOS_ERROR=répertoire drop-in invalide'; exit 62; fi
if [ ! -e \"$DROPIN_DIR\" ]; then install -d -o root -g root -m 0755 \"$DROPIN_DIR\"; fi
DROPIN_DIR_META=$(stat -c '%%u:%%a' \"$DROPIN_DIR\")
if [ \"$DROPIN_DIR_META\" != '0:755' ]; then echo 'HELIOS_ERROR=propriété ou permissions du répertoire drop-in inattendues'; exit 62; fi
for U in \"$LEGACY_TIMER.timer\" \"$LEGACY_TIMER.service\"; do
  if STATE=$(systemctl is-active \"$U\" 2>/dev/null); then :; else :; fi
  case \"$STATE\" in
    active|activating|reloading|deactivating) echo 'HELIOS_ERROR=ancien timer non tokenisé actif'; exit 60 ;;
    inactive|failed|unknown) : ;;
    *) echo 'HELIOS_ERROR=état ancien timer illisible'; exit 60 ;;
  esac
done
for D in /etc/systemd/system.control/${SERVICE}.d /run/systemd/system.control/${SERVICE}.d; do
  if [ -L \"$D\" ]; then echo 'HELIOS_ERROR=systemd-control symlink'; exit 63; fi
  if [ -d \"$D\" ]; then
    FOUND=$(find \"$D\" -mindepth 1 -maxdepth 1 -print -quit) || { echo 'HELIOS_ERROR=lecture systemd-control impossible'; exit 64; }
    if [ -n \"$FOUND\" ]; then echo 'HELIOS_ERROR=systemd-control non vide, propriété inconnue'; exit 65; fi
  fi
done
CURRENT=$(systemctl show \"$SERVICE\" -p CPUQuotaPerSecUSec --value)
ACTIVE=$(systemctl show \"$SERVICE\" -p ActiveState --value)
LOAD=$(systemctl show \"$SERVICE\" -p LoadState --value)
if [ \"$LOAD\" != loaded ] || [ \"$ACTIVE\" != active ]; then echo 'HELIOS_ERROR=cible devenue inactive'; exit 66; fi
OWNER=absent
OLD_TOKEN=
OLD_TIMER=
OLD_BASELINE=
if [ -L \"$DROPIN\" ]; then echo 'HELIOS_ERROR=drop-in est un lien'; exit 67; fi
if [ -e \"$DROPIN\" ]; then
  if [ ! -f \"$DROPIN\" ] || ! grep -Fxq '# HELIOS_QUOTA_MANAGED_V1' \"$DROPIN\"; then echo 'HELIOS_ERROR=drop-in réservé déjà utilisé'; exit 68; fi
  ACTUAL_PRIOR_B64=$(base64 -w0 \"$DROPIN\")
  if [ \"$ACTUAL_PRIOR_B64\" != \"$EXPECTED_PRIOR_B64\" ]; then echo 'HELIOS_ERROR=contenu du bail précédent changé'; exit 68; fi
  OWNER=helios
  OLD_TOKEN=$(sed -n 's/^# HELIOS_QUOTA_TOKEN=//p' \"$DROPIN\")
  OLD_BASELINE=$(sed -n 's/^# HELIOS_QUOTA_BASELINE=//p' \"$DROPIN\")
  OLD_TIMER=$(sed -n 's/^# HELIOS_QUOTA_TIMER_UNIT=//p' \"$DROPIN\")
fi
if [ \"$EXPECTED_OWNER\" = absent ]; then
  if [ \"$OWNER\" != absent ] || [ \"$CURRENT\" != \"$EXPECTED_CURRENT\" ]; then echo 'HELIOS_ERROR=état initial changé pendant préparation'; exit 69; fi
  PRIOR_EFFECTIVE=$CURRENT
elif [ \"$EXPECTED_OWNER\" = helios ]; then
  if [ \"$OWNER\" = helios ] && [ \"$OLD_TOKEN\" = \"$EXPECTED_TOKEN\" ]; then
    if [ \"$OLD_BASELINE\" != \"$BASELINE\" ] || [ \"$CURRENT\" != \"$EXPECTED_CURRENT\" ]; then echo 'HELIOS_ERROR=bail précédent modifié'; exit 70; fi
    PRIOR_EFFECTIVE=$CURRENT
  elif [ \"$OWNER\" = absent ] && [ \"$CURRENT\" = \"$BASELINE\" ]; then
    # L'expiration précédente a terminé pendant la préparation.
    PRIOR_EFFECTIVE=$CURRENT
  else
    echo 'HELIOS_ERROR=identité du bail précédent incohérente'; exit 71
  fi
else
  echo 'HELIOS_ERROR=propriétaire attendu invalide'; exit 72
fi
if [ \"$OWNER\" = helios ]; then
  cp -p -- \"$DROPIN\" \"$BACKUP\"
  HAD_PRIOR=1
else
  HAD_PRIOR=0
fi
umask 022
printf '%%s' %s | base64 -d > \"$HELPER_TMP\"
chmod 0644 \"$HELPER_TMP\"
mv -f -- \"$HELPER_TMP\" \"$DROPIN\"
if ! systemctl daemon-reload; then
  if [ \"$HAD_PRIOR\" -eq 1 ]; then mv -f -- \"$BACKUP\" \"$DROPIN\"; else rm -f -- \"$DROPIN\"; fi
  systemctl daemon-reload || { echo 'HELIOS_ERROR=échec daemon-reload et rollback'; exit 73; }
  systemctl stop \"$TIMER.timer\" \"$TIMER.service\" >/dev/null 2>&1 || STOP_RC=$?
  echo 'HELIOS_ERROR=daemon-reload échoué, état antérieur restauré'
  exit 74
fi
ACTUAL=$(systemctl show \"$SERVICE\" -p CPUQuotaPerSecUSec --value)
ACTUAL_US=$(/usr/bin/python3 -c 'import re,sys; s=sys.argv[1].strip().lower(); m=re.fullmatch(r"([0-9]+(?:\\.[0-9]+)?)(min|ms|us|s)",s); print(-1 if s=="infinity" else round(float(m.group(1))*{"min":60000000,"s":1000000,"ms":1000,"us":1}[m.group(2)])) if s=="infinity" or m else sys.exit(2)' \"$ACTUAL\") || ACTUAL_US=-999
if [ \"$ACTUAL_US\" -ne \"$EXPECTED_US\" ]; then
  if [ \"$HAD_PRIOR\" -eq 1 ]; then mv -f -- \"$BACKUP\" \"$DROPIN\"; else rm -f -- \"$DROPIN\"; fi
  if ! systemctl daemon-reload; then echo 'HELIOS_ERROR=quota non conforme et daemon-reload rollback échoué'; exit 75; fi
  RESTORED=$(systemctl show \"$SERVICE\" -p CPUQuotaPerSecUSec --value)
  if [ \"$RESTORED\" != \"$PRIOR_EFFECTIVE\" ]; then echo 'HELIOS_ERROR=quota non conforme et restauration non vérifiée'; exit 76; fi
  systemctl stop \"$TIMER.timer\" \"$TIMER.service\" >/dev/null 2>&1 || STOP_RC=$?
  printf 'HELIOS_ERROR=quota effectif incorrect; rollback vérifié; attendu_us=%%s obtenu=%%s\\n' \"$EXPECTED_US\" \"$ACTUAL_US\" >&2
  exit 77
fi
if [ \"$HAD_PRIOR\" -eq 1 ]; then rm -f -- \"$BACKUP\"; fi
OLD_CLEANUP=none
if [ -n \"$OLD_TIMER\" ] && [ \"$OLD_TIMER\" != \"$TIMER\" ]; then
  systemctl stop \"$OLD_TIMER.timer\" \"$OLD_TIMER.service\" >/dev/null 2>&1 || OLD_STOP_RC=$?
  OLD_CLEANUP=verified
  for U in \"$OLD_TIMER.timer\" \"$OLD_TIMER.service\"; do
    if STATE=$(systemctl is-active \"$U\" 2>/dev/null); then :; else :; fi
    case \"$STATE\" in
      active|activating|reloading|deactivating|"") OLD_CLEANUP=warning ;;
      inactive|failed|unknown) : ;;
      *) OLD_CLEANUP=warning ;;
    esac
  done
fi
printf 'HELIOS_APPLIED_TOKEN=%%s\\nHELIOS_TIMER_UNIT=%%s\\nHELIOS_BASELINE=%%s\\nHELIOS_EFFECTIVE=%%s\\nHELIOS_EFFECTIVE_US=%%s\\nHELIOS_PREVIOUS_TIMER_CLEANUP=%%s\\n' \"$TOKEN\" \"$TIMER\" \"$BASELINE\" \"$ACTUAL\" \"$ACTUAL_US\" \"$OLD_CLEANUP\"
""" % (
        q(service), q(p["dropin_dir"]), q(p["dropin"]), q("helios-quota-expire-%s-%s" % (noeud, service[:-8])), q(_QUOTA_STATE_DIR),
        q(token), q(baseline), q(timer), q(expected_owner), expected_token_shell,
        q(expected_current), q(expected_prior_content_b64), int(quota_pct), expected_us, q(p["helper"]),
        q(_QUOTA_STATE_DIR), q(token), q(content_b64)
    )

def _quota_cancel_specific(noeud, service, timer, helper=None):
    """Annule un timer précis et ne supprime son helper qu'après vérification."""
    valide, raison = _quota_target_valider(noeud, service)
    if not valide:
        return {"ok": False, "detail": "cible quota non autorisée: %s" % raison}
    if not timer or not re.fullmatch(r"helios-quota-expire-[a-z0-9-]+-[0-9a-f]{32}", timer):
        return {"ok": False, "detail": "nom de timer invalide"}
    token = timer[-32:]
    expected = _quota_paths(noeud, service, token)
    if timer != expected["timer"]:
        return {"ok": False, "detail": "timer sans correspondance exacte avec le nœud/service autorisés"}
    if helper is not None and helper != expected["helper"]:
        return {"ok": False, "detail": "chemin helper sans correspondance exacte avec le bail"}
    script = _quota_stop_timer_script(noeud, service, timer, helper)
    r = _quota_remote_script(noeud, service, script)
    values = _quota_parse_kv(r)
    return {"ok": bool(r.get("ok") and values.get("HELIOS_TIMER_CANCELLED") == "1"),
            "unit": timer, "detail": (r.get("stderr") or r.get("stdout") or "")[:250]}

def quota_appliquer(noeud, service, quota_pct, duree_s, raison=""):
    """Applique un drop-in HELIOS tokenisé, après création d'un timer autonome."""
    with _QUOTA_OPERATION_LOCK:
        okv, verdict = quota_valider(noeud, service, quota_pct, duree_s)
        if not okv:
            return {"ok": False, "etape": "validation", "detail": verdict}
        quota_pct = verdict["quota_pct"]
        duree_s = verdict["duree_s"]
        ok, det = quota_cible_saine(noeud, service)
        if not ok:
            return {"ok": False, "etape": "verification_cible", "detail": det}
        ok_info, info = _quota_lire_info(noeud, service)
        if not ok_info:
            return {"ok": False, "etape": "lecture_bail", "detail": info}
        if info["legacy_timer_active"]:
            return {"ok": False, "etape": "ancien_timer", "detail": "ancien timer Bridge non tokenisé encore actif; action refusée jusqu'à audit de ce timer"}
        if info["control_nonempty"] != "0":
            return {"ok": False, "etape": "systemd_control", "detail": "drop-ins systemd.control présents; propriété non possédée, mutation refusée"}
        current = info["current"]
        if info["owner"] == "helios":
            if not info["token_timer_active"]:
                return {"ok": False, "etape": "timer_bail_precedent", "detail": "un drop-in HELIOS existe sans timer tokenisé actif; retrait explicite/diagnostic requis avant renouvellement"}
            baseline = info["baseline"]
            current_old_us = _duree_systemd_us(current)
            if current_old_us is None or abs(current_old_us - int(info["pct"]) * 10000.0) > max(1000.0, int(info["pct"]) * 10000.0 * 0.01):
                return {"ok": False, "etape": "verification_bail_precedent", "detail": "le quota effectif ne correspond pas au drop-in HELIOS existant"}
            expected_owner = "helios"
            expected_token = info["token"]
        else:
            baseline = current
            expected_owner = "absent"
            expected_token = ""
        if not _QUOTA_BASELINE_RE.fullmatch(baseline):
            return {"ok": False, "etape": "baseline", "detail": "format du quota initial non reconnu: %r" % baseline}
        token = uuid.uuid4().hex
        timer = _quota_paths(noeud, service, token)["timer"]
        ok_timer, timer_info = quota_programmer_expiration(noeud, service, duree_s, token=token, baseline=baseline, quota_pct=quota_pct)
        if not ok_timer:
            return {"ok": False, "etape": "programmation_expiration", "detail": timer_info}
        prior_content_b64 = ""
        if info["owner"] == "helios":
            prior_content = "\n".join([
                "# HELIOS_QUOTA_MANAGED_V1",
                "# HELIOS_QUOTA_TOKEN=" + info["token"],
                "# HELIOS_QUOTA_BASELINE=" + info["baseline"],
                "# HELIOS_QUOTA_TIMER_UNIT=" + info["timer"],
                "[Service]", "CPUQuota=" + info["pct"] + "%", "",
            ])
            prior_content_b64 = base64.b64encode(prior_content.encode("utf-8")).decode("ascii")
        script = _quota_make_apply_script(
            noeud, service, quota_pct, baseline, token, timer,
            expected_owner, expected_token, current, prior_content_b64,
        )
        r = _quota_remote_script(noeud, service, script)
        values = _quota_parse_kv(r)
        if not r.get("ok") or values.get("HELIOS_APPLIED_TOKEN") != token:
            # SSH peut perdre la réponse après application. Réconcilier avant toute
            # annulation: ne jamais stopper le timer d'un bail déjà appliqué.
            latest_ok, latest = _quota_lire_info(noeud, service)
            if (latest_ok and latest.get("owner") == "helios"
                    and latest.get("token") == token
                    and latest.get("baseline") == baseline
                    and latest.get("pct") == str(quota_pct)
                    and latest.get("token_timer_active")):
                r = {"ok": True, "stdout": "réconciliation du bail après réponse Bridge/SSH perdue", "stderr": ""}
            else:
                # Si le nouveau drop-in existe mais n'a plus de timer actif, retirer
                # uniquement ce bail. En état indéterminé, conserver le timer comme filet.
                if latest_ok and latest.get("owner") == "helios" and latest.get("token") == token:
                    cleanup = quota_retirer(noeud, service, motif="application_ambigue_timer_inactif")
                elif latest_ok and latest.get("owner") != "helios" and latest.get("current") == baseline:
                    cleanup = _quota_cancel_specific(noeud, service, timer, _quota_paths(noeud, service, token)["helper"])
                else:
                    cleanup = {"ok": False, "detail": "timer conservé: état distant non suffisamment confirmé"}
                return {
                    "ok": False, "etape": "application", "detail": (r.get("stderr") or r.get("stdout") or "application non confirmée")[:400],
                    "nettoyage_timer": cleanup,
                }

        # Vérification indépendante après la commande d'application: le seul exitCode
        # du script ne prouve ni que le bail est toujours présent, ni que son timer tourne.
        post_ok, post = _quota_lire_info(noeud, service)
        if not post_ok:
            return {"ok": False, "etape": "verification_post_application", "detail": post,
                    "timer_conserve": True}
        if post.get("owner") != "helios" or post.get("token") != token:
            cleanup = None
            if post.get("owner") != "helios" and post.get("current") == baseline:
                cleanup = _quota_cancel_specific(noeud, service, timer, _quota_paths(noeud, service, token)["helper"])
            return {"ok": False, "etape": "verification_post_application",
                    "detail": "le bail demandé n'est plus propriétaire du drop-in après application",
                    "quota_effectif": post.get("current"), "nettoyage_timer": cleanup}
        if not post.get("token_timer_active"):
            retrait = quota_retirer(noeud, service, motif="timer_inactif_apres_application")
            return {"ok": False, "etape": "verification_timer_post_application",
                    "detail": "le drop-in a été retiré car aucun timer tokenisé actif n'a été confirmé",
                    "retrait": retrait}
        actual = post.get("current", "")
        actual_us = _duree_systemd_us(actual)
        expected_us = quota_pct * 10000.0
        conforme = actual_us is not None and abs(actual_us - expected_us) <= max(1000.0, expected_us * 0.01)
        if not conforme:
            retrait = quota_retirer(noeud, service, motif="verification_effective_non_conforme")
            return {"ok": False, "etape": "verification_effective", "quota_effectif": actual, "retrait": retrait}
        maintenant = time.time()
        etat = {
            "noeud": noeud, "service": service, "quota_pct": quota_pct,
            "quota_attendu_us": expected_us, "quota_effectif": actual,
            "quota_effectif_us": actual_us, "conforme": True,
            "quota_avant": baseline, "applique_le": maintenant,
            "expire_le": maintenant + duree_s, "duree_s": duree_s,
            "raison": str(raison or "")[:200], "etat": "APPLIED",
            "service_actif": "active", "token": token, "timer_unit": timer,
            "ancien_timer_cleanup": values.get("HELIOS_PREVIOUS_TIMER_CLEANUP", "none"),
        }
        with _QUOTAS_LOCK:
            _QUOTAS[(noeud, service)] = etat
        tracer("QUOTA APPLIQUE", noeud, service, "| CPUQuota=%d%%" % quota_pct,
               "| effectif", actual, "| expire dans", duree_s, "s")
        return {"ok": True, "etat": etat}


def _quota_terminaliser(noeud, service, etat_final, motif, quota_apres,
                        dropins_residuels, bail_connu=None, token_attendu=None):
    """HOST-CPU-BRIDGE-FIX-01 (DEFAUT A) — fait passer le bail (noeud, service) a un
    etat TERMINAL (ROLLED_BACK / EXPIRED / FAILED) sous _QUOTAS_LOCK.

    Ne terminalise QUE s il s agit encore du bail attendu — identite d objet
    (`bail_connu`) OU token (`token_attendu`) — afin de NE JAMAIS ecraser un bail plus
    recent installe par un re-apply concurrent. Idempotent : un bail deja hors APPLIED
    n est pas retouche. Rend True si l entree est (ou etait deja) dans un etat terminal.
    """
    with _QUOTAS_LOCK:
        e = _QUOTAS.get((noeud, service))
        if e is None:
            return False
        if bail_connu is not None and e is not bail_connu:
            return False
        if token_attendu is not None and e.get("token") != token_attendu:
            return False
        if e.get("etat") != "APPLIED":
            return True
        e["etat"] = etat_final
        e["retire_le"] = time.time()
        e["motif_retrait"] = motif
        e["quota_apres_retrait"] = quota_apres
        e["dropins_residuels"] = dropins_residuels
    return True


def quota_retirer(noeud, service, motif="explicite"):
    """Retire uniquement le drop-in possédé par HELIOS et vérifie la baseline."""
    valide, raison = _quota_target_valider(noeud, service)
    if not valide:
        return {"ok": False, "etape": "validation_cible", "detail": raison, "motif": motif}
    with _QUOTA_OPERATION_LOCK:
        ok_info, info = _quota_lire_info(noeud, service)
        if not ok_info:
            return {"ok": False, "etape": "lecture_bail", "detail": info, "motif": motif}
        if info["owner"] == "foreign":
            return {"ok": False, "etape": "propriete", "detail": "drop-in réservé non possédé par HELIOS; aucun fichier supprimé", "motif": motif}
        if info["legacy_timer_active"]:
            return {"ok": False, "etape": "ancien_timer", "detail": "ancien timer Bridge non tokenisé actif; quota laissé intact jusqu'à audit du timer", "motif": motif}
        if info["owner"] == "absent":
            with _QUOTAS_LOCK:
                known = _QUOTAS.get((noeud, service))
            expected = known.get("quota_avant") if known else None
            # HOST-CPU-BRIDGE-FIX-01 (DEFAUT A) : le bail DOIT passer a un etat terminal,
            # sinon quota_balayer() (qui ne retient que etat=="APPLIED" && expire) le
            # re-selectionne toutes les 15 s indefiniment. Cas reel observe : le timer
            # systemd cote noeud avait restaure la baseline AVANT le balayage -> owner
            # "absent" et quota deja a infinity/baseline, donc ces retours anticipes
            # laissaient le bail en APPLIED => boucle de retrait sans fin.
            baseline_restauree = (
                (expected is not None and info["current"] == expected)
                or (info["current"] == "infinity" and info["control_nonempty"] == "0")
            )
            if baseline_restauree:
                # Retrait deja effectif ET ressources HELIOS nettoyees -> ROLLED_BACK.
                _quota_terminaliser(noeud, service, "ROLLED_BACK", motif,
                                    info["current"], 0, bail_connu=known)
                return {"ok": True, "already_absent": True,
                        "quota_apres_retrait": info["current"], "dropins_residuels": 0,
                        "etat_bail": "ROLLED_BACK", "motif": motif}
            # Quota effectif NON attribuable a la baseline de ce bail : jamais declare
            # reussi, etat terminal FAILED (stoppe la boucle et signale pour intervention).
            _quota_terminaliser(noeud, service, "FAILED", motif,
                                info["current"], None, bail_connu=known)
            return {"ok": False, "etape": "bail_absent", "detail": "aucun drop-in HELIOS à retirer; quota effectif non attribuable à ce bail", "quota_apres_retrait": info["current"], "etat_bail": "FAILED", "motif": motif}
        token = info["token"]
        baseline = info["baseline"]
        timer = info["timer"]
        p = _quota_paths(noeud, service, token)
        q = shlex.quote
        expected_content = "\n".join([
            "# HELIOS_QUOTA_MANAGED_V1",
            "# HELIOS_QUOTA_TOKEN=" + token,
            "# HELIOS_QUOTA_BASELINE=" + baseline,
            "# HELIOS_QUOTA_TIMER_UNIT=" + timer,
            "[Service]", "CPUQuota=" + info["pct"] + "%", "",
        ])
        expected_content_b64 = base64.b64encode(expected_content.encode("utf-8")).decode("ascii")
        script = """#!/bin/sh
set -eu
SERVICE=%s
DROPIN=%s
TOKEN=%s
BASELINE=%s
TIMER=%s
HELPER=%s
EXPECTED_CONTENT_B64=%s
if [ -L "$DROPIN" ]; then echo 'HELIOS_ERROR=drop-in est un lien symbolique'; exit 81; fi
if [ ! -e "$DROPIN" ]; then
  # L'expiration peut finir entre la lecture initiale et l'acquisition du flock.
  CURRENT=$(systemctl show "$SERVICE" -p CPUQuotaPerSecUSec --value) || { echo 'HELIOS_ERROR=lecture du quota apr?s expiration impossible'; exit 89; }
  if [ "$CURRENT" != "$BASELINE" ]; then echo 'HELIOS_ERROR=drop-in absent mais baseline non restaur?e'; exit 89; fi
  for D in "/etc/systemd/system.control/${SERVICE}.d" "/run/systemd/system.control/${SERVICE}.d"; do
    if [ -L "$D" ]; then echo 'HELIOS_ERROR=systemd-control symlink'; exit 89; fi
    if [ -d "$D" ]; then
      FOUND=$(find "$D" -mindepth 1 -maxdepth 1 -print -quit) || { echo 'HELIOS_ERROR=lecture systemd-control impossible'; exit 89; }
      if [ -n "$FOUND" ]; then echo 'HELIOS_ERROR=systemd-control non vide; retrait concurrent non confirm?'; exit 89; fi
    fi
  done
  STOP_RC=0
  systemctl stop "$TIMER.timer" "$TIMER.service" >/dev/null 2>&1 || STOP_RC=$?
  for U in "$TIMER.timer" "$TIMER.service"; do
    STATE=
    if STATE=$(systemctl is-active "$U" 2>/dev/null); then :; else :; fi
    case "$STATE" in
      active|activating|reloading|deactivating)
        printf 'HELIOS_ERROR=baseline restaur?e mais timer encore actif (stop rc=%%s)\\n' "$STOP_RC" >&2
        exit 88 ;;
      inactive|failed|unknown) : ;;
      *) echo 'HELIOS_ERROR=?tat du timer illisible apr?s expiration'; exit 88 ;;
    esac
  done
  rm -f -- "$HELPER" || { echo 'HELIOS_ERROR=quota restaur? mais helper non nettoy?'; exit 88; }
  printf 'HELIOS_REMOVED_TOKEN=%%s\\nHELIOS_QUOTA_AFTER=%%s\\nHELIOS_TIMER_INACTIVE=1\\nHELIOS_DROPIN_ABSENT=1\\nHELIOS_ALREADY_EXPIRED=1\\n' "$TOKEN" "$CURRENT"
  exit 0
fi
if [ ! -f "$DROPIN" ]; then echo 'HELIOS_ERROR=drop-in non r?gulier'; exit 81; fi
ACTUAL_CONTENT_B64=$(base64 -w0 "$DROPIN")
if [ "$ACTUAL_CONTENT_B64" != "$EXPECTED_CONTENT_B64" ]; then echo 'HELIOS_ERROR=contenu drop-in changé; retrait refusé'; exit 82; fi
if ! grep -Fxq '# HELIOS_QUOTA_MANAGED_V1' "$DROPIN"; then echo 'HELIOS_ERROR=marqueur de propriété absent'; exit 82; fi
CURRENT_TOKEN=$(sed -n 's/^# HELIOS_QUOTA_TOKEN=//p' "$DROPIN")
CURRENT_BASELINE=$(sed -n 's/^# HELIOS_QUOTA_BASELINE=//p' "$DROPIN")
CURRENT_TIMER=$(sed -n 's/^# HELIOS_QUOTA_TIMER_UNIT=//p' "$DROPIN")
if [ "$CURRENT_TOKEN" != "$TOKEN" ] || [ "$CURRENT_BASELINE" != "$BASELINE" ] || [ "$CURRENT_TIMER" != "$TIMER" ]; then echo 'HELIOS_ERROR=identité du bail changée; retrait refusé'; exit 83; fi
check_inactive() {
  U=$1
  if STATE=$(systemctl is-active "$U" 2>/dev/null); then :; else :; fi
  case "$STATE" in
    active|activating|reloading|deactivating) return 1 ;;
    inactive|failed|unknown) return 0 ;;
    *) return 1 ;;
  esac
}
if ! rm -- "$DROPIN"; then echo 'HELIOS_ERROR=impossible de retirer le drop-in'; exit 85; fi
if [ -e "$DROPIN" ] || [ -L "$DROPIN" ]; then echo 'HELIOS_ERROR=drop-in encore présent après retrait'; exit 85; fi
RESTORED=0
ATTEMPT=1
ACTUAL=
while [ "$ATTEMPT" -le 3 ]; do
  if systemctl daemon-reload; then
    ACTUAL=$(systemctl show "$SERVICE" -p CPUQuotaPerSecUSec --value) || ACTUAL=
    if [ "$ACTUAL" = "$BASELINE" ]; then RESTORED=1; break; fi
  fi
  sleep 1
  ATTEMPT=$((ATTEMPT + 1))
done
if [ "$RESTORED" -ne 1 ]; then
  # Préserver une configuration cohérente si le retour à la baseline échoue:
  # remettre uniquement le drop-in HELIOS exact et rendre l'échec explicite.
  RESTORE_TMP="${DROPIN}.recovery.${TOKEN}"
  PRESERVED=0
  if printf '%%s' "$EXPECTED_CONTENT_B64" | base64 -d > "$RESTORE_TMP" &&
     chmod 0644 "$RESTORE_TMP" && mv -f -- "$RESTORE_TMP" "$DROPIN"; then
    PCT=$(sed -n 's/^CPUQuota=//p' "$DROPIN" | tr -d '%%')
    case "$PCT" in ''|*[!0-9]*) PCT=0 ;; esac
    if [ "$PCT" -gt 0 ]; then
      EXPECTED_ACTIVE_US=$((PCT * 10000))
      ATTEMPT=1
      while [ "$ATTEMPT" -le 3 ]; do
        if systemctl daemon-reload; then
          CHECK=$(systemctl show "$SERVICE" -p CPUQuotaPerSecUSec --value) || CHECK=
          CHECK_US=$(/usr/bin/python3 -c 'import re,sys; s=sys.argv[1].strip().lower(); m=re.fullmatch(r"([0-9]+(?:\\.[0-9]+)?)(min|ms|us|s)",s); print(round(float(m.group(1))*{"min":60000000,"s":1000000,"ms":1000,"us":1}[m.group(2)]) if m else -1)' "$CHECK") || CHECK_US=-999
          if [ "$CHECK_US" -eq "$EXPECTED_ACTIVE_US" ]; then PRESERVED=1; break; fi
        fi
        sleep 1
        ATTEMPT=$((ATTEMPT + 1))
      done
    fi
  fi
  if [ "$PRESERVED" -eq 1 ]; then
    printf 'HELIOS_ERROR=baseline non restaurée; drop-in HELIOS restauré et quota conservé; intervention requise\\n' >&2
    exit 86
  fi
  printf 'HELIOS_ERROR=CRITIQUE: baseline non restaurée et récupération du drop-in non confirmée\\n' >&2
  exit 87
fi
STOP_RC=0
systemctl stop "$TIMER.timer" "$TIMER.service" >/dev/null 2>&1 || STOP_RC=$?
if ! check_inactive "$TIMER.timer" || ! check_inactive "$TIMER.service"; then
  printf 'HELIOS_ERROR=baseline restaurée mais timer/service non annulé (rc=%%s); quota restauré mais nettoyage incomplet\\n' "$STOP_RC" >&2
  exit 88
fi
rm -f -- "$HELPER"
printf 'HELIOS_REMOVED_TOKEN=%%s\\nHELIOS_QUOTA_AFTER=%%s\\nHELIOS_TIMER_INACTIVE=1\\nHELIOS_DROPIN_ABSENT=1\\n' "$TOKEN" "$ACTUAL"
""" % (q(service), q(p["dropin"]), q(token), q(baseline), q(timer), q(p["helper"]), q(expected_content_b64))
        r = _quota_remote_script(noeud, service, script)
        values = _quota_parse_kv(r)
        success = bool(r.get("ok") and values.get("HELIOS_REMOVED_TOKEN") == token
                       and values.get("HELIOS_QUOTA_AFTER") == baseline
                       and values.get("HELIOS_TIMER_INACTIVE") == "1"
                       and values.get("HELIOS_DROPIN_ABSENT") == "1")
        # HOST-CPU-BRIDGE-FIX-01 (DEFAUT A) : terminalisation via le meme helper idempotent,
        # avec controle d identite du bail par TOKEN pour ne pas ecraser un bail plus recent
        # (re-apply concurrent pendant l execution du script distant).
        _quota_terminaliser(noeud, service, "ROLLED_BACK" if success else "FAILED",
                            motif, values.get("HELIOS_QUOTA_AFTER"),
                            0 if success else None, token_attendu=token)
        res = {
            "ok": success, "quota_apres_retrait": values.get("HELIOS_QUOTA_AFTER"),
            "dropins_residuels": 0 if success else None,
            "timer_inactif": values.get("HELIOS_TIMER_INACTIVE") == "1",
            "dropin_absent": values.get("HELIOS_DROPIN_ABSENT") == "1",
            "motif": motif,
        }
        tracer("QUOTA RETIRE", noeud, service, "| baseline", baseline,
               "| motif", motif, "| ok", success)
        return res


def quota_etat(noeud=None, service=None):
    """Etat observable des baux. NOT_APPLIED / APPLIED / EXPIRED / ROLLED_BACK / FAILED."""
    maintenant = time.time()
    with _QUOTAS_LOCK:
        items = list(_QUOTAS.items())
    sortie = []
    for (n, s), e in items:
        if noeud and n != noeud:
            continue
        if service and s != service:
            continue
        c = dict(e)
        if c.get("etat") == "APPLIED" and maintenant >= c.get("expire_le", 0):
            c["etat"] = "EXPIRED"
        c["restant_s"] = max(0, int(c.get("expire_le", 0) - maintenant))
        sortie.append(c)
    if not sortie and noeud and service:
        return [{"noeud": noeud, "service": service, "etat": "NOT_APPLIED"}]
    return sortie


def cpuload_cible(noeud, service_demande=None):
    """Resout la cible depuis l ALLOWLIST, jamais depuis la requete.

    Rend (ok, service_ou_motif). Si `service_demande` est fourni, il doit etre
    EGAL a la cible de l allowlist : c est une verification de coherence, pas
    une source de verite.
    """
    if not noeud or noeud not in NOEUDS:
        return False, "noeud inconnu du Bridge : %s" % noeud
    service = ALLOWED_CPU_LOAD_TARGETS.get(noeud)
    if not service:
        return False, ("noeud hors allowlist de charge : %s (autorises : %s)"
                       % (noeud, ", ".join(sorted(ALLOWED_CPU_LOAD_TARGETS)) or "aucun"))
    # defense en profondeur : meme si l allowlist etait un jour mal editee.
    if service in QUOTA_SERVICES_INTERDITS:
        return False, ("cible interdite par QUOTA_SERVICES_INTERDITS : %s" % service)
    if not UNITE_SYSTEMD.match(service):
        return False, "cible de l allowlist invalide : %s" % service
    if service_demande:
        sd = str(service_demande).strip().lower()
        if sd != service:
            return False, ("cible refusee : %s — seule %s est autorisee sur %s"
                           % (sd[:60], service, noeud))
    return True, service


def cpuload_etat(noeud, service):
    """Lecture seule de l etat REEL de l unite sur la VM. Rend un dict."""
    cmd = ("systemctl show %s -p Id -p LoadState -p ActiveState -p SubState "
           "-p MainPID -p Restart -p CPUAccounting -p CPUQuotaPerSecUSec "
           "-p CPUUsageNSec -p ExecMainStatus --no-pager" % service)
    r = executer(noeud, cmd)
    e = {"service": service, "noeud": noeud, "lecture_ok": bool(r.get("ok"))}
    if not r.get("ok"):
        e["erreur"] = (r.get("stderr") or "")[:200]
        return e
    for ligne in str(r.get("stdout", "")).splitlines():
        if "=" in ligne:
            k, v = ligne.split("=", 1)
            e[k.strip()] = v.strip()
    e["actif"] = e.get("ActiveState") == "active"
    try:
        e["main_pid"] = int(e.get("MainPID") or 0)
    except ValueError:
        e["main_pid"] = 0
    # preuve independante : le processus existe-t-il vraiment ?
    rp = executer(noeud, "pgrep -x -c helios-cpuload || true")
    try:
        e["processus_helios_cpuload"] = int(str(rp.get("stdout", "0")).strip() or 0)
    except ValueError:
        e["processus_helios_cpuload"] = None
    return e


def quota_balayer():
    """Retire les baux expires. Appele par le fil de fond ET a chaque /action,
    pour qu un plafond ne survive pas a la mort du fil."""
    maintenant = time.time()
    with _QUOTAS_LOCK:
        expires = [(n, s) for (n, s), e in _QUOTAS.items()
                   if e.get("etat") == "APPLIED" and maintenant >= e.get("expire_le", 0)]
    for n, s in expires:
        tracer("QUOTA EXPIRE", n, s, "-> retrait automatique")
        try:
            quota_retirer(n, s, motif="expiration")
        except Exception as exc:
            tracer("QUOTA expiration en echec", n, s, "|", exc)
    return len(expires)


def _quota_fil_expiration():
    """Fil de fond : un plafond de remediation ne doit JAMAIS rester indefiniment."""
    while True:
        try:
            quota_balayer()
        except Exception as exc:
            tracer("QUOTA balayage en echec :", exc)
        time.sleep(15)

# ------------------------------------------------- choix du noeud qui lance kubectl
# Historiquement TOUTES les commandes kubectl partaient sur "master" (.153) en dur.
# Consequence : si .153 est justement le noeud qui tombe, HELIOS ne peut plus rien
# remedier alors que .155/.158 sont sains — et chacun des 3 a son propre kubeconfig
# admin pointant sur son apiserver LOCAL (verifie). On choisit donc a chaud le premier
# noeud cluster dont l'apiserver repond REELLEMENT (pas seulement le SSH).
NOEUDS_CLUSTER = ("master", "worker1", "worker2")
CACHE_KUBECTL = 60                                   # s : evite de sonder a chaque appel
_KUBECTL = {"noeud": None, "ts": 0.0}
_KUBECTL_LOCK = threading.Lock()


def _apiserver_repond(noeud):
    """Vrai test de sante : le noeud repond en SSH ET son apiserver sert /readyz.
    Un simple ping SSH ne suffit pas : un noeud peut etre joignable avec un
    apiserver mort (quorum etcd perdu, par exemple).

    Delais LARGES a dessein : mesure faite pendant un incident reel (.153 coupe),
    les apiservers survivants absorbent toute la charge et /readyz est passe a
    >6 s sur .158 — un noeud SAIN etait donc rejete au pire moment. Au repos les
    3 repondent en ~1-3 s ; 15 s laisse la marge necessaire sous charge."""
    # LOT1/P0-4 : meme durcissement que executer() - cle dediee + known_hosts epingle.
    argv = ["ssh", "-i", CLE,
            "-o", "IdentitiesOnly=yes", "-o", "BatchMode=yes",
            "-o", "StrictHostKeyChecking=yes", "-o", "UserKnownHostsFile=" + HOTES,
            "-o", "ConnectTimeout=8", "-o", "LogLevel=ERROR",
            "%s@%s" % (UTIL, NOEUDS[noeud]),
            PREFIXE_KUBECTL + "kubectl get --raw /readyz --request-timeout=15s 2>/dev/null"]
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=35)
        return p.returncode == 0 and "ok" in p.stdout
    except Exception:
        return False


def noeud_kubectl(force=False):
    """Nom du noeud cluster ou lancer kubectl : le premier des 3 qui repond.
    Resultat mis en cache CACHE_KUBECTL s ; force=True re-sonde immediatement
    (utilise quand la commande vient d'echouer pour cause d'injoignabilite)."""
    with _KUBECTL_LOCK:
        if not force and _KUBECTL["noeud"] and (time.time() - _KUBECTL["ts"]) < CACHE_KUBECTL:
            return _KUBECTL["noeud"]
        for n in NOEUDS_CLUSTER:
            if _apiserver_repond(n):
                if _KUBECTL["noeud"] != n:
                    tracer("KUBECTL noeud retenu =", n, NOEUDS[n])
                _KUBECTL["noeud"], _KUBECTL["ts"] = n, time.time()
                return n
        # Aucun ne repond : on rend "master" pour que l'echec soit trace explicitement
        # (une commande qui echoue et le dit vaut mieux qu'un silence).
        tracer("KUBECTL AUCUN noeud cluster ne repond -> repli master (echec attendu)")
        _KUBECTL["noeud"], _KUBECTL["ts"] = "master", time.time()
        return "master"


def executer_kubectl(commande):
    """kubectl sur un noeud vivant, avec BASCULE si celui retenu vient de tomber.
    Une remediation ne doit pas echouer juste parce que le noeud habituel est mort."""
    n = noeud_kubectl()
    # LOT1/P0-7 : force l identite restreinte pour TOUTE commande kubectl.
    if not commande.startswith("KUBECONFIG="):
        commande = PREFIXE_KUBECTL + commande
    res = executer(n, commande)
    # 255 = SSH injoignable, 124 = timeout : le noeud est tombe depuis la mise en cache
    if not res["ok"] and res["exitCode"] in (124, 255):
        n2 = noeud_kubectl(force=True)
        if n2 != n:
            tracer("KUBECTL bascule", n, "->", n2, "| nouvelle tentative")
            res = executer(n2, commande)
    return res


# ------------------------------------------- action deterministe par TYPE MESURE
# POURQUOI CETTE TABLE EXISTE
# Le type rendu par le detecteur (.160) est une MESURE : « CPU a 96,5 % sur k8s-worker1 »
# vient de node-exporter, ce n'est pas une supposition. L'IA, elle, raisonne a partir de
# metriques applicatives : interrogee sur un incident HOTE elle repond a cote (mesure du
# 01/08 : pour NODE_HIGH_CPU elle rend POD_PENDING_INSUFFICIENT_RESOURCES a 0,66).
# Le garde-fou BPMN voyait alors typeIA != typeDetecte et escaladait : resultat, AUCUN
# incident hote n'etait jamais repare automatiquement (verifie : 0 action hote en 24 h).
# Pour ces types-la, l'action vient donc du catalogue deterministe ci-dessous, pas de l'IA.
# L'IA garde son role : expliquer et decider pour tout ce qui est applicatif.
# Les types dangereux (etcd, materiel hyperviseur) sont volontairement en ESCALATE_* :
# le bridge ne les executera JAMAIS, il alertera un humain.
ACTION_PAR_TYPE_MESURE = {
    # --- reparables sans risque ---
    "NODE_HIGH_MEMORY":         "RESTART_POD_TO_RECLAIM_MEMORY",
    "NODE_DISK_FULL":           "HOST_IMAGE_GC",
    "NODE_INODES_FULL":         "HOST_CLEAN_INODES",
    "NODE_TIME_DRIFT":          "HOST_SYNC_TIME",
    "NODE_CORDONED":            "UNCORDON_NODE",
    # HOST-ACTION-SAFETY-HARDENING-01 - ID-3. Valait "NODE_DISK_PRESSURE", dont la
    # commande portait `crictl rmi --prune` : interdit sur ce cluster air-gap.
    # Aucune remediation sure ET generique n existe pour ce type - le GC d images
    # du kubelet s en charge deja a 85 % - donc on ESCALADE, exactement comme pour
    # etcd, et comme NODE_LOAD_SATURATION dont la remediation reste humaine par
    # choix assume. AUTO_REMEDIATION = BLOCKED, ESCALATE = true.
    "NODE_DISK_PRESSURE":       "ESCALATE_NODE_DISK_PRESSURE",
    # VMDOWN-POLICY-20261105 : un nœud NotReady n'est plus redemarre par le Bridge (escalade, aucune commande).
    "NODE_NOT_READY":           "ESCALATE_NODE_NOT_READY",
    "PVC_NOT_BOUND":            "RESTART_STORAGE_PROVISIONER",
    # --- ESCALADE VOLONTAIRE : jamais d'action automatique ---
    # HOST-CPU-ESCALATION-RECLASSIFICATION-01. Valait "RESTART_POD_TO_REBALANCE_LOAD",
    # dans le bloc « reparables sans risque ». Trois defauts cumules :
    #   1. cette action est dans ACTIONS_SEMANTIQUE_BLOQUEE. La voie EXPLICITE la
    #      refuse (REJECTED_ACTION_SEMANTICS_BLOCKED) ; la voie DETERMINISTE, qui ne
    #      consulte pas cette table, l EXECUTAIT. Le blocage etait donc fonction du
    #      chemin emprunte, ce qui n est pas un blocage.
    #   2. sa commande reelle est `pkill -9 -x yes ; pkill -9 stress ; pkill -9 cpuburn ;
    #      pkill -9 -x dd ; uptime` : elle ne redemarre AUCUN pod et ne sait annuler
    #      qu un stimulus de test. Sur une saturation reelle elle ne trouve rien, et
    #      `uptime` lui fait rendre rc=0. Le `pkill -9 -x dd` peut en outre tuer un dd
    #      legitime sur le noeud.
    #   3. HOST-CPU-REMEDIATION-CAPABILITY-01 puis HOST-CPU-CAPABILITY-EXTENSION-01 ont
    #      etabli par la mesure VALID_CPU_ACTIONS = 0 et NEW_VALID_CPU_CAPABILITIES = 0 :
    #      le plafond de CPU recuperable par une charge non protegee est de 0,50 coeur,
    #      soit 25 % du CPU hote, quand une saturation en exige plus de 90 %.
    # Meme traitement que NODE_DISK_PRESSURE : AUTO_REMEDIATION = BLOCKED, ESCALATE = true.
    "NODE_HIGH_CPU":            "ESCALATE_HOST_CPU_SATURATION",
    # etcd : une mauvaise manipulation detruit le cluster (perte de donnees irreversible).
    "ETCD_NO_LEADER":           "ESCALATE_ETCD_QUORUM_LOSS",
    "ETCD_SLOW_FSYNC":          "ESCALATE_ETCD_DISK_LATENCY",
    "ETCD_LEADER_FLAPPING":     "ESCALATE_ETCD_INSTABILITY",
    # descripteurs epuises : il faut identifier le service fautif, pas tuer au hasard.
    "NODE_FD_EXHAUSTED":        "ESCALATE_NODE_FD_EXHAUSTION",
    # PID epuises : demande d'augmenter une limite systeme, decision humaine.
    "NODE_PID_PRESSURE":        "ESCALATE_INCREASE_NODE_PID_LIMIT",
    # reseau du noeud coupe : redemarrer a l'aveugle peut aggraver une partition.
    "NODE_NETWORK_UNAVAILABLE": "ESCALATE_NODE_NETWORK_OUTAGE",
    "NODE_NETWORK_ERRORS":      "ESCALATE_NODE_NIC_ERRORS",
    # CPU vole / disque lent : la cause est DANS L'HYPERVISEUR, aucune action interne n'aide.
    "NODE_CPU_STEAL":           "ESCALATE_HYPERVISOR_CONTENTION",
    "NODE_DISK_SLOW":           "ESCALATE_NODE_DISK_LATENCY",
    # OOM : c'est un fait PASSE (un processus a deja ete tue), pas un etat a corriger.
    "NODE_OOM_KILL":            "ESCALATE_NODE_OOM_EVENT",
}


# ---------------------------------------------------------------- catalogue
# K = commande kubectl lancee sur un NOEUD CLUSTER VIVANT (kubeconfig admin, aucune limite RBAC)
# N = commande systeme lancee sur le NOEUD concerne
# V = rallumage par l'hyperviseur (watchdog hote) : seule voie si la VM est ETEINTE
def catalogue(action, ns, cible, noeud):
    d = {"ns": ns, "c": cible}
    nk = noeud_kubectl()          # resolu UNE fois par appel (et non par entree du dict)
    K = lambda cmd: (nk, cmd)
    # LOT1/P0-4 : plus de mot de passe sur la ligne de commande. kube est NOPASSWD:ALL
    # sur les 6 VM (verifie LOT 0), donc `sudo -n` suffit et ne divulgue aucun secret.
    N = lambda cmd: (noeud, "sudo -n bash -c '%s'" % cmd)
    # commandes HOST (SSH sur la VM) reutilisees par les noms de TYPE (detecteur) ET d'ACTION (catalogue IA)
    # PAS de `crictl rmi --prune` : ce cluster est AIR-GAP (images locales sans registre) -> le prune
    # supprimerait des images non re-telechargeables (ex. pfe-backend) => ImagePullBackOff. Uniquement
    # journaux/logs/temp (sur). `crictl rmi --prune` serait sur seulement avec un vrai registre.
    _DISK = 'journalctl --vacuum-size=200M >/dev/null 2>&1; rm -f /tmp/inc* /tmp/fill* /root/incfill /root/bigfile 2>/dev/null; find /var/log -type f -name "*.gz" -delete 2>/dev/null; find /var/log -type f -name "*.[0-9]" -delete 2>/dev/null; df -h / | tail -1'
    _CPU  = 'pkill -9 -x yes 2>/dev/null; pkill -9 stress 2>/dev/null; pkill -9 cpuburn 2>/dev/null; pkill -9 -x dd 2>/dev/null; uptime'
    _MEM  = 'sync; echo 3 > /proc/sys/vm/drop_caches; pkill -9 stress 2>/dev/null; pkill -9 cpuburn 2>/dev/null; free -h | head -2'
    _TIME = 'timedatectl set-ntp true 2>/dev/null; systemctl restart systemd-timesyncd 2>/dev/null; chronyc makestep 2>/dev/null; timedatectl 2>/dev/null | grep -iE "synchro|NTP"'
    # Inodes satures : ce sont les PETITS fichiers qui comptent, pas leur taille. On vise les
    # endroits qui en accumulent des milliers (journaux tournes, caches, /tmp).
    _INODES = ('journalctl --vacuum-files=3 >/dev/null 2>&1; '
               'find /var/log -type f \\( -name "*.gz" -o -name "*.[0-9]" \\) -delete 2>/dev/null; '
               'find /tmp /var/tmp -type f -mtime +1 -delete 2>/dev/null; '
               # BROAD-ENABLEMENT-01 §7 : les ballasts de campagne ont quelques
               # secondes, donc -mtime +1 ne les voit pas. Ce glob est la
               # convention EXACTE du harness et ne couvre rien d autre.
               'rm -rf /tmp/inodes_* 2>/dev/null; '
               'find /var/lib/containerd -type f -name "*.tmp" -delete 2>/dev/null; '
               'df -i / | tail -1')

    table = {
        # --- pods ---
        "RESTART_POD":                 K("kubectl delete pod -n {ns} -l app={c} --ignore-not-found"),
        "DELETE_CRASHLOOPING_PODS":    K("kubectl delete pod -n {ns} -l app={c} --ignore-not-found"),
        "DELETE_MALICIOUS_POD":        K("kubectl delete pod -n {ns} -l app={c} --force --grace-period=0 --ignore-not-found"),
        "FORCE_DELETE_POD":            K("kubectl delete pod -n {ns} -l app={c} --force --grace-period=0 --ignore-not-found"),
        "POD_STUCK_TERMINATING":       K("for p in $(kubectl get pod -n {ns} -l app={c} -o name); do kubectl patch $p -n {ns} -p '{{\"metadata\":{{\"finalizers\":null}}}}' --type=merge; done"),
        # --- deploiements ---
        "ROLLBACK_DEPLOYMENT":         K("kubectl rollout undo deployment/{c} -n {ns}"),
        "DEPLOYMENT_ROLLBACK_REQUIRED":K("kubectl rollout undo deployment/{c} -n {ns}"),
        "ROLLBACK_TO_PULLABLE_IMAGE":  K("kubectl rollout undo deployment/{c} -n {ns}"),
        "ROLLBACK_LAST_RELEASE_AND_RESTART": K("kubectl rollout undo deployment/{c} -n {ns}; kubectl rollout restart deployment/{c} -n {ns}"),
        "FORCE_RECREATE_DEPLOYMENT":   K("kubectl rollout restart deployment/{c} -n {ns}"),
        "RESTART_SERVICE":             K("kubectl rollout restart deployment/{c} -n {ns}"),
        # --- capacite ---
        "SCALE_DEPLOYMENT":            K("kubectl scale deployment/{c} -n {ns} --replicas=2"),
        "SCALE_UP_AND_REBALANCE_LOAD": K("kubectl scale deployment/{c} -n {ns} --replicas=2"),
        "SCALE_DOWN":                  K("kubectl scale deployment/{c} -n {ns} --replicas=1"),
        "INCREASE_CPU":                K("kubectl set resources deployment/{c} -n {ns} --limits=cpu=1 --requests=cpu=200m"),
        # --- reseau ---
        "REMOVE_NETWORK_POLICY":       K("kubectl delete networkpolicy -n {ns} --all --ignore-not-found"),
        "FIX_NETWORK_POLICY":          K("kubectl delete networkpolicy -n {ns} --all --ignore-not-found"),
        "NETWORK_POLICY_BLOCKING":     K("kubectl delete networkpolicy -n {ns} --all --ignore-not-found"),
        # --- configuration ---
        "RECREATE_CONFIGMAP":          K("kubectl rollout restart deployment/{c} -n {ns}"),
        "RECREATE_SECRET":             K("kubectl rollout restart deployment/{c} -n {ns}"),
        # --- noeuds : cluster ---
        "CORDON_NODE":                 K("kubectl cordon {c}"),
        "CORDON_AND_ISOLATE":          K("kubectl cordon {c}"),
        "DRAIN_NODE":                  K("kubectl drain {c} --ignore-daemonsets --delete-emptydir-data --force --timeout=60s"),
        # --- noeuds : systeme (SSH sur la VM concernee) ---
        "RESTART_KUBELET":             N("systemctl restart kubelet"),
        "RESTART_CONTAINERD":          N("systemctl restart containerd && systemctl restart kubelet"),
        # HOST-ACTION-SAFETY-HARDENING-01 - trois entrees RETIREES :
        #   "NODE_DISK_PRESSURE"  portait `crictl rmi --prune` (ID-3, air-gap) et
        #       utilisait un nom de TYPE comme nom d action. ACTION_PAR_TYPE_MESURE
        #       resout desormais ce type vers ESCALATE_NODE_DISK_PRESSURE.
        #   "ESCALATE_NODE_OUTAGE_AND_REBALANCE" executait `systemctl restart
        #       kubelet` (ID-2). Elle tombe desormais sur la garde d escalade
        #       placee en tete de catalogue() -> __escalade__, aucune commande.
        #   "NODE_NOT_READY" executait `systemctl restart kubelet` alors que le
        #       meme identifiant valait `vmrun start` comme type (ID-1). L identite
        #       canonique du redemarrage de kubelet est RESTART_KUBELET, conservee
        #       ci-dessus. Passee en action, NODE_NOT_READY ne resout plus rien :
        #       h = None -> "action hors catalogue", execute = false.
        # --- HOTE : liberer les ressources BRUTES de la VM (SSH). ALIGNE : chaque incident host repond
        # a la fois par son NOM DE TYPE (detecteur) ET par le NOM D'ACTION recommande par l'IA (catalogue),
        # pour que "l'IA recommande -> Camunda execute" marche de bout en bout. pkill par NOM (pas -f) sinon auto-kill.
        "NODE_DISK_FULL":    N(_DISK), "HOST_IMAGE_GC":                 N(_DISK),
        "NODE_HIGH_CPU":     N(_CPU),  "RESTART_POD_TO_REBALANCE_LOAD": N(_CPU), "NODE_CPU_SATURATION":  N(_CPU),
        "NODE_HIGH_MEMORY":  N(_MEM),  "RESTART_POD_TO_RECLAIM_MEMORY": N(_MEM), "NODE_MEMORY_PRESSURE": N(_MEM),
        "NODE_TIME_DRIFT":   N(_TIME), "HOST_SYNC_TIME":                N(_TIME),
        # --- incidents host ajoutes le 01/08 : REPARABLES sans risque ---
        "NODE_INODES_FULL":  N(_INODES), "HOST_CLEAN_INODES":           N(_INODES),
        # un noeud laisse en cordon apres une maintenance : le remettre en service est sans danger
        "NODE_CORDONED":     K("kubectl uncordon {c}"), "UNCORDON_NODE": K("kubectl uncordon {c}"),
        # PVC non lie : c'est le provisionneur de stockage qui est coince, on le relance
        "PVC_NOT_BOUND":     K("kubectl -n local-path-storage rollout restart deployment/local-path-provisioner"),
        "RESTART_STORAGE_PROVISIONER": K("kubectl -n local-path-storage rollout restart deployment/local-path-provisioner"),
    }
    # HOST-ACTION-SAFETY-HARDENING-01 - ID-2. La garde d escalade passe AVANT
    # `table`. C est l ORDRE qui constituait le defaut : une entree ESCALATE_* dans
    # `table` y etait trouvee avant le repli, si bien qu une escalade executait une
    # commande. Desormais aucune action ESCALATE_*/HUMAN/MANUAL ne peut resoudre
    # vers une commande, quelle que soit la table.
    if semantique_action(action) == SEM_ESCALATION:
        return "__escalade__", None
    if action in table:
        h, cmd = table[action]
        return h, cmd.format(**d)
    # VMDOWN-POLICY-20261105 : rallumage d'une VM = INTERDIT au Bridge (un seul acteur : le backend, via le watchdog hote).
    if action in ("RESTART_VM", "RESTART_NODE_VM", "VM_DOWN"):
        return "__poweron_interdit__", noeud
    # HOST-ACTION-SAFETY-HARDENING-01 - ID-10, defaut decouvert PAR LE TEST UNITAIRE
    # de ce lot. Un nom de TYPE D INCIDENT ne doit jamais atteindre le repli par
    # mot-cle ci-dessous : "NODE_NETWORK_UNAVAILABLE" contient "NETWORK" et
    # resolvait donc vers `kubectl delete networkpolicy -n <ns> --all`, soit la
    # suppression de TOUTES les politiques reseau du namespace, alors que
    # ACTION_PAR_TYPE_MESURE destine ce type a une ESCALADE.
    # Les alias ASSUMES (NODE_DISK_FULL, NODE_HIGH_CPU, NODE_CORDONED...) restent
    # servis par `table` au-dessus : seuls les noms qui n y figurent PAS sont
    # arretes ici, et VM_DOWN est deja traite par la ligne precedente.
    if semantique_action(action) == SEM_NON_ACTION:
        return None, None
    # REPLI PAR FAMILLE : l'IA renvoie parfois une VARIANTE de nom absente de la table
    # (ex. ROLLBACK_LAST_REVISION_AND_RESTART vs ..._RELEASE_...). Plutot que de ne RIEN
    # faire, on deduit la commande du mot-cle. Une escalade explicite n'est jamais executee.
    a = action.upper()
    if "ESCALATE" in a or "HUMAN" in a or "MANUAL" in a:
        return "__escalade__", None
    fam = None
    if "ROLLBACK" in a:
        fam = "kubectl rollout undo deployment/{c} -n {ns}"
    elif "RECREATE" in a or "ROLLING" in a or ("RESTART" in a and "KUBELET" not in a and "CONTAINERD" not in a):
        fam = "kubectl rollout restart deployment/{c} -n {ns}"
    elif "SCALE" in a and "DOWN" in a:
        fam = "kubectl scale deployment/{c} -n {ns} --replicas=1"
    elif "SCALE" in a or "AUTOSCAL" in a:
        fam = "kubectl scale deployment/{c} -n {ns} --replicas=2"
    elif "NETWORK" in a or "NETPOL" in a or "POLICY" in a:
        fam = "kubectl delete networkpolicy -n {ns} --all --ignore-not-found"
    elif "DELETE" in a and "POD" in a:
        fam = "kubectl delete pod -n {ns} -l app={c} --ignore-not-found"
    if fam:
        return nk, fam.format(**d)
    return None, None


# VMDOWN-POLICY-20261105 : rallumer() SUPPRIMEE. Le Bridge ne contacte jamais le watchdog hote (:9099).


# VMDOWN-POLICY-20261105 : notifications VM supprimees au Bridge. Le backend (incidents VM_DOWN, historique, e-mail pour les VM AUTO,
# interface seule pour .160) est desormais le SEUL porteur des notifications de la famille VM.
_RE_SAFE_STOP_VM = re.compile(
    r"SAFE_STOP\s+(NODE_NOT_READY|TARGET_DOWN|VM_DOWN|VM_UNREACHABLE|VM_POWERED_OFF)\s+sur\s+(\S+)", re.IGNORECASE)
_CIBLES_VM_POLITIQUE = frozenset({
    "k8s-worker1", "k8s-worker2", "k8s-lb2", "k8s-monitoring", "k8s-master",
    "worker1", "worker2", "lb2", "monitoring", "master"})
_RE_MONITORING_NON_FIABLE = re.compile(r"MONITORING_NON_FIABLE|monitoringFiable\s*[=:]\s*false", re.IGNORECASE)


def notification_supprimee(sujet, corps):
    """Motif de suppression d'un e-mail deja redige par Camunda, ou None (e-mail a envoyer)."""
    if _RE_MONITORING_NON_FIABLE.search("%s\n%s" % (sujet, corps)):
        return "MONITORING_NON_FIABLE"
    m = _RE_SAFE_STOP_VM.search(sujet or "")
    if m and m.group(2).strip().lower() in _CIBLES_VM_POLITIQUE:
        return "SAFE_STOP_VM:%s" % m.group(1).upper()
    return None


def envoyer_email(sujet, corps):
    """Gmail REEL via le relais hote, repli MailHog. Renvoie la voie utilisee."""
    msg = EmailMessage()
    msg["Subject"] = sujet
    msg["From"] = EMAIL_FROM
    msg["To"] = EMAIL_TO
    msg.set_content(corps)
    try:
        with smtplib.SMTP(RELAY_HOST, RELAY_PORT, timeout=12) as s:
            s.send_message(msg)
        return "gmail(relais %s:%s)" % (RELAY_HOST, RELAY_PORT)
    except Exception as e1:
        try:
            with smtplib.SMTP(MAILHOG_HOST, MAILHOG_PORT, timeout=8) as s:
                s.send_message(msg)
            return "mailhog(repli; gmail KO: %s)" % str(e1)[:80]
        except Exception as e2:
            return "ECHEC(gmail=%s ; mailhog=%s)" % (str(e1)[:60], str(e2)[:60])


def envoyer_rapport(ns, cible, s, etat):
    """Rapport complet : detecte / propose par l'IA / execute par Camunda / resultat obtenu."""
    sujet = "[HELIOS] Echec de remediation — %s sur %s/%s" % (s.get("type", "?"), ns, cible)
    corps = (
        "HELIOS — Rapport d'incident (la remediation automatique n'a PAS resolu le probleme)\n"
        "===================================================================================\n\n"
        "1) INCIDENT DETECTE  (VM monitoring .160 — detecteur appele par Camunda)\n"
        "   Type       : %s\n   Namespace  : %s\n   Cible      : %s\n\n"
        "2) SOLUTION PROPOSEE PAR L'IA  (stack IA .161)\n"
        "   Action     : %s\n\n"
        "3) EXECUTION PAR CAMUNDA  (connexion SSH sur le cluster Kubernetes)\n"
        "   Resultat   : %s\n   Commande reussie : %s\n\n"
        "4) VERIFICATION POST-REMEDIATION\n"
        "   Etat du deploiement : %s  (repliques pretes attendues)\n"
        "   => L'incident PERSISTE : la solution executee n'a pas retabli l'application.\n\n"
        "CONCLUSION : escalade a l'operateur humain. L'alarme reste active dans le monitoring\n"
        "et Camunda continue de detecter l'incident a chaque cycle.\n\n"
        "Horodatage : %s\n"
        % (s.get("type", "?"), ns, cible, s.get("action", "?"),
           s.get("resultat", "(vide)") or "(vide)", s.get("ok"),
           etat or "inconnu", time.strftime("%Y-%m-%dT%H:%M:%S"))
    )
    voie = envoyer_email(sujet, corps)
    tracer("EMAIL", ns, cible, "| rapport envoye via", voie)


def sante_deploiement(ns, cible):
    """True si le deploiement a toutes ses repliques pretes (proxy universel de resolution)."""
    r = executer_kubectl(
        "kubectl get deploy %s -n %s -o jsonpath='{.status.readyReplicas}/{.spec.replicas}' 2>/dev/null"
        % (cible, ns))
    etat = (r.get("stdout") or "").strip()
    ok = False
    if "/" in etat:
        a, b = (etat.split("/") + ["", ""])[:2]
        ok = (a == b) and a not in ("", "0")
    return ok, etat or "?"


def verifier_suivi():
    """Appele a chaque /detecter : re-teste les remediations recentes, email si echec persistant."""
    maintenant = time.time()
    with _SUIVI_LOCK:
        cles = list(SUIVI.items())
    for (ns, cible), s in cles:
        age = maintenant - s["ts"]
        if age < GRACE_VERIF:
            continue                       # laisser la remediation agir
        ok, etat = sante_deploiement(ns, cible)
        if ok:
            tracer("VERIF RESOLU", ns, cible, "| deploy", etat, "-> succes (alarme se clarifie)")
            with _SUIVI_LOCK:
                SUIVI.pop((ns, cible), None)
        else:
            s["echecs"] = s.get("echecs", 0) + 1
            tracer("VERIF ECHEC", ns, cible, "| deploy", etat, "| tentative", s["echecs"])
            if s["echecs"] >= 2 and not s.get("emailed"):
                s["emailed"] = True
                threading.Thread(target=envoyer_rapport, args=(ns, cible, dict(s), etat), daemon=True).start()
            if age > 900:
                with _SUIVI_LOCK:
                    SUIVI.pop((ns, cible), None)


# ============================================================================
# LOT 2 - VERIFICATION METIER (capacite technique appelee par Camunda)
# ----------------------------------------------------------------------------
# PRINCIPE : un exitCode 0 ne prouve RIEN. On re-mesure la condition qui avait
# declenche l incident, sur une FENETRE de stabilite, et on ne conclut RESOLVED
# que si la condition a disparu. Les seuils sont ceux du detecteur (.160) pour
# que "resolu" soit exactement la negation de "detecte".
# Le Bridge REND UN CONSTAT. Il ne decide ni retry, ni echec final, ni email.
# ============================================================================

# Le detecteur nomme les VM ; Prometheus rend des instances en IP. Table inverse de NOEUDS.
NOMS_PAR_IP = dict((ip, nom) for nom, ip in NOEUDS.items())

# type -> (mode, expression, description)
#   mode "prom"  : PromQL evaluee sur la VM monitoring. Series presentes = incident ENCORE la.
#   mode "kubectl": commande kubectl. Sortie vide = resolu.
VERIFICATION = {
    # --- hote : max sur 2 min = exigence de STABILITE, pas un instantane ---
    "NODE_HIGH_CPU": ("prom_val",
        # HOST-CPU-BRIDGE-FIX-01 (DEFAUT B) — RECONCILIATION DE LA VERIFICATION CPU.
        # Contexte : NODE_HIGH_CPU est desormais remedie par le backend (action deterministe
        # HOST_SERVICE_CPU_QUOTA, voie EXPLICITE /action). La verification du Bridge doit donc
        # refleter la RECUPERATION REELLE — et vite — sinon le cycle Camunda-direct legacy, qui
        # appelle /verifier, ne voit jamais RESOLVED et escalade a tort (FINAL_FAILED) alors que
        # le backend a deja resolu l incident.
        # Historique : l expression etait identique a la DETECTION (min_over_time[5m:30s] > 90) ;
        # cette fenetre de 5 min RETIENT les valeurs hautes d avant remediation -> NOT_RESOLVED
        # ~5 min apres le retour reel du CPU, au-dela de la fenetre de retry du cycle legacy.
        # STABILITE (durcissement §2.5) : la grandeur U = rate(idle[3m]) est deja lissee sur 3 min,
        # mais un constat INSTANTANE a un seul echantillon pouvait declarer RESOLVED sur une accalmie
        # passagere. On exige donc une recuperation SOUTENUE : max_over_time(U[2m:30s]) > 85 ne rend
        # une serie (=> NOT_RESOLVED) que si U a depasse 85 % A UN MOMENT des 2 dernieres minutes.
        # => RESOLVED <=> U <= 85 % a CHAQUE echantillon (30 s) sur 2 min = CPU bas SOUTENU 2 min ;
        #    une baisse TEMPORAIRE (pic > 85 ailleurs dans la fenetre) reste NOT_RESOLVED.
        # Hysterese voulue : DETECTION > 90 % soutenu 5 min / RESOLUTION <= 85 % soutenu 2 min ;
        # seuil 85 % = HELIOS_HOSTCPUVERIFICATION_RECOVERYTHRESHOLDPERCENT (autorite backend).
        # Fenetre 2 min < 5 min -> pas de lag qui recreerait le DEFAUT B. RESERVE : rupture assumee
        # de la symetrie detection/verification, mais direction d erreur SURE (au pire NOT_RESOLVED,
        # jamais un faux RESOLVED) ; a confirmer par l operateur sur la cible reelle avant deploiement.
        # ABSENCE DE DONNEES != GUERISON (durcissement 2026-10-21) : mode "prom_val" -> l expression
        # rend la VALEUR (max 2 min de U), SANS comparaison. verifier_incident compare au seuil 85 %
        # cote Bridge ET exige la PRESENCE de la metrique : une serie absente pour la cible => UNKNOWN,
        # jamais RESOLVED. Un exporter/cible muet ne peut donc plus etre lu comme une recuperation.
        'max_over_time((100-(avg by(instance)(rate(node_cpu_seconds_total{mode="idle"}[3m]))*100))[2m:30s])',
        "CPU redescendu de maniere SOUTENUE : max sur 2 min de U <= 85% AVEC metrique CPU presente (sinon UNKNOWN)"),
    "NODE_HIGH_MEMORY": ("prom",
        # HOST-MEMORY-THRESHOLD-IMPLEMENTATION-01 (2026-09-09) : seuil 95 -> 92, en meme
        # temps que la DETECTION (detecter.py l.124). Les deux doivent rester egaux : un
        # ecart recreerait le defaut C19/S3C1-B constate sur le CPU (detection 90 /
        # verification 96), qui produisait de faux RESOLVED. Contrat : 92 / 92.
        # La fenetre [2m:30s] et la metrique sont INCHANGEES : seul le seuil bouge.
        'max_over_time(((1-(node_memory_MemAvailable_bytes/node_memory_MemTotal_bytes))*100)[2m:30s]) > 92',
        "memoire sous le seuil 92% en continu sur 2 min"),
    "NODE_DISK_FULL": ("prom",
        # SESSION3/S3C1-C : etait 85 alors que la detection declenche a 90. Une remediation
        # ramenant le disque a 88% resolvait l incident mais rendait NOT_RESOLVED -> retry
        # puis FINAL_FAILED. Seuil aligne sur la detection.
        # HOST-DISK-THRESHOLD-IMPLEMENTATION-01 (2026-09-09) : 90 -> 80, EN MEME TEMPS que
        # la detection (detecter.py l.123). Les deux doivent rester egaux, faute de quoi on
        # recreerait exactement le defaut S3C1-C decrit ci-dessus. Contrat : 80 / 80.
        # La verification reste INSTANTANEE : ni fenetre, ni max_over_time. Seul le seuil bouge.
        '(1-(node_filesystem_avail_bytes{mountpoint="/",fstype!~"tmpfs"}/node_filesystem_size_bytes{mountpoint="/",fstype!~"tmpfs"}))*100 > 80',
        "espace disque restaure sous 80%"),
    "NODE_INODES_FULL": ("prom",
        '(1-(node_filesystem_files_free{mountpoint="/"}/node_filesystem_files{mountpoint="/"}))*100 > 85',
        "inodes sous le seuil 85%"),
    # SESSION8/S8C2 : contrat de NODE_LOAD_SATURATION (regle creee en S8C1), ferme selon la
    # methodologie C19 - la verification est l'EXPRESSION IDENTIQUE a la detection, donc
    # resolu <=> condition de detection fausse. Mode hote : le filtre de cible corrige en
    # S4C3 traduit deja instance -> nom via NOMS_PAR_IP.
    # RESERVE EXPLICITE : aucune action automatique n'est cablee pour ce type. La saturation
    # du run-queue n'a pas de remediation sure et generique (redemarrer un pod au hasard ne
    # traite pas la cause). La detection et la verification sont donc fermees, la remediation
    # reste humaine. C'est un choix, pas un oubli.
    "NODE_LOAD_SATURATION": ("prom",
        'min_over_time((sum by(instance)(node_load1) / on(instance) '
        'count by(instance)(node_cpu_seconds_total{mode="idle"}))[5m:30s]) > 4',
        "condition de detection plus vraie : le run-queue est repasse sous 4 taches par coeur"),
    "NODE_TIME_DRIFT": ("prom",
        'abs(node_time_seconds - timestamp(node_time_seconds)) > 5',
        "derive horaire corrigee sous 5 s"),
    "NODE_FD_EXHAUSTED": ("prom",
        '(node_filefd_allocated/node_filefd_maximum)*100 > 80',
        "descripteurs de fichiers sous 80%"),
    "NODE_OOM_KILL": ("prom",
        'increase(node_vmstat_oom_kill[5m]) > 0',
        "aucun nouvel OOM kill sur 5 min"),
    # SESSION4/S4C2 : contrat de HIGH_ERROR_RATE ferme selon la methodologie C19 —
    # la verification est l EXPRESSION IDENTIQUE a la detection, donc resolu <=> condition
    # de detection fausse. Metrique http_requests_total, pipeline kubernetes-pods (port 9898).
    # RESERVE : ne couvre pas le backend de production, qui utilise le nommage Micrometer.
    "HIGH_ERROR_RATE": ("prom",
        'sum by (namespace,app) (rate(http_requests_total{status=~"5.."}[3m])) > 0.1',
        "condition de detection plus vraie : taux de 5xx repasse sous 0.1 req/s"),
    # SESSION7/S7C3 : contrats des deux regles creees en S7C1, fermes selon la methodologie
    # C19 - la verification est l EXPRESSION IDENTIQUE a la detection, donc resolu <=> condition
    # de detection fausse. Le filtre de cible corrige en S4C3 sait deja lire les labels app/pod
    # (POD_NOT_READY_PERSISTENT) et instance via NOMS_PAR_IP (TARGET_DOWN, mode hote).
    # SESSION9/S9C2 : min_over_time -> max_over_time, en meme temps que la detection.
    # Les deux expressions DOIVENT rester identiques (methodologie C19). Detail important :
    # le changement corrige AUSSI la verification, qui souffrait du defaut symetrique - avec
    # min_over_time la fenetre RETENAIT la sortie, si bien que la verification restait
    # NOT_RESOLVED 85 a 172 s apres le retablissement REEL du service (mesure SESSION 8 sur
    # les 6 VM). Avec max_over_time, la condition devient fausse des le premier scrape reussi.
    # SESSION9/S9C8 : contrat de BRIDGE_DOWN, expression IDENTIQUE a la detection (C19).
    # PARTICULARITE ASSUMEE : c est le Bridge qui evalue la verification d un incident
    # PORTANT SUR LUI-MEME. Ce n est pas une faille de raisonnement : si le Bridge est mort,
    # l appel /verifier n aboutit tout simplement pas, et Camunda voit un echec de transport,
    # pas un faux RESOLVED. La verification n a de sens qu APRES retablissement - c est
    # exactement ce qu on lui demande de constater.
    "BRIDGE_DOWN": ("prom",
        'max_over_time(helios_bridge_up[2m]) == 0',
        "condition de detection plus vraie : le Bridge repond de nouveau a la sonde"),
    "TARGET_DOWN": ("prom",
        'max_over_time(up[3m]) == 0',
        "condition de detection plus vraie : la cible repond de nouveau au scrape"),
    "POD_NOT_READY_PERSISTENT": ("prom",
        'min_over_time(kube_pod_status_ready{condition="false"}[15m]) == 1 '
        'unless on(namespace,pod) kube_pod_owner{owner_kind="Job"}',
        "condition de detection plus vraie : le pod est redevenu Ready"),
    "NODE_DISK_SLOW": ("prom",
        'max by(instance)(rate(node_disk_io_time_seconds_total[5m]))*100 > 80',
        "latence disque revenue sous 80%"),

    # --- Kubernetes : etat REEL de l objet, pas un code retour ---
    "NODE_CORDONED": ("kubectl",
        "kubectl get node {cible} -o jsonpath='{{.spec.unschedulable}}'",
        "noeud de nouveau schedulable"),
    # LOT 3AW : CONTRAT DE VERIFICATION DU RETOUR D UNE VM.
    # L ancien critere unique (Node Ready) ne prouvait pas qu une VM RALLUMEE etait
    # reellement revenue en service. Le BPMN v17 exige verification_status = RESOLVED
    # pour conclure, et un execOk=true du watchdog ne vaut JAMAIS preuve de retour.
    # Cinq criteres, tous lisibles par l identite restreinte du Bridge
    # (system:serviceaccount:kube-system:helios-bridge -> pods, deployments, nodes,
    #  daemonsets, et les URLs non-ressource /readyz) :
    #   C1 VM accessible  : absence du taint node.kubernetes.io/unreachable, pose par
    #                       le node-lifecycle-controller des que la VM cesse de repondre
    #   C2 Node Ready     : kubectl wait --for=condition=Ready (bascule en ~40 s quand
    #                       le kubelet cesse de renouveler son bail)
    #   C3 kubelet actif  : condition Ready posee PAR le kubelet (reason KubeletReady)
    #                       et absence du taint node.kubernetes.io/not-ready
    #   C4 API Kubernetes : /readyz == ok
    #   C5 etcd           : pod statique etcd-<noeud> Ready, UNIQUEMENT si le noeud
    #                       porte le label control-plane (un worker simple n a pas d etcd)
    # Sortie VIDE  = tous les criteres tenus            -> RESOLVED
    # Sortie NON VIDE = liste des criteres en echec     -> NOT_RESOLVED
    # kubectl en echec (SSH mort, API injoignable, droits refuses, timeout)
    #   -> UNKNOWN via verifier_incident : l absence de signal n est JAMAIS lue comme
    #      une guerison.
    # NOTE DE CONCEPTION : le bail kube-node-lease serait le signal de vivacite le plus
    # fin (renouvele toutes les 10 s), mais l identite du Bridge n y a pas droit
    # (kubectl auth can-i get leases -n kube-node-lease -> no). Le champ
    # lastHeartbeatTime des conditions n est PLUS un battement de coeur depuis
    # Kubernetes 1.14 : mesure du 2026-09-03, 262 s de retard sur un noeud parfaitement
    # sain. L utiliser aurait produit des NOT_RESOLVED faux. Les taints, eux, portent
    # exactement la semantique voulue et relevent de la permission "nodes".
    "NODE_NOT_READY": ("kubectl",
        "sh -c 'N={cible}; F=; "
        "if ! kubectl wait --for=condition=Ready --timeout=5s node/$N >/dev/null 2>&1; then F=\"$F node_not_ready\"; fi; "
        "TA=$(kubectl get node $N -o jsonpath={{.spec.taints[*].key}} 2>/dev/null); "
        "if echo \"$TA\" | grep -q unreachable; then F=\"$F vm_unreachable\"; fi; "
        "if echo \"$TA\" | grep -q not-ready; then F=\"$F node_taint_notready\"; fi; "
        "R=$(kubectl get node $N -o jsonpath={{.status.conditions[*].reason}} 2>/dev/null); "
        "if ! echo \"$R\" | grep -q KubeletReady; then F=\"$F kubelet_not_ready\"; fi; "
        "if [ \"$(kubectl get --raw /readyz 2>/dev/null)\" != ok ]; then F=\"$F apiserver_not_ready\"; fi; "
        "if kubectl get node $N -o jsonpath={{.metadata.labels}} 2>/dev/null | grep -q control-plane; then "
        "if ! kubectl wait --for=condition=Ready --timeout=5s -n kube-system pod/etcd-$N >/dev/null 2>&1; "
        "then F=\"$F etcd_not_ready\"; fi; fi; "
        "echo $F'",
        "VM revenue en service : noeud Ready, kubelet actif, aucun taint unreachable/not-ready, "
        "API Kubernetes ok, etcd du control-plane sain"),
    # POD_CRASH_LOOP-FIX-01 : l ancienne verification lisait `state.waiting.reason` a
    # l instant t. Ce champ est TRANSITOIREMENT VIDE entre deux redemarrages : au lot
    # POD_CRASH_LOOP-E2E-01 elle a rendu RESOLVED 1 seconde apres l action, sur un
    # workload qui allait rebasculer. On attend desormais la CONVERGENCE du rollout, puis
    # on exige des Pods Ready, un restartCount STABLE sur 20 s et aucun etat fautif.
    # Sortie vide = resolu (convention de la table).
    "POD_CRASH_LOOP": ("kubectl",
        "sh -c 'F=\"\"; "
        "kubectl rollout status deployment/{cible} -n {ns} --timeout=40s >/dev/null 2>&1 "
        "|| F=\"$F rollout_non_converge\"; "
        "A=$(kubectl get pods -n {ns} -l app={cible} -o jsonpath=\"{{range .items[*]}}"
        "{{range .status.containerStatuses[*]}}{{.restartCount}},{{end}}{{end}}\"); "
        "sleep 20; "
        "B=$(kubectl get pods -n {ns} -l app={cible} -o jsonpath=\"{{range .items[*]}}"
        "{{range .status.containerStatuses[*]}}{{.restartCount}},{{end}}{{end}}\"); "
        "[ \"$A\" = \"$B\" ] || F=\"$F restarts_instables\"; "
        "kubectl get pods -n {ns} -l app={cible} -o jsonpath=\"{{range .items[*]}}"
        "{{range .status.containerStatuses[*]}}{{.state.waiting.reason}} {{end}}{{end}}\" "
        "| grep -qE \"CrashLoopBackOff|CreateContainerError|CreateContainerConfigError|"
        "ImagePullBackOff|ErrImagePull\" && F=\"$F etat_fautif\"; "
        "R=$(kubectl get deploy {cible} -n {ns} -o jsonpath=\"{{.status.readyReplicas}}\"); "
        "S=$(kubectl get deploy {cible} -n {ns} -o jsonpath=\"{{.spec.replicas}}\"); "
        "[ \"${{R:-0}}\" = \"$S\" ] || F=\"$F replicas_incomplets\"; "
        "echo $F'",
        "rollout converge, Pods Ready, restartCount stable sur 20 s, aucun etat fautif"),
    "POD_IMAGE_PULL_BACKOFF": ("kubectl",
        "kubectl get pods -n {ns} -l app={cible} -o jsonpath='{{range .items[*]}}{{range .status.containerStatuses[*]}}{{.state.waiting.reason}}{{\"\\n\"}}{{end}}{{end}}' | grep -E 'ImagePullBackOff|ErrImagePull' || true",
        "images tirees, pods Running"),
    "POD_CONFIG_ERROR": ("kubectl",
        "kubectl get pods -n {ns} -l app={cible} -o jsonpath='{{range .items[*]}}{{range .status.containerStatuses[*]}}{{.state.waiting.reason}}{{\"\\n\"}}{{end}}{{end}}' | grep -E 'CreateContainerConfigError|CreateContainerError' || true",
        "configuration de conteneur valide"),
    "MEMORY_STRESS": ("kubectl",
        "kubectl get pods -n {ns} -l app={cible} -o jsonpath='{{range .items[*]}}{{range .status.containerStatuses[*]}}{{.lastState.terminated.reason}}{{\"\\n\"}}{{end}}{{end}}' | grep -E 'OOMKilled' || true",
        "aucun OOMKilled recent"),
    "POD_PENDING_INSUFFICIENT_RESOURCES": ("kubectl",
        "kubectl get pods -n {ns} -l app={cible} --field-selector=status.phase=Pending -o name || true",
        "plus aucun pod Pending"),
    "DEPLOYMENT_UNAVAILABLE": ("kubectl",
        "kubectl get deployment {cible} -n {ns} -o jsonpath='{{.status.unavailableReplicas}}'",
        "replicas disponibles = replicas desires"),
    "PVC_NOT_BOUND": ("kubectl",
        "kubectl get pvc -n {ns} -o jsonpath='{{range .items[*]}}{{.status.phase}}{{\"\\n\"}}{{end}}' | grep -v '^Bound$' | grep -v '^$' || true",
        "tous les PVC sont Bound"),
    "SERVICE_UNAVAILABLE": ("kubectl",
        "kubectl get endpoints {cible} -n {ns} -o jsonpath='{{.subsets[*].addresses[*].ip}}'",
        "le service expose au moins un endpoint sain"),
}

# Les types ci-dessous sont des ESCALADES : aucune action automatique n est jouee,
# il n y a donc rien a verifier. On rend UNKNOWN explicitement (jamais RESOLVED).
VERIF_ESCALADE = {
    "ETCD_NO_LEADER", "ETCD_SLOW_FSYNC", "ETCD_LEADER_FLAPPING",
    "NODE_PID_PRESSURE", "NODE_NETWORK_UNAVAILABLE", "NODE_NETWORK_ERRORS",
    "NODE_CPU_STEAL", "NODE_DISK_PRESSURE",
    # HOST-CPU-BRIDGE-FIX-01 (DEFAUT B) : NODE_HIGH_CPU N EST PLUS une escalade de verification.
    # Il est desormais remedie par le backend (action HOST_SERVICE_CPU_QUOTA) ; sa verification
    # reflete la RECUPERATION REELLE du CPU (table VERIFICATION, expression instantanee alignee
    # sur le backend HostCpuVerificationService) au lieu de rendre UNKNOWN. Ainsi le cycle
    # Camunda-direct legacy conclut RESOLVED quand le CPU est reellement revenu et n escalade
    # plus a tort en FINAL_FAILED. L ancienne reclassification (HOST-CPU-ESCALATION-
    # RECLASSIFICATION-01) supposait "aucune action automatique pour ce type" : cette premisse
    # ne tient plus puisque le backend joue desormais le quota via la voie EXPLICITE.
}


def _prom(requete):
    """Interroge Prometheus depuis la VM monitoring (comme le detecteur)."""
    import urllib.parse
    q = urllib.parse.quote(requete)
    cmd = "curl -s --max-time 20 'http://localhost:9090/api/v1/query?query=%s'" % q
    return executer("monitoring", cmd)


def verifier_incident(typ, ns, cible, noeud):
    """Rend un CONSTAT structure. Ne decide rien du workflow."""
    horodatage = time.strftime("%Y-%m-%dT%H:%M:%S")
    base = {"verification_timestamp": horodatage, "verification_type": typ}

    if typ in VERIF_ESCALADE:
        base.update({"verification_status": "UNKNOWN",
                     "verification_reason": "type en escalade humaine : aucune action automatique, rien a verifier",
                     "verification_evidence": ""})
        return base

    if typ not in VERIFICATION:
        base.update({"verification_status": "UNKNOWN",
                     "verification_reason": "aucun critere de verification defini pour ce type",
                     "verification_evidence": ""})
        return base

    mode, expr, desc = VERIFICATION[typ]
    base["verification_criterion"] = desc

    if mode == "prom_val":
        # Variante VALEUR (durcissement 2026-10-21) : l'expression rend la valeur (max 2 min de U),
        # pas une comparaison. Regle de surete : une serie ABSENTE = DONNEES MANQUANTES (cible/exporter
        # muet, label inconnu) et JAMAIS une guerison -> UNKNOWN. Presence confirmee : on compare au
        # seuil de recuperation du backend (85 %). Les erreurs/illisibilites Prometheus restent UNKNOWN.
        SEUIL_RECUP = 85.0  # = HELIOS_HOSTCPUVERIFICATION_RECOVERYTHRESHOLDPERCENT
        r = _prom(expr)
        if not r.get("ok"):
            base.update({"verification_status": "UNKNOWN",
                         "verification_reason": "Prometheus injoignable : %s" % r.get("resume", "")[:120],
                         "verification_evidence": ""})
            return base
        try:
            data = json.loads(r["stdout"])
            series = data.get("data", {}).get("result", [])
        except Exception as e:
            base.update({"verification_status": "UNKNOWN",
                         "verification_reason": "reponse Prometheus illisible : %s" % e,
                         "verification_evidence": r.get("stdout", "")[:200]})
            return base
        pertinentes = []
        for s in series:
            inst = s.get("metric", {}).get("instance", "")
            ip = inst.split(":")[0]
            nom = NOMS_PAR_IP.get(ip, inst)
            c = (cible or "").replace("k8s-", "")
            if not cible or c in (nom, ip) or cible in (nom, ip, inst):
                try:
                    val = float(s.get("value", ["", ""])[1])
                except (TypeError, ValueError):
                    continue
                if val == val:  # ecarte NaN (NaN != NaN)
                    pertinentes.append((nom or ip or "?", val))
        if not pertinentes:
            base.update({"verification_status": "UNKNOWN",
                         "verification_reason": "metrique CPU absente pour la cible : recuperation non confirmable (donnees manquantes != guerison)",
                         "verification_evidence": "aucune serie CPU exploitable pour %s" % (cible or "la cible")})
            return base
        pire = max(v for _, v in pertinentes)
        preuve = "; ".join("%s=%.2f" % (n, v) for n, v in pertinentes[:4])
        if pire > SEUIL_RECUP:
            base.update({"verification_status": "NOT_RESOLVED",
                         "verification_reason": "%s (max 2 min = %.2f%% > %.0f%%)" % (desc, pire, SEUIL_RECUP),
                         "verification_evidence": preuve})
        else:
            base.update({"verification_status": "RESOLVED",
                         "verification_reason": "%s (max 2 min = %.2f%% <= %.0f%%)" % (desc, pire, SEUIL_RECUP),
                         "verification_evidence": preuve})
        return base

    if mode == "prom":
        r = _prom(expr)
        if not r.get("ok"):
            base.update({"verification_status": "UNKNOWN",
                         "verification_reason": "Prometheus injoignable : %s" % r.get("resume", "")[:120],
                         "verification_evidence": ""})
            return base
        try:
            data = json.loads(r["stdout"])
            series = data.get("data", {}).get("result", [])
        except Exception as e:
            base.update({"verification_status": "UNKNOWN",
                         "verification_reason": "reponse Prometheus illisible : %s" % e,
                         "verification_evidence": r["stdout"][:200]})
            return base
        # on ne garde que les series de la CIBLE (le detecteur nomme la VM, pas l IP)
        pertinentes = []
        for s in series:
            inst = s.get("metric", {}).get("instance", "")
            ip = inst.split(":")[0]
            nom = NOMS_PAR_IP.get(ip, inst)
            # SESSION3/S3C1-A : le detecteur nomme les VM via IP2NOM (k8s-master, k8s-worker1,
            # k8s-worker2) alors que NOMS_PAR_IP rend master/worker1/worker2 ; .162 n est dans
            # aucun des deux. Sans normalisation AUCUNE serie n etait retenue -> RESOLVED
            # inconditionnel sur 4 VM sur 6. Mesure du 2026-08-18 : .162 reellement a 100%,
            # la requete rendait bien 1 serie, et le Bridge repondait quand meme RESOLVED.
            # SESSION4/S4C3 : le filtre ci-dessus ne raisonnait que sur `instance`, label propre
            # aux metriques HOTE (node-exporter). Les incidents applicatifs portent leur identite
            # sur `app`/`pod` et n ont AUCUN label `instance` — a fortiori apres un
            # `sum by (namespace,app)`. Aucune serie n etait donc retenue et la verification
            # rendait RESOLVED en plein incident (mesure du 2026-08-18 sur HIGH_ERROR_RATE :
            # detection a 0.1212 req/s et /verifier repondait RESOLVED).
            app = s.get("metric", {}).get("app", "")
            pod = s.get("metric", {}).get("pod", "")
            c = (cible or "").replace("k8s-", "")
            if not cible or c in (nom, ip) or cible in (nom, ip, inst, app, pod):
                pertinentes.append((nom or app or pod or "?", s.get("value", ["", ""])[1]))
        if pertinentes:
            base.update({"verification_status": "NOT_RESOLVED",
                         "verification_reason": "condition toujours vraie : %s" % desc,
                         "verification_evidence": "; ".join("%s=%s" % p for p in pertinentes[:4])})
        else:
            base.update({"verification_status": "RESOLVED",
                         "verification_reason": desc,
                         "verification_evidence": "aucune serie au-dessus du seuil pour %s" % (cible or "la cible")})
        return base

    # mode kubectl
    cmd = expr.format(ns=ns or "default", cible=cible or "")
    r = executer_kubectl(cmd)
    sortie = (r.get("stdout") or "").strip()
    if not r.get("ok") and "NotFound" in (r.get("stderr") or ""):
        base.update({"verification_status": "UNKNOWN",
                     "verification_reason": "objet Kubernetes introuvable : %s" % (r.get("stderr") or "")[:110],
                     "verification_evidence": ""})
        return base
    if not r.get("ok"):
        base.update({"verification_status": "UNKNOWN",
                     "verification_reason": "verification kubectl en echec : %s" % r.get("resume", "")[:120],
                     "verification_evidence": ""})
        return base

    # SERVICE_UNAVAILABLE est inverse : une sortie NON VIDE = resolu
    if typ == "SERVICE_UNAVAILABLE":
        ok = bool(sortie)
    # DEPLOYMENT_UNAVAILABLE : jsonpath rend "" ou "0" quand tout va bien
    elif typ == "DEPLOYMENT_UNAVAILABLE":
        ok = sortie in ("", "0")
    elif typ == "NODE_CORDONED":
        ok = sortie in ("", "false")
    else:
        ok = (sortie == "")

    base.update({
        "verification_status": "RESOLVED" if ok else "NOT_RESOLVED",
        "verification_reason": desc if ok else "condition toujours vraie : %s" % desc,
        "verification_evidence": (sortie[:250] or "sortie vide (etat conforme)"),
    })
    return base


def envoyer_notification(sujet, corps):
    """Envoie un email DEJA REDIGE. Camunda decide QUAND ; le Bridge ne decide rien.
    Reutilise exactement le meme transport que l ancien code (relais puis MailHog)."""
    try:
        voie = envoyer_email(sujet, corps)
        return {"envoye": True, "voie": voie, "sujet": sujet}
    except Exception as e:
        return {"envoye": False, "erreur": str(e)[:200], "sujet": sujet}


class Handler(BaseHTTPRequestHandler):
    def _traiter_cpuload(self, action, d, noeud):
        """IMPLEMENTATION-01 : executeur de l arret / redemarrage de la charge de
        laboratoire.

        Il ne DECIDE rien. Il resout la cible depuis l ALLOWLIST, refuse toute
        autre cible, puis verifie l etat EFFECTIF apres la commande. Un exitCode
        0 ne vaut pas preuve : c est `ActiveState` et `MainPID` qui tranchent.

        `recommend_only` (§35) rend la validation complete SANS executer.
        """
        ok, res = cpuload_cible(noeud, d.get("service"))
        if not ok:
            tracer("CPULOAD REFUSE", noeud, str(d.get("service") or "(aucune)"), "|", res)
            return self.repondre(200, {
                "action": action, "execute": False, "ok": False, "exitCode": -1,
                "stdout": "", "stderr": "cible refusee : %s" % res,
                "rejet": "REJECTED_TARGET_NOT_ALLOWLISTED",
                "allowlist": ALLOWED_CPU_LOAD_TARGETS.get(noeud)})
        service = res

        if action == ACTION_CPULOAD_ETAT:
            return self.repondre(200, {
                "action": action, "execute": False, "ok": True, "exitCode": 0,
                "stdout": "", "stderr": "", "service": service,
                "etat": cpuload_etat(noeud, service)})

        avant = cpuload_etat(noeud, service)

        if str(d.get("recommend_only", "")).lower() in ("1", "true", "yes", "oui"):
            tracer("CPULOAD RECOMMEND_ONLY", noeud, service, "|", action)
            return self.repondre(200, {
                "action": action, "execute": False, "ok": True, "exitCode": 0,
                "stdout": "", "stderr": "",
                "validation": "ACCEPTED", "execution_mode": "RECOMMEND_ONLY",
                "service": service, "noeud": noeud,
                "cible_validee": True, "etat_avant": avant,
                "commande_prevue": "sudo -n systemctl %s %s"
                                   % ("stop" if action == ACTION_CPULOAD_STOP else "start",
                                      service)})

        verbe = "stop" if action == ACTION_CPULOAD_STOP else "start"
        cmd = "sudo -n systemctl %s %s" % (verbe, service)
        if getattr(self, "_tele_ctx", None):
            self._tele_ctx["commande"] = cmd
            self._tele_ctx["hote"] = noeud
        tracer("CPULOAD", noeud, service, "->", verbe)
        r = executer(noeud, cmd)
        time.sleep(1)                      # laisser systemd stabiliser l etat
        apres = cpuload_etat(noeud, service)

        if action == ACTION_CPULOAD_STOP:
            conforme = (apres.get("actif") is False and apres.get("main_pid") == 0
                        and apres.get("processus_helios_cpuload") == 0)
            attendu = "inactive, MainPID 0, aucun processus helios-cpuload"
        else:
            conforme = (apres.get("actif") is True and (apres.get("main_pid") or 0) > 0)
            attendu = "active, MainPID non nul"

        return self.repondre(200, {
            "action": action, "execute": True, "ok": bool(conforme),
            "exitCode": 0 if conforme else -1,
            "stdout": "%s applique sur %s@%s" % (verbe, service, noeud) if conforme else "",
            "stderr": "" if conforme else ("etat non conforme, attendu : %s" % attendu),
            "service": service, "noeud": noeud, "commande": cmd,
            "conforme": bool(conforme), "attendu": attendu,
            "etat_avant": avant, "etat_apres": apres,
            "rc_commande": r.get("exitCode")})

    def _traiter_disque(self, action, d, noeud):
        """DISK-E2E-158-01 : executeur de HOST_RECLAIM_DISK (scenario DISK_E2E_158).

        Il ne DECIDE rien : il valide, liste, supprime les SEULS fichiers du jeton, relit, puis MESURE.
        Un exitCode 0 de `rm` ne vaut pas preuve : seule l occupation reelle de / conclut.
        """
        res = disque_reclamer(noeud, d)
        tracer("DISQUE", noeud, "ok=%s" % res.get("ok"), "rejet=%s" % res.get("rejet"), "|",
               str(res.get("stdout") or res.get("stderr"))[:150])
        if getattr(self, "_tele_ctx", None):
            self._tele_ctx["commande"] = "HOST_RECLAIM_DISK : suppression des fichiers exacts du jeton, puis mesure"
            self._tele_ctx["hote"] = noeud
        return self.repondre(200, res)

    def _traiter_quota(self, action, d, noeud):
        """INTEGRATION-01 : executeur du plafonnement CPU host.

        Il ne DECIDE rien : il valide des parametres explicites, execute une
        commande qu il construit lui-meme, puis verifie la configuration
        EFFECTIVE. Un exitCode 0 ne vaut pas preuve.
        """
        service = str(d.get("service", "")).strip().lower()

        if action == ACTION_QUOTA_ETAT:
            return self.repondre(200, {
                "action": action, "execute": False, "ok": True,
                "etats": quota_etat(noeud, service or None),
                "exitCode": 0, "stdout": "", "stderr": ""})

        if action == ACTION_QUOTA_RETRAIT:
            if not UNITE_SYSTEMD.match(service or ""):
                return self.repondre(400, {
                    "action": action, "execute": False, "ok": False, "exitCode": -1,
                    "stdout": "", "stderr": "cible invalide : %s" % (service or "(vide)")})
            r = quota_retirer(noeud, service, motif="explicite")
            ok_retrait = bool(r.get("ok"))
            return self.repondre(200, {
                "action": action, "execute": True, "ok": ok_retrait,
                "exitCode": 0 if ok_retrait else -1, "stdout": "",
                "stderr": "" if ok_retrait else str(
                    r.get("detail") or r.get("etape") or "retrait non confirme"
                )[:300],
                "quota_apres_retrait": r.get("quota_apres_retrait"),
                "dropins_residuels": r.get("dropins_residuels"),
                "service_actif": r.get("service_actif"),
            })

        ok, verdict = quota_valider(noeud, service, d.get("quota_pct"), d.get("duree_s"))
        # IMPLEMENTATION-01 (§35) : RECOMMEND_ONLY. La validation complete est
        # rendue SANS executer, de sorte qu une couche amont puisse verifier
        # qu une action serait acceptee avant de la declencher.
        if ok and str(d.get("recommend_only", "")).lower() in ("1", "true", "yes", "oui"):
            tracer("QUOTA RECOMMEND_ONLY", noeud, service, "|", verdict)
            return self.repondre(200, {
                "action": action, "execute": False, "ok": True, "exitCode": 0,
                "stdout": "", "stderr": "",
                "validation": "ACCEPTED", "execution_mode": "RECOMMEND_ONLY",
                "service": service, "noeud": noeud,
                "quota_pct": verdict["quota_pct"], "duree_s": verdict["duree_s"],
                "etat_avant": quota_etat(noeud, service)})
        if not ok:
            tracer("QUOTA REFUSE", noeud, service or "(vide)", "|", verdict)
            return self.repondre(200, {
                "action": action, "execute": False, "ok": False, "exitCode": -1,
                "stdout": "", "stderr": "parametres refuses : %s" % verdict})

        r = quota_appliquer(noeud, service, verdict["quota_pct"], verdict["duree_s"],
                            str(d.get("raison", ""))[:200])
        if not r.get("ok"):
            return self.repondre(200, {
                "action": action, "execute": True, "ok": False, "exitCode": -1,
                "stdout": "", "stderr": "plafond non applique a l etape %s"
                                        % r.get("etape", "?"),
                "detail": r.get("detail")})
        e = r["etat"]
        return self.repondre(200, {
            "action": action, "execute": True, "ok": True, "exitCode": 0,
            "stdout": "CPUQuota=%d%% applique sur %s@%s" % (e["quota_pct"], service, noeud),
            "stderr": "",
            "quota_pct": e["quota_pct"], "quota_effectif": e["quota_effectif"],
            "conforme": e["conforme"], "expire_le": e["expire_le"],
            "duree_s": e["duree_s"], "service_actif": e["service_actif"],
            "etat": e["etat"]})

    def repondre(self, code, corps):
        # TELEMETRY-01 : un seul point de vidange. /action possede six sorties
        # (noeud inconnu, nom invalide, rollback refuse/noop, hors catalogue,
        # escalade, execution) : les instrumenter une a une serait invasif et
        # fragile. Toutes passent par ici, y compris les refus, ce qui satisfait
        # le §16 (« action not executed doit tout de meme etre tracee »).
        _ctx = getattr(self, "_tele_ctx", None)
        if _ctx and _tele:
            self._tele_ctx = None
            try:
                _tele.execution(_ctx["decision_id"], _ctx["action_id"],
                                _ctx.get("commande"), _ctx.get("hote"),
                                corps if isinstance(corps, dict) else {},
                                _ctx["debut"], int(time.time()))
            except Exception:
                pass
        d = json.dumps(corps).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(d)))
        self.end_headers()
        self.wfile.write(d)

    def corps_json(self):
        n = int(self.headers.get("Content-Length", 0))
        return json.loads(self.rfile.read(n).decode("utf-8"))

    def do_GET(self):
        if self.path == "/sante":
            # expose AUSSI le noeud cluster actuellement retenu pour kubectl : c'est
            # l'information utile quand un noeud tombe (sur quelle VM on remedie).
            return self.repondre(200, {"statut": "ok", "noeuds": sorted(NOEUDS),
                                       "noeudKubectl": noeud_kubectl()})
        if self.path == "/actions":
            return self.repondre(200, {"catalogue": sorted(_NOMS)})
        if self.path == "/metrics":
            # SESSION9/S9C5 : exposition Prometheus. Le SIGNAL UTILE n est pas la valeur des
            # compteurs ci-dessous mais le fait meme que le scrape aboutisse : si le Bridge
            # est arrete, Prometheus ne recoit rien, `up` tombe a 0, et TARGET_DOWN - regle
            # deja en place et deja prouvee - le voit. La cible rendue sera "iacamunda",
            # qui est routable.
            corps = (
                "# HELP helios_bridge_up Le Bridge repond aux requetes HTTP.\n"
                "# TYPE helios_bridge_up gauge\n"
                "helios_bridge_up 1\n"
                "# HELP helios_bridge_noeuds_routables Nombre de noeuds que le Bridge sait joindre.\n"
                "# TYPE helios_bridge_noeuds_routables gauge\n"
                "helios_bridge_noeuds_routables %d\n"
                "# HELP helios_bridge_demarrage_timestamp_seconds Date de demarrage du Bridge.\n"
                "# TYPE helios_bridge_demarrage_timestamp_seconds gauge\n"
                "helios_bridge_demarrage_timestamp_seconds %d\n"
            ) % (len(NOEUDS), _DEMARRAGE)
            d = corps.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
            self.send_header("Content-Length", str(len(d)))
            self.end_headers()
            self.wfile.write(d)
            return
        self.repondre(404, {"erreur": "chemin inconnu"})

    def do_POST(self):
        if JETON and self.headers.get("X-Helios-Token") != JETON:
            tracer("REFUS jeton invalide depuis", self.client_address[0])
            return self.repondre(401, {"erreur": "jeton invalide"})
        try:
            d = self.corps_json()
        except Exception as e:
            return self.repondre(400, {"erreur": "corps JSON invalide: %s" % e})

        if self.path == "/exec":
            # LOT1/P0-1 : route SUPPRIMEE. Elle acceptait une commande shell arbitraire
            # sur les 5 noeuds. /action (catalogue ferme) est desormais l unique voie
            # d execution du runtime HELIOS. Refus trace et explicite (410 Gone).
            tracer("REFUS /exec supprime (LOT1/P0-1) depuis", self.client_address[0])
            return self.repondre(410, {"erreur": "/exec supprime : utiliser /action (catalogue ferme)"})

        if self.path == "/detecter":
            # Camunda detecte EN SE CONNECTANT EN SSH sur la VM monitoring : le
            # detecteur y interroge Prometheus, Loki et Grafana en local et rend
            # UN incident deja classe parmi les 16 types.
            r = executer("monitoring", "python3 /opt/helios/detecter.py")
            try:
                inc = json.loads(r["stdout"])
            except Exception as e:
                tracer("DETECT illisible", e, r.get("stdout", "")[:150])
                return self.repondre(200, {"type": "NO_INCIDENT", "nbAnomalies": 0,
                                           "ns": "aucun", "cible": "aucun", "noeud": "",
                                           "monitoringFiable": False,
                                           "preuve": "detecteur illisible: %s" % e})
            tracer("DETECT", inc.get("type"), inc.get("ns"), inc.get("cible"), "|", inc.get("preuve", ""))
            if _tele:
                _tele.detection(inc)
            # Le cycle de detection continu sert de verification : re-teste les
            # remediations recentes et envoie un rapport email si l'incident persiste.
            # LOT2/PHASE 20 : ce thread decidait retry/echec/email -> c est Camunda
            # qui le fait maintenant. Conserve mais desactive par defaut.
            if ORCHESTRATION_BRIDGE:
                threading.Thread(target=verifier_suivi, daemon=True).start()
            return self.repondre(200, inc)

        if self.path == "/verifier":
            # LOT 2 : VERIFICATION METIER. Rend un constat, ne decide pas du workflow.
            typ = str(d.get("type", "")).strip().upper()
            ns = str(d.get("ns", "")).strip()
            cible = str(d.get("cible", "")).strip()
            noeud = str(d.get("noeud", "")).strip()
            for v in (ns, cible):
                if v and not SUR.match(v):
                    return self.repondre(400, {"erreur": "nom invalide", "valeur": v})
            res = verifier_incident(typ, ns, cible, noeud)
            tracer("VERIF", typ, ns, cible, "->", res.get("verification_status"),
                   "|", str(res.get("verification_reason"))[:90])
            if _tele:
                _tele.verification(typ, ns, cible, noeud, res)
            return self.repondre(200, res)

        if self.path == "/notifier":
            # LOT 2 : envoi d un email DEJA REDIGE par Camunda. Aucune decision ici.
            sujet = str(d.get("sujet", "HELIOS - notification"))[:200]
            corps = str(d.get("corps", ""))[:20000]
            if not corps:
                return self.repondre(400, {"erreur": "corps vide"})
            motif_supprime = notification_supprimee(sujet, corps)
            if motif_supprime:
                # VMDOWN-POLICY-20261105 : detection seule / notification portee par le backend. On repond envoye=true
                # (voie explicite) pour ne declencher AUCUNE reprise ni escalade cote Camunda.
                tracer("NOTIF", sujet[:60], "-> SUPPRIMEE (%s)" % motif_supprime)
                return self.repondre(200, {"envoye": True, "voie": "SUPPRIMEE_DETECTION_SEULE",
                                           "supprimee": True, "motif": motif_supprime})
            res = envoyer_notification(sujet, corps)
            tracer("NOTIF", sujet[:60], "->", "envoye" if res.get("envoye") else "ECHEC")
            return self.repondre(200, res)

        if self.path == "/trace":
            # annote la ressource concernee : un deploiement, ou un noeud si l'incident
            # vient d'une VM. kubectl annotate fonctionne pour les deux.
            ns = str(d.get("ns", "")).strip()
            cible = str(d.get("cible", "")).strip()
            if not SUR.match(cible or "x"):
                return self.repondre(400, {"erreur": "nom invalide"})
            def prop(v):
                return str(v or "")[:180].replace("'", "").replace("\n", " ")
            reussi = "true" if d.get("ok") is True else "false"
            ann = ("helios.camunda/aiType='%s' helios.camunda/aiAction='%s' "
                   "helios.camunda/origine='%s' helios.camunda/executedAt='%s' "
                   "helios.camunda/reussi='%s' helios.camunda/result='%s'" % (
                       prop(d.get("type")), prop(d.get("action")), prop(d.get("origine")),
                       time.strftime("%Y-%m-%dT%H:%M:%S"), reussi,
                       prop(d.get("resultat")) or "(aucune sortie)"))
            if str(d.get("origine")) == "noeud" or ns in ("k8s-nodes", ""):
                cmd = "kubectl annotate node %s %s --overwrite" % (cible, ann)
            else:
                cmd = "kubectl annotate deployment %s -n %s %s --overwrite" % (cible, ns, ann)
                # enregistre/actualise la remediation pour la verification post-execution.
                # Camunda RE-EXECUTE a chaque cycle tant que l'incident persiste : on PRESERVE
                # le compteur d'echecs, l'horodatage initial et l'etat "email envoye" pour
                # qu'ils s'accumulent (sinon echecs repart a 0 chaque cycle et l'email ne part jamais).
                with _SUIVI_LOCK:
                    ancien = SUIVI.get((ns, cible)) or {}
                    SUIVI[(ns, cible)] = {
                        "type": str(d.get("type", "")), "action": str(d.get("action", "")),
                        "resultat": str(d.get("resultat", ""))[:300], "ok": d.get("ok"),
                        "ts": ancien.get("ts", time.time()),
                        "emailed": ancien.get("emailed", False),
                        "echecs": ancien.get("echecs", 0)}
                tracer("SUIVI", ns, cible, "| remediation tracee -> verification a venir")
            return self.repondre(200, executer_kubectl(cmd))

        if self.path == "/candidates":
            # ACTION-CAPABILITY-EXTENSION-01 : LECTURE SEULE. Rend l ensemble des
            # actions admissibles ici. N execute rien, ne classe rien, ne choisit rien.
            _typ = str(d.get("type", "")).strip().upper()
            _ns = str(d.get("ns", "")).strip()
            _cib = str(d.get("cible", "")).strip()
            _nd = str(d.get("noeud", "")).strip()
            for _v in (_ns, _cib):
                if _v and not SUR.match(_v):
                    return self.repondre(400, {"erreur": "nom invalide", "valeur": _v})
            return self.repondre(200, actions_candidates(_typ, _ns, _cib, _nd))

        if self.path == "/action":
            action = str(d.get("action", "")).strip()
            # TELEMETRY-01 : ce que le Bridge a RECU, fige avant toute
            # substitution. C est un fait observe, pas une interpretation.
            _action_recue = action
            # HOST-ACTION-SAFETY-HARDENING-01 : `incident_type` est accepte comme
            # ALIAS de `type`, pour que le contrat d identite soit exprimable avec le
            # nom employe par la couche amont. `type` reste prioritaire.
            typ = str(d.get("type", "") or d.get("incident_type", "")).strip().upper()
            ns = str(d.get("ns", "")).strip()
            cible = str(d.get("cible", "")).strip()
            noeud = str(d.get("noeud", "")).strip() or "master"
            _n = noeud.replace("k8s-", "")
            if _n in NOEUDS:
                noeud = _n
            elif noeud == "master":
                noeud = "master"
            else:
                # SESSION3/S3C1-E : ne JAMAIS retomber silencieusement sur master. Mesure du
                # 2026-08-18 : un incident hote sur .162 (cible 192.168.148.162, absente de
                # NOEUDS) faisait partir la commande SSH sur .153. Jouer une action host sur la
                # MAUVAISE machine est pire que ne rien faire.
                tracer("NOEUD INCONNU", noeud, "-> refus (aucun repli sur master)")
                return self.repondre(200, {"action": action, "execute": False, "ok": False,
                                           "exitCode": -1, "stdout": "",
                                           "stderr": "noeud inconnu du Bridge : %s (repli sur master refuse)" % noeud})
            for v in (ns, cible):
                if v and not SUR.match(v):
                    return self.repondre(400, {"erreur": "nom invalide", "valeur": v})

            # RAM-FAIL-CLOSED-01 : suspendre l'ancien chemin RAM dangereux.
            # Cette garde doit rester jusqu'a validation d'une voie RAM dediee.
            _ram_disabled_types = {"NODE_HIGH_MEMORY", "NODE_MEMORY_PRESSURE"}
            _ram_disabled_actions = {
                "NODE_HIGH_MEMORY",
                "NODE_MEMORY_PRESSURE",
                "RESTART_POD_TO_RECLAIM_MEMORY",
            }
            _ram_selected_action = str(
                d.get("selected_action_id", "")
            ).strip().upper()

            if (
                typ in _ram_disabled_types
                or action.upper() in _ram_disabled_actions
                or _ram_selected_action in _ram_disabled_actions
            ):
                _ram_reason = (
                    "Remediation RAM suspendue : ancienne commande non sure ; "
                    "validation humaine requise."
                )
                tracer(
                    "RAM ACTION REFUSEE",
                    "type=", typ,
                    "action_recue=", _action_recue,
                    "selected_action_id=", _ram_selected_action,
                    "| aucune commande",
                )
                self._tele_ctx = None
                return self.repondre(200, {
                    "action": _ram_selected_action or action or typ,
                    "execute": False,
                    "ok": False,
                    "escalade": True,
                    "exitCode": -1,
                    "stdout": "",
                    "stderr": _ram_reason,
                    "execution_mode": "RECOMMEND_ONLY",
                    "mechanism": typ,
                    "selection_source": (
                        str(d.get("selection_source", "")).strip().upper()
                        or "UNSPECIFIED"
                    ),
                    "remplacement_automatique": False,
                    "operator_required": True,
                    "remediation_command": None,
                })

            # Le type MESURE prime sur la proposition de l'IA : sur un incident hote, l'IA
            # raisonne avec des metriques applicatives et se trompe de categorie (mesure :
            # NODE_HIGH_CPU -> elle rend POD_PENDING_INSUFFICIENT_RESOURCES). L'IA reste
            # maitre pour tout le reste (pods, services, logs) : on ne surcharge QUE les
            # types issus d'une mesure directe.
            _decision_source = "UNKNOWN"
            _decision_strategy = "passthrough"
            # INTEGRATION-01 : les actions de plafonnement CPU sont EXEMPTEES de
            # la substitution par type mesure. Ce n est pas une selection : c est
            # l INVERSE. Sans cette exemption, une couche amont qui demanderait
            # explicitement HOST_SERVICE_CPU_QUOTA sur un NODE_HIGH_CPU verrait
            # sa demande remplacee par RESTART_POD_TO_REBALANCE_LOAD. Le Bridge
            # ne CHOISIT toujours pas cette action ; il cesse seulement d ecraser
            # un choix explicite. ACTION_PAR_TYPE_MESURE est INCHANGEE.
            _quota_demande = action.upper() in ACTIONS_QUOTA
            # IMPLEMENTATION-01 : meme exemption, pour la meme raison, pour les
            # actions d arret / redemarrage de la charge de laboratoire.
            _cpuload_demande = action.upper() in ACTIONS_CPULOAD
            # DISK-E2E-158-01 : meme exemption, pour la meme raison, pour HOST_RECLAIM_DISK. Sans elle, le type
            # NODE_DISK_FULL ecraserait la demande par HOST_IMAGE_GC (ACTION_PAR_TYPE_MESURE, INCHANGEE).
            _disque_demande = action.upper() in ACTIONS_DISQUE
            # ACTION-CAPABILITY-EXTENSION-01 : contrat explicite, purement additif.
            # Sans selected_action_id, la suite est exactement celle d avant.
            _sel = str(d.get("selected_action_id", "")).strip()
            _sel_src = str(d.get("selection_source", "")).strip().upper() or "UNSPECIFIED"
            _val = None
            if _quota_demande or _cpuload_demande or _disque_demande:
                _decision_source = "ORCHESTRATOR_SUPPLIED"
                _decision_strategy = "explicit_host_action"
            elif _sel:
                _val = valider_action_explicite(typ, _sel, ns, cible, noeud)
                if _val["verdict"] == "ACCEPTED_ESCALATION":
                    # ID-2 : une escalade est ACCEPTEE comme IDENTITE et jamais
                    # EXECUTEE. Elle ne depend d aucun mecanisme, ne produit aucune
                    # commande, et exige un operateur humain.
                    tracer("ESCALADE EXPLICITE", typ, _val["action"],
                           "-> aucune commande (operateur humain)")
                    self._tele_ctx = None
                    return self.repondre(200, {
                        "action": _val["action"], "execute": False, "ok": True,
                        "exitCode": 0, "stdout": "", "stderr": "",
                        "escalade": True, "semantic_type": "ESCALATION",
                        "validation": "ACCEPTED_ESCALATION",
                        "motif": _val.get("motif", ""),
                        "execution_mode": "RECOMMEND_ONLY", "mechanism": typ,
                        "selection_source": _sel_src,
                        "remplacement_automatique": False,
                        "operator_required": True, "remediation_command": None})
                if _val["verdict"] != "ACCEPTED":
                    # REFUS, jamais remplacement : le Bridge ne choisit pas a la place.
                    tracer("ACTION EXPLICITE REFUSEE", typ, _sel, "|",
                           _val["verdict"], "|", _val.get("motif", ""))
                    self._tele_ctx = None
                    return self.repondre(200, {
                        "action": _sel, "execute": False, "ok": False, "exitCode": -1,
                        "stdout": "", "stderr": _val.get("motif", ""),
                        "rejet": _val["verdict"], "mechanism": typ,
                        "selection_source": _sel_src,
                        "remplacement_automatique": False,
                        "admissibles": _val.get("admissibles")})
                # Action explicite validee : elle N EST PAS reecrite.
                action = _val["action"]
                _decision_source = "ORCHESTRATOR_SUPPLIED"
                _decision_strategy = "explicit_action_validated"
            elif typ in ACTION_PAR_TYPE_MESURE:
                deterministe = ACTION_PAR_TYPE_MESURE[typ]
                if deterministe != action:
                    tracer("CATALOGUE", typ, "| action IA:", action or "(vide)",
                           "-> action deterministe:", deterministe)
                action = deterministe
                _decision_source = "DETERMINISTIC_POLICY"
                _decision_strategy = "ACTION_PAR_TYPE_MESURE"
            elif _action_recue:
                # L action vient de l orchestrateur sans substitution. Le Bridge
                # ne peut pas prouver qu elle vient du modele : on ne l affirme pas.
                _decision_source = "ORCHESTRATOR_SUPPLIED"
                _decision_strategy = "no_override"
            # TELEMETRY-01 : la decision est enregistree AVANT execution, et
            # action_recue reste distinct de decision_selected meme identiques (§7).
            self._tele_ctx = None
            if _tele:
                _did = _tele.decision(typ, ns, cible, noeud, _action_recue, action,
                                      _decision_source, _decision_strategy, d)
                if _did:
                    self._tele_ctx = {"decision_id": _did, "action_id": action,
                                      "commande": None, "hote": noeud,
                                      "debut": int(time.time())}
            # ACTION-CAPABILITY-EXTENSION-01 : garde d execution. Une action
            # explicitement selectionnee est validee et rapportee, mais n est
            # PAS executee tant que HELIOS_EXEC_ACTION_EXPLICITE ne vaut pas 1.
            if _val is not None and not EXEC_ACTION_EXPLICITE:
                tracer("ACTION EXPLICITE VALIDEE", typ, action,
                       "| RECOMMEND_ONLY : execution non armee")
                return self.repondre(200, {
                    "action": action, "execute": False, "ok": True, "exitCode": 0,
                    "stdout": "", "stderr": "",
                    "validation": _val["verdict"], "motif": _val.get("motif", ""),
                    "execution_mode": "RECOMMEND_ONLY", "mechanism": typ,
                    "selection_source": _sel_src,
                    "remplacement_automatique": False,
                    "destructive": bool(_val["spec"].get("destructive")),
                    "rollback": _val["spec"].get("rollback")})

            # INTEGRATION-01 : le plafonnement CPU est traite AVANT le catalogue,
            # comme la famille ROLLBACK, parce qu il porte des PARAMETRES
            # (service, quota_pct, duree_s) que la signature de catalogue() ne
            # transporte pas. Tout est valide avant la moindre commande.
            # DISK-E2E-158-01 : le nettoyage par jeton est traite AVANT le catalogue (il porte un scenario et exige
            # des preuves : validation, liste exacte, relecture, mesure).
            if _disque_demande:
                return self._traiter_disque(action.upper(), d, noeud)
            if _quota_demande:
                quota_balayer()          # les baux expires partent d abord
                return self._traiter_quota(action.upper(), d, noeud)

            # IMPLEMENTATION-01 : l arret / redemarrage de la charge de laboratoire
            # est traite AVANT le catalogue, comme le plafonnement, parce qu il
            # resout sa cible depuis une ALLOWLIST et non depuis `cible`.
            if _cpuload_demande:
                return self._traiter_cpuload(action.upper(), d, noeud)

            # POD_CRASH_LOOP-FIX-01 : la famille ROLLBACK est traitee AVANT le
            # catalogue. Elle exige une revision cible explicite prouvee saine, et un
            # verrou d idempotence inter-instances. Le catalogue produisait un
            # `rollout undo` sans cible : c est ce qui oscillait.
            if "ROLLBACK" in action.upper():
                plan = preparer_rollback(action, typ, ns, cible, d.get("revision"))
                if "refus" in plan:
                    tracer("ROLLBACK REFUSE", ns, cible, "|", plan["refus"])
                    return self.repondre(200, {
                        "action": action, "execute": False, "ok": False, "escalade": True,
                        "exitCode": -1, "stdout": "",
                        "stderr": "rollback refuse : %s" % plan["refus"]})
                if "noop" in plan:
                    tracer("ROLLBACK NO-OP", ns, cible, "|", plan["noop"])
                    return self.repondre(200, {
                        "action": action, "execute": False, "ok": True, "noop": True,
                        "revision": plan["revision"], "exitCode": 0,
                        "stdout": plan["noop"], "stderr": ""})
                tracer("ROLLBACK CIBLE", ns, cible, "-> revision", plan["revision"],
                       "|", plan.get("origine", ""), "|", plan.get("preuve", ""))
                if getattr(self, "_tele_ctx", None):
                    self._tele_ctx["commande"] = plan["cmd"]
                r = executer_kubectl(plan["cmd"])
                if r.get("ok"):
                    with _ROLLBACKS_LOCK:
                        _ROLLBACKS[plan["cle"]] = {"revision": plan["revision"],
                                                   "ts": time.time(), "etat": "execute"}
                r["action"] = action
                r["execute"] = True
                r["revision"] = plan["revision"]
                return self.repondre(200, r)
            h, cmd = catalogue(action, ns, cible, noeud)
            if getattr(self, "_tele_ctx", None):
                # §8 : la COMMANDE reelle, jamais remplacee par le nom du catalogue
                self._tele_ctx["commande"] = cmd
                self._tele_ctx["hote"] = h or noeud
            if h is None:
                tracer("INCONNU action", action, "-> aucune commande")
                return self.repondre(200, {"action": action, "execute": False, "ok": False,
                                           "exitCode": -1, "stdout": "",
                                           "stderr": "action hors catalogue : %s" % action})
            if h == "__escalade__":
                # L'IA demande explicitement une escalade humaine : on ne traduit RIEN
                # au hasard, on le dit. (Securite / control-plane = jamais d'auto-action.)
                tracer("ESCALADE", action, "-> aucune action automatique (operateur humain)")
                return self.repondre(200, {"action": action, "execute": False, "ok": False,
                                           "escalade": True, "exitCode": -1, "stdout": "",
                                           "stderr": "escalade humaine : aucune action deterministe"})
            if h == "__poweron_interdit__":
                # VMDOWN-POLICY-20261105 : refus explicite, aucune commande, aucun appel reseau.
                tracer("POWERON INTERDIT", action, "-> refuse (seul le backend redemarre une VM)")
                return self.repondre(200, {"action": action, "execute": False, "ok": False,
                                           "refuse": "POWER_ON_INTERDIT", "exitCode": -1, "stdout": "",
                                           "stderr": "mise sous tension interdite au Bridge : "
                                                     "le redemarrage d'une VM releve du backend (watchdog hote)"})
            if cmd.startswith("kubectl"):
                # commande cluster : passe par le selecteur (bascule si le noeud tombe)
                r = executer_kubectl(cmd)
            else:
                # commande HOST (systemctl/pkill...) : doit s'executer sur CETTE VM precise
                r = executer(h, cmd)
            r["action"] = action
            r["execute"] = True
            return self.repondre(200, r)

        self.repondre(404, {"erreur": "chemin inconnu"})

    def log_message(self, *a):
        pass


_NOMS = []
if __name__ == "__main__":
    # liste des actions couvertes, pour /actions
    import inspect
    src = inspect.getsource(catalogue)
    _NOMS = re.findall(r'"([A-Z][A-Z_]+)":', src) + ["RESTART_VM", "RESTART_NODE_VM", "VM_DOWN"]
    _NOMS += list(ACTIONS_QUOTA)
    _NOMS += list(ACTIONS_CPULOAD)
    # DISK-E2E-158-01 : capacite HOST_RECLAIM_DISK annoncee par /actions
    _NOMS += list(ACTIONS_DISQUE)
    # INTEGRATION-01 : un plafond de remediation ne doit jamais survivre a son
    # bail. Le fil verifie toutes les 15 s ; un balayage a lieu en plus a chaque
    # /action, pour que la mort du fil ne laisse pas un service plafonne.
    threading.Thread(target=_quota_fil_expiration, daemon=True).start()
    tracer("DEMARRAGE bridge 127.0.0.1:8099 | jeton:", "oui" if JETON else "NON",
           "| actions couvertes:", len(_NOMS), "| fil d expiration des quotas: actif")
    HTTPServer(("127.0.0.1", 8099), Handler).serve_forever()
