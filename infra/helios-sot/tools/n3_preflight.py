# -*- coding: utf-8 -*-
"""PRÉ-VOL GO / NO-GO d'un essai réel (N3) d'UNE VM — LECTURE SEULE.

Ce que fait cet outil : il LIT l'état du cluster, de la base, du watchdog et de l'hôte et dit si les conditions du plan
(§H) sont réunies pour arrêter VRAIMENT la VM donnée. Ce qu'il ne fait JAMAIS : arrêter, démarrer, activer, écrire en base,
poser un drapeau ou lancer un job. Un GO technique n'est PAS une autorisation : l'arrêt réel d'une VM exige l'autorisation
écrite nominative de l'utilisateur, VM par VM.

  python infra/helios-sot/tools/n3_preflight.py --vm k8s-worker1
Code de sortie : 0 = GO, 1 = NO-GO (au moins un critère bloquant), 2 = VM non éligible (k8s-monitoring, inconnue…).

Critères (bloquants sauf mention « attention ») : watchdog prêt et VM RUNNING en mode AUTO sans maintenance ni cooldown ;
backend 2/2 et sans signe de panne Camunda/watchdog depuis 10 min ; 3 nœuds Ready ; etcd 5/5 ; CNPG 1 primaire + 1 réplique
en streaming ; aucun incident ouvert ni RESTART_VM récent sur la cible ; l'interface VMware Workstation ne tient pas le verrou .vmx de la
cible (sinon `vmrun start` échoue : « The file is already in use », cas réel du 22 nov. 2026) ; sauvegarde < 120 min ; aucun gel d'E/S ou réseau de l'hôte
dans les 60 dernières minutes (fsync WAL ou RTT etcd dégradés sur >= 3 membres ; Prometheus muet = NO-GO) ; RAM hôte >= 2 300 Mo (maintenant
et sur les relevés de la boucle d'observation) ; hôte sur secteur ; disque C: >= 20 Go ; aucun drapeau de maintenance ;
filet de sécurité vm_revive.ps1 présent ; politique du dépôt = cible AUTO avec la route/les notifications attendues.
Propre au répartiteur .162 : l'AUTRE HAProxy (.160) sain et la cible saine (page de stats :8404/;csv). Attention (non bloquant) :
le leader du backend, le primaire PostgreSQL ou le leader etcd est sur la cible — l'outil affiche alors les EFFETS ATTENDUS de
l'arrêt avant toute autorisation.
Propre au plan de contrôle .153 : les apiservers des DEUX autres nœuds répondent /readyz ; les DEUX HAProxy sont sains (ils portent
l'API et le web) ; le kubeconfig du poste ne pointe PAS sur la cible (sinon kubectl et tous les outils sont aveugles pendant l'essai).
Attention (non bloquant) : leaders controller-manager/scheduler, opérateur CNPG (épinglé sur .153 le 17 nov. 2026 : pas de bascule
PostgreSQL automatique pendant la panne), pods HELIOS sur la cible.
Cibles = les QUATRE VM AUTO de la politique : k8s-worker1 (.158), k8s-worker2 (.155), k8s-lb2 (.162) et k8s-master (.153, depuis la
consigne de l'utilisateur du 17 nov. 2026, qui INVERSE « .153 jamais cible »). k8s-monitoring (.160) n'est PAS une cible : c'est la VM
de supervision, en DÉTECTION SEULE (consigne finale du 6 nov. 2026, après-midi) ; elle n'est jamais éligible à un essai d'auto-rallumage
(code de sortie 2).

Option `--accept-host-copy` (17 nov. 2026) : le critère « sauvegarde < 120 min » accepte AUSSI une copie hors cluster VÉRIFIÉE
(helios-state/backups/pfe_db-hostcopy-<UTC>.sql.gz : gzip relu en entier, marqueur « dump complete », taille minimale), SANS rotation et
sans suppression de sauvegarde (les CronJobs gardent 7 dumps et SUPPRIMENT le plus ancien à chaque nouveau dump). L'emploi de l'option
est visible dans le libellé du critère. Sans l'option, le critère est inchangé (Jobs des CronJobs uniquement).

Option `--ack-ram-mo MO` (17 nov. 2026) : ACQUITTEMENT EXPLICITE de l'utilisateur du seul critère « RAM hôte >= 2300 Mo » (« j'accepte la RAM
à 2 Go ») : la RAM disponible doit alors atteindre MO, entre 1500 Mo (garde du watchdog au démarrage, JAMAIS franchie) et 2299 Mo. Aucun autre
critère n'est touché (gel d'E/S, sauvegarde, etcd… restent bloquants) et le libellé du critère affiche l'acquittement.
"""
import argparse
import datetime as dt
import gzip
import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
import zlib

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # pragma: no cover
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
_spec = importlib.util.spec_from_file_location("shadow_observe", os.path.join(HERE, "shadow_observe.py"))
so = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(so)
_co_spec = importlib.util.spec_from_file_location("classify_observation", os.path.join(HERE, "classify_observation.py"))
co = importlib.util.module_from_spec(_co_spec)
_co_spec.loader.exec_module(co)

