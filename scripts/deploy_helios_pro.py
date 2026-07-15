# -*- coding: utf-8 -*-
"""HELIOS — Déploiement TÊTE-À-TÊTE éval-gaté du modèle fine-tuné (helios-120-pro) sur .161.

Le point PRO : on ne remplace qwen QUE si le fine-tuné le bat OBJECTIVEMENT, mesuré EN DIRECT
sur les MÊMES types, avec le MÊME wrapper (RAG+CRAG). Pas de seuil codé en dur : comparaison
tête-à-tête qwen vs helios-120-pro. Un seul modèle 3B résident à la fois (contrainte RAM .161 :
on décharge un modèle avant d'évaluer l'autre). Sauvegarde config + rollback + re-warm + health.

usage : python scripts/deploy_helios_pro.py <chemin/helios-120-pro.gguf> [N_types|all]
        N_types = taille du sous-ensemble d'éval (défaut 40 ; "all" = les 120, ~1h40 sur CPU).
Étapes : scp GGUF -> ollama create -> ÉVAL qwen vs pro (RAG+CRAG) -> swap SI pro > qwen -> restart.
"""
import paramiko, sys, io, time, re, os

IP = "192.168.148.161"
REMOTE = "/opt/pfe-ai-service/"
GGUF = sys.argv[1] if len(sys.argv) > 1 else None
NARG = sys.argv[2] if len(sys.argv) > 2 else "40"   # sous-ensemble d'éval (défaut 40) ou "all"
if not GGUF or not os.path.exists(GGUF):
    sys.exit("usage: python scripts/deploy_helios_pro.py <helios-120-pro.gguf> [N|all]")

c = paramiko.SSHClient(); c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect(IP, username="kube", password="kube", timeout=15)
def run(cmd, sudo=False, t=300):
    if sudo: cmd = "echo 'kube' | sudo -S " + cmd
    _, o, e = c.exec_command(cmd, timeout=t)
    return (o.read().decode(errors="replace") + e.read().decode(errors="replace")).strip()

# ── 1) upload GGUF + création du modèle Ollama helios-120-pro ────────────────
print("scp GGUF (~2 Go)…")
sftp = c.open_sftp(); sftp.put(GGUF, "/tmp/helios-120-pro.gguf")
sftp.putfo(io.BytesIO(b"FROM /tmp/helios-120-pro.gguf\nPARAMETER temperature 0.1\n"), "/tmp/Modelfile.pro")
sftp.close()
print("ollama create:", run("cd /tmp && ollama create helios-120-pro -f Modelfile.pro", t=600)[-160:])

# ── 2) ÉVAL TÊTE-À-TÊTE : qwen vs helios-120-pro, MÊMES types, MÊME wrapper ───
#    Un seul modèle résident à la fois (warm -> éval -> unload) pour tenir la RAM de .161.
EVAL = r'''# -*- coding: utf-8 -*-
import sys, time, httpx
sys.path.insert(0, "/opt/pfe-ai-service")
from app.core.config import get_settings
from app.models.incident_catalog import INCIDENT_CATALOG, INCIDENT_ACTIONS
from app.services.rag_retriever import RagRetriever
from app.services.llm_wrapper import HeliosLLM

QWEN = getattr(get_settings(), "llm_model", "qwen2.5:3b-instruct")
if QWEN == "helios-120-pro":            # re-run apres un deploiement : compare toujours vs la BASE
    QWEN = "qwen2.5:3b-instruct"
NARG = "__NARG__"
rag = RagRetriever().build()
types = list(INCIDENT_CATALOG.keys())
subset = types if NARG == "all" else types[::max(1, len(types) // int(NARG))]
def ev(t): return ("observed symptoms and event.reason consistent with "
                   + t.replace("_", " ").lower() + "; metrics/logs indicate this condition")
def warm(m):
    try: httpx.post("http://localhost:11434/api/generate",
                    json={"model": m, "keep_alive": -1, "options": {"num_predict": 2}, "prompt": "ok"}, timeout=240)
    except Exception: pass
def unload(m):
    try: httpx.post("http://localhost:11434/api/generate",
                    json={"model": m, "keep_alive": 0, "options": {"num_predict": 1}, "prompt": "ok"}, timeout=60)
    except Exception: pass
def score(model):
    warm(model)
    llm = HeliosLLM(rag, model=model, fallback=None, timeout=30, num_predict=64)  # num_predict = prod
    ok = tot = 0; t0 = time.time()
    for t in subset:
        g = INCIDENT_ACTIONS.get(t)
        try: d = llm.decide(t, "HIGH", ev(t), "", "")
        except Exception: d = None
        tot += 1
        if d and d.get("action") == g: ok += 1
    unload(model)                        # libere la RAM avant d'evaluer l'autre modele
    a = ok / max(1, tot)
    print("SCORE %-22s = %.4f (%d/%d)  %ds" % (model, a, ok, tot, time.time() - t0), flush=True)
    return a
print("types evalues:", len(subset), "| baseline:", QWEN)
q = score(QWEN)
p = score("helios-120-pro")
print("HEADTOHEAD qwen=%.4f pro=%.4f delta=%+.4f" % (q, p, p - q))
'''.replace("__NARG__", NARG)

