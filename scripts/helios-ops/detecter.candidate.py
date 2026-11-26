#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Detecteur HELIOS — tourne SUR LA VM MONITORING (.160), interroge Prometheus,
Loki et Grafana en local, et rend UN incident classe.

Camunda l'appelle par SSH via la passerelle. Toutes les regles ont ete calibrees
pour rendre ZERO serie sur un cluster sain (verifie) : une regle bruyante ferait
declencher Camunda toutes les 60 s pour rien.

Ordre = priorite : une VM tombee prime sur un pod, un motif kubelet precis prime
sur le symptome generique "replicas indisponibles".
"""
import json, os, sys, time, urllib.parse, urllib.request

PROM = "http://localhost:9090"
LOKI = "http://localhost:3100"
GRAFANA = "http://localhost:3000"
SYSTEME = ("kube-system", "kube-public", "kube-node-lease", "cnpg-system",
           "calico-system", "tigera-operator", "falco", "local-path-storage", "monitoring")
# node-exporter donne l'instance en IP ; on la traduit en NOM de VM (que le bridge SSH
# sait joindre) pour que la remediation host s'execute sur la BONNE machine.
IP2NOM = {"192.168.148.153": "k8s-master", "192.168.148.155": "k8s-worker2",
          "192.168.148.158": "k8s-worker1", "192.168.148.160": "monitoring",
          "192.168.148.161": "iacamunda",
          # SESSION9/S9C3 - anomalie C16 : .162 etait ABSENTE de cette table. Le repli
          # IP2NOM.get(ip, ip) rendait alors l IP nue "192.168.148.162" comme cible. La
          # detection et la verification fonctionnaient, mais AUCUNE remediation n etait
          # possible : le Bridge ne connait que des NOMS. Nom retenu "lb2" : le hostname
          # reel est k8s-lb2, et la convention deja en place pour les VM hors-cluster est
          # de retirer le prefixe k8s- (k8s-monitoring -> monitoring, k8s-aiCamunda ->
          # iacamunda). ATTENTION : cette entree doit rester synchronisee avec NOEUDS dans
          # bridge.py - corriger l une sans l autre PRODUIT UN FAUX RESOLVED, car le filtre
          # de cible du Bridge ne reconnaitrait plus la cible emise ici.
          "192.168.148.162": "lb2",
          # SESSION9/S9C4 : deux cibles Prometheus sont scrutees via 127.0.0.1 et produisaient
          # donc la cible "127.0.0.1", absente de NOEUDS - detection possible, remediation
          # impossible, exactement le meme defaut que C16. VERIFIE, PAS SUPPOSE : les deux jobs
          # concernes sont des static_configs de /etc/prometheus/prometheus.yml sur .160
          # (job prometheus -> 127.0.0.1:9090, job kube-state-metrics -> 127.0.0.1:8080), et
          # les deux processus tournent bien SUR .160 (prometheus pid 1044, et
          # /usr/local/bin/kube-state-metrics pid 2039 - binaire natif, PAS un pod du cluster).
          # Le detecteur s execute sur .160 et interroge le Prometheus LOCAL de .160 : toute
          # instance 127.0.0.1 qu il y lit est donc, par construction, sur .160. Le mapping est
          # exact pour cette architecture, ce n est pas un contournement.
          # RESERVE : si Prometheus lui-meme tombe, le detecteur ne peut plus l interroger et
          # s arrete sur monitoringFiable=false. PROMETHEUS_DOWN reste donc auto-aveugle, et
          # cette correction ne change rien a ce point - elle ne beneficie qu a kube-state-metrics.
          "127.0.0.1": "monitoring"}

# DETECTOR-PRIORITY-01 (2026-09-08) — EXCEPTION EXPLICITEMENT BORNEE, PAS UNE REGLE GENERALE.
# Mesure du 2026-09-05 : POD_NOT_READY_PERSISTENT (rang 30) est active 100 % du temps sur
# 7 jours — pfe/postgres-4 est non-Ready depuis au moins le 2026-08-29. Avec un `break` au
# premier match, elle occupait donc le verdict en PERMANENCE et rendait inatteignables les
# 6 regles de rang 31 a 36, dont DEUX classes MVP-25 : NETWORK_PARTITION et
# DEPLOYMENT_UNAVAILABLE. Demontre par replay : le 2026-09-05 a 10:12 UTC, NETWORK_PARTITION
# satisfaisait sa condition (12 > seuil 3) sans jamais etre evaluee.
#
# Une regle "de fond" ne prend le verdict que si elle est SEULE a matcher. Elle reste
# toujours visible dans le champ `matches`, donc aucune information n est perdue.
#
# NE PAS ETENDRE cet ensemble sans justification et test dedies : chaque nom ajoute retire
# une regle du classement statique, qui reste le mecanisme de priorite par defaut.
REGLES_DE_FOND = {
    "POD_NOT_READY_PERSISTENT",
}

# DETECTOR-PRIORITY-02 (2026-11-11) - 2e EXCEPTION, ELLE AUSSI EXPLICITEMENT BORNEE.
# Mesure du 2026-11-11 : le disque racine de la VM monitoring (.160, 19 Go) se tient entre
# 79,5 et 81,2 % (TSDB Prometheus + journaux ; la remise a 65,88 % de HOST-DISK-MONITORING-
# CLEANUP-01 n a pas dure) et franchit le seuil de 80 % de NODE_DISK_FULL en quelques minutes
# (oscillation de +-0,6 point due au compactage Prometheus). NODE_DISK_FULL (rang 9) precede les
# 27 regles de rang 10 a 36 : tant qu elle matche, elle prend le verdict et rend NODE_HIGH_CPU
# (rang 11) inatteignable. Demontre le 2026-11-11 a 21:49 : `type=NODE_DISK_FULL` avec
# `matches=[NODE_DISK_FULL, NODE_HIGH_CPU]` ; le test de charge CPU de l utilisateur (.158,
# 21:35) est reste sans incident ni remediation jusqu au nettoyage du disque. L incident
# NODE_DISK_FULL de monitoring (#3858) est ouvert depuis le 2026-10-23.
#
# Un couple (type, cible) "de fond" ne prend le verdict que s il est SEUL a matcher, ou si sa
# mesure atteint SEUIL_URGENCE_FOND : un disque de supervision a 90 % est une urgence (Prometheus
# ne pourrait bientot plus ecrire) et redevient prioritaire. Il reste toujours visible dans
# `matches`. Les noeuds Kubernetes ne sont PAS concernes : un NODE_DISK_FULL de worker garde sa
# priorite (le kubelet evince des 85 %). Le SEUIL de la regle (80) est INCHANGE : le contrat 80/80
# avec la verification de bridge.py est preserve, ainsi que l ordre des regles.
#
# NE PAS ETENDRE cet ensemble sans justification et test dedies
# (scripts/helios-ops/test_detecter_priorite.py : il fige aussi ce contenu).
CIBLES_DE_FOND = {
    ("NODE_DISK_FULL", "monitoring"),
}
SEUIL_URGENCE_FOND = 90.0


# DETECTOR-PRIORITY-03 (2026-11-12) - ANOMALIE PERSISTANTE = "DE FOND" (generalise PRIORITY-01 et -02).
# Le detecteur n'elit qu'UNE anomalie par cycle : la 1re dans l'ordre des regles. Une condition qui DURE
# (disque de supervision a 80 %, VM eteinte, noeud cordonne, memoire haute, bascules etcd...) occupait donc le
# verdict en PERMANENCE et rendait inatteignables toutes les regles de rang inferieur : un test de charge CPU
# (rang 11) restait sans incident (11 nov. 2026). Chaque correctif statique (-01, -02) ne couvrait qu'UNE cause.
#
# Regle generale : tout couple (type, cible) qui matche SANS INTERRUPTION depuis au moins DUREE_PERSISTANTE_S
# passe "de fond" : il ne prend plus le verdict que s'il est SEUL a matcher (ou si tous les matchs sont de fond :
# comportement historique). Il reste toujours visible dans `matches`. Un incident a deja ete leve a sa 1re
# detection : l'elire encore a chaque cycle n'apporte rien et empeche les anomalies NOUVELLES d'etre traitees.
# Une anomalie NOUVELLE de rang eleve garde sa priorite pendant ses premieres minutes (rien n'est retarde).
#
# Etat = petit fichier JSON PAR UTILISATEUR (/var/tmp/helios-detecteur-etat-<uid>.json), ecriture atomique.
# Absent, illisible, non inscriptible, ou toute erreur => AUCUN etat => election des versions precedentes :
# cette fonction ne peut jamais faire echouer le detecteur. Le format de sortie JSON est INCHANGE.
# Mode `--diag` : rapport lisible (qui matche, depuis quand, qui est elu) a la place du JSON.
#
# NE PAS MODIFIER ces durees sans test (scripts/helios-ops/test_detecter_persistance.py).
DUREE_PERSISTANTE_S = 600     # 10 min de presence continue => "de fond"
INTERRUPTION_MAX_S = 300      # une absence plus courte que 5 min ne remet pas l'anciennete a zero
ETAT_DIR = os.environ.get("HELIOS_DETECTEUR_ETAT_DIR", "/var/tmp")   # "" desactive l'etat


def horloge():
    return time.time()


def cle_persistance(typ, cible):
    return "%s|%s" % (typ, cible)


def _etat_chemin():
    if not ETAT_DIR:
        return None
    try:
        uid = os.getuid()
    except AttributeError:   # pas de getuid (tests sous Windows uniquement)
        uid = 0
    return os.path.join(ETAT_DIR, "helios-detecteur-etat-%d.json" % uid)


def lire_etat():
    """Etat precedent {cle: {"first": t0, "last": t1}} ; {} si absent, illisible ou invalide."""
    chemin = _etat_chemin()
    if not chemin:
        return {}
    try:
        with open(chemin, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def ecrire_etat(etat):
    """Ecriture atomique (fichier temporaire + rename) ; toute erreur est ignoree."""
    chemin = _etat_chemin()
    if not chemin:
        return
    tmp = "%s.%d.tmp" % (chemin, os.getpid())
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(etat, f)
        os.replace(tmp, chemin)
    except Exception:
        try:
            os.remove(tmp)
        except Exception:
            pass


def maj_persistance(etat, cles, maintenant):
    """Met a jour l'etat avec les couples qui matchent MAINTENANT. Fonction PURE (aucune E/S).

    etat : {cle: {"first": t0, "last": t1}} ; cles : iterable de cles ; maintenant : secondes (float).
    Retourne (nouvel etat, {cle: anciennete en secondes}). Un couple vu il y a moins de INTERRUPTION_MAX_S
    garde son debut ; sinon (premiere vue, longue absence, horloge revenue en arriere) il repart de zero."""
    nouveau = {}
    anciennetes = {}
    for k in cles:
        first = maintenant
        ancien = etat.get(k) if isinstance(etat, dict) else None
        if isinstance(ancien, dict):
            try:
                f0, l0 = float(ancien["first"]), float(ancien["last"])
                if 0 <= maintenant - l0 <= INTERRUPTION_MAX_S and f0 <= maintenant:
                    first = f0
            except Exception:
                pass
        nouveau[k] = {"first": first, "last": maintenant}
        anciennetes[k] = maintenant - first
    # les couples qui ne matchent plus sont gardes brievement : une courte interruption ne remet rien a zero
    if isinstance(etat, dict):
        for k, ancien in etat.items():
            if k in nouveau or not isinstance(ancien, dict):
                continue
            try:
                f0, l0 = float(ancien["first"]), float(ancien["last"])
                if 0 <= maintenant - l0 <= INTERRUPTION_MAX_S:
                    nouveau[k] = {"first": f0, "last": l0}
            except Exception:
                continue
    return nouveau, anciennetes


def age_de(ages, typ, info):
    return ages.get(cle_persistance(typ, info.get("cible")), 0.0)


def est_de_fond(typ, info, serie, anciennete=0.0):
    """True si ce match ne doit PAS prendre le verdict quand une autre regle matche.

    Trois cas, rien d autre : une regle de REGLES_DE_FOND ; un couple (type, cible) de CIBLES_DE_FOND dont la
    mesure reste sous SEUIL_URGENCE_FOND ; une anomalie PERSISTANTE (anciennete >= DUREE_PERSISTANTE_S)."""
    if typ in REGLES_DE_FOND:
        return True
    try:
        if anciennete >= DUREE_PERSISTANTE_S:
            return True
    except Exception:
        pass
    if (typ, info.get("cible")) in CIBLES_DE_FOND:
        try:
            return float(serie["value"][1]) < SEUIL_URGENCE_FOND
        except Exception:
            return False  # mesure illisible : comportement historique, le match garde sa priorite
    return False


def duree_lisible(secondes):
    s = int(secondes)
    if s < 120:
        return "%d s" % s
    if s < 7200:
        return "%d min" % (s // 60)
    return "%d h %02d min" % (s // 3600, (s % 3600) // 60)


def diagnostic(trouves, ages, res):
    """Rapport lisible (mode --diag) : qui matche, depuis quand, et qui est elu."""
    noms = [r[0] for r in REGLES]
    lignes = ["DIAGNOSTIC DU DETECTEUR : %d regle(s) en alerte" % len(trouves)]
    if trouves:
        lignes.append("%-4s %-26s %-14s %-11s %s" % ("rang", "type", "cible", "anciennete", "statut"))
    for typ, info, s, nb in trouves:
        age = age_de(ages, typ, info)
        if typ == res.get("type") and info.get("cible") == res.get("cible"):
            statut = "ELU (c'est lui que Camunda traite)"
        elif est_de_fond(typ, info, s, age):
            statut = "DE FOND (persistante ou toleree : ne masque rien)"
        else:
            statut = "MASQUEE par l'elu (rang plus bas)"
        lignes.append("%-4d %-26s %-14s %-11s %s" % (noms.index(typ) + 1, typ, info.get("cible"), duree_lisible(age), statut))
    lignes.append("Verdict envoye a Camunda : type=%s cible=%s" % (res.get("type"), res.get("cible")))
    lignes.append("Persistante = presente sans interruption depuis %s ; etat dans %s" % (
        duree_lisible(DUREE_PERSISTANTE_S), _etat_chemin() or "(desactive)"))
    return "\n".join(lignes)


def http(url, delai=12):
    try:
        with urllib.request.urlopen(url, timeout=delai) as r:
            return json.loads(r.read().decode())
    except Exception:
        return {}


def prom(requete):
    d = http(PROM + "/api/v1/query?query=" + urllib.parse.quote(requete))
    return d.get("data", {}).get("result", []) if d.get("status") == "success" else []


def loki(requete):
    d = http(LOKI + "/loki/api/v1/query?query=" + urllib.parse.quote(requete), 20)
    return d.get("data", {}).get("result", []) if d.get("status") == "success" else []


def deploiement(pod):
    """helios-latency-679b5dc75f-xdnfm -> helios-latency"""
    bouts = pod.split("-")
    return "-".join(bouts[:-2]) if len(bouts) > 2 else pod


def depuis_filename(f):
    """/var/log/containers/<pod>_<namespace>_<conteneur>-<id>.log -> (ns, pod)"""
    base = f.rstrip().split("/")[-1]
    bouts = base.split("_")
    return (bouts[1], bouts[0]) if len(bouts) >= 2 else ("", "")


# ------------------------------------------------------------------ les regles
# (type, requete, source, extracteur) ; ordre = priorite decroissante
REGLES = [
    # ================= CONTROL-PLANE (priorite maximale) =================
    # Ces deux incidents cassent TOUT le cluster en silence : sans leader etcd plus aucune
    # ecriture n'aboutit, et un fsync lent fait expirer les baux de leader du
    # controller-manager/scheduler -> CrashLoopBackOff en boucle, ReplicaSets et Endpoints
    # figes. C'est exactement ce qui est arrive le 31/07 (478 redemarrages).
    # Mesure au repos : fsync p99 = 0,007 a 0,130 s ; tous les membres ont un leader.
    ("ETCD_NO_LEADER", 'etcd_server_has_leader == 0', "prom", "hote"),
    ("ETCD_SLOW_FSYNC", 'histogram_quantile(0.99, sum by(instance,le)(rate(etcd_disk_wal_fsync_duration_seconds_bucket[5m]))) > 0.5', "prom", "hote"),
    # bascules de leader repetees = instabilite reseau ou disque (repos : max 2/h)
    ("ETCD_LEADER_FLAPPING", 'increase(etcd_server_leader_changes_seen_total[1h]) > 5', "prom", "hote"),

    # ================= ETAT DU NOEUD (signaux kubelet) =================
    ("NODE_NOT_READY", 'kube_node_status_condition{condition="Ready",status="true"} == 0', "prom", "noeud"),
    ("NODE_DISK_PRESSURE", 'kube_node_status_condition{condition="DiskPressure",status="true"} == 1', "prom", "noeud"),
    ("NODE_PID_PRESSURE", 'kube_node_status_condition{condition="PIDPressure",status="true"} == 1', "prom", "noeud"),
    ("NODE_NETWORK_UNAVAILABLE", 'kube_node_status_condition{condition="NetworkUnavailable",status="true"} == 1', "prom", "noeud"),
    # noeud sorti de l'ordonnancement (cordon) : souvent un oubli apres maintenance
    ("NODE_CORDONED", 'kube_node_spec_unschedulable == 1', "prom", "noeud"),
    # --- metriques BRUTES des VMs (node-exporter) : detecte la saturation hote, pas seulement pod ---
    # Seuils calibres sur un cluster tres charge (CPU repos ~80-90%, .161 RAM ~90%) : DISK>85 (repos max 76),
    # MEM>92 (voir le bloc HOST-MEMORY-THRESHOLD-IMPLEMENTATION-01 ci-dessous), CPU>96 (repos max ~90). Zero serie au repos = pas de bruit.
    # HOST-DISK-THRESHOLD-IMPLEMENTATION-01 (2026-09-09) : seuil 90 -> 80, decide par
    # HOST-DISK-THRESHOLD-REVIEW-01. A 90 %, la detection tombait DANS la zone de reclamation
    # du kubelet : nodefs, imagefs et containerfs etant le MEME filesystem, l imageGC et
    # l eviction imagefs se declenchent des 85 % d occupation (15 % libres), soit AVANT la
    # detection HELIOS a 90 %. NODE_DISK_PRESSURE (rang 5) masquait alors NODE_DISK_FULL
    # (rang 9), et le kubelet liberait lui-meme l espace : preuve E2E impossible.
    # A 80 % la detection precede de 5 points (1,86 GiB) le premier evenement kubelet, tout
    # en restant 5,99 points au-dessus du maximum au repos sur 7 j des noeuds K8s (74,01 %).
    # Le bruit sur monitoring, qui aurait declenche 47,9 % du temps, a ete leve au prealable
    # par HOST-DISK-MONITORING-CLEANUP-01 (86,26 % -> 65,88 %).
    # Le seuil de VERIFICATION (bridge.py) est change en meme temps : contrat 80/80.
    ("NODE_DISK_FULL", '(1-(node_filesystem_avail_bytes{mountpoint="/",fstype!~"tmpfs"}/node_filesystem_size_bytes{mountpoint="/",fstype!~"tmpfs"}))*100 > 80', "prom", "hote"),
    # HOST-MEMORY-THRESHOLD-IMPLEMENTATION-01 (2026-09-09) : seuil 95 -> 92, decide par
    # HOST-MEMORY-THRESHOLD-REVIEW-01. A 95 %, le declenchement laissait 21.1 MiB avant
    # l eviction dure du kubelet (memory.available<100Mi) : INFERIEUR a la variation
    # memoire mediane du noeud sur 2 min (32.5 MiB sur .153), donc le noeud pouvait
    # atteindre l eviction par simple bruit de fonctionnement. A 92 % la marge passe a
    # 93.7 MiB (2.9x cette variation) tout en restant 4.02 points au-dessus du maximum
    # au repos mesure sur 7 jours (87.98 % sur .161). Le seuil de VERIFICATION
    # (bridge.py) est change en meme temps : le contrat reste 92/92.
    ("NODE_HIGH_MEMORY", '(1-(node_memory_MemAvailable_bytes/node_memory_MemTotal_bytes))*100 > 92', "prom", "hote"),
    # SESSION2/S2C1 (2026-08-14) : fenetre 10m -> 5m. La rampe rate[3m] impose W+3 min de
    # charge continue ; W=10 exigeait 13 min, impraticable. W=5 exige 8 min. Seuil INCHANGE a 90.
    # Mesure au repos post-campagne (2 fenetres calmes) : pire cas min_5m = 77.21 sur .158,
    # soit 12.79 pts de marge ; le passage 10m->5m ne coute que +1.80 a +7.83 pts.
    ("NODE_HIGH_CPU", 'min_over_time((100-(avg by(instance)(rate(node_cpu_seconds_total{mode="idle"}[3m]))*100))[5m:30s]) > 90', "prom", "hote"),
    # Horloge desynchronisee : incident host critique.
    # La comparaison entre heure du noeud et instant du scrape
    # inclut le delai de collecte Prometheus et genere des faux positifs.
    # Utiliser la mesure directe du decalage expose par le noyau :
    # node_timex_offset_seconds.
    # Ne pas conditionner cette regle a node_timex_sync_status :
    # VMware Tools peut maintenir l'horloge sans synchronisation NTP classique.
    # Seuil conserve : 5 secondes.
    # Validation du 07/11/2026 : offset maximal observe de 0.308 s
    # sur les six machines pendant les 24 heures examinees.
    ("NODE_TIME_DRIFT", 'abs(node_timex_offset_seconds) > 5', "prom", "hote"),
    # --- autres saturations hote (seuils calibres le 01/08, valeurs au repos en commentaire) ---
    # Inodes : un disque peut etre "plein" sans etre plein en octets (millions de petits fichiers).
    # Invisible pour NODE_DISK_FULL, d'ou une regle dediee. Repos : 7,7 a 16,1 %.
    ("NODE_INODES_FULL", '(1-(node_filesystem_files_free{mountpoint="/"}/node_filesystem_files{mountpoint="/"}))*100 > 85', "prom", "hote"),
    # Descripteurs de fichiers epuises : plus aucun socket ni fichier ouvrable. Repos : 0 %.
    ("NODE_FD_EXHAUSTED", '(node_filefd_allocated/node_filefd_maximum)*100 > 80', "prom", "hote"),
    # Le noyau a tue un processus faute de RAM. Repos : 0. Tout evenement compte.
    ("NODE_OOM_KILL", 'increase(node_vmstat_oom_kill[5m]) > 0', "prom", "hote"),
    # CPU vole par l'hyperviseur : la VM attend le PC hote. Specifique a un cluster virtualise.
    # Repos : 0 %. Au-dela de 20 %, la VM est bridee par l'hote, pas par sa propre charge.
    ("NODE_CPU_STEAL", 'avg by(instance)(rate(node_cpu_seconds_total{mode="steal"}[5m]))*100 > 20', "prom", "hote"),
    # Disque sature en E/S : cause directe des fsync etcd lents. Repos : 0,8 a 8,4 %.
    ("NODE_DISK_SLOW", 'max by(instance)(rate(node_disk_io_time_seconds_total[5m]))*100 > 80', "prom", "hote"),
    # SESSION8/S8C1 : saturation du run-queue. node_load1 existe sur les 6 VM depuis
    # l'origine et AUCUNE regle ne le lisait (lacune documentee en SESSION 5, 6 et 7).
    # NON SUBSUME PAR NODE_HIGH_CPU - mesure sur 7 j : les 3 noeuds K8s ont passe 8,9 a
    # 10,5 % du temps avec un load/coeur > 4 ALORS QUE le CPU restait sous 90 %, avec des
    # pointes a 22,3 (.153), 22,2 (.155) et 15,0 (.158) load par coeur. La regle CPU, dont
    # le seuil est 90 %, ne pouvait structurellement pas voir ce regime.
    # SEUIL CALIBRE PAR MESURE, pas choisi : dans le regime sain actuel (fenetre 12 h) le
    # taux de declenchement est de 0,00 % sur les 6 VM, le maximum observe est 2,73 load par
    # coeur, et il y a 0 serie au repos. Persistance 5 min alignee sur NODE_HIGH_CPU.
    # RANG VOLONTAIREMENT BAS dans le bloc hote : la saturation est un SYMPTOME ; disque,
    # memoire, CPU, inodes et descripteurs sont des CAUSES plus actionnables et gardent la
    # priorite. Lecon SESSION7/S7C2 : un rang ne se choisit pas a l'intuition.
    ("NODE_LOAD_SATURATION",
     'min_over_time((sum by(instance)(node_load1) / on(instance) '
     'count by(instance)(node_cpu_seconds_total{mode="idle"}))[5m:30s]) > 4', "prom", "hote"),
    # Erreurs materielles reseau (cable, pilote, MTU). Repos : 0/s.
    ("NODE_NETWORK_ERRORS", 'max by(instance)(rate(node_network_receive_errs_total[5m])) > 1', "prom", "hote"),

    # ================= VOLUMES PERSISTANTS =================
    # Un PVC non lie = le pod qui l'attend ne demarrera JAMAIS (base de donnees bloquee).
    # Repos : 0. NB : le remplissage des PVC n'est pas mesurable ici (le provisionneur
    # local-path n'expose pas kubelet_volume_stats_*) ; leur espace est celui du disque du
    # noeud, deja couvert par NODE_DISK_FULL.
    ("PVC_NOT_BOUND", 'kube_persistentvolumeclaim_status_phase{phase=~"Pending|Lost"} == 1', "prom", "pvc"),
    ("POD_IMAGE_PULL_BACKOFF", 'kube_pod_container_status_waiting_reason{reason=~"ImagePullBackOff|ErrImagePull|InvalidImageName"} > 0', "prom", "pod"),
    ("POD_CRASH_LOOP", 'max_over_time(kube_pod_container_status_waiting_reason{reason="CrashLoopBackOff"}[3m]) > 0', "prom", "pod"),
    ("POD_CONFIG_ERROR", 'kube_pod_container_status_waiting_reason{reason=~"CreateContainerConfigError|CreateContainerError"} > 0', "prom", "pod"),
    ("MEMORY_STRESS", 'kube_pod_container_status_last_terminated_reason{reason="OOMKilled"} > 0', "prom", "pod"),
    ("POD_INIT_CONTAINER_FAILURE", 'max_over_time(kube_pod_init_container_status_ready[5m:30s]) == 0', "prom", "pod"),
    # 'phase=Pending' est TROP LARGE : un pod en ContainerCreating (qui telecharge encore
    # son image) a lui aussi la phase Pending. Resultat mesure : une image invalide etait
    # classee POD_PENDING au lieu de POD_IMAGE_PULL_BACKOFF, parce que la detection tombait
    # pendant le telechargement, avant que l'echec ne soit confirme.
    # 'scheduled=false' ne matche QUE les pods que le scheduler n'a pas pu placer.
    ("POD_PENDING_INSUFFICIENT_RESOURCES", 'kube_pod_status_scheduled{condition="false"} > 0', "prom", "pod"),
    ("SERVICE_UNAVAILABLE", 'kube_endpoint_address_available == 0 and on(namespace,endpoint) '
                            'label_replace(kube_deployment_status_replicas_available > 0, "endpoint", "$1", "deployment", "(.*)")', "prom", "endpoint"),
    # ------- SESSION7/S7C1 : deux regles nouvelles, placees ICI a dessein -------
    # SESSION7/S7C2 : rangs INVERSES apres mesure. Placee initialement au-dessus, la regle
    # POD_NOT_READY_PERSISTENT a MASQUE un arret REEL du service IA : pendant tout le test du
    # 2026-08-18 22:14-22:18, min_over_time(up{job="ai-service"}[3m]) == 0 rendait bien 1 serie,
    # mais le detecteur rendait POD_NOT_READY_PERSISTENT sur postgres-4, incident CHRONIQUE de
    # severite warning. Un composant de la plateforme injoignable (severite critical) doit
    # primer sur un pod non-Ready.
    #
    # TARGET_DOWN lit `up`, le signal le plus simple de la plateforme : 24 cibles scrutees
    # sur 11 jobs, et AUCUNE regle ne les lisait. Une seule regle couvre l injoignabilite de
    # Camunda, du service IA, du backend, d etcd, des kubelets et des node-exporters.
    # Mode "hote" : l instance est traduite par IP2NOM, donc la cible est un NOM routable par
    # le Bridge (ex. iacamunda pour 192.168.148.161:8000).
    #
    # SESSION9/S9C2 : min_over_time -> max_over_time. LA VERSION PRECEDENTE NE FOURNISSAIT PAS
    # LA PERSISTANCE QUE SON COMMENTAIRE LUI PRETAIT, et la SESSION 8 l a mesure.
    # La semantique de min_over_time S INVERSE selon le comparateur : avec "> seuil" elle exige
    # que la valeur soit restee au-dessus pendant TOUTE la fenetre (vraie persistance, c est le
    # cas de NODE_HIGH_CPU et NODE_LOAD_SATURATION). Mais avec "== 0" sur une metrique BINAIRE,
    # 0 est le minimum possible : le minimum vaut 0 des qu UN SEUL echantillon vaut 0.
    # L expression signifiait donc "au moins un scrape rate dans les 3 dernieres minutes".
    # MESURES SESSION 8 (regime sain, 12 h) : la regle occupait 5,83 % du temps contre 0,42 %
    # pour max_over_time - 104 minutes-cible de declenchement pour 3 minutes-cible de panne
    # reellement soutenue, soit 86 a 93 % de bruit en severite CRITICAL. Faux positif observe
    # en direct : la cible etcd 192.168.148.162:2381 a rate UN scrape et a produit un incident
    # de 3 minutes sur une cible saine, masquant au passage POD_NOT_READY_PERSISTENT.
    # max_over_time(up[3m]) == 0 exige que TOUS les echantillons de la fenetre valent 0, donc
    # une indisponibilite REELLE d au moins 3 minutes. Cout assume : la latence de detection
    # passe d environ 17 s a environ 3 min. C est le prix d une detection qui ne ment pas.
    # SESSION9/S9C8 : BRIDGE_DOWN. Le Bridge est le Policy Enforcement Point - Camunda ne
    # peut atteindre le detecteur QUE par lui. Le test VM_DOWN de la SESSION 8 a montre que
    # sa perte aveugle toute la chaine EN SILENCE : aucun job Prometheus ne le scrutait, donc
    # TARGET_DOWN ne pouvait pas le voir.
    # POURQUOI CETTE METRIQUE ET PAS UNE CIBLE DIRECTE : le Bridge ecoute UNIQUEMENT sur
    # 127.0.0.1:8099 par conception (durcissement LOT 1). Verifie : un curl sur
    # 192.168.148.161:8099 rend 000 meme depuis .161. On ne defait PAS ce controle de securite
    # pour obtenir une cible. La chaine mise en place en S9C7 est donc :
    #   sonde locale (.161) -> textfile -> node-exporter (.161) -> Prometheus (.160) -> ici.
    # L observateur final est EXTERIEUR au composant observe, ce que la section 29 exige.
    # PERSISTANCE 2 min, et max_over_time et non min_over_time : lecon S9C2 - sur une metrique
    # binaire, min_over_time(...) == 0 est vrai des UN SEUL echantillon a zero. La sonde tourne
    # toutes les 15 s ; 2 min de zeros consecutifs ecartent un timeout curl isole.
    # COUVERTURE COMPLEMENTAIRE, PAS REDONDANTE : si .161 tombe ENTIEREMENT, node-exporter tombe
    # avec elle, cette metrique DISPARAIT (aucune serie, donc cette regle ne declenche pas) et
    # c est TARGET_DOWN qui voit la panne. Les deux regles couvrent deux pannes distinctes.
    ("BRIDGE_DOWN", 'max_over_time(helios_bridge_up[2m]) == 0', "prom", "hote"),
    ("TARGET_DOWN", 'max_over_time(up[3m]) == 0', "prom", "hote"),
    # Elles sont AU-DESSUS des regles Loki (DNS_FAILURE, DATABASE_CONNECTIVITY,
    # NETWORK_PARTITION) parce que celles-ci sont des motifs de logs bruyants
    # (D29 mesuree a 288 declenchements par 24 h) : un composant reellement injoignable
    # ou un pod reellement casse doit primer sur une correlation textuelle.
    #
    # POD_NOT_READY_PERSISTENT comble la lacune decouverte en SESSION5 et toujours ouverte :
    # postgres-4 est Ready=False avec une sonde de demarrage en HTTP 500 et 4 redemarrages,
    # et AUCUNE des 32 regles ne le voyait. La couverture etait centree Deployment et
    # waiting_reason ; un pod Running mais durablement non-Ready, hors Deployment, echappait
    # a tout. Deux garde-fous mesures le 2026-08-18 :
    #   - persistance 15 min : elimine les non-Ready transitoires des redemarrages normaux
    #   - exclusion des pods de Job : SANS elle la requete rend 14 series dont 13 FAUX
    #     POSITIFS (les postgres-backup-* termines restent non-Ready en permanence)
    # Avec les deux, la requete rend EXACTEMENT 1 serie : pfe/postgres-4, l incident reel.
    ("POD_NOT_READY_PERSISTENT",
     'min_over_time(kube_pod_status_ready{condition="false"}[15m]) == 1 '
     'unless on(namespace,pod) kube_pod_owner{owner_kind="Job"}', "prom", "pod"),
    ("DNS_FAILURE", 'topk(1, sum by (filename) (count_over_time({job="k8s-containers", filename!~".*_(kube-system|calico-system|tigera-operator|falco|cnpg-system|local-path-storage|monitoring)_.*"} '
                    '|~ "(?i)(no such host|SERVFAIL|name resolution|bad address|lookup .* on )" [5m]))) > 3', "loki", "fichier"),
    ("DATABASE_CONNECTIVITY", 'topk(1, sum by (filename) (count_over_time({job="k8s-containers", filename!~".*_(kube-system|calico-system|tigera-operator|falco|cnpg-system|local-path-storage|monitoring)_.*"} '
                              '|~ "(?i)(could not connect to server|refused.*5432|FATAL.*database|password authentication failed)" [5m]))) > 3', "loki", "fichier"),
    ("NETWORK_PARTITION", 'topk(1, sum by (filename) (count_over_time({job="k8s-containers", filename!~".*_(kube-system|calico-system|tigera-operator|falco|cnpg-system|local-path-storage|monitoring)_.*"} '
                          '|~ "(?i)(i/o timeout|no route to host|connection timed out|network is unreachable)" [5m]))) > 3', "loki", "fichier"),
    # RESTAUREE par SESSION4/S4C1 (2026-08-18). Le retrait C6 reposait sur une mesure faite
    # alors qu AUCUNE application n exposait http_requests_total : le Service podinfo etait
    # orphelin, son deploiement ayant ete supprime. Le NOM de la metrique etait correct — c est
    # le pipeline kubernetes-pods (file_sd alimente par helios-cibles, port 9898) qui la fournit,
    # avec exactement les labels namespace et app utilises ici.
    # Mesure du 2026-08-18 avec 5xx reels (podinfo /status/500) : la condition rend 0.1689 puis
    # 0.2318, 0.3016, 0.3740 req/s — le seuil 0.1 est franchi largement et sans ambiguite.
    # RESERVE : ne couvre QUE les applications exposant http_requests_total sur le pipeline 9898.
    # Le backend de production utilise le nommage Micrometer (http_server_requests_seconds_count)
    # et n est donc PAS couvert par cette regle.
    ("HIGH_ERROR_RATE", 'sum by (namespace,app) (rate(http_requests_total{status=~"5.."}[3m])) > 0.1', "prom", "app"),
    # RETIREE par DETECTOR-CORRECTION C6 (metrique inexistante / condition impossible) :
    # ("LATENCY_DEGRADATION", 'histogram_quantile(0.95, sum by (namespace,app,le) '
    # '(rate(http_request_duration_seconds_bucket[3m]))) > 1', "prom", "app"),
    # RETIREE par DETECTOR-CORRECTION C7 (metrique inexistante / condition impossible) :
    # ("AUTOSCALING", 'kube_horizontalpodautoscaler_status_desired_replicas > on(namespace,horizontalpodautoscaler) '
    # 'kube_horizontalpodautoscaler_spec_max_replicas', "prom", "hpa"),
    # CPU_STRESS ne concerne QUE les apps : etcd/apiserver/calico consomment normalement > 0.15 coeur
    # sur ce cluster charge (3 masters sur 1 PC). Sans ce filtre, la regle remontait CPU_STRESS etcd
    # en continu (faux positif chronique -> escalade email a chaque cycle, car aucune remediation possible
    # sur un pod systeme). On exclut donc les namespaces systeme directement dans la requete.
    ("CPU_STRESS", 'sum by (namespace,pod) (rate(container_cpu_usage_seconds_total{container!="POD",container!="",namespace!~"kube-system|kube-public|kube-node-lease|cnpg-system|calico-system|tigera-operator|falco|local-path-storage|monitoring"}[3m])) > 0.15', "prom", "pod"),
    # dernier : symptome generique, quand aucun motif precis n'a parle
    ("DEPLOYMENT_UNAVAILABLE", 'kube_deployment_status_replicas_unavailable > 0', "prom", "deploiement"),
]


def extraire(mode, serie):
    m = serie.get("metric", {})
    if mode == "noeud":
        n = m.get("node", "")
        return {"ns": "k8s-nodes", "cible": n, "noeud": n}
    if mode == "hote":
        # node-exporter : instance = "192.168.148.155:9100" -> NOM de la VM (que le
        # bridge SSH sait joindre pour executer la remediation host sur la bonne machine).
        ip = m.get("instance", "").split(":")[0]
        nom = IP2NOM.get(ip, ip)
        return {"ns": "k8s-nodes", "cible": nom, "noeud": nom}
    if mode == "pod":
        pod = m.get("pod", "")
        return {"ns": m.get("namespace", ""), "cible": deploiement(pod), "pod": pod, "noeud": ""}
    if mode == "deploiement":
        return {"ns": m.get("namespace", ""), "cible": m.get("deployment", ""), "noeud": ""}
    if mode == "endpoint":
        return {"ns": m.get("namespace", ""), "cible": m.get("endpoint", ""), "noeud": ""}
    if mode == "app":
        return {"ns": m.get("namespace", ""), "cible": m.get("app", ""), "noeud": ""}
    if mode == "hpa":
        return {"ns": m.get("namespace", ""), "cible": m.get("horizontalpodautoscaler", ""), "noeud": ""}
    if mode == "pvc":
        # kube-state-metrics nomme le PVC dans `persistentvolumeclaim`
        return {"ns": m.get("namespace", ""), "cible": m.get("persistentvolumeclaim", ""), "noeud": ""}
    if mode == "fichier":
        ns, pod = depuis_filename(m.get("filename", ""))
        return {"ns": ns, "cible": deploiement(pod), "pod": pod, "noeud": ""}
    return {"ns": "", "cible": "", "noeud": ""}


def main():
    # Garde : si le monitoring est lui-meme malade, on ne decide sur rien.
    sante = http(GRAFANA + "/api/health", 8)
    fiable = sante.get("database") == "ok" and bool(prom("up"))

    res = {"type": "NO_INCIDENT", "ns": "aucun", "cible": "aucun", "noeud": "",
           "nbAnomalies": 0, "monitoringFiable": fiable, "preuve": "", "severite": "none",
           "grafana": sante.get("version", "?"),
           # DETECTOR-PRIORITY-01 : toutes les regles ayant matche, dans l ordre
           # d evaluation. Purement ADDITIF — `type` reste le champ que lit Camunda.
           "matches": []}
    if not fiable:
        res["preuve"] = "monitoring non fiable (Grafana/Prometheus)"
        print(json.dumps(res))
        return

    nb_logs = 0
    d = loki('sum(count_over_time({job="k8s-containers", filename!~".*_(kube-system|calico-system|tigera-operator|falco|cnpg-system|local-path-storage|monitoring)_.*"} |~ "(?i)(error|exception|panic)" [5m]))')
    if d:
        try:
            nb_logs = int(float(d[0]["value"][1]))
        except Exception:
            pass
    res["nbErreursLogs"] = nb_logs

    # DETECTOR-PRIORITY-01 : on evalue TOUTES les regles et on accumule les matchs, au lieu
    # de s arreter au premier. L ordre statique reste le mecanisme de priorite : l election
    # ci-dessous reprend le premier match, a la seule exception des REGLES_DE_FOND, des CIBLES_DE_FOND et des anomalies persistantes.
    reponses = []
    for typ, requete, source, mode in REGLES:
        series = prom(requete) if source == "prom" else loki(requete)
        if not series:
            continue
        reponses.append((typ, mode, series))

    # DETECTOR-PRIORITY-03 : anciennete (sans interruption) de chaque couple (type, cible) qui matche.
    # Toute erreur => aucune anciennete => election des versions precedentes (jamais d'echec du detecteur).
    ages = {}
    try:
        cles = []
        for typ, mode, series in reponses:
            for s in series:
                cible = extraire(mode, s).get("cible")
                if cible:
                    cles.append(cle_persistance(typ, cible))
        nouvel_etat, ages = maj_persistance(lire_etat(), cles, horloge())
        ecrire_etat(nouvel_etat)
    except Exception:
        ages = {}

    trouves = []
    for typ, mode, series in reponses:
        # on privilegie une cible hors namespace systeme, sinon on prend la premiere.
        # DETECTOR-PRIORITY-02/-03 : a egalite, une serie qui n est PAS "de fond" (ni toleree, ni persistante)
        # passe d abord (un NODE_DISK_FULL de worker avant celui de monitoring) ; pour toute autre regle, la
        # premiere boucle retombe sur le choix historique ci-dessous : rien ne change.
        choisie = None
        for s in series:
            info = extraire(mode, s)
            if info.get("ns") not in SYSTEME and not est_de_fond(typ, info, s, age_de(ages, typ, info)):
                choisie = (s, info)
                break
        if choisie is None:
            for s in series:
                info = extraire(mode, s)
                if info.get("ns") not in SYSTEME:
                    choisie = (s, info)
                    break
        if choisie is None:
            choisie = (series[0], extraire(mode, series[0]))
        s, info = choisie
        if not info.get("cible"):
            continue
        trouves.append((typ, info, s, len(series)))

    res["matches"] = [t for t, _, _, _ in trouves]

    if trouves:
        # 1er match hors "de fond" (REGLES_DE_FOND, CIBLES_DE_FOND, anomalies persistantes) ; si TOUS sont de
        # fond, le plus RECENT d'entre eux (la nouvelle la plus fraiche d'abord ; a anciennete egale, le 1er dans
        # l'ordre des regles : comportement historique, dont le premier cycle apres un demarrage).
        premier = next((m for m in trouves if not est_de_fond(m[0], m[1], m[2], age_de(ages, m[0], m[1]))), None)
        if premier is None:
            premier = min(trouves, key=lambda m: age_de(ages, m[0], m[1]))
        typ, info, s, nb_series = premier
        res.update(info)
        res["type"] = typ
        res["nbAnomalies"] = 1
        # critical = le cluster ou une donnee est en peril immediat ; warning = degradation.
        res["severite"] = "critical" if typ in (
            # control-plane : sans ca, plus rien ne fonctionne
            "ETCD_NO_LEADER", "ETCD_SLOW_FSYNC",
            # noeud hors service ou saturation qui va provoquer des evictions
            "NODE_NOT_READY", "NODE_DISK_PRESSURE", "NODE_DISK_FULL", "NODE_HIGH_MEMORY",
            "NODE_INODES_FULL", "NODE_FD_EXHAUSTED", "NODE_PID_PRESSURE",
            "NODE_NETWORK_UNAVAILABLE", "NODE_TIME_DRIFT", "NODE_OOM_KILL",
            # donnees : un PVC non lie bloque la base
            "PVC_NOT_BOUND", "DATABASE_CONNECTIVITY",
            # SESSION7/S7C1 : un composant injoignable aveugle une partie de la plateforme
            "TARGET_DOWN",
            # SESSION9/S9C8 : sans le Bridge, AUCUNE remediation n est possible et Camunda
            # ne peut meme plus declencher de detection. C est le point d application des
            # decisions : sa perte est un peril immediat pour toute la chaine.
            "BRIDGE_DOWN") else "warning"
        res["preuve"] = "%s = %s (%d serie(s))" % (typ, s.get("value", ["", "?"])[1], nb_series)

    if "--diag" in sys.argv[1:]:
        print(diagnostic(trouves, ages, res))
        return
    print(json.dumps(res))


if __name__ == "__main__":
    main()
