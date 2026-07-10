"""Test rapide du RAG HELIOS : construit l'index des 120 cartes et affiche le top-3
pour quelques incidents types. Runnable localement (repli haché) ou sur .161 (MiniLM).

    cd backend/backend/ai-service
    python scripts/test_rag.py
"""
from __future__ import annotations
import os, sys

# rendre le package `app` importable depuis ai-service/
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from app.services.rag_retriever import RagRetriever  # noqa: E402

QUERIES = [
    "payment pod OOMKilled, memory keeps growing until the container dies",
    "checkout CrashLoopBackOff right after a new deploy, panic loading config",
    "new image cannot be pulled, ImagePullBackOff on the replica",
    "etcd lost quorum, cluster is read-only",
    "coredns not resolving, dns lookups time out",
    "node NotReady, VM unreachable, pods stuck",
    "PVC stays Pending, no volume bound",
    "5xx errors spike after the last release",
]

def main():
    rag = RagRetriever().build()
    print(f"Index prêt : {len(rag._cards)} cartes | encodeur = "
          f"{'MiniLM' if rag._encoder.loaded else 'repli haché'}\n")
    for q in QUERIES:
        print(f"### {q}")
        for c in rag.retrieve(q, k=3):
            print(f"   {c['score']:.3f}  {c['type']:<34} action={c['action']}")
        print()

if __name__ == "__main__":
    main()