GO, NOGO, WARN = "GO", "NO-GO", "ATTENTION"
STALL_LOOKBACK_MIN = 60    # écart C4 (6 nov. 2026) : le gel d'E/S de l'hôte du 6 nov. 18:22-18:23Z s'est terminé 87 s avant l'arrêt réel de .155
RAM_MIN_MB = 2300          # plan §H
# Option EXPLICITE `--ack-ram-mo MO` (17 nov. 2026) : ACQUITTEMENT de l'utilisateur (« j'accepte la RAM à 2 Go », 23:2x) du SEUL critère
# « RAM hôte >= 2300 Mo ». Plancher JAMAIS franchi, acquitté ou non : la garde du watchdog au démarrage (1 500 Mo disponibles) ; l'arrêt de la
# cible libère sa mémoire (≈ 2-3 Go) avant le démarrage. Aucun autre critère n'est touché ; le libellé du critère affiche l'acquittement.
RAM_ACK_FLOOR_MB = 1500
BACKUP_MAX_MIN = 120       # plan §H
# Option EXPLICITE `--accept-host-copy` (17 nov. 2026) : une copie fraîche SANS rotation, hors cluster, VÉRIFIÉE, vaut preuve de sauvegarde.
# Pourquoi : les CronJobs gardent 7 dumps et SUPPRIMENT le plus ancien à chaque nouvelle sauvegarde (règle de l'utilisateur : aucune suppression
# de sauvegarde) ; lancer un Job manuel juste pour satisfaire ce critère déclencherait donc une suppression indirecte. Sans l'option, le critère
# est INCHANGÉ (Jobs des CronJobs uniquement).
HOST_BACKUP_DIR = r"C:\Users\houss\helios-state\backups"
HOST_COPY_RE = re.compile(r"^pfe_db-hostcopy-(\d{8})-(\d{6})\.sql\.gz$")     # horodatage en UTC
DUMP_MARKER = b"PostgreSQL database dump complete"
HOST_COPY_MIN_BYTES = 50_000
DISK_MIN_GO = 20           # un démarrage de VM recrée ~3 Go de fichiers mémoire
EVIDENCE_MAX_AGE_MS = 15000
REPL_LAG_MAX_BYTES = 1_000_000
RECENT_WARN_PATTERNS = ("Camunda injoignable", "Failed to activate jobs", "watchdog hôte injoignable", " ERROR ")
VM_ROOT = r"C:\Users\houss\Documents\Virtual Machines"
VMRUN = r"C:\Program Files (x86)\VMware\VMware Workstation\vmrun.exe"
REVIVE = os.path.join(REPO, "infra", "ha01", "ops", "vm_revive.ps1")
WATCHDOG_DEPLOYED = r"C:\Users\houss\helios-vm-watchdog.py"
ETCD_IPS = ["192.168.148.153", "192.168.148.155", "192.168.148.158", "192.168.148.160", "192.168.148.162"]
LB_IPS = ["192.168.148.160", "192.168.148.162"]   # répartiteurs HAProxy (actif/actif, sans VIP) ; santé = page de stats :8404/;csv
POLICY_YAML = os.path.join(REPO, "infra", "helios-vm-policy.yaml")   # source de vérité de la politique (copiée en Java et Python)

# Cibles d'essai réel = les QUATRE VM en mode AUTO de la politique : .155 .158 .162 (consigne finale de l'utilisateur du 6 nov.
# 2026, après-midi) et .153 (consigne du 17 nov. 2026, qui INVERSE « .153 jamais cible »). k8s-monitoring (.160, VM de supervision :
# DETECT_ONLY) n'y figure JAMAIS.
# Cette table DOIT rester égale aux entrées `mode: AUTO` de infra/helios-vm-policy.yaml : un test le vérifie (c'est l'écart
# entre cette table et la politique qui avait produit la contradiction signalée le 6 nov.).
TARGETS = {
    "k8s-worker1": {"ip": "192.168.148.158", "kind": "worker", "route": "CAMUNDA"},
    "k8s-worker2": {"ip": "192.168.148.155", "kind": "worker", "route": "CAMUNDA"},
    "k8s-lb2": {"ip": "192.168.148.162", "kind": "infra", "route": "CAMUNDA"},
    "k8s-master": {"ip": "192.168.148.153", "kind": "control-plane", "route": "CAMUNDA"},
}
NODE_KINDS = ("worker", "control-plane")   # incident NODE_NOT_READY du détecteur Kubernetes (namespace k8s-nodes)
# HA-155-20261122 : fichier de verrou <vmx>.lck\M<n>.lck = "...<pid>-<horodatage>(<exe>) ..." (octets de contrôle compris)
LOCK_OWNER_RE = re.compile(rb"(\d{1,10})-(\d{1,20})\(([A-Za-z0-9_.-]{1,64}\.exe)\)")


def _process_alive(pid, exe):
    """True/False si le processus <pid> de nom <exe> existe, None si indéterminable (le pré-vol le traite alors comme VIVANT)."""
    try:
        r = subprocess.run(["tasklist", "/FI", "PID eq %d" % pid, "/FO", "CSV", "/NH"], capture_output=True, text=True,
                           errors="replace", timeout=8, stdin=subprocess.DEVNULL)
        return re.search(r'"%s","%d"' % (re.escape(exe), pid), r.stdout or "", re.IGNORECASE) is not None
    except Exception:  # noqa: BLE001
        return None


def vmx_lock_owner(vm, root=None, alive=None):
    """Verrou de configuration <vmx>.lck de la VM — LECTURE SEULE : {"lu": bool, "proprietaire": None | {"pid", "exe", "alive"}}.

    HA-155-20261122 : l'interface VMware Workstation garde ce verrou tant que la VM est ouverte dans un onglet ; `vmrun start` (donc
    l'auto-rallumage du watchdog) échoue alors avec « The file is already in use ». Le 22 nov. 2026 : .155 et .158 tenues par vmware.exe,
    .153/.160/.161/.162 sans verrou. `lu` = False si le dossier de verrou existe mais n'a pas pu être lu (fail-closed côté évaluation)."""
    directory = os.path.join(root or VM_ROOT, vm, vm + ".vmx.lck")
    if not os.path.isdir(directory):
        return {"lu": True, "proprietaire": None}
    try:
        names = sorted(os.listdir(directory))
    except OSError:
        return {"lu": False, "proprietaire": None}
    for name in names:
        if not (name.upper().startswith("M") and name.lower().endswith(".lck")):
            continue
        try:
            with open(os.path.join(directory, name), "rb") as fh:
                m = LOCK_OWNER_RE.search(fh.read(1024))
        except OSError:
            return {"lu": False, "proprietaire": None}
        if m:
            pid, exe = int(m.group(1)), m.group(3).decode("ascii", "replace")
            return {"lu": True, "proprietaire": {"pid": pid, "exe": exe, "alive": (alive or _process_alive)(pid, exe)}}
    return {"lu": True, "proprietaire": None}


def scope_condition(vm):
    """Périmètre d'incident de la cible (noms fermés, jamais une saisie libre)."""
    if TARGETS[vm]["kind"] in NODE_KINDS:
        return f"namespace='k8s-nodes' and app='{vm}' and incident_type='NODE_NOT_READY'"
    return f"namespace='infra' and app='{vm}' and incident_type in ('VM_DOWN','VM_UNREACHABLE')"


def restart_cooldown_minutes(path=None, default=30):
    """Cooldown de redémarrage du BACKEND, en minutes, lu dans la politique (infra/helios-vm-policy.yaml, `thresholds.restart_cooldown_minutes`) :
    UNE source pour le pré-vol (fenêtre « aucune action RESTART_VM récente »), `auto_status.py` et leurs tests. 30 → 5 le 18 nov. 2026
    (« go cooldown 5 min »). Illisible → 30 (valeur historique, la plus PRUDENTE : fenêtre de pré-vol la plus large)."""
    try:
        with open(path or POLICY_YAML, encoding="utf-8") as fh:
            for ln in fh:
                m = re.match(r"\s*restart_cooldown_minutes:\s*(\d+)\b", ln)
                if m:
                    return int(m.group(1))
    except OSError:
        pass
    return default


