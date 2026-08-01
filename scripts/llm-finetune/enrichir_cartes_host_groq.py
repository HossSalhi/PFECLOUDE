#!/usr/bin/env python3
"""Enrichit le texte des 12 fiches RAG host/etcd/PVC ajoutees le 01/08 via Groq (Llama-3.3-70B).

DIFFERENCE avec l'ancien pipeline de fine-tuning (gen_120_dataset.py -> groq_label.py) : ici Groq
ne recoit AUCUNE donnee inventee et n'invente RIEN — on lui donne le TYPE, la CATEGORIE, le SEUIL
PromQL reel et l'ACTION reellement executee par le bridge (camunda-direct/bridge/bridge.py), et on
lui demande d'ELABORER un texte clair et pedagogique autour de CES FAITS, sans les changer. Sortie
= reecriture du champ "resolution" de incident_catalog.py, jamais un exemple d'entrainement.

Lit GROQ_API_KEY depuis /etc/helios/groq.env (hors repo git).
"""
import json, os, re, time, urllib.request, urllib.error

KEY = os.environ["GROQ_API_KEY"]
MODEL = os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile")
URL = "https://api.groq.com/openai/v1/chat/completions"

# Les FAITS REELS (seuil PromQL exact, action reellement executee) — Groq ne fait qu'ecrire
# autour, il ne peut rien y ajouter de faux puisque tout est deja donne.
FAITS = [
    ("NODE_CORDONED", "Nœuds",
     "kube_node_spec_unschedulable == 1",
     "UNCORDON_NODE (auto-remediable)",
     "le nœud a ete volontairement retire de l'ordonnancement (souvent un oubli apres une maintenance)"),
    ("NODE_NETWORK_UNAVAILABLE", "Nœuds",
     'kube_node_status_condition{condition="NetworkUnavailable",status="true"} == 1',
     "ESCALATE_NODE_NETWORK_OUTAGE (escalade humaine, aucune action automatique)",
     "le kubelet signale son propre reseau (CNI/Calico) indisponible ; un redemarrage aveugle pourrait aggraver une partition reseau"),
    ("NODE_INODES_FULL", "Nœuds",
     'inodes utilises sur "/" > 85%',
     "HOST_CLEAN_INODES (auto-remediable : purge des journaux tournes et fichiers temporaires anciens)",
     "le disque est sature en NOMBRE de fichiers (souvent des millions de petits fichiers issus de journaux ou de caches), pas en octets : df -h peut sembler sain alors que df -i est presque plein"),
    ("NODE_FD_EXHAUSTED", "Nœuds",
     "descripteurs de fichiers alloues / maximum > 80%",
     "ESCALATE_NODE_FD_EXHAUSTION (escalade humaine)",
     "plus aucun socket ni fichier ne peut etre ouvert sur ce nœud ; identifier le processus responsable (fuite probable) avant d'agir"),
    ("NODE_OOM_KILL", "Nœuds",
     "increase(compteur OOM-killer du noyau[5min]) > 0",
     "ESCALATE_NODE_OOM_EVENT (escalade humaine)",
     "le noyau Linux a deja tue un processus faute de memoire disponible ; c'est un evenement PASSE, pas un etat a corriger, il faut identifier quel processus a ete vise"),
    ("NODE_CPU_STEAL", "Nœuds",
     "temps CPU vole par l'hyperviseur > 20%",
     "ESCALATE_HYPERVISOR_CONTENTION (escalade humaine, aucune action interne ne peut aider)",
     "la VM est prete a executer mais attend que le PC hote VMware lui alloue du temps CPU ; la cause est une sur-souscription de l'hote, exterieure a la VM elle-meme"),
    ("NODE_DISK_SLOW", "Nœuds",
     "temps d'entrees/sorties disque actif > 80%",
     "ESCALATE_NODE_DISK_LATENCY (escalade humaine)",
     "le disque du nœud est sature en entrees/sorties ; c'est une cause frequente de latence etcd en cascade sur ce meme nœud"),
    ("NODE_NETWORK_ERRORS", "Nœuds",
     "taux d'erreurs de reception reseau > 1/s",
     "ESCALATE_NODE_NIC_ERRORS (escalade humaine)",
     "des erreurs materielles apparaissent sur l'interface reseau du nœud (cable, pilote ou MTU mal configure) ; un diagnostic materiel est necessaire, aucune correction logicielle n'est sure"),
    ("ETCD_NO_LEADER", "ControlPlane",
     "etcd_server_has_leader == 0",
     "ESCALATE_ETCD_QUORUM_LOSS (escalade humaine, JAMAIS d'action automatique)",
     "le quorum etcd est perdu (majorite des membres indisponible) : plus AUCUNE ecriture n'aboutit dans le cluster (kubectl apply, scale, rollout echouent tous) tant que le quorum n'est pas restaure"),
    ("ETCD_SLOW_FSYNC", "ControlPlane",
     "latence d'ecriture disque etcd (p99) > 0,5 seconde",
     "ESCALATE_ETCD_DISK_LATENCY (escalade humaine)",
     "un disque etcd lent fait expirer les baux de leader du controller-manager et du scheduler, provoquant des CrashLoopBackOff en cascade sur tout le cluster (deja observe en conditions reelles : 478 redemarrages avant correction du delai de bail)"),
    ("ETCD_LEADER_FLAPPING", "ControlPlane",
     "plus de 5 changements de leader etcd en 1 heure",
     "ESCALATE_ETCD_INSTABILITY (escalade humaine)",
     "le leader etcd change anormalement souvent, signe d'une instabilite reseau ou disque entre les membres du cluster"),
    ("PVC_NOT_BOUND", "Stockage",
     'statut du PersistentVolumeClaim = "Pending" ou "Lost"',
     "RESTART_STORAGE_PROVISIONER (auto-remediable : redemarrage du provisionneur local-path-provisioner)",
     "le pod qui attend ce volume persistant ne demarrera JAMAIS tant que le PVC n'est pas lie ; le provisionneur de stockage est probablement bloque"),
]

