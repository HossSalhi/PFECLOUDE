#!/usr/bin/env python3
"""Corpus SFT PROPRE des 120 — format décision net ACTION=|STRATEGIE=|RAISON= (pour fine-tune LLM).

La v1 (build_sft_corpus.py) produisait des paragraphes verbeux + 560 exemples publics génériques
-> le modèle fine-tuné apprenait du bruit (AWS/CI). Ici : réponses COURTES et NETTES, une action
technique par type (du catalogue HELIOS), plusieurs formulations de question -> un modèle qui décide.
Sortie : corpus_120_clean.jsonl
"""
import json, os, importlib.util

HERE = os.path.dirname(os.path.abspath(__file__))
CATALOG = os.path.normpath(os.path.join(HERE, "..", "..", "backend", "backend",
                                        "ai-service", "app", "models", "incident_catalog.py"))
OUT = os.path.join(HERE, "corpus_120_clean.jsonl")
spec = importlib.util.spec_from_file_location("incident_catalog", CATALOG)
cat = importlib.util.module_from_spec(spec); spec.loader.exec_module(cat)
CATALOG_120 = cat.INCIDENT_CATALOG
ACTIONS = getattr(cat, "INCIDENT_ACTIONS", {})

SYSTEM = ("Tu es le cerveau decisionnel de HELIOS (AIOps Kubernetes). Pour tout incident parmi les "
          "120 types, tu reponds STRICTEMENT en une ligne: ACTION=<action_technique> | "
          "STRATEGIE=<courte> | RAISON=<8 mots max>. L'IA decide dynamiquement, Camunda execute.")

# stratégie + raison courtes dérivées par famille (nettes, pas verbeuses)
def strat_reason(itype, meta, action):
    res = (meta.get("resolution", "") or "").rstrip(".").lower()
    strat = res[:60] if res else "remediation adaptee au contexte"
    reason = itype.replace("_", " ").lower()
    return strat, reason

Q = [
    "Incident detecte dans Kubernetes : {t}. Analyse et decide l'action.",
    "Le monitoring leve {t}. Quelle action et pourquoi ?",
    "{t} : donne l'action executable pour Camunda.",
    "En tant que cerveau HELIOS, comment traites-tu {t} ?",
    "Incident: {t}",
]

def main():
    n = 0
    with open(OUT, "w", encoding="utf-8") as f:
        for itype, meta in CATALOG_120.items():
            action = ACTIONS.get(itype, "ESCALATE_" + itype)
            strat, reason = strat_reason(itype, meta, action)
            answer = f"ACTION={action} | STRATEGIE={strat} | RAISON={reason}"
            for q in Q:
                rec = {"messages": [
                    {"role": "system", "content": SYSTEM},
                    {"role": "user", "content": q.format(t=itype)},
                    {"role": "assistant", "content": answer},
                ]}
                f.write(json.dumps(rec, ensure_ascii=False) + "\n"); n += 1
    print(f"corpus PROPRE ecrit : {OUT}")
    print(f"  types : {len(CATALOG_120)}/120 | exemples : {n} ({len(Q)}/type) | format ACTION= net, 0 bruit public")

if __name__ == "__main__":
    main()