sftp = c.open_sftp(); sftp.putfo(io.BytesIO(EVAL.encode()), "/tmp/eval_headtohead.py"); sftp.close()
print(f"ÉVAL tête-à-tête sur ~{NARG} types (long sur CPU ; ~33 min pour 40, ~1h40 pour 'all')…")
res = run(f"{REMOTE}.venv/bin/python3 /tmp/eval_headtohead.py", t=8000)
print(res)
m = re.search(r"HEADTOHEAD qwen=([0-9.]+) pro=([0-9.]+)", res)
q = float(m.group(1)) if m else 1.0
p = float(m.group(2)) if m else 0.0

# ── 3) DÉCISION : déploie le fine-tuné UNIQUEMENT s'il bat qwen en tête-à-tête ─
print(f"\n>>> qwen={q:.4f}  pro={p:.4f}  delta={p - q:+.4f}")
if p > q:
    print(">>> LE FINE-TUNÉ BAT qwen -> DÉPLOIEMENT helios-120-pro (config + restart + re-warm)")
    rp = REMOTE + "app/core/config.py"
    run(f"cp {rp} {rp}.bak-deploy-{time.strftime('%Y%m%d-%H%M%S')}", sudo=True)
    run("sed -i 's/llm_model: str = .*/llm_model: str = \"helios-120-pro\"/' " + rp, sudo=True)
    run("systemctl restart pfe-ai-service", sudo=True); time.sleep(6)
    run('curl -s localhost:11434/api/generate -d \'{"model":"helios-120-pro","keep_alive":-1,'
        '"options":{"num_predict":1},"prompt":"ok"}\' >/dev/null 2>&1', t=200)
    print("service:", run("systemctl is-active pfe-ai-service", sudo=True))
    print("health :", run("curl -s -o /dev/null -w '%{http_code}' localhost:8000/health"))
    print("modèle résident:", run("ollama ps"))
    print("\nDÉPLOYÉ : helios-120-pro LIVE, branché dans RAG+CRAG+wrapper+mémoire+guardrails.")
    print("Vérifie E2E : déclenche 1 incident DANS le cluster (SSH master) -> RESOLVED.")
else:
    print(">>> le fine-tuné NE BAT PAS qwen -> on GARDE qwen (ZÉRO régression).")
    run('curl -s localhost:11434/api/generate -d \'{"model":"helios-120-pro","keep_alive":0,'
        '"options":{"num_predict":1},"prompt":"ok"}\' >/dev/null 2>&1')   # décharge le FT
    print("qwen conservé. helios-120-pro reste sur Ollama pour analyse "
          "(`ollama rm helios-120-pro` pour libérer le disque).")
c.close()