SYS = (
    "Tu es un expert SRE Kubernetes qui redige des fiches de connaissance courtes et precises pour "
    "une base RAG. On te donne le TYPE d'incident, sa CATEGORIE, le SEUIL de detection REEL et "
    "l'ACTION REELLEMENT EXECUTEE par le systeme (a ne JAMAIS changer ni inventer). Tu recois aussi un "
    "fait technique de contexte. Ecris UNE SEULE phrase claire en francais (200 a 320 caracteres) qui "
    "explique le symptome, la cause et comment l'ACTION donnee resout le probleme — en te basant "
    "UNIQUEMENT sur les faits fournis, sans en ajouter. Reponds en JSON strict : "
    '{"resolution": "..."}'
)


def enrichir(itype, cat, seuil, action, fait):
    user = (
        f"TYPE: {itype}\nCATEGORIE: {cat}\nSEUIL DE DETECTION: {seuil}\n"
        f"ACTION REELLEMENT EXECUTEE (ne pas changer): {action}\nFAIT TECHNIQUE: {fait}"
    )
    body = json.dumps({
        "model": MODEL, "temperature": 0.3, "max_tokens": 180,
        "response_format": {"type": "json_object"},
        "messages": [{"role": "system", "content": SYS}, {"role": "user", "content": user}],
    }).encode()
    req = urllib.request.Request(URL, data=body, headers={
        "Authorization": "Bearer " + KEY, "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0"})
    for tentative in range(10):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                d = json.loads(r.read())
            obj = json.loads(d["choices"][0]["message"]["content"])
            return re.sub(r"\s+", " ", str(obj.get("resolution", ""))).strip()
        except urllib.error.HTTPError as e:
            if e.code in (429, 503):
                time.sleep(2); continue
            raise
    raise RuntimeError("echec apres 10 tentatives pour %s" % itype)


def main():
    resultats = {}
    for itype, cat, seuil, action, fait in FAITS:
        texte = enrichir(itype, cat, seuil, action, fait)
        resultats[itype] = texte
        print("%-26s -> %s" % (itype, texte))
        time.sleep(0.5)  # doux sur le quota gratuit
    with open("/tmp/cartes_enrichies.json", "w", encoding="utf-8") as f:
        json.dump(resultats, f, ensure_ascii=False, indent=2)
    print("\n%d fiches ecrites dans /tmp/cartes_enrichies.json" % len(resultats))


if __name__ == "__main__":
    main()
