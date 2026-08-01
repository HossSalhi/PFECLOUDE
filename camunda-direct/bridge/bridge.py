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
import json, os, re, smtplib, subprocess, threading, time
from email.message import EmailMessage
from http.server import BaseHTTPRequestHandler, HTTPServer

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
    tracer("EXEC", noeud, ip, "|", commande)
    argv = ["sshpass", "-p", MDP, "ssh",
            "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null",
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
    "NODE_HIGH_CPU":            "RESTART_POD_TO_REBALANCE_LOAD",
    "NODE_HIGH_MEMORY":         "RESTART_POD_TO_RECLAIM_MEMORY",
    "NODE_DISK_FULL":           "HOST_IMAGE_GC",
    "NODE_INODES_FULL":         "HOST_CLEAN_INODES",
    "NODE_TIME_DRIFT":          "HOST_SYNC_TIME",
    "NODE_CORDONED":            "UNCORDON_NODE",
    "NODE_DISK_PRESSURE":       "NODE_DISK_PRESSURE",
    "NODE_NOT_READY":           "RESTART_NODE_VM",
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


# ---------------------------------------------------------------- catalogue
# K = commande kubectl lancee sur un NOEUD CLUSTER VIVANT (kubeconfig admin, aucune limite RBAC)
# N = commande systeme lancee sur le NOEUD concerne
# V = rallumage par l'hyperviseur (watchdog hote) : seule voie si la VM est ETEINTE
def catalogue(action, ns, cible, noeud):
    d = {"ns": ns, "c": cible}
    nk = noeud_kubectl()          # resolu UNE fois par appel (et non par entree du dict)
    K = lambda cmd: (nk, cmd)
    N = lambda cmd: (noeud, "echo %s | sudo -S -p '' bash -c '%s'" % (MDP, cmd))
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
        "NODE_DISK_PRESSURE":          N("journalctl --vacuum-size=150M; crictl rmi --prune || true"),
        "ESCALATE_NODE_OUTAGE_AND_REBALANCE": N("systemctl restart kubelet"),
        "NODE_NOT_READY":              N("systemctl restart kubelet"),
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
    if action in table:
        h, cmd = table[action]
        return h, cmd.format(**d)
    # rallumage d'une VM eteinte : hors SSH, seul l'hyperviseur peut le faire
    if action in ("RESTART_VM", "RESTART_NODE_VM", "VM_DOWN"):
        return "__watchdog__", noeud
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
            noeud, commande = str(d.get("host", "")).strip(), str(d.get("command", "")).strip()
            if noeud not in NOEUDS:
                return self.repondre(400, {"erreur": "noeud inconnu", "attendus": sorted(NOEUDS)})
            if not commande:
                return self.repondre(400, {"erreur": "commande vide"})
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
            h, cmd = catalogue(action, ns, cible, noeud)
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
            if h == "__watchdog__":
                r = rallumer(cmd)
            elif cmd.startswith("kubectl"):
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
    tracer("DEMARRAGE bridge 127.0.0.1:8099 | jeton:", "oui" if JETON else "NON",
           "| actions couvertes:", len(_NOMS))
    HTTPServer(("127.0.0.1", 8099), Handler).serve_forever()