def policy_entries(path=None):
    """Entrées de la table de politique (infra/helios-vm-policy.yaml) : {nom: {mode, restart_route, notifications, …}}.
    Lecture ligne à ligne des entrées `- {name: …, mode: …}` (commentaires ignorés), sans dépendance à PyYAML."""
    out = {}
    with open(path or POLICY_YAML, encoding="utf-8") as fh:
        for line in fh:
            m = re.match(r"^\s*-\s*\{(.*)\}\s*$", line)
            if not m:
                continue
            fields = {}
            for part in m.group(1).split(","):
                k, _, v = part.partition(":")
                fields[k.strip()] = v.strip()
            if "name" in fields:
                out[fields["name"]] = fields
    return out


def consequences(f):
    """Effets ATTENDUS de l'arrêt réel de la cible, à connaître AVANT d'autoriser (lignes de texte, aucune action)."""
    vm = f["vm"]
    ip = TARGETS[vm]["ip"]
    out = []
    if f.get("leader_node") == vm:
        out.append("le leader du backend tombe avec la VM : l'autre pod reprend en 60 s au plus (le test dure 1 à 2 min de plus)")
    prim = next((p.get("node") for p in (f.get("cnpg") or {}).get("pods", []) if p.get("role") == "primary"), None)
    if prim == vm:
        out.append("le PRIMAIRE PostgreSQL tombe : bascule CNPG vers la réplique (≈ 55 s mesurées en HA-01) ; le backend est sans base "
                   "pendant ce temps ; au retour de la VM, l'ancien primaire ne revient en réplique QUE si pg_rewind réussit — le 6 nov. il est resté bloqué (WAL manquant, pas d'archive WAL) et a exigé un RE-CLONAGE ; aucun switchover n'est fait")
    if f.get("etcd_leader") == ip:
        out.append("le LEADER etcd tombe : ré-élection (quorum conservé à 4/5) ; aucune réparation etcd automatique")
    if vm == "k8s-lb2":
        out.append("l'entrée API/web bascule sur le HAProxy de .160 ; clients DNS en cache coupés 13 à 31 s et .153 NotReady ≈ 10 s (mesures HA-01)")
    if TARGETS[vm]["kind"] == "control-plane":
        cpf = f.get("cp") or {}
        out.append("etcd, kube-apiserver, controller-manager et scheduler de la cible tombent : etcd reste à 4/5 (ré-élection si elle est leader) ; "
                   "l'API reste servie par les deux autres nœuds via HAProxy (la cible n'y est qu'en SECOURS : aucun changement de routage) ; les "
                   "leaders controller-manager/scheduler, s'ils étaient sur elle, sont ré-élus en ≈ 15 à 40 s")
        if cpf.get("operator_node") == vm:
            out.append("l'opérateur CNPG (ÉPINGLÉ sur la cible) est INDISPONIBLE pendant la panne : aucune bascule PostgreSQL automatique tant que la "
                       "cible n'est pas revenue ; les instances PostgreSQL, elles, continuent de tourner (un 2e incident simultané ne serait pas traité)")
        out.append("aucun pod HELIOS (backend, frontend, PostgreSQL) n'est censé s'y trouver ; calico-kube-controllers (1 réplique) et 1 des 2 CoreDNS "
                   "y sont : replanifiés après ≈ 5 min, sans effet sur les pods en marche (relevé du 17 nov. 2026)")
    return out


# ── Évaluation pure (testée sans réseau) ─────────────────────────────────────────────────────────────────────────

