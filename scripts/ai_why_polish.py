#!/usr/bin/env python3
# Final polish of the LLM "Why": don't restate type/severity, raise token budget to
# avoid mid-sentence cutoff, and keep only COMPLETE sentences (drop trailing fragments).
import shutil, sys
P = "/opt/pfe-ai-service/app/models/inference.py"
s = open(P).read()
shutil.copy(P, P + ".bak.whypolish")

# 1) tighter system prompt
old_sys = ('"You are a senior SRE. Explain WHY the recommended action resolves this incident, "\n'
           '                "in at most TWO short sentences (max 40 words). Use ONLY the facts given. "\n'
           '                "Do NOT speculate about causes that are not in the evidence. No preamble, no lists."')
new_sys = ('"You are a senior SRE. In at most TWO short sentences (max 45 words), justify WHY the "\n'
           '                "recommended action fixes the incident. Do NOT restate the incident type, severity or "\n'
           '                "numbers (they are already shown). Do NOT speculate about causes not in the evidence. "\n'
           '                "No preamble, no lists. Write complete sentences."')
if old_sys not in s:
    print("WARN: system prompt not matched (continuing)")
else:
    s = s.replace(old_sys, new_sys, 1)

# 2) raise num_predict for the why call (only the backup-model call uses 160)
s = s.replace('"num_predict": 160', '"num_predict": 220', 1)

# 3) keep only COMPLETE sentences
old_pp = ('            parts = _re.split(r"(?<=[.!?])\\s+", raw)\n'
          '            concise = " ".join(parts[:2]).strip()\n'
          '            if len(concise) > 320:\n'
          '                concise = concise[:317].rstrip() + "..."\n'
          '            return concise')
new_pp = ('            sents = _re.findall(r"[^.!?]*[.!?]", raw)\n'
          '            concise = " ".join(x.strip() for x in sents[:2]).strip()\n'
          '            return concise if concise else None')
if old_pp not in s:
    print("ERROR: post-process block not matched"); sys.exit(1)
s = s.replace(old_pp, new_pp, 1)

open(P, "w").write(s)
print("LLM why polished (no restate, num_predict=220, complete-sentences only)")
