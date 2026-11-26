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
import importlib.util as _ilu
import json, os, re, shlex, smtplib, subprocess, threading, time
import helios_incident_card_client as CARD
from email.message import EmailMessage
from http.server import BaseHTTPRequestHandler, HTTPServer

# ------------------------------------------------------- CATALOGUE CANONIQUE (autorite)
# helios_action_catalog.py est la SEULE autorite sur ce qui est executable. Il est depose
# a cote de ce fichier par deploy_bridge.py.
#
# FAIL-CLOSED, volontairement brutal : si le catalogue est absent, illisible, ou si ses
# controles d'integrite echouent, le bridge NE DEMARRE PAS. Un Policy Enforcement Point
# sans politique n'a aucune raison de tourner, et tout repli silencieux serait exactement
# la classe de faille que cette version supprime (audit du 2026-08-05).
_CAT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "helios_action_catalog.py")
try:
    _spec = _ilu.spec_from_file_location("helios_action_catalog", _CAT_PATH)
    CAT = _ilu.module_from_spec(_spec)
    _spec.loader.exec_module(CAT)
except Exception as _e:                                       # pragma: no cover
    raise SystemExit(
        "HELIOS FATAL : catalogue d'actions introuvable ou illisible (%s) : %s\n"
        "Le bridge est un Policy Enforcement Point : sans catalogue, il ne demarre pas."
        % (_CAT_PATH, _e))
_PB_CAT = CAT.verifier()
if _PB_CAT:                                                   # pragma: no cover
    raise SystemExit("HELIOS FATAL : catalogue incoherent :\n  - "
                     + "\n  - ".join(_PB_CAT))

NOEUDS = {"master": "192.168.148.153", "worker1": "192.168.148.158", "worker2": "192.168.148.155",
          "monitoring": "192.168.148.160",   # .160 : detection (Prometheus/Loki/Grafana en local)
          "iacamunda": "192.168.148.161"}    # .161 : remediation host possible (RAM Ollama, etc.)
UTIL = os.environ.get("HELIOS_SSH_USER", "kube")
MDP = os.environ.get("HELIOS_SSH_PASS", "kube")
JETON = os.environ.get("HELIOS_BRIDGE_TOKEN", "")
WATCHDOG = os.environ.get("HELIOS_WATCHDOG", "http://192.168.148.1:9099")
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

SUR = re.compile(r"^[a-z0-9][a-z0-9.-]{0,62}$")   # noms K8s : pas d'injection shell
# Un ACTION_ID n'a jamais ete valide : il partait directement dans la deduction par
# mots-cles. Il est desormais contraint AVANT toute consultation du catalogue.
ACTION_SUR = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
# Metacaracteres shell. Servent a l'invariant structurel de _rendu_sans_injection().
METACARACTERES = (";", "&&", "||", "|", "`", "$(", "${", ">", "<", "\n", "\r", "\\", "&")
# Namespace conventionnel des incidents de scope NOEUD (detecter.py:152-160 : les modes
# "noeud" et "hote" rendent {"ns": "k8s-nodes", "cible": n, "noeud": n}).
NS_NOEUDS = ("k8s-nodes", "")
# /exec expose une commande ARBITRAIRE : c'est une surface d'administration, pas une
# brique du cycle self-healing (verifie : le BPMN n'appelle que /detecter, /action,
# /trace). Le verrou permet de la fermer sans toucher au reste. Defaut = comportement
# actuel preserve, pour ne pas casser la verification post-deploiement de deploy_bridge.py.
EXEC_ACTIF = os.environ.get("HELIOS_EXEC_ENABLED", "1") not in ("0", "false", "no")


def tracer(*parts):
    ligne = time.strftime("%Y-%m-%dT%H:%M:%S") + " " + " ".join(str(p) for p in parts)
    try:
        with open(JOURNAL, "a", encoding="utf-8") as f:
            f.write(ligne + "\n")
    except Exception:
        pass
    print(ligne, flush=True)