def evaluate(f):
    """f = faits collectés. Retourne la liste de lignes (statut, critère, détail). Tout fait absent = NO-GO (fail-closed)."""
    vm = f["vm"]
    rows = []

    def add(ok, label, detail, blocking=True):
        rows.append((GO if ok else (NOGO if blocking else WARN), label, detail))

    # 1. Watchdog : états, mode, garde-fous
    st = f.get("vm_states") or {}
    stale = [n for n, v in st.items() if (v.get("age_ms") is None or v["age_ms"] > EVIDENCE_MAX_AGE_MS)]
    off = [n for n, v in st.items() if v.get("etat") != "RUNNING"]
    add(len(st) == 5 and not off and not stale, "les 5 VM sont RUNNING (preuve fraîche du watchdog)",
        f"{len(st)} VM lues ; pas RUNNING : {off or 'aucune'} ; preuve périmée : {stale or 'aucune'}")
    t = st.get(vm) or {}
    add(t.get("mode") == "AUTO", f"{vm} est en mode AUTO côté watchdog", f"mode={t.get('mode')}")
    add(st.get("k8s-monitoring", {}).get("mode") == "DETECT_ONLY", "k8s-monitoring (.160) est DETECT_ONLY côté watchdog",
        f"mode={st.get('k8s-monitoring', {}).get('mode')}")
    refus = t.get("refus") or []
    add("MAINTENANCE" not in refus and (t.get("cooldown_s") or 0) == 0, "aucune maintenance, aucun cooldown actif de la cible",
        f"refus={refus} cooldown_s={t.get('cooldown_s')}")
    # Un démarrage antérieur (essai précédent) n'empêche PAS un nouvel essai une fois le cooldown écoulé : seule l'IDEMPOTENCE par
    # incident compte côté watchdog. C'est une information, pas un blocage (6 nov. : .158/.162 ont déjà été validées une fois).
    add(not t.get("dernier_demarrage"), "aucun démarrage antérieur de la cible (information)",
        f"dernier démarrage : {t.get('dernier_demarrage')}", blocking=False)
    wc = f.get("watchdog_check") or {}
    add(bool(wc.get("vmrun_found")) and bool((wc.get("vmx") or {}).get(vm)) and bool(wc.get("token_configured")),
        "watchdog : vmrun, fichier .vmx de la cible et jeton présents", json.dumps(
            {"vmrun": wc.get("vmrun_found"), "vmx": (wc.get("vmx") or {}).get(vm), "jeton": wc.get("token_configured")}))
    h = f.get("host") or {}
    tasks = h.get("taches") or {}
    add(h.get("ecouteur_9099") == 1 and tasks.get("HELIOS-Watchdog") == "Running" and h.get("ecouteur_9999") == 0,
        "watchdog en marche (9099 répond, 9999 fermé, tâche Running)",
        f"9099={h.get('ecouteur_9099')} 9999={h.get('ecouteur_9999')} tâche={tasks.get('HELIOS-Watchdog')}")
    add((h.get("drapeaux_maintenance") or 0) == 0, "aucun drapeau de maintenance sur l'hôte",
        f"{h.get('drapeaux_maintenance')} drapeau(x)")
    # HA-155-20261122 : l'interface VMware qui tient le verrou .vmx de la cible empêche `vmrun start` (« The file is already in use »)
    lk = h.get("verrou_vmx") or {}
    own = lk.get("proprietaire") or {}
    ui = bool(own) and str(own.get("exe", "")).lower() == "vmware.exe" and own.get("alive") is not False
    if lk.get("lu") is not True:
        lock_detail = "verrou .vmx non lu (fail-closed)"
    elif ui:
        lock_detail = f"verrou TENU par vmware.exe pid={own.get('pid')} : fermer l'interface (ou l'onglet de la VM) avant l'essai"
    elif own:
        lock_detail = f"verrou tenu par {own.get('exe')} pid={own.get('pid')} (vivant={own.get('alive')}) : sans effet sur le démarrage"
    else:
        lock_detail = "aucun verrou .vmx"
    add(lk.get("lu") is True and not ui,
        "l'interface VMware Workstation ne tient pas le verrou .vmx de la cible (sinon `vmrun start` échoue : « The file is already in use »)",
        lock_detail)

    # 2. Backend et chaîne Camunda
    cfg = f.get("config") or {}
    add(cfg.get("HELIOS_VMPOWER_ENABLED") == "true", "surveillance de puissance activée (ENABLED=true)", json.dumps(cfg))
    pods = f.get("pods") or []
    add(len(pods) == 2 and all(p.get("ready") for p in pods), "backend : 2 pods Ready",
        ", ".join(f"{p['name'][-5:]}@{p.get('node')} ready={p.get('ready')}" for p in pods) or "aucun pod")
    add(all((p.get("restarts") or 0) == 0 for p in pods), "backend : aucun redémarrage de pod",
        str([p.get("restarts") for p in pods]), blocking=False)
    if TARGETS[vm]["route"] == "CAMUNDA":
        warned = {p["name"][-5:]: p.get("warn_recent", 0) for p in pods}
        add(bool(pods) and all(v == 0 for v in warned.values()), "aucun signe de panne Camunda/watchdog/erreur depuis 10 min",
            f"lignes d'alerte par pod : {warned}")
    leader_node = f.get("leader_node")
    add(leader_node is not None and leader_node != vm, "le leader du backend n'est pas sur la cible",
        f"leader sur {leader_node or 'non établi (bail en transition après un redémarrage du backend)'}", blocking=False)

    # 3. Cluster
    nodes = f.get("nodes") or {}
    add(len(nodes) == 3 and all(nodes.values()), "3 nœuds Kubernetes Ready", json.dumps(nodes))
    etcd = f.get("etcd") or {}
    add(len(etcd) == 5 and all(etcd.values()), "etcd : 5 membres sains", json.dumps({k[-3:]: v for k, v in etcd.items()}))
    c = f.get("cnpg") or {}
    cp = c.get("pods") or []
    roles = sorted(p.get("role") or "?" for p in cp)
    add(len(cp) == 2 and all(p.get("ready") for p in cp) and roles == ["primary", "replica"],
        "CNPG : 1 primaire + 1 réplique, tous Ready", f"{[(p['name'], p.get('role'), p.get('ready')) for p in cp]}")
    add(c.get("repl_state") == "streaming" and (c.get("repl_lag_bytes") if c.get("repl_lag_bytes") is not None else 10**12)
        < REPL_LAG_MAX_BYTES, "CNPG : réplication en streaming, retard < 1 Mo",
        f"état={c.get('repl_state')} retard={c.get('repl_lag_bytes')} o")
    prim_node = next((p.get("node") for p in cp if p.get("role") == "primary"), None)
    add(prim_node != vm, "le primaire PostgreSQL n'est pas sur la cible", f"primaire sur {prim_node}", blocking=False)

    # 3b. Répartiteurs HAProxy : cible .162 -> l'autre répartiteur (.160) doit tenir l'entrée ; cible .153 -> les DEUX portent l'API
    lb = f.get("lb")
    kind = TARGETS[vm]["kind"]
    if kind == "control-plane" and lb is None:
        add(False, "les DEUX répartiteurs HAProxy (.160 et .162) sont sains (ils portent l'API et le web pendant la panne)",
            "pages de stats non lues (fail-closed)")
    if lb is not None:
        ha = lb.get("ha") or {}
        other = [ip for ip in LB_IPS if ip != TARGETS[vm]["ip"]]
        label = ("l'AUTRE répartiteur HAProxy est sain (il porte l'entrée pendant la panne)" if kind == "infra"
                 else "les DEUX répartiteurs HAProxy (.160 et .162) sont sains (ils portent l'API et le web pendant la panne)")
        add(bool(other) and all((ha.get(ip) or {}).get("ok") for ip in other), label,
            json.dumps({ip: (ha.get(ip) or {}).get("detail") for ip in other}, ensure_ascii=False))
        if kind == "infra":
            mine = ha.get(TARGETS[vm]["ip"]) or {}
            add(bool(mine.get("ok")), "HAProxy de la cible est sain (état de départ)", str(mine.get("detail")))

    # 3c. Leader etcd (toutes les cibles portent un membre etcd : .155 .158 .162) — attention, jamais bloquant :
    #     un transfert de leadership est INTERDIT par le plan ; on le subit seulement si l'utilisateur l'accepte.
    leader_ip = f.get("etcd_leader")
    add(leader_ip is not None and leader_ip != TARGETS[vm]["ip"], "le leader etcd n'est pas sur la cible",
        f"leader etcd : {leader_ip or 'non établi'}", blocking=False)

    # 3d. Plan de contrôle (cible .153) : l'API doit rester servie par les deux autres nœuds ; effets et risques à connaître
    if kind == "control-plane":
        cpf = f.get("cp") or {}
        api = cpf.get("apiserver") or {}
        add(len(api) == 2 and all(api.values()), "les apiservers des DEUX autres nœuds répondent /readyz (ils portent l'API pendant la panne)",
            json.dumps({ip[-3:]: ok for ip, ok in api.items()}) if api else "non lu (fail-closed)")
        kc = cpf.get("kubeconfig_server")
        add(bool(kc) and TARGETS[vm]["ip"] not in kc,
            "le kubeconfig du poste ne pointe pas sur la cible (sinon kubectl et tous les outils sont aveugles pendant l'essai)",
            f"server={kc or 'non lu'} (attendu : https://api.helios.orange.tn:6443)")
        lead = cpf.get("leaders") or {}
        add(bool(lead) and all(node != vm for node in lead.values()), "les leaders controller-manager et scheduler ne sont pas sur la cible",
            json.dumps(lead) if lead else "non établis", blocking=False)
        op = cpf.get("operator_node")
        add(op is not None and op != vm, "l'opérateur CNPG n'est pas sur la cible",
            f"opérateur sur {op or 'non établi'}" + (" (ÉPINGLÉ : indisponible pendant la panne)" if op == vm else ""), blocking=False)
        sur_cible = [p["name"] for p in pods if p.get("node") == vm] + [p["name"] for p in cp if p.get("node") == vm]
        add(not sur_cible, "aucun pod HELIOS (backend, PostgreSQL) sur la cible", f"sur la cible : {sur_cible or 'aucun'}", blocking=False)

    # 3e. La politique du dépôt déclare la cible AUTO avec la route et les notifications attendues
    pol = f.get("policy") or {}
    exp_notif = "EMAIL_AND_UI"   # les trois cibles AUTO : notifications habituelles (la restriction « interface seulement » est celle de .160)
    add(pol.get("mode") == "AUTO" and pol.get("restart_route") == TARGETS[vm]["route"] and pol.get("notifications") == exp_notif,
        "politique du dépôt : cible AUTO, route et notifications conformes",
        f"mode={pol.get('mode')} route={pol.get('restart_route')} (attendu {TARGETS[vm]['route']}) notifications={pol.get('notifications')} (attendu {exp_notif})")

    # 4. Base : rien d'ouvert, rien de récent sur la cible
    db = f.get("db") or {}
    add(db.get("open_incidents") == 0, "aucun incident ouvert sur le périmètre de la cible", f"{db.get('open_incidents')} ouvert(s)")
    add(db.get("recent_restart_actions") == 0,
        f"aucune action RESTART_VM sur la cible depuis {restart_cooldown_minutes()} min (cooldown du backend)",
        f"{db.get('recent_restart_actions')} action(s)")

    # 5. Sauvegardes
    age = f.get("backup_age_min")
    hc = f.get("host_copy") if f.get("accept_host_copy") else None
    cron_ok = age is not None and age < BACKUP_MAX_MIN
    hc_ok = bool(hc and hc.get("ok") and hc.get("age_min") is not None and 0 <= hc["age_min"] < BACKUP_MAX_MIN)
    if not cron_ok and hc_ok:
        add(True, f"sauvegarde PostgreSQL de moins de {BACKUP_MAX_MIN} min — COPIE HÔTE vérifiée (option explicite --accept-host-copy)",
            f"{os.path.basename(hc['path'])} : {hc['age_min']:.0f} min, gzip relu en entier, marqueur « dump complete », "
            f"{hc['size']} o, sha256 {hc['sha256'][:16]}… ; Jobs des CronJobs : "
            + ("inconnue" if age is None else f"{age:.0f} min") + " (AUCUNE rotation déclenchée, aucune sauvegarde supprimée)")
    else:
        detail = "inconnue" if age is None else f"la plus récente date de {age:.0f} min"
        if f.get("accept_host_copy") and not hc_ok:
            if not hc:
                detail += f" ; copie hôte : aucune trouvée dans {HOST_BACKUP_DIR}"
            elif not hc.get("ok"):
                detail += f" ; copie hôte REFUSÉE : {hc.get('raison')}"
            else:
                detail += f" ; copie hôte trop ancienne : {hc.get('age_min'):.0f} min"
        add(cron_ok, f"sauvegarde PostgreSQL de moins de {BACKUP_MAX_MIN} min", detail)

    # 6. Hôte
    ram = h.get("ram_dispo_mo")
    hist = f.get("ram_history") or []
    hs = f.get("host_stalls") or {}
    add(bool(hs.get("evalue")) and not hs.get("fenetres"),
        f"aucun gel d'E/S ou réseau de l'hôte dans les {STALL_LOOKBACK_MIN} dernières minutes (>= 3 membres etcd en fsync ou RTT dégradés)",
        "non évalué (Prometheus .160 injoignable : fail-closed)" if not hs.get("evalue")
        else f"{len(hs.get('fenetres') or [])} gel(s) ; dernier terminé il y a {hs.get('derniere_fin_il_y_a_min')} min")
    ack = f.get("ack_ram_mo")
    ack = ack if isinstance(ack, int) and not isinstance(ack, bool) and RAM_ACK_FLOOR_MB <= ack < RAM_MIN_MB else None   # hors bornes : ignoré
    seuil = ack if ack is not None else RAM_MIN_MB                          # seuil effectif (acquitté par l'utilisateur, ou plan §H)
    if ram is not None and ram < RAM_MIN_MB and ack is not None and ram >= ack:
        add(True, f"RAM hôte disponible >= {RAM_MIN_MB} Mo maintenant — ACQUITTÉ par l'utilisateur à >= {ack} Mo (option --ack-ram-mo)",
            f"{ram} Mo ; garde du watchdog au démarrage : {RAM_ACK_FLOOR_MB} Mo (l'arrêt de la cible libère sa mémoire avant le démarrage)")
    else:
        add(ram is not None and ram >= RAM_MIN_MB, f"RAM hôte disponible >= {RAM_MIN_MB} Mo maintenant",
            f"{ram} Mo" + (f" ; acquittement demandé >= {ack} Mo NON satisfait" if ack is not None and (ram is None or ram < RAM_MIN_MB) else ""))
    low = bool(hist) and min(hist) < seuil   # une valeur basse récente est BLOQUANTE ; un historique trop court = attention
    add(len(hist) >= 2 and not low, f"RAM hôte >= {seuil} Mo sur les 2 derniers relevés de la boucle (>= 15 min)",
        f"relevés : {hist}" + ("" if len(hist) >= 2 else " (historique trop court : prendre un relevé de plus)"), blocking=low)
    add(h.get("sur_secteur") is True, "hôte sur secteur", f"sur secteur={h.get('sur_secteur')} batterie={h.get('batterie_pct')} %")
    add((h.get("disque_c_libre_go") or 0) >= DISK_MIN_GO, f"disque C: >= {DISK_MIN_GO} Go libres",
        f"{h.get('disque_c_libre_go')} Go ({h.get('disque_c_libre_pct')} %)")

    # 7. Filet de sécurité et observation
    add(bool(f.get("revive_present")), "filet de sécurité vm_revive.ps1 présent", REVIVE)
    lp = f.get("loop") or {}
    add(lp.get("last_verdict") == "OK", "dernier relevé de la boucle d'observation = OK",
        f"{lp.get('last_ts')} : {lp.get('last_verdict')}", blocking=False)
    return rows


