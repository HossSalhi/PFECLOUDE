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
    ("NODE_NOT_READY", 'kube_node_status_condition{condition="Ready",status="true"} == 0', "prom", "noeud"),
    ("NODE_DISK_PRESSURE", 'kube_node_status_condition{condition="DiskPressure",status="true"} == 1', "prom", "noeud"),
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
    ("CPU_STRESS", 'sum by (namespace,pod) (rate(container_cpu_usage_seconds_total{container!="POD",container!=""}[3m])) > 0.15', "prom", "pod"),
    # dernier : symptome generique, quand aucun motif precis n'a parle
    ("DEPLOYMENT_UNAVAILABLE", 'kube_deployment_status_replicas_unavailable > 0', "prom", "deploiement"),
]


def extraire(mode, serie):
    m = serie.get("metric", {})
    if mode == "noeud":
        n = m.get("node", "")
        return {"ns": "k8s-nodes", "cible": n, "noeud": n}
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
        res["severite"] = "critical" if typ in ("NODE_NOT_READY", "NODE_DISK_PRESSURE",
                                                "DATABASE_CONNECTIVITY") else "warning"
        res["preuve"] = "%s = %s (%d serie(s))" % (typ, s.get("value", ["", "?"])[1], len(series))
        break

    print(json.dumps(res))


if __name__ == "__main__":
    main()