def executer(noeud, commande, stdin_data=None):
    ip = NOEUDS[noeud]
    tracer("EXEC", noeud, ip, "|", commande)

    password_read_fd = None
    password_write_fd = None

    try:
        # Descripteur distinct pour sshpass : le secret n'est pas dans argv.
        password_read_fd, password_write_fd = os.pipe()
        os.write(password_write_fd, (MDP + "\n").encode("utf-8"))
        os.close(password_write_fd)
        password_write_fd = None

        argv = ["sshpass", "-d", str(password_read_fd), "ssh",
                "-o", "StrictHostKeyChecking=no",
                "-o", "UserKnownHostsFile=/dev/null",
                "-o", "ConnectTimeout=8", "-o", "LogLevel=ERROR",
                "%s@%s" % (UTIL, ip), commande]

        run_kwargs = {
            "capture_output": True,
            "text": True,
            "timeout": DELAI,
            "pass_fds": (password_read_fd,),
        }
        if stdin_data is not None:
            run_kwargs["input"] = stdin_data

        p = subprocess.run(argv, **run_kwargs)
        res = {
            "host": noeud, "ip": ip, "command": commande,
            "exitCode": p.returncode,
            "stdout": p.stdout.strip()[:4000],
            "stderr": p.stderr.strip()[:2000],
            "ok": p.returncode == 0,
        }
    except subprocess.TimeoutExpired:
        res = {
            "host": noeud, "ip": ip, "command": commande,
            "exitCode": 124, "stdout": "",
            "stderr": "timeout apres %ds" % DELAI,
            "ok": False,
        }
    except (OSError, ValueError) as exc:
        res = {
            "host": noeud, "ip": ip, "command": commande,
            "exitCode": 126, "stdout": "",
            "stderr": "echec local de lancement SSH (%s)" % type(exc).__name__,
            "ok": False,
        }
    finally:
        for fd in (password_write_fd, password_read_fd):
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass

    # Resume HONNETE : en cas d'echec, remonter l'erreur sans exposer le secret.
    if res["ok"]:
        res["resume"] = res["stdout"][:300].replace("\\n", " ") or "commande executee"
    else:
        res["resume"] = "ECHEC(exit=%s) %s" % (
            res["exitCode"],
            (res["stderr"] or res["stdout"] or "aucune sortie")[:250].replace("\\n", " ")
        )
    tracer("RES ", noeud, "exit=%s" % res["exitCode"], "|", res["resume"][:130])
    return res


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
    argv = ["sshpass", "-p", MDP, "ssh",
            "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null",
            "-o", "ConnectTimeout=8", "-o", "LogLevel=ERROR",
            "%s@%s" % (UTIL, NOEUDS[noeud]),
            "kubectl get --raw /readyz --request-timeout=15s 2>/dev/null"]
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
    "NODE_HIGH_CPU":            "HOST_SERVICE_CPU_QUOTA",
    "NODE_CPU_SATURATION":      "HOST_SERVICE_CPU_QUOTA",
    "NODE_HIGH_MEMORY":         "RESTART_POD_TO_RECLAIM_MEMORY",
    "NODE_DISK_FULL":           "HOST_RECLAIM_DISK",
    "NODE_INODES_FULL":         "HOST_CLEAN_INODES",
    "NODE_TIME_DRIFT":          "HOST_SYNC_TIME",
    "NODE_CORDONED":            "UNCORDON_NODE",
    # P1 corrige le 2026-08-05 : l'action "NODE_DISK_PRESSURE" lancait
    # `journalctl --vacuum-size=150M; crictl rmi --prune || true`. Or `--prune` supprime
    # TOUTE image non referencee par un conteneur en cours — donc pfe-backend pendant un
    # rollout ou apres un scale-to-zero — sur un cluster AIR-GAP ou rien n'est
    # re-telechargeable => ImagePullBackOff. Le commentaire l.213-214 de ce meme fichier
    # l'interdisait deja explicitement ; le code le faisait quand meme.
    # HOST_RECLAIM_DISK libere plus d'espace par des voies sures (journaux, logs tournes,
    # /tmp) et ne touche a aucune image.
    "NODE_DISK_PRESSURE":       "HOST_RECLAIM_DISK",
    "NODE_NOT_READY":           "HOST_RESTART_KUBELET",
    "VM_POWERED_OFF":           "RESTART_NODE_VM",
    "VM_DOWN":                  "RESTART_NODE_VM",
    "PVC_NOT_BOUND":            "RESTART_STORAGE_PROVISIONER",
    # --- ESCALADE VOLONTAIRE : jamais d'action automatique ---
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


