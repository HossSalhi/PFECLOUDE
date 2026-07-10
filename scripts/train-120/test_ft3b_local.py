#!/usr/bin/env python3
"""Test LOCAL du 3B fine-tune (GGUF via llama-cpp-python) sur un panel des 120 types.
Zero dependance a .161 / Ollama : tourne sur le CPU du PC. Valide la qualite des decisions."""
import os, time
from llama_cpp import Llama

GGUF = r"C:/Users/houss/IdeaProjects/PFE-Orange/scripts/train-120/aiops-sre-120-ft3b.gguf"
SYSTEM = ("Tu es le cerveau decisionnel de HELIOS (AIOps Kubernetes). Pour tout incident parmi "
          "les 120 types, tu reponds STRICTEMENT en une ligne: ACTION=<action_technique> | "
          "STRATEGIE=<courte> | RAISON=<8 mots max>. L'IA decide dynamiquement, Camunda execute.")

# panel representatif A->J + cas durs
TYPES = [
    ("A", "POD_OOM_KILLED"), ("A", "POD_CRASH_LOOP"), ("A", "POD_ERR_IMAGE_PULL"),
    ("B", "DEPLOYMENT_UNAVAILABLE"), ("B", "DEPLOYMENT_VERSION_MISMATCH"),
    ("C", "SERVICE_NO_ENDPOINTS"), ("C", "NETWORK_POLICY_BLOCKING"), ("C", "DNS_FAILURE"),
    ("D", "NODE_KUBELET_DOWN"), ("D", "NODE_NOT_READY"), ("D", "NODE_DISK_FULL"),
    ("E", "ETCD_HIGH_LATENCY"), ("E", "API_SERVER_DOWN"), ("E", "ETCD_QUORUM_LOST"),
    ("F", "PVC_PENDING"), ("F", "VOLUME_MOUNT_FAILURE"),
    ("G", "DB_PRIMARY_DOWN"), ("G", "DB_DEADLOCK"), ("G", "DB_CONNECTION_POOL_EXHAUSTION"),
    ("H", "HIGH_ERROR_RATE_5XX"), ("H", "LATENCY_DEGRADATION"),
    ("I", "MALICIOUS_POD"), ("I", "CRYPTO_MINING_DETECTED"), ("I", "RBAC_MISCONFIGURATION"),
    ("J", "PREDICTED_DISK_EXHAUSTION"), ("J", "CASCADING_FAILURE"),
]

print("Chargement du 3B fine-tune (GGUF q8)...", flush=True)
t0 = time.time()
llm = Llama(model_path=GGUF, n_ctx=1024, n_threads=6, chat_format="chatml", verbose=False)
print(f"charge en {time.time()-t0:.0f}s\n" + "="*90, flush=True)

for cat, t in TYPES:
    msgs = [{"role": "system", "content": SYSTEM},
            {"role": "user", "content": f"Incident detecte dans Kubernetes : {t}. Analyse et decide l'action."}]
    r = llm.create_chat_completion(messages=msgs, temperature=0.1, max_tokens=90)
    out = r["choices"][0]["message"]["content"].strip().replace("\n", " ")
    print(f"[{cat}] {t:38s} -> {out}", flush=True)

print("="*90 + "\nFINI.", flush=True)
