# -*- coding: utf-8 -*-
"""HELIOS — génère un dataset DPO (preference tuning) depuis sft_120.jsonl.
chosen  = réponse CORRECTE structurée (action catalogue précise) — ce qu'on VEUT.
rejected = réponse VAGUE « investigate » (le mode d'échec mesuré du fine-tune SFT) — ce qu'on FUIT.
DPO apprend au modèle à préférer l'action précise/structurée à la prose floue, EN gardant le format.
(Optionnel depuis le décodage contraint : ceci améliore la qualité INTRINSÈQUE du modèle.)"""
import json, re, os
HERE=os.path.dirname(__file__)
IN=os.path.join(HERE,"sft_120.jsonl"); OUT=os.path.join(HERE,"dpo_120.jsonl")

def degrade(assistant: str) -> str:
    """Fabrique la réponse REJETÉE = prose vague sans action précise (le pattern régressé)."""
    m=re.search(r"DIAGNOSTIC:\s*(.+?)\s*(?:\||$)", assistant)
    cause=(m.group(1).strip() if m else "an anomaly is affecting the workload")
    return (f"The root cause appears to be that {cause}. To resolve this issue, we need to "
            f"investigate the application logs and address the underlying problem accordingly.")

n=0
with open(IN,encoding="utf-8") as f, open(OUT,"w",encoding="utf-8") as g:
    for line in f:
        line=line.strip()
        if not line: continue
        ex=json.loads(line); msgs=ex.get("messages",[])
        if len(msgs)<3 or msgs[-1].get("role")!="assistant": continue
        chosen=msgs[-1]["content"]
        if "ACTION=" not in chosen: continue          # garde seulement les exemples avec action précise
        rejected=degrade(chosen)
        if rejected.strip()==chosen.strip(): continue
        # format conversationnel (TRL DPOTrainer : prompt=liste de messages, chosen/rejected=str)
        g.write(json.dumps({"prompt": msgs[:-1], "chosen": chosen, "rejected": rejected},
                           ensure_ascii=False)+"\n")
        n+=1
print(f"écrit {n} paires DPO -> {OUT}")