# ------------------------------------------------- RESOLUTION D'ACTION (Policy Enforcement)
# CE BLOC EST LE POINT D'APPLICATION DE LA POLITIQUE DE SECURITE.
# Il n'existe plus de table locale, plus de repli, plus de second systeme de decision :
# une commande ne peut provenir QUE d'un gabarit fige de helios_action_catalog.py.
#
# SUPPRIME le 2026-08-05 (audit P0/P1) :
#   - la table locale de 44 entrees, dont 3 lancaient `kubectl delete networkpolicy
#     -n {ns} --all` (REMOVE_NETWORK_POLICY, FIX_NETWORK_POLICY, NETWORK_POLICY_BLOCKING) ;
#   - "ESCALATE_NODE_OUTAGE_AND_REBALANCE": `systemctl restart kubelet` — un identifiant
#     prefixe ESCALATE_ qui executait une remediation ;
#   - "NODE_DISK_PRESSURE": `crictl rmi --prune` — interdit sur ce cluster air-gap par le
#     commentaire de ce fichier lui-meme ;
#   - le REPLI PAR FAMILLE (`"NETWORK" in a`, `"ROLLBACK" in a`, `"RESTART" in a`, ...) :
#     tout identifiant contenant un sous-mot devenait une commande. Mesure du 2026-08-05 :
#     `FOO_POLICY_BAR` effacait toutes les NetworkPolicies du namespace, et
#     `PLEASE_DO_NOT_RESTART` redemarrait le deploiement.


def _rendu_sans_injection(gabarit, commande):
    """INVARIANT STRUCTUREL, independant de toute expression reguliere.

    Substituer {ns}/{c}/{n} ne doit INTRODUIRE aucun metacaractere shell absent du
    gabarit. Si un parametre contenait `;`, `$(`, `&&`, un saut de ligne..., le rendu
    en porterait davantage que le gabarit d'origine.

    C'est une SECONDE barriere, de nature differente de SUR : elle ne suppose rien du
    contenu des parametres, elle compare l'AVANT et l'APRES. Elle tient donc meme si
    SUR etait un jour affaiblie, remplacee, ou contournee par un appelant.
    """
    for m in METACARACTERES:
        if commande.count(m) != gabarit.count(m):
            return False
    return True


def _scope_coherent(scope, ns, cible):
    """Une action de scope NOEUD ne doit pas recevoir une cible applicative, et
    reciproquement.

    Fonde sur une mesure, pas sur une convention supposee : detecter.py:152-160 emet
    les incidents de scope noeud avec ns="k8s-nodes" et cible = nom du noeud, et les
    incidents applicatifs avec le namespace reel et cible = nom du deploiement.
    """
    if scope == CAT.SCOPE_NODE:
        return ns in NS_NOEUDS and bool(cible)
    if scope == CAT.SCOPE_WORKLOAD:
        return ns not in NS_NOEUDS and bool(cible)
    return True          # cluster / vm / none : aucune contrainte de cible