def host_stalls_recent(now=None, fetch=None):
    """Gels de l'hôte (fsync WAL ou RTT etcd dégradés sur >= 3 membres au même instant) des STALL_LOOKBACK_MIN dernières minutes.
    Lecture seule (GET Prometheus .160). evalue=False si Prometheus est injoignable : le pré-vol le traite comme NO-GO (fail-closed)."""
    now = now if now is not None else dt.datetime.now().timestamp()
    kw = {"fetch": fetch} if fetch else {}
    allw, _fs, _rtt = co.host_stalls(now - STALL_LOOKBACK_MIN * 60, now, **kw)
    if allw is None:
        return {"evalue": False, "fenetres": [], "derniere_fin_il_y_a_min": None}
    last = max((b for _a, b in allw), default=None)
    return {"evalue": True, "fenetres": [(a, b) for a, b in allw],
            "derniere_fin_il_y_a_min": None if last is None else round((now - last) / 60.0, 1)}


def verdict(rows):
    return NOGO if any(r[0] == NOGO for r in rows) else GO


# ── Collecte (lecture seule) ─────────────────────────────────────────────────────────────────────────────────────

BACKUP_CRONJOBS = ("postgres-backup", "postgres-backup-b")


def latest_backup_age_min(jobs, now=None):
    """Âge (min) de la sauvegarde RÉUSSIE la plus récente, ou None. `jobs` = items de `kubectl get jobs -o json`.

    Sont reconnus les Jobs issus des CronJobs de sauvegarde, planifiés OU lancés à la main avec
    `kubectl create job --from=cronjob/...` (reconnus par leur ownerReference, pas par leur nom). Un Job en échec, sans
    heure de fin ou étranger aux sauvegardes est ignoré."""
    now = now or dt.datetime.now(dt.timezone.utc)
    ages = []
    for j in jobs:
        st = j.get("status") or {}
        done = st.get("completionTime")
        if not (st.get("succeeded") and done):
            continue
        meta = j.get("metadata") or {}
        owned = any(o.get("kind") == "CronJob" and o.get("name") in BACKUP_CRONJOBS for o in (meta.get("ownerReferences") or []))
        if not owned and not str(meta.get("name", "")).startswith("postgres-backup"):
            continue
        ages.append((now - dt.datetime.fromisoformat(done.replace("Z", "+00:00"))).total_seconds() / 60)
    return min(ages) if ages else None


