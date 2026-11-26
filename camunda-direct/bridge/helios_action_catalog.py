#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""HELIOS — CATALOGUE D'ACTIONS CANONIQUE. Source unique de verite.

CE FICHIER EST LE CONTRAT DE SECURITE. Il repond a une seule question :
    « pour cet ACTION_ID, quelle commande exacte s'execute, sur quelle cible ? »

Principes NON NEGOCIABLES
-------------------------
1. WHITELIST STRICTE. `resolve()` ne fait AUCUNE deduction : ni mot-cle, ni
   sous-chaine, ni ressemblance. Un identifiant inconnu rend None. Point.
2. UN ACTION_ID = UN SCOPE. Un identifiant ne peut pas designer a la fois une
   charge de travail et un noeud (bug constate le 2026-08-05 :
   RESTART_POD_TO_RECLAIM_MEMORY servait POD_MEMORY_LEAK, NODE_HIGH_MEMORY et
   MEMORY_LEAK_APPLICATION, et s'executait en `drop_caches` sur un NOEUD).
3. LE NOM NE PROMET QUE CE QUE LA COMMANDE FAIT. Pas de
   « ..._AND_TUNE_MEMORY_LIMITS » si aucune limite n'est ajustee.
4. LES ALIAS SONT EXPLICITES ET FINIS. ALIASES est une table close, testable,
   derivee de l'audit du 2026-08-05. Elle remplace le repli par mots-cles.
5. TOUTE ACTION NON EXECUTABLE EST UNE ESCALADE NOMMEE, jamais un silence.
6. AUCUNE INTERPOLATION HORS {ns} {c} {n}. Les gabarits sont figes ; l'appelant
   valide ns/c/n contre une regex avant rendu. Aucune commande n'est construite
   a partir d'un texte de LLM.

Deploiement : fichier autonome, stdlib uniquement. Copie a cote de bridge.py
(/opt/helios-ssh-bridge/), du collecteur (/opt/helios/) et du service IA
(/opt/pfe-ai-service/).
"""

# --------------------------------------------------------------------------- scopes
SCOPE_WORKLOAD = "workload"   # gabarit consomme {ns} et {c} (deploiement)
SCOPE_NODE = "node"           # gabarit consomme {n} (nom du noeud) ou s'execute en SSH dessus
SCOPE_CLUSTER = "cluster"     # gabarit sans cible variable
SCOPE_VM = "vm"               # hors K8s : hyperviseur
SCOPE_NONE = "none"

# --------------------------------------------------------------------------- executeurs
EXEC_KUBECTL = "KUBECTL"      # kubectl sur un noeud cluster vivant
EXEC_NODE_SSH = "NODE_SSH"    # commande systeme, en SSH sur le noeud concerne
EXEC_WATCHDOG = "WATCHDOG"    # hyperviseur hote (VM eteinte)
EXEC_ESCALATE = "ESCALATE"    # jamais execute : operateur humain
EXEC_NONE = "NONE"            # etat sain : rien a faire

# --------------------------------------------------------------------------- risques
RISK_NONE, RISK_LOW, RISK_MEDIUM, RISK_HIGH, RISK_CRITICAL = (
    "NONE", "LOW", "MEDIUM", "HIGH", "CRITICAL")

# Commandes hote, reprises VERBATIM de bridge.py (2026-08-05) pour garantir
# l'equivalence de comportement. Le `sudo` est ajoute par l'appelant.
_DISK = (
    'set -eu; printf "DISK_BEFORE\n"; df -B1 /; df -i /; '
    'journalctl --vacuum-size=200M >/dev/null 2>&1 || true; '
    'rm -f /tmp/incfill /tmp/fill /root/incfill /root/bigfile 2>/dev/null || true; '
    'printf "DISK_AFTER\n"; df -B1 /; df -i /'
)
_CPU = ('pkill -9 -x yes 2>/dev/null; pkill -9 stress 2>/dev/null; '
        'pkill -9 cpuburn 2>/dev/null; pkill -9 -x dd 2>/dev/null; uptime')
_CPU_QUOTA = r"""set -eu; svc=helios-cpuload.service; test "$(systemctl is-active "$svc")" = active; cg="$(systemctl show "$svc" -p ControlGroup --value)"; test "$cg" = "/system.slice/helios-cpuload.service"; p="/sys/fs/cgroup${{cg}}/cpu.max"; test -f "$p"; lock=/run/helios-cpu-quota.lock; state=/run/helios-cpu-quota-restore; if ! mkdir "$lock" 2>/dev/null; then printf "CPU_QUOTA_LOCKED\n" >&2; exit 1; fi; if [ -e "$state" ]; then rmdir "$lock"; printf "CPU_QUOTA_STATE_EXISTS\n" >&2; exit 1; fi; old="$(cat "$p")" || {{ rmdir "$lock"; exit 1; }}; umask 077; if ! printf "%s\n%s\n" "$p" "$old" > "$state"; then rmdir "$lock"; exit 1; fi; nohup sh -c 'sleep 241; s=/run/helios-cpu-quota-restore; lock=/run/helios-cpu-quota.lock; log=/run/helios-cpu-quota-restore.log; if [ -r "$s" ]; then p=$(sed -n "1p" "$s"); old=$(sed -n "2p" "$s"); if [ "$p" = "/sys/fs/cgroup/system.slice/helios-cpuload.service/cpu.max" ]; then if printf "%s\n" "$old" > "$p"; then if rm -f "$s"; then if rmdir "$lock"; then printf "CPU_QUOTA_RESTORED\n" >> "$log"; else printf "CPU_QUOTA_RESTORE_FAILED lock_cleanup\n" >> "$log"; fi; else printf "CPU_QUOTA_RESTORE_FAILED state_cleanup\n" >> "$log"; fi; else printf "CPU_QUOTA_RESTORE_FAILED write_failed\n" >> "$log"; fi; else printf "CPU_QUOTA_RESTORE_FAILED invalid_target\n" >> "$log"; fi; else printf "CPU_QUOTA_RESTORE_SKIPPED no_state\n" >> "$log"; fi' </dev/null >/dev/null 2>&1 & restore_pid=$!; sleep 1; if ! kill -0 "$restore_pid" 2>/dev/null; then rm -f "$state"; rmdir "$lock" || true; printf "CPU_QUOTA_RESTORE_SCHEDULING_FAILED\n" >&2; exit 1; fi; if ! printf "25000 100000\n" > "$p"; then if printf "%s\n" "$old" > "$p"; then rm -f "$state"; rmdir "$lock" || true; else printf "CPU_QUOTA_APPLY_ROLLBACK_FAILED\n" >&2; fi; printf "CPU_QUOTA_APPLY_FAILED\n" >&2; exit 1; fi; printf "CPU_QUOTA_APPLIED service=%s old=%s new=25000 100000 lease=240s\n" "$svc" "$old""" + '"'


_MEM = ('sync; echo 3 > /proc/sys/vm/drop_caches; pkill -9 stress 2>/dev/null; '
        'pkill -9 cpuburn 2>/dev/null; free -h | head -2')
_TIME = ('timedatectl set-ntp true 2>/dev/null; '
         'systemctl restart systemd-timesyncd 2>/dev/null; chronyc makestep 2>/dev/null; '
         'timedatectl 2>/dev/null | grep -iE "synchro|NTP"')
_INODES = ('journalctl --vacuum-files=3 >/dev/null 2>&1; '
           'find /var/log -type f \\( -name "*.gz" -o -name "*.[0-9]" \\) -delete 2>/dev/null; '
           'find /tmp /var/tmp -type f -mtime +1 -delete 2>/dev/null; '
           'find /var/lib/containerd -type f -name "*.tmp" -delete 2>/dev/null; df -i / | tail -1')


def _a(executor, scope, risk, template, effet, diagnostic=None):
    return {"executor": executor, "scope": scope, "risk": risk,
            "template": template, "effet": effet, "diagnostic": diagnostic}


# ===========================================================================
# ACTIONS CANONIQUES — la totalite de ce que HELIOS peut executer.
# Rien en dehors de cette table ne s'execute. Jamais.
# ===========================================================================
ACTIONS = {

    # ------------------------------------------------------------ charge de travail
    "RESTART_WORKLOAD": _a(
        EXEC_KUBECTL, SCOPE_WORKLOAD, RISK_MEDIUM,
        "kubectl rollout restart deployment/{c} -n {ns}",
        "Redemarre TOUS les pods du deploiement, un par un. Ne cible pas un pod."),

    "ROLLBACK_WORKLOAD": _a(
        EXEC_KUBECTL, SCOPE_WORKLOAD, RISK_MEDIUM,
        "kubectl rollout undo deployment/{c} -n {ns}",
        "Revient au ReplicaSet precedent. N'ajuste AUCUNE limite de ressource."),

    "ROLLBACK_AND_RESTART_WORKLOAD": _a(
        EXEC_KUBECTL, SCOPE_WORKLOAD, RISK_MEDIUM,
        "kubectl rollout undo deployment/{c} -n {ns}; "
        "kubectl rollout restart deployment/{c} -n {ns}",
        "Revient a la revision precedente puis force un redemarrage progressif."),

    "DELETE_PODS": _a(
        EXEC_KUBECTL, SCOPE_WORKLOAD, RISK_MEDIUM,
        "kubectl delete pod -n {ns} -l app={c} --ignore-not-found",
        "Supprime les pods du selecteur app={c} ; le controleur les recree."),

    "FORCE_DELETE_PODS": _a(
        EXEC_KUBECTL, SCOPE_WORKLOAD, RISK_HIGH,
        "kubectl delete pod -n {ns} -l app={c} --force --grace-period=0 --ignore-not-found",
        "Suppression immediate sans arret propre. Perte de donnees en vol possible."),

    "UNSTICK_TERMINATING_PODS": _a(
        EXEC_KUBECTL, SCOPE_WORKLOAD, RISK_HIGH,
        "for p in $(kubectl get pod -n {ns} -l app={c} -o name); do "
        "kubectl patch $p -n {ns} -p '{{\"metadata\":{{\"finalizers\":null}}}}' "
        "--type=merge; done",
        "Retire les finalizers d'un pod bloque en Terminating. Court-circuite un "
        "nettoyage que le controleur n'a pas termine."),

    "SCALE_WORKLOAD_UP": _a(
        EXEC_KUBECTL, SCOPE_WORKLOAD, RISK_LOW,
        "kubectl scale deployment/{c} -n {ns} --replicas=2",
        "Fixe le nombre de repliques a 2. Valeur FIXE, pas un incrementeur."),

    "SCALE_WORKLOAD_DOWN": _a(
        EXEC_KUBECTL, SCOPE_WORKLOAD, RISK_LOW,
        "kubectl scale deployment/{c} -n {ns} --replicas=1",
        "Fixe le nombre de repliques a 1. Valeur FIXE."),

    "INCREASE_WORKLOAD_CPU": _a(
        EXEC_KUBECTL, SCOPE_WORKLOAD, RISK_LOW,
        "kubectl set resources deployment/{c} -n {ns} --limits=cpu=1 --requests=cpu=200m",
        "Fixe limite CPU=1 et requete=200m. Valeurs FIXES, non calculees."),

    "RESTART_STORAGE_PROVISIONER": _a(
        EXEC_KUBECTL, SCOPE_CLUSTER, RISK_LOW,
        "kubectl -n local-path-storage rollout restart deployment/local-path-provisioner",
        "Relance le provisionneur local-path. Cible fixe, independante de l'incident."),

    # ------------------------------------------------------------------- noeud (K8s)
    # {n} = NOM DU NOEUD. Distinct de {c} : confondre les deux cordonnait un
    # deploiement inexistant (bug constate le 2026-08-05 : `kubectl uncordon backend`).
    "CORDON_NODE": _a(
        EXEC_KUBECTL, SCOPE_NODE, RISK_MEDIUM,
        "kubectl cordon {n}",
        "Rend le noeud non ordonnancable. Les pods deja presents CONTINUENT de tourner."),

    "UNCORDON_NODE": _a(
        EXEC_KUBECTL, SCOPE_NODE, RISK_LOW,
        "kubectl uncordon {n}",
        "Remet le noeud dans l'ordonnancement."),

    "DRAIN_NODE": _a(
        EXEC_KUBECTL, SCOPE_NODE, RISK_HIGH,
        "kubectl drain {n} --ignore-daemonsets --delete-emptydir-data --force --timeout=60s",
        "Evacue TOUS les pods du noeud, y compris ceux sans controleur (--force) et "
        "detruit les emptyDir. Perte de donnees locales certaine."),

    # ------------------------------------------------------------- noeud (systeme SSH)
    "HOST_RECLAIM_DISK": _a(
        EXEC_NODE_SSH, SCOPE_NODE, RISK_LOW, _DISK,
        "Purge journaux, logs tournes et fichiers de test. PAS de `crictl rmi --prune` : "
        "cluster air-gap, les images ne sont pas re-telechargeables."),

    "HOST_RECLAIM_INODES": _a(
        EXEC_NODE_SSH, SCOPE_NODE, RISK_LOW, _INODES,
        "Supprime les PETITS fichiers accumules (journaux tournes, /tmp > 1 jour)."),

    "HOST_SERVICE_CPU_QUOTA": _a(
        EXEC_NODE_SSH, SCOPE_NODE, RISK_MEDIUM, _CPU_QUOTA,
        "Limite helios-cpuload.service a 25% d'un coeur pour 240 s et restaure le quota initial."),
    "HOST_RELIEVE_CPU": _a(
        EXEC_NODE_SSH, SCOPE_NODE, RISK_LOW, _CPU,
        "Tue les processus de charge par NOM EXACT (yes, stress, cpuburn, dd). "
        "Ne touche aucun pod ni aucun service."),

    "HOST_RELIEVE_MEMORY": _a(
        EXEC_NODE_SSH, SCOPE_NODE, RISK_LOW, _MEM,
        "Vide le cache de page du NOEUD et tue les generateurs de charge. "
        "N'a AUCUN effet sur une fuite memoire applicative."),

    "HOST_SYNC_TIME": _a(
        EXEC_NODE_SSH, SCOPE_NODE, RISK_LOW, _TIME,
        "Reactive NTP et force un pas d'horloge."),

    "HOST_RESTART_KUBELET": _a(
        EXEC_NODE_SSH, SCOPE_NODE, RISK_MEDIUM, "systemctl restart kubelet",
        "Redemarre le kubelet. Le noeud passe brievement NotReady."),

    "HOST_RESTART_CONTAINERD": _a(
        EXEC_NODE_SSH, SCOPE_NODE, RISK_HIGH,
        "systemctl restart containerd && systemctl restart kubelet",
        "Redemarre le runtime : TOUS les conteneurs du noeud redemarrent."),

    # --------------------------------------------------------------------------- VM
    "RESTART_NODE_VM": _a(
        EXEC_WATCHDOG, SCOPE_VM, RISK_HIGH, None,
        "Rallumage par l'hyperviseur hote. Seule voie si la VM est ETEINTE."),

    # ------------------------------------------------------------------- etat sain
    "NO_ACTION": _a(EXEC_NONE, SCOPE_NONE, RISK_NONE, None,
                    "Aucun incident : rien a faire."),
}


# ===========================================================================
# ESCALADES — non executables PAR CONSTRUCTION.
# `diagnostic` est une commande STRICTEMENT LECTURE SEULE jointe au rapport
# humain : l'escalade doit etre actionnable, pas un cul-de-sac.
# ===========================================================================
ESCALADES = {

    # ---- P0 : la politique reseau ----------------------------------------------
    # `kubectl delete networkpolicy --all` supprime TOUTES les policies du namespace.
    # Aucun signal de la chaine n'identifie la policy fautive : la regle de detection
    # de NETWORK_PARTITION est un simple comptage de lignes « i/o timeout » dans Loki,
    # elle ne porte aucune identite de policy. Une NetworkPolicy est de plus un
    # CONTROLE DE SECURITE : l'echanger contre de la disponibilite est une decision
    # humaine, jamais automatique. Il n'existe donc pas de version sure de cette action.
    "ESCALATE_REMOVE_BLOCKING_NETWORK_POLICY": _a(
        EXEC_ESCALATE, SCOPE_WORKLOAD, RISK_NONE, None,
        "Suppression de NetworkPolicy : decision humaine. Aucune action automatique.",
        diagnostic="kubectl get networkpolicy -n {ns} -o wide"),

    "ESCALATE_ISOLATE_COMPROMISED_NODE": _a(
        EXEC_ESCALATE, SCOPE_NODE, RISK_NONE, None,
        "Isolation d'un noeud sur soupcon de compromission : confirmation humaine "
        "obligatoire avant cordon/drain.",
        diagnostic="kubectl get pods -A -o wide --field-selector spec.nodeName={n}"),

    "ESCALATE_SCALE_COREDNS": _a(
        EXEC_ESCALATE, SCOPE_CLUSTER, RISK_NONE, None,
        "CoreDNS vit dans kube-system, hors du namespace de l'incident : aucun gabarit "
        "existant ne le cible. Executer l'ancien gabarit redimensionnait la mauvaise "
        "charge de travail.",
        diagnostic="kubectl -n kube-system get deployment coredns -o wide"),

    "ESCALATE_DATABASE_FAILOVER": _a(
        EXEC_ESCALATE, SCOPE_WORKLOAD, RISK_NONE, None,
        "Une bascule de base de donnees n'est pas un redemarrage de deploiement. "
        "Aucune action de bascule n'est implementee.",
        diagnostic="kubectl -n {ns} get cluster,pods -l cnpg.io/cluster"),

    # bridge.py:261 mappait cet identifiant sur `systemctl restart kubelet` : un
    # ESCALATE_* qui execute une remediation. La regle est absolue — un identifiant
    # ESCALATE_* n'execute JAMAIS. Si un redemarrage de kubelet est voulu, l'appelant
    # doit demander HOST_RESTART_KUBELET explicitement, sous son vrai nom.
    "ESCALATE_NODE_OUTAGE_AND_REBALANCE": _a(
        EXEC_ESCALATE, SCOPE_NODE, RISK_NONE, None,
        "Panne de noeud : le redemarrage du kubelet est une decision humaine. "
        "L'ancien mappage vers `systemctl restart kubelet` violait le contrat ESCALATE_.",
        diagnostic="kubectl get node {n} -o wide"),

    # DRAIN_NODE_TO_EVACUATE_WORKLOADS / _TO_REDUCE_WORKLOADS ne produisaient AUCUNE
    # commande avant le 2026-08-05 (execute:False silencieux). Les aliaser vers
    # DRAIN_NODE AJOUTERAIT une capacite destructive qui n'existait pas
    # (`--force --delete-emptydir-data` : eviction des pods sans controleur et perte
    # des volumes locaux). Une correction de securite ne doit jamais ajouter une
    # capacite : ils deviennent donc une escalade NOMMEE, a effet operationnel
    # identique (aucune commande) mais desormais explicite et actionnable.
    "ESCALATE_DRAIN_NODE": _a(
        EXEC_ESCALATE, SCOPE_NODE, RISK_NONE, None,
        "Evacuation d'un noeud : `kubectl drain --force --delete-emptydir-data` "
        "detruit les volumes locaux et evince les pods sans controleur. Decision humaine.",
        diagnostic="kubectl get pods -A -o wide --field-selector spec.nodeName={n}"),
}

ACTIONS.update(ESCALADES)


# ===========================================================================
# ALIAS — table CLOSE, derivee de l'audit du 2026-08-05.
# Remplace integralement le repli par mots-cles de bridge.py:284-304.
# Regle : un alias ne mappe QUE vers une action dont l'effet correspond
# reellement au nom. Sinon -> escalade.
# ===========================================================================
ALIASES = {
    # --- rollback (le nom decrit bien un retour arriere) ---
    "ROLLBACK_TO_LAST_HEALTHY_REVISION": "ROLLBACK_WORKLOAD",
    "ROLLBACK_TO_STABLE_VERSION": "ROLLBACK_WORKLOAD",
    "ROLLBACK_TO_UNIFORM_VERSION": "ROLLBACK_WORKLOAD",
    "ROLLBACK_TO_PULLABLE_IMAGE": "ROLLBACK_WORKLOAD",
    "ROLLBACK_TO_VIABLE_RESOURCE_REQUEST": "ROLLBACK_WORKLOAD",
    "ROLLBACK_STUCK_ROLLOUT": "ROLLBACK_WORKLOAD",
    "ROLLBACK_RECENT_SECURITY_CONTEXT_CHANGE": "ROLLBACK_WORKLOAD",
    "ROLLBACK_DEPLOYMENT": "ROLLBACK_WORKLOAD",
    "DEPLOYMENT_ROLLBACK_REQUIRED": "ROLLBACK_WORKLOAD",
    "ROLLBACK_RECENT_CHANGE_AND_RESTART": "ROLLBACK_AND_RESTART_WORKLOAD",
    "ROLLBACK_RECENT_CONFIG_CHANGE_AND_RESTART": "ROLLBACK_AND_RESTART_WORKLOAD",
    "ROLLBACK_RECENT_PROBE_CHANGE_AND_RESTART": "ROLLBACK_AND_RESTART_WORKLOAD",
    "ROLLBACK_LAST_RELEASE_AND_RESTART": "ROLLBACK_AND_RESTART_WORKLOAD",

    # --- redemarrage de charge de travail ---
    "RESTART_SERVICE": "RESTART_WORKLOAD",
    "FORCE_RECREATE_DEPLOYMENT": "RESTART_WORKLOAD",
    "RECREATE_CONFIGMAP": "RESTART_WORKLOAD",
    "RECREATE_SECRET": "RESTART_WORKLOAD",
    "RESTART_POD_TO_DRAIN_CONNECTION_POOL": "RESTART_WORKLOAD",
    "RESTART_POD_TO_RESET_THREAD_POOL": "RESTART_WORKLOAD",
    "RESTART_POD_TO_REBUILD_CACHE": "RESTART_WORKLOAD",
    "RESTART_POD": "RESTART_WORKLOAD",

    # --- suppression de pods ---
    "DELETE_CRASHLOOPING_PODS": "DELETE_PODS",
    "DELETE_CRASHLOOPING_OR_EVICTED_POD": "DELETE_PODS",
    "DELETE_CRASHLOOPING_OR_MALICIOUS_POD": "FORCE_DELETE_PODS",
    "DELETE_MALICIOUS_POD": "FORCE_DELETE_PODS",
    "FORCE_DELETE_POD": "FORCE_DELETE_PODS",
    "DELETE_CRASHLOOPING_OR_STUCK_POD": "UNSTICK_TERMINATING_PODS",
    "POD_STUCK_TERMINATING": "UNSTICK_TERMINATING_PODS",

    # --- capacite ---
    "SCALE_DEPLOYMENT": "SCALE_WORKLOAD_UP",
    "SCALE_UP_AND_REBALANCE_LOAD": "SCALE_WORKLOAD_UP",
    "SCALE_UP_AND_REBALANCE_RESOURCES": "SCALE_WORKLOAD_UP",
    "SCALE_UP_TO_ABSORB_LOAD": "SCALE_WORKLOAD_UP",
    "SCALE_UP_TO_MEET_DEMAND": "SCALE_WORKLOAD_UP",
    "SCALE_UP_TO_RESTORE_THROUGHPUT": "SCALE_WORKLOAD_UP",
    "SCALE_DOWN": "SCALE_WORKLOAD_DOWN",
    "SCALE_DOWN_TO_FITTING_REPLICAS": "SCALE_WORKLOAD_DOWN",
    "INCREASE_CPU": "INCREASE_WORKLOAD_CPU",

    # --- noeud : systeme ---
    "HOST_IMAGE_GC": "HOST_RECLAIM_DISK",
    "HOST_CLEAN_INODES": "HOST_RECLAIM_INODES",
    "RESTART_POD_TO_REBALANCE_LOAD": "HOST_RELIEVE_CPU",     # scope NOEUD, cf. INCOHERENCES
    "RESTART_KUBELET": "HOST_RESTART_KUBELET",
    "RESTART_CONTAINERD": "HOST_RESTART_CONTAINERD",
    "NODE_NOT_READY": "HOST_RESTART_KUBELET",
    "NODE_DISK_FULL": "HOST_RECLAIM_DISK",
    "NODE_DISK_PRESSURE": "HOST_RECLAIM_DISK",
    "NODE_INODES_FULL": "HOST_RECLAIM_INODES",
    "NODE_HIGH_CPU": "HOST_SERVICE_CPU_QUOTA",
    "NODE_HIGH_MEMORY": "HOST_RELIEVE_MEMORY",
    "NODE_CPU_SATURATION": "HOST_SERVICE_CPU_QUOTA",
    "NODE_MEMORY_PRESSURE": "HOST_RELIEVE_MEMORY",
    "NODE_TIME_DRIFT": "HOST_SYNC_TIME",
    "NODE_CORDONED": "UNCORDON_NODE",
    "CORDON_AND_ISOLATE": "CORDON_NODE",

    # --- stockage ---
    "PVC_NOT_BOUND": "RESTART_STORAGE_PROVISIONER",

    # --- VM ---
    "RESTART_VM": "RESTART_NODE_VM",
    "VM_DOWN": "RESTART_NODE_VM",
    "VM_POWERED_OFF": "RESTART_NODE_VM",

    # --- reseau : TOUT converge vers l'escalade (P0) ---
    "REMOVE_BLOCKING_NETWORK_POLICY": "ESCALATE_REMOVE_BLOCKING_NETWORK_POLICY",
    "REMOVE_NETWORK_POLICY": "ESCALATE_REMOVE_BLOCKING_NETWORK_POLICY",
    "FIX_NETWORK_POLICY": "ESCALATE_REMOVE_BLOCKING_NETWORK_POLICY",
    "NETWORK_POLICY_BLOCKING": "ESCALATE_REMOVE_BLOCKING_NETWORK_POLICY",

    # --- noms dont la commande ne tenait pas la promesse -> escalade ---
    "SCALE_UP_COREDNS_REPLICAS": "ESCALATE_SCALE_COREDNS",
    "RESTART_POD_OR_FAILOVER_DATABASE": "ESCALATE_DATABASE_FAILOVER",
    "CORDON_NODE_TO_ISOLATE": "ESCALATE_ISOLATE_COMPROMISED_NODE",
    "DRAIN_NODE_TO_EVACUATE_WORKLOADS": "ESCALATE_DRAIN_NODE",
    "DRAIN_NODE_TO_REDUCE_WORKLOADS": "ESCALATE_DRAIN_NODE",

    # ---- ARBITRAGES DU 2026-08-05 (etape C) : restauration de couverture -------
    # Ces 3 identifiants etaient EXECUTES avant la securisation. Les laisser REJECTED
    # supprimait la remediation de NODE_HIGH_MEMORY et MEMORY_STRESS (2 des 8 classes
    # d'incident du MVP), de POD_OOM_KILLED, HIGH_ERROR_RATE_5XX et INGRESS_5XX_ERRORS.
    # Chaque alias reproduit EXACTEMENT la commande d'avant : aucune capacite ajoutee.
    # Leurs noms restent trompeurs -> cf. NOMS_TROMPEURS, a renommer en etape D.
    #
    # RESTART_POD_TO_RECLAIM_MEMORY executait deja `_MEM` (drop_caches) : c'est une
    # action de scope NOEUD. Le garde de scope du bridge la rejette desormais quand
    # elle arrive avec un namespace applicatif — ce qui corrige le bug ou
    # POD_MEMORY_LEAK et MEMORY_LEAK_APPLICATION vidaient le cache de page du master.
    "RESTART_POD_TO_RECLAIM_MEMORY": "HOST_RELIEVE_MEMORY",
    "ROLLBACK_RECENT_CHANGE_AND_TUNE_MEMORY_LIMITS": "ROLLBACK_WORKLOAD",
    "ROLLBACK_LAST_RELEASE_AND_SHED_TRAFFIC": "ROLLBACK_WORKLOAD",
}


# ===========================================================================
# NOMS TROMPEURS — arbitres et surs, mais le nom promet plus que la commande.
# Dette de nommage a solder en etape D (renommage cote incident_catalog.py +
# collecteur, donc hors du perimetre de l'etape C).
# ===========================================================================
NOMS_TROMPEURS = {
    "RESTART_POD_TO_RECLAIM_MEMORY": (
        "Ne redemarre AUCUN pod : vide le cache de page du noeud et tue les "
        "generateurs de charge. Scope NOEUD malgre le prefixe RESTART_POD_. "
        "Le garde de scope du bridge rejette desormais cet identifiant lorsqu'il "
        "arrive avec un namespace applicatif, ce qui corrige le bug ou "
        "POD_MEMORY_LEAK vidait le cache du master. "
        "Renommage propose (etape D) : HOST_RELIEVE_MEMORY."),
    "ROLLBACK_RECENT_CHANGE_AND_TUNE_MEMORY_LIMITS": (
        "`rollout undo` n'ajuste AUCUNE limite memoire. Le retour arriere corrige "
        "bien un OOM cause par une limite abaissee, mais la seconde moitie du nom "
        "n'est pas implementee. "
        "Renommage propose (etape D) : ROLLBACK_TO_LAST_HEALTHY_REVISION."),
    "ROLLBACK_LAST_RELEASE_AND_SHED_TRAFFIC": (
        "Aucun delestage de trafic n'est implemente ; seul le retour arriere a lieu. "
        "Renommage propose (etape D) : ROLLBACK_TO_LAST_HEALTHY_REVISION."),
}


# ===========================================================================
# INCOHERENCES CONNUES — a arbitrer, PAS a masquer par un alias.
# Chacune est un desaccord reel entre un nom d'action et son effet.
# ===========================================================================
INCOHERENCES = {
    # Resolution 2026-10-16 : NODE_DISK_PRESSURE -> HOST_RECLAIM_DISK; ancienne incoherence archivee dans la sauvegarde.
    "RESTART_POD_AND_CAPTURE_CPU_PROFILE": (
        "Aucun profil CPU n'est capture. Arbitrage : renommer ou escalader."),
    "SCALE_DEPLOYMENT_AND_CAPTURE_CPU_PROFILE": (
        "Aucun profil CPU n'est capture. Arbitrage : renommer ou escalader."),
    "RESTART_POD_AND_SCALE_MEMORY_LIMITS": (
        "Aucune limite memoire n'est ajustee. Arbitrage : renommer ou escalader."),
    "SHIFT_TRAFFIC_AND_SCALE_DEPLOYMENT": (
        "Aucun deplacement de trafic n'est implemente ; seul le scale l'est."),
}


# =========================================================================== API
def resolve(action_id):
    """ACTION_ID (ou alias) -> ACTION_ID canonique, ou None.

    AUCUNE deduction vers une commande. Une entree inconnue rend None :
    l'appelant DOIT rejeter.

    UNIQUE regle de prefixe : un identifiant inconnu commencant par ESCALATE_
    est accepte comme escalade generique. Ce N'EST PAS un repli par mot-cle :
      - il ne produit JAMAIS de commande (executor = ESCALATE, template = None) ;
      - il ne peut donc que RETIRER une capacite, jamais en ajouter ;
      - sans lui, les 66 identifiants ESCALATE_* d'INCIDENT_ACTIONS seraient
        rejetes en bloc et perdraient leur libelle, qui est l'information utile
        transmise a l'operateur.
    La difference avec le repli supprime est exactement celle-ci : `"NETWORK" in a`
    fabriquait une commande destructrice ; `startswith("ESCALATE_")` en interdit une.
    """
    if not action_id or not isinstance(action_id, str):
        return None
    a = action_id.strip().upper()
    if a in ACTIONS:
        return a
    if a in ALIASES:
        return ALIASES[a]
    if a.startswith("ESCALATE_"):
        ACTIONS.setdefault(a, _a(
            EXEC_ESCALATE, SCOPE_NONE, RISK_NONE, None,
            "Escalade nommee : aucune action automatique. Libelle transmis a l'operateur."))
        return a
    return None


def spec_for(action_id):
    """Specification complete, ou None si l'action n'est pas dans la whitelist."""
    canon = resolve(action_id)
    return dict(ACTIONS[canon], action_id=canon) if canon else None


def is_executable(action_id):
    s = spec_for(action_id)
    return bool(s) and s["executor"] in (EXEC_KUBECTL, EXEC_NODE_SSH, EXEC_WATCHDOG)


def render(action_id, ns="", cible="", noeud=""):
    """(canon, executor, commande) ou (None, None, None) si hors whitelist.

    Le rendu n'utilise QUE {ns} {c} {n}. L'appelant a valide ces trois valeurs.
    """
    s = spec_for(action_id)
    if not s:
        return None, None, None
    if s["template"] is None:
        return s["action_id"], s["executor"], None
    return (s["action_id"], s["executor"],
            s["template"].format(ns=ns, c=cible, n=noeud))


def catalogue_ids():
    """Vocabulaire complet accepte : canoniques + alias. Rien d'autre n'existe."""
    return sorted(set(ACTIONS) | set(ALIASES))


# =========================================================================== controles
def verifier():
    """Controles d'integrite du catalogue. Rend la liste des problemes (vide = sain)."""
    p = []
    for src, dst in ALIASES.items():
        if dst not in ACTIONS:
            p.append("alias %s -> %s inexistant" % (src, dst))
        if src in ACTIONS:
            p.append("%s est a la fois canonique et alias" % src)
        # REGLE ABSOLUE : un identifiant ESCALATE_* ne peut pas, meme par alias,
        # aboutir a une commande. C'est ce controle qui manquait le 2026-08-05 et qui
        # a laisse passer ESCALATE_NODE_OUTAGE_AND_REBALANCE -> systemctl restart kubelet.
        if src.startswith("ESCALATE_") and ACTIONS.get(dst, {}).get("executor") != EXEC_ESCALATE:
            p.append("alias %s (ESCALATE_) -> %s qui EXECUTE : contrat viole" % (src, dst))
    for aid, s in ACTIONS.items():
        t = s["template"]
        if s["executor"] == EXEC_ESCALATE and t is not None:
            p.append("%s est une escalade mais porte un gabarit executable" % aid)
        if aid.startswith("ESCALATE_") and s["executor"] != EXEC_ESCALATE:
            p.append("%s porte le prefixe ESCALATE_ mais s'execute" % aid)
        if t and "networkpolicy" in t and "--all" in t:
            p.append("%s supprime toutes les NetworkPolicies (P0)" % aid)
        if t:
            champs = set(__import__("re").findall(r"(?<!\{)\{(\w+)\}", t))
            inconnus = champs - {"ns", "c", "n"}
            if inconnus:
                p.append("%s : placeholders interdits %s" % (aid, sorted(inconnus)))
            if "{n}" in t and s["scope"] != SCOPE_NODE:
                p.append("%s utilise {n} hors du scope noeud" % aid)
            if ("{c}" in t or "{ns}" in t) and s["scope"] == SCOPE_NODE:
                p.append("%s (scope noeud) utilise {c}/{ns}" % aid)
    for aid in INCOHERENCES:
        if aid in ALIASES or aid in ACTIONS:
            p.append("%s est listee comme incoherence NON ARBITREE mais deja resolue" % aid)
    return p


if __name__ == "__main__":
    pb = verifier()
    print("catalogue HELIOS : %d actions canoniques, %d alias, %d incoherences a arbitrer"
          % (len(ACTIONS), len(ALIASES), len(INCOHERENCES)))
    print("controles : " + ("OK" if not pb else "%d PROBLEME(S)" % len(pb)))
    for x in pb:
        print("  -", x)