def resoudre_action(action, ns, cible, noeud):
    """ACTION_ID -> decision. SEUL chemin du systeme vers une commande.

    decision : REJECTED | ESCALADE | WATCHDOG | AUCUNE | EXECUTE
    Aucune branche ne fabrique de commande : `cmd` provient toujours d'un gabarit fige
    du catalogue, rendu puis controle.
    """
    def _r(decision, motif, host=None, cmd=None, canon=None, spec=None):
        return {"decision": decision, "motif": motif, "host": host, "cmd": cmd,
                "action": canon or action, "spec": spec}

    if not isinstance(action, str) or not ACTION_SUR.match(action.strip().upper()):
        return _r("REJECTED", "identifiant d'action malforme")
    spec = CAT.spec_for(action)
    if spec is None:
        return _r("REJECTED", "action absente du catalogue canonique")
    canon, executor = spec["action_id"], spec["executor"]

    if executor == CAT.EXEC_ESCALATE:
        diag = spec.get("diagnostic") or ""
        if diag:
            # Rendu TEXTUEL uniquement, transmis a l'operateur dans la reponse.
            # Le bridge ne l'execute JAMAIS : une escalade informe, elle n'agit pas.
            try:
                diag = diag.format(ns=ns, c=cible, n=cible)
            except Exception:
                diag = spec.get("diagnostic") or ""
        return _r("ESCALADE", spec.get("effet", ""), canon=canon,
                  spec=dict(spec, diagnostic=diag))
    if executor == CAT.EXEC_NONE:
        return _r("AUCUNE", spec.get("effet", ""), canon=canon, spec=spec)
    if executor == CAT.EXEC_WATCHDOG:
        return _r("WATCHDOG", spec.get("effet", ""), host="__watchdog__", cmd=noeud,
                  canon=canon, spec=spec)

    # ---- executables : TOUTE la validation a lieu AVANT le rendu ----------------
    if not _scope_coherent(spec["scope"], ns, cible):
        return _r("REJECTED", "scope %s incompatible avec ns=%r cible=%r"
                  % (spec["scope"], ns, cible), canon=canon, spec=spec)
    for nom, val in (("ns", ns), ("cible", cible), ("noeud", noeud)):
        if val and not SUR.match(str(val)):
            return _r("REJECTED", "parametre %s invalide" % nom, canon=canon, spec=spec)
    gabarit = spec["template"]
    # Une valeur VIDE passait le garde `if v and not SUR.match(v)` et produisait une
    # commande tronquee. Un champ requis par le gabarit doit etre renseigne.
    for champ, val in (("{ns}", ns), ("{c}", cible), ("{n}", cible)):
        if champ in gabarit and not val:
            return _r("REJECTED", "parametre %s requis et vide" % champ,
                      canon=canon, spec=spec)

    _canon, _ex, cmd = CAT.render(canon, ns=ns, cible=cible, noeud=cible)
    if cmd is None:
        return _r("REJECTED", "le catalogue n'a produit aucune commande",
                  canon=canon, spec=spec)
    if not _rendu_sans_injection(gabarit, cmd):
        tracer("INJECTION REFUSEE |", canon, "| ns=%r cible=%r" % (ns, cible))
        return _r("REJECTED", "metacaractere shell introduit par un parametre",
                  canon=canon, spec=spec)

    if executor == CAT.EXEC_KUBECTL:
        return _r("EXECUTE", spec.get("effet", ""), host=noeud_kubectl(), cmd=cmd,
                  canon=canon, spec=spec)
    if executor == CAT.EXEC_NODE_SSH:
        return _r("EXECUTE", spec.get("effet", ""), host=noeud,
                  cmd="sudo -S -p '' bash -c " + shlex.quote(cmd),
                  canon=canon, spec=spec)
    return _r("REJECTED", "executeur inconnu : %s" % executor, canon=canon, spec=spec)


