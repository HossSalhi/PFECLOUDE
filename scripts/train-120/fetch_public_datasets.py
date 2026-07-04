#!/usr/bin/env python3
"""Augmente le corpus SFT avec de VRAIES données publiques K8s (HuggingFace datasets-server API).

Aucune lib à installer (urllib stdlib). Le LLM raisonne sur du TEXTE -> pas de mur de transfert de
schéma (contrairement au ML). On tire des lignes réelles et on les convertit au format chat SFT,
puis on les ajoute à corpus_120_sft.jsonl.

Datasets :
  - ComponentSoft/k8s-kubectl-cot-20k  : question -> raisonnement chaîné -> commande kubectl
  - genaidevops/kubernetes-stackoverflow-questions : Q/R de dépannage K8s réelles
Usage : python fetch_public_datasets.py [n_par_dataset]   (défaut 300)
"""
import json, os, sys, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "corpus_120_sft.jsonl")
API = "https://datasets-server.huggingface.co/rows"
SYSTEM = ("Tu es le cerveau décisionnel de HELIOS (AIOps Kubernetes). Tu raisonnes puis tu proposes "
          "l'action kubectl/la résolution adaptée au contexte.")

def rows(dataset, split="train", n=300, config="default"):
    got, off = [], 0
    while len(got) < n:
        length = min(100, n - len(got))
        url = f"{API}?dataset={dataset}&config={config}&split={split}&offset={off}&length={length}"
        try:
            with urllib.request.urlopen(url, timeout=20) as r:
                data = json.load(r)
        except Exception as e:
            print(f"  [{dataset}] arrêt à {len(got)} lignes ({e})"); break
        batch = data.get("rows", [])
        if not batch: break
        got += [b["row"] for b in batch]; off += length
    return got[:n]

def to_chat(user, assistant):
    return {"messages": [{"role": "system", "content": SYSTEM},
                         {"role": "user", "content": user},
                         {"role": "assistant", "content": assistant}]}

def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 300
    added = 0
    with open(OUT, "a", encoding="utf-8") as f:
        # 1) kubectl chain-of-thought
        for r in rows("ComponentSoft/k8s-kubectl-cot-20k", n=n):
            q = r.get("question") or r.get("objective")
            cot = r.get("chain_of_thought", ""); cmd = r.get("command", "")
            if not q or not cmd: continue
            f.write(json.dumps(to_chat(q, f"Raisonnement : {cot}\nAction : {cmd}"), ensure_ascii=False) + "\n")
            added += 1
        # 2) StackOverflow K8s troubleshooting
        for r in rows("genaidevops/kubernetes-stackoverflow-questions", n=n):
            q = r.get("question") or r.get("title") or r.get("Question")
            a = r.get("answer") or r.get("Answer") or r.get("accepted_answer")
            if not q or not a: continue
            f.write(json.dumps(to_chat(str(q)[:1200], str(a)[:1600]), ensure_ascii=False) + "\n")
            added += 1
    print(f"ajoutées : {added} lignes réelles publiques -> {OUT}")
    total = sum(1 for _ in open(OUT, encoding="utf-8"))
    print(f"total corpus : {total} exemples SFT")

if __name__ == "__main__":
    main()
