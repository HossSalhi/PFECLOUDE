#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Regenere la liste des pods a scraper pour Prometheus (file_sd).

Pourquoi ce detour : la VM monitoring est HORS du cluster, les IP de pods (Calico)
ne sont pas routables depuis elle, et le paquet Debian de Prometheus 2.45 est
compile SANS kubernetes_sd_configs. On passe donc par le PROXY de l'API server,
avec une liste de cibles rafraichie ici et relue automatiquement par Prometheus.
"""
import json, os, ssl, urllib.request

API = "https://192.168.148.150:6443"
JETON = "/etc/prometheus/scraper-token"
SORTIE = "/etc/prometheus/targets/pods.json"
PORT = 9898                      # port des metriques podinfo
NAMESPACES = ("pfe", "demo-camunda", "demo-ssh", "demo-ssh2", "default")

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE


def api(chemin):
    with open(JETON) as f:
        tok = f.read().strip()
    req = urllib.request.Request(API + chemin, headers={"Authorization": "Bearer " + tok})
    with urllib.request.urlopen(req, context=ctx, timeout=20) as r:
        return json.loads(r.read().decode())


def main():
    cibles = []
    for pod in api("/api/v1/pods").get("items", []):
        meta, spec, statut = pod["metadata"], pod.get("spec", {}), pod.get("status", {})
        ns = meta.get("namespace", "")
        if ns not in NAMESPACES or statut.get("phase") != "Running":
            continue
        ports = [p.get("containerPort") for c in spec.get("containers", []) for p in c.get("ports", []) or []]
        if PORT not in ports:
            continue
        nom = meta["name"]
        cibles.append({
            "targets": ["192.168.148.150:6443"],
            "labels": {
                "__metrics_path__": "/api/v1/namespaces/%s/pods/%s:%d/proxy/metrics" % (ns, nom, PORT),
                "namespace": ns,
                "pod": nom,
                "app": meta.get("labels", {}).get("app", nom),
            },
        })
    os.makedirs(os.path.dirname(SORTIE), exist_ok=True)
    tmp = SORTIE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(cibles, f, indent=1)
    os.replace(tmp, SORTIE)          # remplacement atomique : Prometheus ne lit jamais un fichier partiel
    print("%d cible(s) ecrite(s) dans %s" % (len(cibles), SORTIE))


if __name__ == "__main__":
    main()