def catalogue(action, ns, cible, noeud):
    """Compatibilite historique : (host, commande).

    (None, None) = REJETE : aucune commande n'a ete produite, rien ne sera execute.
    """
    r = resoudre_action(action, ns, cible, noeud)
    if r["decision"] == "EXECUTE":
        return r["host"], r["cmd"]
    if r["decision"] == "ESCALADE":
        return "__escalade__", None
    if r["decision"] == "WATCHDOG":
        return "__watchdog__", r["cmd"]
    return None, None


def rallumer(noeud):
    """VM eteinte : SSH impossible, on passe par le watchdog hote (vmrun)."""
    import urllib.request
    url = "%s/restart/%s" % (WATCHDOG, "k8s-" + noeud if not noeud.startswith("k8s-") else noeud)
    tracer("VMRUN", noeud, "->", url)
    try:
        req = urllib.request.Request(url, data=b"{}", headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=60) as r:
            corps = r.read().decode()[:500]
        tracer("VMRUN", noeud, "->", corps)
        return {"host": noeud, "command": "vmrun start (watchdog)", "exitCode": 0,
                "stdout": corps, "stderr": "", "ok": True}
    except Exception as e:
        tracer("VMRUN", noeud, "KO", e)
        return {"host": noeud, "command": "vmrun start (watchdog)", "exitCode": 1,
                "stdout": "", "stderr": str(e), "ok": False}


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



# LOT Incident Card : activ? uniquement par configuration explicite.
_CARD_INTEGRATION_ENABLED = (
    os.environ.get("HELIOS_CARD_INTEGRATION_ENABLED", "0").strip().lower()
    in ("1", "true", "yes", "on")
)

_CARD_SCENARIOS_BY_TYPE = {
    "NODE_HIGH_CPU": "CPU",
    "NODE_CPU_SATURATION": "CPU",
    "NODE_DISK_FULL": "DISK",
    "NODE_DISK_PRESSURE": "DISK",
    "VM_DOWN": "VM_DOWN",
    "VM_POWERED_OFF": "VM_DOWN",
}


def _card_target(cible, noeud):
    raw = str(cible or "").strip()
    alias = raw[4:] if raw.startswith("k8s-") else raw
    if alias not in NOEUDS:
        alias = str(noeud or "").replace("k8s-", "").strip()
    if alias not in NOEUDS:
        raise ValueError("cible de carte hors liste de n?uds")
    return "k8s-" + alias


def _card_capture_outputs(scenario, target):
    alias = target[4:]
    if alias not in NOEUDS:
        raise ValueError("alias de n?ud non autoris?")

    if scenario == "CPU":
        commands = [
            "date -Is",
            "uptime",
            "cat /proc/loadavg",
            "systemctl is-active helios-cpuload.service",
            "cat /sys/fs/cgroup/system.slice/helios-cpuload.service/cpu.max",
        ]
        runner = lambda cmd: executer(alias, cmd)
    elif scenario == "DISK":
        commands = ["date -Is", "df -B1 /", "df -i /"]
        runner = lambda cmd: executer(alias, cmd)
    elif scenario == "VM_DOWN":
        commands = ["kubectl get node " + target + " -o wide"]
        runner = executer_kubectl
    else:
        raise ValueError("sc?nario non autoris?")

    outputs = []
    for command in commands:
        result = runner(command)
        if not isinstance(result, dict):
            result = {}
        try:
            exit_code = int(result.get("exitCode", 1))
        except (TypeError, ValueError):
            exit_code = 1
        outputs.append({
            "command": command,
            "exitCode": exit_code,
            "stdout": str(result.get("stdout") or ""),
            "stderr": str(result.get("stderr") or ""),
            "capturedAt": time.strftime("%Y-%m-%dT%H:%M:%S"),
        })
    return outputs


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