def verify_host_copy(path, now=None):
    """Vérifie UNE copie hôte `pfe_db-hostcopy-<AAAAMMJJ-HHMMSS UTC>.sql.gz` (copie fraîche SANS rotation, hors cluster) : nom horodaté,
    gzip INTÈGRE (relecture complète = `gzip -t`, CRC vérifié), marqueur « PostgreSQL database dump complete » en fin de flux, taille
    minimale. Retourne {ok, path, age_min, size, sha256, raison}. Ne modifie rien ; un fichier illisible n'est jamais un succès."""
    now = now or dt.datetime.now(dt.timezone.utc)
    res = {"ok": False, "path": path, "age_min": None, "size": None, "sha256": None, "raison": ""}
    m = HOST_COPY_RE.match(os.path.basename(path))
    if not m:
        res["raison"] = "nom inattendu (attendu pfe_db-hostcopy-AAAAMMJJ-HHMMSS.sql.gz, UTC)"
        return res
    try:
        stamp = dt.datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S").replace(tzinfo=dt.timezone.utc)
        res["age_min"] = (now - stamp).total_seconds() / 60
        res["size"] = os.path.getsize(path)
        h = hashlib.sha256()
        with open(path, "rb") as fh:
            for bloc in iter(lambda: fh.read(1 << 20), b""):
                h.update(bloc)
        res["sha256"] = h.hexdigest()
        tail = b""
        with gzip.open(path, "rb") as gz:
            for bloc in iter(lambda: gz.read(1 << 20), b""):
                tail = (tail + bloc)[-8192:]
    except (OSError, EOFError, ValueError, zlib.error) as ex:      # BadGzipFile est un OSError
        res["raison"] = f"gzip illisible ou tronqué ({type(ex).__name__})"
        return res
    if res["age_min"] < -2:
        res["raison"] = "horodatage dans le futur"
    elif res["size"] < HOST_COPY_MIN_BYTES:
        res["raison"] = f"trop petite ({res['size']} o < {HOST_COPY_MIN_BYTES})"
    elif DUMP_MARKER not in tail:
        res["raison"] = "marqueur « PostgreSQL database dump complete » absent (dump incomplet)"
    else:
        res["ok"] = True
    return res


def latest_verified_host_copy(directory=None, now=None):
    """La copie hôte la plus RÉCENTE (par horodatage du nom), vérifiée ; None s'il n'y en a aucune. Une copie récente corrompue n'est
    PAS remplacée par une plus ancienne saine : l'outil rapporte la plus récente telle qu'elle est (fail-closed)."""
    directory = directory or HOST_BACKUP_DIR
    try:
        noms = sorted((n for n in os.listdir(directory) if HOST_COPY_RE.match(n)), reverse=True)
    except OSError:
        return None
    return verify_host_copy(os.path.join(directory, noms[0]), now) if noms else None


def parse_haproxy_csv(text):
    """Santé d'un HAProxy d'après sa page de stats CSV (:8404/;csv) — MÊME lecture que VmRecoveryVerifier : au moins un serveur,
    tous les serveurs (hors lignes FRONTEND/BACKEND) `UP`, frontend `k8s_api` `OPEN`, backend `k8s_masters` `UP`.
    Retourne (ok, détail). Un CSV vide ou illisible n'est jamais un succès."""
    lines = [ln for ln in (text or "").splitlines() if ln.strip()]
    if not lines or not lines[0].startswith("#"):
        return False, "page de stats absente ou illisible"
    header = [h.strip() for h in lines[0][1:].strip().split(",")]
    try:
        ipx, isv, ist = header.index("pxname"), header.index("svname"), header.index("status")
    except ValueError:
        return False, "en-tête de stats inattendu"
    api_open = masters_up = False
    servers = not_up = 0
    for ln in lines[1:]:
        c = ln.split(",")
        if len(c) <= max(ipx, isv, ist):
            return False, "ligne de stats tronquée"
        px, sv, status = c[ipx], c[isv], c[ist]
        if px == "k8s_api" and sv == "FRONTEND":
            api_open = status.startswith("OPEN")
        elif px == "k8s_masters" and sv == "BACKEND":
            masters_up = status.startswith("UP")
        if sv not in ("FRONTEND", "BACKEND"):
            servers += 1
            not_up += 0 if status.startswith("UP") else 1
    ok = api_open and masters_up and servers > 0 and not_up == 0
    return ok, f"k8s_api OPEN={api_open}, k8s_masters UP={masters_up}, serveurs={servers} dont hors-UP={not_up}"


