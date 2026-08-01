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
import json, sys, urllib.parse, urllib.request

PROM = "http://localhost:9090"
LOKI = "http://localhost:3100"
GRAFANA = "http://localhost:3000"
SYSTEME = ("kube-system", "kube-public", "kube-node-lease", "cnpg-system",
           "calico-system", "tigera-operator", "falco", "local-path-storage", "monitoring")
# node-exporter donne l'instance en IP ; on la traduit en NOM de VM (que le bridge SSH
# sait joindre) pour que la remediation host s'execute sur la BONNE machine.
IP2NOM = {"192.168.148.153": "k8s-master", "192.168.148.155": "k8s-worker2",
          "192.168.148.158": "k8s-worker1", "192.168.148.160": "monitoring",
          "192.168.148.161": "iacamunda"}


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
    # MEM>95 (repos max 90.4 sur .161), CPU>96 (repos max ~90). Zero serie au repos = pas de bruit.
    ("NODE_DISK_FULL", '(1-(node_filesystem_avail_bytes{mountpoint="/",fstype!~"tmpfs"}/node_filesystem_size_bytes{mountpoint="/",fstype!~"tmpfs"}))*100 > 85', "prom", "hote"),
    ("NODE_HIGH_MEMORY", '(1-(node_memory_MemAvailable_bytes/node_memory_MemTotal_bytes))*100 > 95', "prom", "hote"),
    ("NODE_HIGH_CPU", '(100-(avg by(instance)(rate(node_cpu_seconds_total{mode="idle"}[3m]))*100)) > 96', "prom", "hote"),
    # Horloge desynchronisee : incident host CRITIQUE (casse etcd et les certificats TLS
    # silencieusement). On mesure la DERIVE REELLE, pas le drapeau NTP du noyau :
    # `node_timex_sync_status` reste a 0 en air-gap (aucun serveur NTP joignable, le DNS ne
    # resout meme pas ntp.ubuntu.com) alors que les horloges sont justes — elles sont tenues
    # par open-vm-tools depuis l'hote. Ce drapeau donnait donc un FAUX POSITIF permanent sur
    # les 6 VMs. La formule ci-dessous compare l'heure annoncee par le noeud a l'horodatage
    # du scrape Prometheus : c'est un ecart mesure, pas une supposition. Au repos : ~0,05 s.
    # Seuil 5 s : etcd tolere ~1 s, au-dela de 5 s les baux et les certificats cassent.
    ("NODE_TIME_DRIFT", 'abs(node_time_seconds - timestamp(node_time_seconds)) > 5', "prom", "hote"),
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
    # Erreurs materielles reseau (cable, pilote, MTU). Repos : 0/s.
    ("NODE_NETWORK_ERRORS", 'max by(instance)(rate(node_network_receive_errs_total[5m])) > 1', "prom", "hote"),

    # ================= VOLUMES PERSISTANTS =================
    # Un PVC non lie = le pod qui l'attend ne demarrera JAMAIS (base de donnees bloquee).
    # Repos : 0. NB : le remplissage des PVC n'est pas mesurable ici (le provisionneur
    # local-path n'expose pas kubelet_volume_stats_*) ; leur espace est celui du disque du
    # noeud, deja couvert par NODE_DISK_FULL.
    ("PVC_NOT_BOUND", 'kube_persistentvolumeclaim_status_phase{phase=~"Pending|Lost"} == 1', "prom", "pvc"),
    ("POD_IMAGE_PULL_BACKOFF", 'kube_pod_container_status_waiting_reason{reason=~"ImagePullBackOff|ErrImagePull|InvalidImageName"} > 0', "prom", "pod"),
    ("POD_CRASH_LOOP", 'kube_pod_container_status_waiting_reason{reason="CrashLoopBackOff"} > 0', "prom", "pod"),
    ("POD_CONFIG_ERROR", 'kube_pod_container_status_waiting_reason{reason=~"CreateContainerConfigError|CreateContainerError"} > 0', "prom", "pod"),
    ("MEMORY_STRESS", 'kube_pod_container_status_last_terminated_reason{reason="OOMKilled"} > 0', "prom", "pod"),
    ("POD_INIT_CONTAINER_FAILURE", 'kube_pod_init_container_status_ready == 0', "prom", "pod"),
    # 'phase=Pending' est TROP LARGE : un pod en ContainerCreating (qui telecharge encore
    # son image) a lui aussi la phase Pending. Resultat mesure : une image invalide etait
    # classee POD_PENDING au lieu de POD_IMAGE_PULL_BACKOFF, parce que la detection tombait
    # pendant le telechargement, avant que l'echec ne soit confirme.
    # 'scheduled=false' ne matche QUE les pods que le scheduler n'a pas pu placer.
    ("POD_PENDING_INSUFFICIENT_RESOURCES", 'kube_pod_status_scheduled{condition="false"} > 0', "prom", "pod"),
    ("SERVICE_UNAVAILABLE", 'kube_endpoint_address_available == 0 and on(namespace,endpoint) '
                            'label_replace(kube_deployment_status_replicas_available > 0, "endpoint", "$1", "deployment", "(.*)")', "prom", "endpoint"),
    ("DNS_FAILURE", 'topk(1, sum by (filename) (count_over_time({job="k8s-containers", filename!~".*_(kube-system|calico-system|tigera-operator|falco|cnpg-system|local-path-storage|monitoring)_.*"} '
                    '|~ "(?i)(no such host|SERVFAIL|name resolution|bad address|lookup .* on )" [5m]))) > 3', "loki", "fichier"),
    ("DATABASE_CONNECTIVITY", 'topk(1, sum by (filename) (count_over_time({job="k8s-containers", filename!~".*_(kube-system|calico-system|tigera-operator|falco|cnpg-system|local-path-storage|monitoring)_.*"} '
                              '|~ "(?i)(could not connect to server|refused.*5432|FATAL.*database|password authentication failed)" [5m]))) > 3', "loki", "fichier"),
    ("NETWORK_PARTITION", 'topk(1, sum by (filename) (count_over_time({job="k8s-containers", filename!~".*_(kube-system|calico-system|tigera-operator|falco|cnpg-system|local-path-storage|monitoring)_.*"} '
                          '|~ "(?i)(i/o timeout|no route to host|connection timed out|network is unreachable)" [5m]))) > 3', "loki", "fichier"),
    ("HIGH_ERROR_RATE", 'sum by (namespace,app) (rate(http_requests_total{status=~"5.."}[3m])) > 0.1', "prom", "app"),
    ("LATENCY_DEGRADATION", 'histogram_quantile(0.95, sum by (namespace,app,le) '
                            '(rate(http_request_duration_seconds_bucket[3m]))) > 1', "prom", "app"),
    ("AUTOSCALING", 'kube_horizontalpodautoscaler_status_desired_replicas > on(namespace,horizontalpodautoscaler) '
                    'kube_horizontalpodautoscaler_spec_max_replicas', "prom", "hpa"),
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
           "grafana": sante.get("version", "?")}
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

    for typ, requete, source, mode in REGLES:
        series = prom(requete) if source == "prom" else loki(requete)
        if not series:
            continue
        # on privilegie une cible hors namespace systeme, sinon on prend la premiere
        choisie = None
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
            "PVC_NOT_BOUND", "DATABASE_CONNECTIVITY") else "warning"
        res["preuve"] = "%s = %s (%d serie(s))" % (typ, s.get("value", ["", "?"])[1], len(series))
        break

    print(json.dumps(res))


if __name__ == "__main__":
    main()
