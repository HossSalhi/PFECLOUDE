# HELIOS — Runbook mise en PRODUCTION du nouveau stack IA (2026-07-10)

Déploiement du **LLM fine-tuné `helios-120-3b` + RAG (120 types)** sur `.161`, et test
de production E2E **déclenché DANS le cluster** (jamais depuis Windows C:).

## Architecture cible
```
Monitoring (.160)  →  Camunda (backend, cluster)  DÉTECTE
                                 │  appelle
                          ai-service (.161)  :  ML classe le TYPE
                                                Catalogue → sévérité + action de réf.
                                                RAG → ancre le contexte (cartes proches)
                                                helios-120-3b → DÉCIDE l'action + EXPLIQUE
                                 │  action proposée par l'IA
                          Camunda EXÉCUTE dans le cluster K8s  →  RESOLVED
```
- **ai-service** (Python : ML + LLM + RAG) = service hôte sur **.161**, Ollama sur **.161**.
- **backend/Camunda** = dans le cluster (workers), appelle `.161:8000`.
- **ML live conservé** (prouvé F1 0.87 réel). La richesse 120 vient du **LLM+RAG+catalogue**
  (action + explication). Retrain ML sur données réelles = étape ultérieure.

---

## Pré-requis
- [ ] `.gguf` téléchargé : `Qwen2.5-3B-Instruct.Q4_K_M.gguf` (1.93 GB), chemin connu.
- [ ] `paramiko` installé sur le PC (`pip install paramiko`).
- [ ] Clé SSH master présente : `C:/Users/houss/.ssh/id_ed25519`.

---

## PHASE 1 — Déployer le stack IA sur .161 (1 seule VM)

1. **Allumer UNIQUEMENT `k8s-aiCamunda`** (.161, 6 GB). Attendre ~2 min (Ollama démarre).
2. Déployer (code ai-service + LLM + restart + smoke tests) :
   ```powershell
   python scripts/deploy_ai_stack_161.py --gguf "C:\Users\houss\Downloads\Qwen2.5-3B-Instruct.Q4_K_M.gguf"
   ```
3. **Vérifier la sortie du smoke test** :
   - `/health` → OK
   - `/predict` (crashloop simulé) → renvoie un incident classé
   - **LLM+RAG direct** → une réponse `DIAGNOSTIC … | ACTION=… | EXPLICATION …` cohérente
4. Redéploiement du code seul plus tard (sans re-pousser le LLM) :
   ```powershell
   python scripts/deploy_ai_stack_161.py --code-only
   ```

> ⚠️ Le `.gguf` est le livrable de 2h30 de calcul : garde une copie stable (ex. `models/`).

---

## PHASE 2 — Test de production E2E DANS le cluster (5 VMs)

### ⭐ Voie recommandée : lanceur automatique + garde-fou RAM
```powershell
python scripts/launch_phase2.py            # démarrage gradué (garde-fou RAM) + test réel
python scripts/launch_phase2.py --min-only # master + 1 worker + .161 (RAM minimale)
```
Le lanceur : vérifie la RAM libre (refuse de démarrer si l'hôte gèlerait), allume master
→ workers → monitoring **un par un** en surveillant la RAM, attend le cluster sain
(nodes Ready + podinfo + postgres), puis exécute `prod_test_in_cluster.py`.
**Besoin : ~10.5 Go libres (full) ou ~5.5 Go (--min-only). Fermer navigateur/IDE d'abord.**

### Voie manuelle (détail)

> Avant : rééquilibrer la RAM si besoin (aiCamunda 6→7-8 GB, master 3, workers 3.5).

1. **Allumer les 5 VMs** : master (.153), worker1 (.158), worker2 (.155), monitoring (.160),
   aiCamunda (.161). Attendre que le cluster soit Ready :
   `ssh kube@192.168.148.153 kubectl get nodes` → tous `Ready`.
2. Vérifier que l'app de démo `podinfo` tourne dans `pfe` (sinon `setup` du chaos toolkit).
3. **Lancer le test** (déclenche un vrai crash loop DANS le cluster, observe la remédiation) :
   ```powershell
   python scripts/prod_test_in_cluster.py
   ```
   Ce lanceur copie `prod_test_in_cluster.sh` sur le **master** et l'exécute LÀ-BAS
   (kubectl dans la VM). Il :
   - patche `podinfo` en crash loop → `CrashLoopBackOff`,
   - observe : `POD_CRASH_LOOP` → l'IA classe+décide (ROLLBACK)+explique → Camunda exécute,
   - confirme la remédiation (pods de nouveau Ready, override retiré),
   - affiche la ligne `incident` (type, sévérité, action, explication) depuis `pfe_db`.
4. **Teardown** (si HELIOS n'a pas restauré) :
   ```powershell
   python scripts/prod_test_in_cluster.py --restore
   ```

### Voir la décision de l'IA (classification + action + explication)
- UI HELIOS (incident detail), ou en direct :
  ```
  ssh kube@192.168.148.153 "kubectl exec -n pfe postgres-1 -c postgres -- \
    psql postgresql://appuser:postgres@localhost/pfe_db -c \
    \"SELECT incident_type,severity,status,recommended_action,left(decision_explanation,300) \
      FROM incident ORDER BY id DESC LIMIT 5;\""
  ```

---

## Rollback / sécurité
- Le nouveau moteur LLM est **entièrement gardé** : si `helios-120-3b` est injoignable,
  l'ai-service retombe sur l'explication **déterministe** → le pipeline ne casse jamais.
- Revenir à l'ancien LLM : impossible/inutile (retiré) ; en secours, l'IA reste fonctionnelle
  sans LLM (ML + catalogue + explication déterministe).
- RAM hôte trop juste → n'allumer qu'aiCamunda pour la Phase 1 ; jamais couper 2 workers
  ensemble (quorum etcd).

## Dépannage
| Symptôme | Cause probable | Fix |
|---|---|---|
| smoke `/predict` FAILED | ai-service pas redémarré | attendre + `--code-only` re-run |
| LLM direct vide/erreur | modèle pas créé | vérifier `ollama list` sur .161 |
| RAG en repli haché | sentence-transformers absent sur .161 | ok (hybride reste bon), sinon `pip install` |
| test E2E TIMEOUT | Camunda/leader lease | voir mémoire lease + Zeebe disque .161 |
| pas d'incident créé | leader_lease bloqué post-deploy | `DELETE FROM leader_lease WHERE lease_name='aiops-scheduler';` |

## Fichiers
- `scripts/deploy_ai_stack_161.py` — déploiement stack IA sur .161.
- `scripts/prod_test_in_cluster.py` / `.sh` — test E2E déclenché dans le cluster.
- `scripts/llm-finetune/deploy_helios_120_gguf.py` — déploiement LLM seul (alternatif).
- `backend/backend/ai-service/app/services/rag_retriever.py` — RAG 120 types.
- `backend/backend/ai-service/app/models/inference.py` — moteur (LLM+RAG câblés).