def pick_vantage(pods, leader):
    """Pod backend depuis lequel on LIT l'état (watchdog, etcd, HAProxy…) : le leader s'il est vivant et prêt, sinon n'importe quel
    pod prêt. Juste après un redémarrage du backend le bail pointe encore ~60 s vers un pod qui n'existe plus : sans ce repli, rien ne
    serait lu et le pré-vol refuserait à tort. Retourne None seulement si AUCUN pod n'est prêt."""
    ready = [p["name"] for p in pods if p.get("ready")]
    if leader in ready:
        return leader
    return ready[0] if ready else None


def _psql(primary, sql, errors):
    rc, out, err = so.kube("exec", "-i", "-n", so.NS, primary, "-c", "postgres", "--", "psql", "-U", "postgres", "-d",
                           "pfe_db", "-At", "-F", "|", "-f", "-", timeout=60, stdin=sql)
    if rc != 0:
        errors.append("psql: " + err.strip()[:120])
    res = {}
    for line in out.splitlines():
        if "|" in line:
            k, v = line.split("|", 1)
            res[k] = int(v) if re.fullmatch(r"-?\d+", v) else v
    return res


def collect_control_plane(vm, vantage, errors):
    """Faits propres au plan de contrôle (cible .153), en LECTURE SEULE : /readyz des AUTRES apiservers (depuis un pod du cluster),
    leaders controller-manager/scheduler (leases kube-system), nœud de l'opérateur CNPG, serveur du kubeconfig du poste."""
    others = [t["ip"] for n, t in TARGETS.items() if t["kind"] in NODE_KINDS and n != vm]
    cp = {"apiserver": {}, "leaders": {}, "operator_node": None, "kubeconfig_server": None}
    for ip in others:
        _, out, _ = so.kube("exec", "-n", so.NS, vantage, "--", "sh", "-c", "curl -sk -m 5 https://%s:6443/readyz" % ip, timeout=40)
        cp["apiserver"][ip] = out.strip() == "ok"
    rc, out, err = so.kube("get", "lease", "-n", "kube-system", "kube-controller-manager", "kube-scheduler", "-o", "json")
    if rc == 0:
        for it in json.loads(out).get("items", []):
            holder = (it.get("spec") or {}).get("holderIdentity") or ""
            cp["leaders"][it["metadata"]["name"]] = holder.split("_", 1)[0] or None
    else:
        errors.append("kubectl get lease: " + err.strip()[:100])
    rc, out, err = so.kube("get", "pods", "-n", "cnpg-system", "-o", "json")
    if rc == 0:
        cp["operator_node"] = next((p["spec"].get("nodeName") for p in json.loads(out).get("items", [])
                                    if p["metadata"]["name"].startswith("cnpg-controller-manager")), None)
    else:
        errors.append("kubectl get pods cnpg-system: " + err.strip()[:100])
    rc, out, _ = so.kube("config", "view", "--minify", "-o", "jsonpath={.clusters[0].cluster.server}")
    cp["kubeconfig_server"] = out.strip() if rc == 0 and out.strip() else None
    return cp