class Handler(BaseHTTPRequestHandler):
    def repondre(self, code, corps):
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
            # SURFACE D'ADMINISTRATION, PAS UNE BRIQUE DU CYCLE SELF-HEALING.
            # Verifie le 2026-08-05 : le BPMN n'appelle que /detecter, /action et /trace.
            # Les seuls appelants de /exec sont deploy_bridge.py (verification
            # post-deploiement sur les 3 noeuds) et test_action.py.
            # Cet endpoint execute une commande ARBITRAIRE : il ne passe donc PAS par le
            # catalogue, par conception. HELIOS_EXEC_ENABLED=0 le ferme completement, ce
            # qui rend le bridge integralement gouverne par le catalogue.
            # Defaut = 1 : ne pas casser la verification post-deploiement existante.
            if not EXEC_ACTIF:
                tracer("REFUS /exec desactive (HELIOS_EXEC_ENABLED=0) depuis",
                       self.client_address[0])
                return self.repondre(403, {
                    "erreur": "/exec desactive",
                    "detail": "execution arbitraire fermee ; utiliser /action (catalogue)"})
            noeud, commande = str(d.get("host", "")).strip(), str(d.get("command", "")).strip()
            if noeud not in NOEUDS:
                return self.repondre(400, {"erreur": "noeud inconnu", "attendus": sorted(NOEUDS)})
            if not commande:
                return self.repondre(400, {"erreur": "commande vide"})
            tracer("EXEC-ARBITRAIRE (hors catalogue) depuis", self.client_address[0])
            return self.repondre(200, executer(noeud, commande))

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
            # Le cycle de detection continu sert de verification : re-teste les
            # remediations recentes et envoie un rapport email si l'incident persiste.
            threading.Thread(target=verifier_suivi, daemon=True).start()
            return self.repondre(200, inc)

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

        if self.path == "/action":
            action = str(d.get("action", "")).strip()
            typ = str(d.get("type", "")).strip().upper()   # type MESURE par le detecteur
            ns = str(d.get("ns", "")).strip()
            cible = str(d.get("cible", "")).strip()
            noeud = str(d.get("noeud", "")).strip() or "master"
            noeud = noeud.replace("k8s-", "") if noeud.replace("k8s-", "") in NOEUDS else "master"
            for v in (ns, cible):
                if v and not SUR.match(v):
                    return self.repondre(400, {"erreur": "nom invalide", "valeur": v})
            # Le type MESURE prime sur la proposition de l'IA : sur un incident hote, l'IA
            # raisonne avec des metriques applicatives et se trompe de categorie (mesure :
            # NODE_HIGH_CPU -> elle rend POD_PENDING_INSUFFICIENT_RESOURCES). L'IA reste
            # maitre pour tout le reste (pods, services, logs) : on ne surcharge QUE les
            # types issus d'une mesure directe.
            if typ in ACTION_PAR_TYPE_MESURE:
                deterministe = ACTION_PAR_TYPE_MESURE[typ]
                if deterministe != action:
                    tracer("CATALOGUE", typ, "| action IA:", action or "(vide)",
                           "-> action deterministe:", deterministe)
                action = deterministe
            # SEUL point de decision. Aucune commande ne peut naitre ailleurs.
            dec = resoudre_action(action, ns, cible, noeud)
            etat, canon = dec["decision"], dec["action"]

            # La carte et les preuves initiales doivent exister avant l'action.
            card_id = None
            card_scenario = _CARD_SCENARIOS_BY_TYPE.get(typ)
            card_target = None
            if (_CARD_INTEGRATION_ENABLED and card_scenario
                    and etat in ("EXECUTE", "WATCHDOG")):
                try:
                    card_target = _card_target(cible, noeud)
                    registered = CARD.ingest_host_incident(
                        incident_type=typ,
                        target=card_target,
                        namespace="k8s-nodes",
                        severity=d.get("severity")
                            if isinstance(d.get("severity"), str) else None,
                        detail=d.get("detail")
                            if isinstance(d.get("detail"), str)
                            else "Incident mesur? re?u par le Bridge",
                        episode_key=d.get("episodeKey")
                            if isinstance(d.get("episodeKey"), str) else None,
                    )
                    card_id = int(registered.get("incidentId") or 0)
                    if card_id <= 0:
                        raise RuntimeError("incidentId manquant")

                    CARD.record_execution_event(
                        card_id, "SELECTED", action=canon,
                        detail="Action s?lectionn?e par le catalogue d?terministe",
                    )
                    before = _card_capture_outputs(card_scenario, card_target)
                    CARD.collect_evidence(
                        card_id, vm=card_target, phase="BEFORE",
                        scenario=card_scenario, outputs=before, source="BRIDGE",
                    )
                except Exception as exc:
                    tracer("INCIDENT_CARD_PRE_ACTION_KO", typ, type(exc).__name__)
                    return self.repondre(503, {
                        "action": canon, "execute": False, "ok": False,
                        "exitCode": -1, "incidentId": card_id,
                        "cardTracking": "FAILED",
                        "stderr": "Carte ou preuves initiales indisponibles ; action non ex?cut?e.",
                    })

            if etat == "REJECTED":
                # Refus EXPLICITE et trace. Aucune commande n'a ete produite : ni SSH,
                # ni kubectl, ni modification d'infrastructure.
                tracer("REJECTED", action, "|", dec["motif"])
                return self.repondre(200, {
                    "action": action, "execute": False, "ok": False, "rejected": True,
                    "exitCode": -1, "stdout": "",
                    "motif": dec["motif"],
                    "stderr": "REJECTED : %s -> %s. Aucune commande produite."
                              % (action or "(vide)", dec["motif"])})

            if etat == "ESCALADE":
                # Une escalade INFORME, elle n'agit pas. `diagnostic` est une commande
                # lecture seule transmise a l'operateur : le bridge ne l'execute PAS.
                spec = dec.get("spec") or {}
                tracer("ESCALADE", canon, "-> operateur humain | diag:",
                       spec.get("diagnostic") or "(aucun)")
                return self.repondre(200, {
                    "action": canon, "execute": False, "ok": False, "escalade": True,
                    "exitCode": -1, "stdout": "",
                    "motif": dec["motif"],
                    "diagnostic": spec.get("diagnostic") or "",
                    "diagnosticExecute": False,
                    "stderr": "escalade humaine : aucune action automatique"})

            if etat == "AUCUNE":
                tracer("AUCUNE ACTION", canon, "|", dec["motif"])
                return self.repondre(200, {
                    "action": canon, "execute": False, "ok": True,
                    "exitCode": 0, "stdout": "", "stderr": "",
                    "motif": dec["motif"]})

            if card_id is not None:
                try:
                    CARD.record_execution_event(
                        card_id, "REQUESTED", action=canon,
                        detail="Action demand?e au m?canisme d'ex?cution",
                    )
                    # Pour VM_DOWN, le watchdog ne garantit pas que le d?marrage a commenc?.
                    if etat != "WATCHDOG":
                        CARD.record_execution_event(
                            card_id, "STARTED", action=canon,
                            detail="Ex?cution commenc?e c?t? Bridge",
                        )
                except Exception as exc:
                    tracer("INCIDENT_CARD_START_KO", card_id, type(exc).__name__)
                    return self.repondre(503, {
                        "action": canon, "execute": False, "ok": False,
                        "exitCode": -1, "incidentId": card_id,
                        "cardTracking": "FAILED",
                        "stderr": "?v?nement pr?alable non enregistr? ; action non ex?cut?e.",
                    })

            if etat == "WATCHDOG":
                r = rallumer(dec["cmd"])
            elif dec["host"] == noeud and not dec["cmd"].startswith("kubectl"):
                # commande HOST : doit s'executer sur CETTE VM precise
                r = executer(dec["host"], dec["cmd"], stdin_data=(MDP + "\n") if dec.get("spec", {}).get("executor") == CAT.EXEC_NODE_SSH else None)
            else:
                # commande cluster : passe par le selecteur (bascule si le noeud tombe)
                r = executer_kubectl(dec["cmd"])
            if card_id is not None:
                try:
                    try:
                        exit_code = int(r.get("exitCode", 1))
                    except (TypeError, ValueError):
                        exit_code = 1
                    step_ok = r.get("ok") is True and exit_code == 0

                    if card_scenario == "VM_DOWN":
                        if step_ok:
                            # Accept? ne signifie pas VM revenue ni n?ud Ready.
                            CARD.record_execution_event(
                                card_id, "ACCEPTED", action=canon,
                                detail=(
                                    "Watchdog a accept? la demande ; "
                                    "v?rification du d?marrage encore n?cessaire."
                                ),
                            )
                        else:
                            CARD.record_execution_event(
                                card_id, "FAILED", action=canon,
                                detail="La demande au watchdog a ?chou?.",
                                success=False,
                            )
                        # Ne pas capturer pr?matur?ment la preuve AFTER :
                        # la VM peut encore ?tre en cours de d?marrage.
                        r["verificationStatus"] = "PENDING"
                        r["cardTracking"] = "RECORDED"
                    else:
                        if step_ok:
                            CARD.record_execution_event(
                                card_id, "COMPLETED", action=canon,
                                detail="Commande termin?e ; r?cup?ration fonctionnelle ? v?rifier.",
                                success=True,
                            )
                        else:
                            CARD.record_execution_event(
                                card_id, "FAILED", action=canon,
                                detail="Commande ?chou?e ; consulter les sorties enregistr?es.",
                                success=False,
                            )

                        after = _card_capture_outputs(card_scenario, card_target)
                        CARD.collect_evidence(
                            card_id, vm=card_target, phase="AFTER",
                            scenario=card_scenario, outputs=after, source="BRIDGE",
                        )
                        r["verificationStatus"] = "PENDING"
                        r["cardTracking"] = "RECORDED"
                    r["incidentId"] = card_id
                except Exception as exc:
                    # L'action a d?j? ?t? tent?e : aucune relance automatique.
                    tracer("INCIDENT_CARD_POST_ACTION_KO", card_id, type(exc).__name__)
                    r["incidentId"] = card_id
                    r["cardTracking"] = "INCOMPLETE"
                    r["verificationStatus"] = "PENDING"

            r["action"] = canon
            r["actionDemandee"] = action
            r["execute"] = True
            r["risque"] = (dec.get("spec") or {}).get("risk", "")
            return self.repondre(200, r)

        self.repondre(404, {"erreur": "chemin inconnu"})

    def log_message(self, *a):
        pass


# Vocabulaire expose par GET /actions. Vient du catalogue, seule autorite — et il est
# desormais peuple a l'IMPORT et non plus sous `if __name__ == "__main__"`, ou il restait
# vide quand le module etait importe (GET /actions rendait []).
_NOMS = CAT.catalogue_ids()

if __name__ == "__main__":
    tracer("DEMARRAGE bridge 127.0.0.1:8099 | jeton:", "oui" if JETON else "NON",
           "| catalogue:", len(CAT.ACTIONS), "actions canoniques,",
           len(CAT.ALIASES), "alias |",
           "/exec:", "ACTIF (execution arbitraire)" if EXEC_ACTIF else "ferme")
    if not JETON:
        tracer("AVERTISSEMENT : HELIOS_BRIDGE_TOKEN vide -> AUCUNE authentification")
    if EXEC_ACTIF:
        tracer("AVERTISSEMENT : /exec accepte une commande arbitraire "
               "(HELIOS_EXEC_ENABLED=0 pour le fermer)")
    HTTPServer(("127.0.0.1", 8099), Handler).serve_forever()
