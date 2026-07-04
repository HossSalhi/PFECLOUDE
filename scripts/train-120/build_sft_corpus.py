#!/usr/bin/env python3
"""Construit le CORPUS DE FINE-TUNING (SFT) des 120 incidents pour le LLM de la stack HELIOS.

Source de vérité = le catalogue HELIOS des 120 (backend .../models/incident_catalog.py) :
type -> catégorie, résolution professionnelle, action exécutable spécifique. On en dérive des
exemples instruction->réponse au format CHAT (messages), dans le cadre « l'IA décide dynamiquement,
Camunda exécute » (raisonnement + stratégie choisie + action + orchestration + vérification).

À AUGMENTER avec des données PUBLIQUES réelles (schéma texte, pas de mur de transfert pour un LLM) :
  - ComponentSoft/k8s-kubectl-cot-20k  (19 661 raisonnements chaînés kubectl)
  - genaidevops/kubernetes-stackoverflow-questions  (dépannage réel)
  - RCAEval (télémétrie labellisée) pour le VOLET ML (méthode d'injection Chaos Mesh)
Voir README.md pour les liens + la recette d'augmentation.

Sortie : corpus_120_sft.jsonl (un objet {"messages":[...]} par ligne).
"""
import json, os, sys, importlib.util

HERE = os.path.dirname(os.path.abspath(__file__))
CATALOG = os.path.normpath(os.path.join(
    HERE, "..", "..", "backend", "backend", "ai-service", "app", "models", "incident_catalog.py"))
OUT = os.path.join(HERE, "corpus_120_sft.jsonl")

# import du catalogue HELIOS (module pur : dicts + helpers)
spec = importlib.util.spec_from_file_location("incident_catalog", CATALOG)
cat = importlib.util.module_from_spec(spec); spec.loader.exec_module(cat)

CATALOG_120 = cat.INCIDENT_CATALOG
ACTIONS     = getattr(cat, "INCIDENT_ACTIONS", {})

SYSTEM = ("Tu es le cerveau décisionnel de HELIOS, plateforme AIOps autonome pour Kubernetes. "
          "Pour chaque incident détecté, tu ANALYSES le contexte, tu RAISONNES, tu compares plusieurs "
          "stratégies, tu CHOISIS dynamiquement la meilleure (avec justification, risque, impact), et tu "
          "renvoies l'action EXÉCUTABLE que Camunda orchestrera. Jamais de règle figée : la décision "
          "dépend du contexte observé.")

def humanize(action: str) -> str:
    return action.replace("_", " ").lower() if action else "escalade qualifiée"

def strat_block(itype, meta, action):
    res = meta.get("resolution", "")
    auto = meta.get("auto", False)
    exe = "auto-exécutable par Camunda" if auto else "escaladée (pas de commande in-cluster sûre) — l'IA propose la résolution, un opérateur valide"
    return (f"Analyse : incident de type {itype} (catégorie {meta.get('category','?')}).\n"
            f"Raisonnement : je corrèle métriques, logs, événements et dépendances pour distinguer la cause "
            f"racine (release, ressource, dépendance, config, infra). Je compare plusieurs stratégies et je "
            f"retiens celle qui rétablit le service au moindre risque dans le contexte observé.\n"
            f"Résolution professionnelle : {res}\n"
            f"Action recommandée (exécutable) : {action} — {humanize(action)}.\n"
            f"Exécution : {exe}. Camunda orchestre l'action, vérifie le résultat après remédiation, et "
            f"relance une analyse IA si l'incident persiste (boucle adaptative).")

# 4 formulations de question par type -> variété pour le SFT
Q_TEMPLATES = [
    "Incident détecté dans Kubernetes : {t}. Analyse la situation et décide de l'action.",
    "Le monitoring lève {t} sur une charge du cluster. Quelle est ta stratégie de remédiation et pourquoi ?",
    "{t} : donne ton raisonnement, la stratégie choisie et l'action exécutable pour Camunda.",
    "En tant que cerveau HELIOS, comment traites-tu un {t} ? (raisonnement + décision + exécution)",
]

def main():
    n = 0
    with open(OUT, "w", encoding="utf-8") as f:
        for itype, meta in CATALOG_120.items():
            action = ACTIONS.get(itype, "ESCALATE_" + itype)
            answer = strat_block(itype, meta, action)
            for q in Q_TEMPLATES:
                rec = {"messages": [
                    {"role": "system", "content": SYSTEM},
                    {"role": "user", "content": q.format(t=itype)},
                    {"role": "assistant", "content": answer},
                ]}
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                n += 1
    print(f"corpus écrit : {OUT}")
    print(f"  types couverts : {len(CATALOG_120)} / 120")
    print(f"  exemples SFT   : {n}  ({len(Q_TEMPLATES)} formulations/type)")
    print("  format         : chat (messages system/user/assistant)")
    print("  -> à augmenter avec les datasets publics (voir README.md) avant le fine-tuning GPU")

if __name__ == "__main__":
    main()