def collect(vm, errors, accept_host_copy=False, ack_ram_mo=None):
    f = {"vm": vm}
    rc, out, err = so.kube("get", "pods", "-n", so.NS, "-o", "json")
    allpods = json.loads(out).get("items", []) if rc == 0 else []
    if rc != 0:
        errors.append("kubectl get pods: " + err.strip()[:100])
    pods, cnpg_pods = [], []
    for it in allpods:
        name = it["metadata"]["name"]
        cs = (it["status"].get("containerStatuses") or [{}])[0]
        if name.startswith("backend-"):
            _, lg, _ = so.kube("logs", "-n", so.NS, name, "--since=10m", timeout=60)
            warn = sum(1 for x in lg.splitlines() if any(p in x for p in RECENT_WARN_PATTERNS))
            pods.append({"name": name, "node": it["spec"].get("nodeName"), "ready": bool(cs.get("ready")),
                         "restarts": cs.get("restartCount"), "warn_recent": warn})
        if it["metadata"].get("labels", {}).get("cnpg.io/cluster") == "postgres":
            cnpg_pods.append({"name": name, "node": it["spec"].get("nodeName"), "ready": bool(cs.get("ready")),
                              "role": it["metadata"]["labels"].get("cnpg.io/instanceRole")})
    f["pods"] = pods
    f["config"] = so.collect_config(errors)

    primary = next((p["name"] for p in cnpg_pods if p["role"] == "primary"), None)
    cnpg = {"pods": cnpg_pods}
    if primary:
        r = _psql(primary, "select 'repl_state', coalesce(string_agg(state, ','), 'none') from pg_stat_replication;\n"
                           "select 'repl_lag_bytes', coalesce(max(pg_wal_lsn_diff(sent_lsn, replay_lsn)), 0)::bigint from pg_stat_replication;\n"
                           "select 'bail_leader', holder from leader_lease where lease_name='aiops-scheduler';\n"
                           f"select 'open_incidents', count(*) from incident where status in ('NEW','OPEN','IN_PROGRESS','WAITING_FOR_APPROVAL','RESOLUTION_FAILED') and {scope_condition(vm)};\n"
                           f"select 'recent_restart_actions', count(*) from incident_action where type='RESTART_VM' and target_name='{vm}' and created_at > (now() at time zone 'Africa/Tunis') - interval '{restart_cooldown_minutes()} minutes';\n",
                  errors)
        cnpg["repl_state"], cnpg["repl_lag_bytes"] = r.get("repl_state"), r.get("repl_lag_bytes")
        f["db"] = {"open_incidents": r.get("open_incidents"), "recent_restart_actions": r.get("recent_restart_actions")}
        leader = next((p["name"] for p in pods if str(r.get("bail_leader", "")).startswith(p["name"])), None)
    else:
        errors.append("primaire PostgreSQL introuvable")
        leader = None
    f["cnpg"] = cnpg
    f["leader"] = leader
    f["leader_node"] = next((p["node"] for p in pods if p["name"] == leader), None)

    rc, out, _ = so.kube("get", "nodes", "-o", "json")
    nodes = {}
    if rc == 0:
        for n in json.loads(out).get("items", []):
            conds = {c["type"]: c["status"] for c in n["status"].get("conditions", [])}
            nodes[n["metadata"]["name"]] = conds.get("Ready") == "True"
    f["nodes"] = nodes

    vantage = pick_vantage(pods, leader)
    if vantage:
        loop = " ".join(f'echo "{ip}=$(curl -s -m 4 http://{ip}:2381/health)";' for ip in ETCD_IPS)
        rc, out, _ = so.kube("exec", "-n", so.NS, vantage, "--", "sh", "-c", loop, timeout=60)
        f["etcd"] = {ln.split("=", 1)[0]: '"health":"true"' in ln for ln in out.splitlines() if "=" in ln}
        f["vm_states"] = so.collect_vm_state(vantage, errors)
        # leader etcd (toutes les cibles) : le membre dont la métrique etcd_server_is_leader vaut 1
        parts = ['echo "lead:%s:$(curl -s -m 4 http://%s:2381/metrics | grep -E "^etcd_server_is_leader " | cut -d" " -f2)";' % (ip, ip)
                 for ip in ETCD_IPS]
        _, lbout, _ = so.kube("exec", "-n", so.NS, vantage, "--", "sh", "-c", " ".join(parts), timeout=60)
        f["etcd_leader"] = None
        for ln in lbout.splitlines():
            kind, _, rest = ln.partition(":")
            ip, _, val = rest.rpartition(":")
            if kind == "lead" and val.strip().startswith("1"):
                f["etcd_leader"] = ip
        if TARGETS[vm]["kind"] in ("infra", "control-plane"):   # .162 : .160 doit tenir l'entrée ; .153 : les DEUX HAProxy portent l'API
            lbf = {"ha": {}}
            for ip in LB_IPS:   # page de stats HAProxy (:8404/;csv), lue depuis le pod comme le fait le vérificateur de récupération
                _, csv, _ = so.kube("exec", "-n", so.NS, vantage, "--", "sh", "-c", 'curl -s -m 5 "http://%s:8404/;csv"' % ip, timeout=40)
                ok, detail = parse_haproxy_csv(csv)
                lbf["ha"][ip] = {"ok": ok, "detail": detail}
            f["lb"] = lbf
        if TARGETS[vm]["kind"] == "control-plane":
            f["cp"] = collect_control_plane(vm, vantage, errors)
    f["policy"] = policy_entries().get(vm)

    rc, out, _ = so.kube("get", "jobs", "-n", so.NS, "-o", "json")
    f["backup_age_min"] = latest_backup_age_min(json.loads(out).get("items", [])) if rc == 0 else None
    f["ack_ram_mo"] = ack_ram_mo
    f["accept_host_copy"] = bool(accept_host_copy)
    f["host_copy"] = latest_verified_host_copy() if accept_host_copy else None

    host = so.collect_host(errors)
    host["verrou_vmx"] = vmx_lock_owner(vm)     # HA-155-20261122 : lecture seule du verrou de configuration de la cible
    if not host["verrou_vmx"]["lu"]:
        errors.append("verrou .vmx de la cible illisible")
    rc, out, _ = so.run(["powershell", "-NoProfile", "-NonInteractive", "-Command",
                         "$b = Get-CimInstance Win32_Battery; if ($b) { '{0}|{1}' -f $b.BatteryStatus, $b.EstimatedChargeRemaining } else { 'none' }"],
                        timeout=40)
    out = out.strip()
    if out == "none":
        host["sur_secteur"], host["batterie_pct"] = True, None
    elif "|" in out:
        status, pct = out.split("|", 1)
        host["sur_secteur"] = status.strip() not in ("1", "4", "5")  # 1 décharge, 4 faible, 5 critique = sur batterie
        host["batterie_pct"] = pct.strip()
    f["host"] = host

    rc, out, _ = so.run([sys.executable, "-I", WATCHDOG_DEPLOYED, "--check"], timeout=40)
    try:
        f["watchdog_check"] = json.loads(out)
    except Exception:  # noqa: BLE001
        f["watchdog_check"] = {}
    recs = [r for r in so.load_all() if not r.get("invalide")]
    f["ram_history"] = [r["hote"]["ram_dispo_mo"] for r in recs[-2:] if r.get("hote", {}).get("ram_dispo_mo") is not None]
    f["loop"] = {"last_ts": recs[-1]["ts"], "last_verdict": recs[-1]["verdict"]} if recs else {}
    f["revive_present"] = os.path.isfile(REVIVE)
    f["host_stalls"] = host_stalls_recent()
    return f


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--vm", required=True, help="nom exact de la VM : " + ", ".join(TARGETS))
    ap.add_argument("--accept-host-copy", action="store_true",
                    help="accepte comme preuve de sauvegarde (< %d min) une copie hors cluster VÉRIFIÉE de %s : aucune rotation, aucune suppression"
                         % (BACKUP_MAX_MIN, HOST_BACKUP_DIR))
    ap.add_argument("--ack-ram-mo", type=int, metavar="MO",
                    help="ACQUITTEMENT EXPLICITE de l'utilisateur du seul critère RAM : accepte une RAM hôte disponible >= MO au lieu de %d Mo ; "
                         "refusé hors de [%d ; %d] Mo (%d Mo = garde du watchdog au démarrage, jamais franchie)"
                         % (RAM_MIN_MB, RAM_ACK_FLOOR_MB, RAM_MIN_MB - 1, RAM_ACK_FLOOR_MB))
    a = ap.parse_args(argv)
    if a.ack_ram_mo is not None and not (RAM_ACK_FLOOR_MB <= a.ack_ram_mo < RAM_MIN_MB):
        print(f"REFUSÉ : --ack-ram-mo doit être compris entre {RAM_ACK_FLOOR_MB} Mo (garde du watchdog au démarrage, jamais franchie) "
              f"et {RAM_MIN_MB - 1} Mo (au-delà, le critère normal s'applique) ; reçu {a.ack_ram_mo}.")
        return 2
    if a.vm not in TARGETS:
        print(f"REFUSÉ : '{a.vm}' n'est pas une cible éligible (cibles : {', '.join(TARGETS)}). "
              "k8s-monitoring (.160) est en DÉTECTION SEULE (VM de supervision : pas d'auto-rallumage, consigne du 6 nov. 2026).")
        return 2
    errors = []
    facts = collect(a.vm, errors, accept_host_copy=a.accept_host_copy, ack_ram_mo=a.ack_ram_mo)
    rows = evaluate(facts)
    print(f"\n=== PRÉ-VOL N3 de {a.vm} (lecture seule) ===")
    for status, label, detail in rows:
        print(f"[{status:<9}] {label}\n              {detail}")
    for e in errors:
        print(f"[ATTENTION] collecte incomplète : {e}")
    v = verdict(rows)
    bad = [r for r in rows if r[0] == NOGO]
    print(f"\nVERDICT TECHNIQUE : {v}" + (f" — {len(bad)} critère(s) bloquant(s)" if bad else ""))
    effets = consequences(facts)
    if effets:
        print(f"\nEFFETS ATTENDUS de l'arrêt réel de {a.vm} (à connaître avant d'autoriser) :")
        for e in effets:
            print(f"  - {e}")
    print("Rappel : un GO technique n'est PAS une autorisation. L'activation et l'arrêt réel de "
          f"{a.vm} exigent l'autorisation écrite nominative de l'utilisateur.")
    return 0 if v == GO else 1


if __name__ == "__main__":
    sys.exit(main())
