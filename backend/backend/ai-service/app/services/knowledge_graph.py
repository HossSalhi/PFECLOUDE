"""
HELIOS — Knowledge Graph des composants Kubernetes.

Rôle : représenter les RELATIONS entre ressources K8s (pod -> deployment -> node,
service -> endpoints, pvc -> pv -> storageclass, etc.) et, pour un type d'incident
donné, fournir le SOUS-GRAPHE pertinent (composant touché + voisins + relations)
comme contexte enrichi injecté dans le prompt du LLM.

Complète le RAG (qui apporte la CARTE catalogue) avec la STRUCTURE : le LLM sait
non seulement "quoi faire" mais "sur quoi agir et quelles dépendances vérifier".

Design volontairement léger (contrainte .161 : CPU, air-gap) : graphe statique en
mémoire, aucune dépendance externe (pas de Neo4j). Purement additif : jamais bloquant.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# ── Le graphe : chaque KIND K8s -> ses relations sortantes (relation, kind_cible) ──
# Modélise la topologie réelle d'un cluster : ownership, exposition, ordonnancement, stockage.
_EDGES: dict[str, list[tuple[str, str]]] = {
    "Pod":            [("owned_by", "ReplicaSet"), ("scheduled_on", "Node"),
                       ("mounts", "PersistentVolumeClaim"), ("uses", "ConfigMap"),
                       ("uses", "Secret"), ("selected_by", "Service")],
    "ReplicaSet":     [("owned_by", "Deployment")],
    "Deployment":     [("exposed_by", "Service"), ("scaled_by", "HorizontalPodAutoscaler")],
    "Service":        [("has", "Endpoints"), ("routed_by", "Ingress"),
                       ("resolved_by", "CoreDNS")],
    "Ingress":        [("fronted_by", "IngressController")],
    "PersistentVolumeClaim": [("bound_to", "PersistentVolume")],
    "PersistentVolume":      [("provisioned_by", "StorageClass"), ("served_by", "CSIDriver")],
    "Node":           [("runs", "Kubelet"), ("runs", "Containerd"),
                       ("member_of", "Cluster")],
    "Cluster":        [("controlled_by", "APIServer"), ("stores_state_in", "etcd"),
                       ("schedules_via", "Scheduler"), ("reconciles_via", "ControllerManager")],
    "APIServer":      [("backed_by", "etcd")],
    "HorizontalPodAutoscaler": [("reads", "MetricsServer")],
    "Database":       [("replicates_to", "DatabaseReplica"), ("reached_via", "Service")],
    "CoreDNS":        [("runs_as", "Deployment"), ("resolves_for", "Service")],
    "etcd":           [("member_of", "Cluster"), ("holds_state_for", "APIServer")],
    "Scheduler":      [("part_of", "Cluster"), ("reads", "APIServer")],
    "ControllerManager": [("part_of", "Cluster"), ("reconciles_via", "APIServer")],
    "Kubelet":        [("reports_to", "APIServer"), ("manages", "Containerd")],
    "Containerd":     [("runs_on", "Node")],
    "IngressController": [("runs_as", "Deployment")],
}

# ── Type d'incident (préfixe) -> KIND principalement touché ─────────────────────
# On mappe par préfixe pour couvrir les 120 types sans les lister un à un.
_PREFIX_KIND: list[tuple[str, str]] = [
    ("POD_", "Pod"), ("DEPLOYMENT_", "Deployment"), ("HPA_", "HorizontalPodAutoscaler"),
    ("REPLICA_", "Deployment"),
    ("SERVICE_", "Service"), ("INGRESS_", "Ingress"), ("LOAD_BALANCER_", "Service"),
    ("TCP_", "Service"), ("NETWORK_", "Node"), ("DNS_", "CoreDNS"),
    ("NODE_", "Node"),
    ("API_SERVER_", "APIServer"), ("ETCD_", "etcd"), ("CONTROLLER_", "ControllerManager"),
    ("SCHEDULER_", "Scheduler"), ("CERTIFICATE_", "Cluster"), ("LEADER_", "Cluster"),
    ("ADMISSION_", "APIServer"),
    ("PVC_", "PersistentVolumeClaim"), ("PV_", "PersistentVolume"),
    ("STORAGE_", "StorageClass"), ("VOLUME_", "PersistentVolumeClaim"),
    ("READ_ONLY_", "PersistentVolume"), ("CSI_", "CSIDriver"), ("SNAPSHOT_", "PersistentVolume"),
    ("DATABASE_", "Database"), ("DB_", "Database"),
    ("LATENCY_", "Service"), ("HIGH_", "Service"), ("REQUEST_", "Service"),
    ("THROUGHPUT_", "Service"), ("TRAFFIC_", "Service"), ("MEMORY_LEAK_", "Pod"),
    ("THREAD_", "Pod"), ("CACHE_", "Service"),
]


class KnowledgeGraph:
    """Graphe statique des composants K8s + extraction de sous-graphe par incident."""

    def __init__(self) -> None:
        self._edges = _EDGES

    def kind_for(self, incident_type: str) -> str | None:
        """KIND K8s principalement concerné par ce type d'incident."""
        t = (incident_type or "").upper()
        for prefix, kind in _PREFIX_KIND:
            if t.startswith(prefix):
                return kind
        return None

    def neighbors(self, kind: str, depth: int = 2) -> list[tuple[str, str, str]]:
        """Relations atteignables depuis `kind` jusqu'à `depth` sauts : (src, relation, dst)."""
        out: list[tuple[str, str, str]] = []
        seen = {kind}
        frontier = [kind]
        for _ in range(max(1, depth)):
            nxt = []
            for src in frontier:
                for rel, dst in self._edges.get(src, []):
                    out.append((src, rel, dst))
                    if dst not in seen:
                        seen.add(dst); nxt.append(dst)
            frontier = nxt
            if not frontier:
                break
        return out

    def context_for(self, incident_type: str, depth: int = 2) -> str:
        """Bloc texte du sous-graphe pertinent, à injecter dans le prompt LLM.
        Vide si le type n'est pas mappé (purement additif, jamais bloquant)."""
        kind = self.kind_for(incident_type)
        if not kind:
            return ""
        rels = self.neighbors(kind, depth=depth)
        if not rels:
            return ""
        lines = [f"Affected Kubernetes component: {kind}. Related components to verify:"]
        for src, rel, dst in rels[:8]:
            lines.append(f"- {src} --{rel}--> {dst}")
        return "\n".join(lines)
