# Entraînement de la stack IA HELIOS sur les 120 incidents

Objectif : rapprocher l'IA de « couvrir les 120 types d'incidents », **à partir de données réelles**,
en distinguant honnêtement ce qui est faisable (LLM) de ce qui est borné (ML).

## Les deux volets (à ne pas confondre)

| Volet | Peut couvrir les 120 ? | Pourquoi |
|---|---|---|
| **LLM** (raisonnement) | ✅ Oui | Il raisonne sur du **texte** → pas de mur de transfert. On l'entraîne sur le catalogue des 120 + des données publiques K8s. **C'est « l'IA décide sur les 120 ».** |
| **ML** (classifieur de signaux) | ❌ Non (~15-25 réels) | Il lui faut des **exemples réels de télémétrie par type**. Le cluster n'en a produit que **9**. Les datasets publics ont un **schéma de features différent** → ne se déposent pas dans HELIOS. |

## Volet LLM — fine-tuning (ce dossier)

Pipeline :
```bash
# 1) corpus des 120 (depuis le catalogue HELIOS)      -> 480 exemples, 120/120 types
python build_sft_corpus.py
# 2) augmentation par vraies données publiques K8s     -> +N exemples réels
python fetch_public_datasets.py 300
# 3) fine-tuning LoRA  ⚠️ SUR GPU (pas .161)
python finetune_lora.py
# 4) fusion -> GGUF (llama.cpp) -> ollama create aiops-sre-ft-v3 -> déploiement .161
```

### ⚠️ Contrainte matérielle mesurée
Le fine-tuning **ne peut pas tourner sur .161** : pas de GPU, ~2.6 Go RAM libre, 4.2 Go disque libre.
Lancer l'étape 3 sur **Colab (T4 gratuit)**, un **runner GPU cloud**, ou une machine CUDA. Le reste
(corpus, augmentation) tourne partout. Le modèle produit (~1 Go GGUF) se redéploie ensuite sur Ollama .161.

### Datasets publics utilisés / recommandés (texte → LLM)
- [ComponentSoft/k8s-kubectl-cot-20k](https://huggingface.co/datasets/ComponentSoft/k8s-kubectl-cot-20k) — 19 661 raisonnements chaînés kubectl (**intégré**)
- [ComponentSoft/k8s-kubectl-35k](https://huggingface.co/datasets/ComponentSoft/k8s-kubectl-35k)
- [genaidevops/kubernetes-stackoverflow-questions](https://huggingface.co/datasets/genaidevops/kubernetes-stackoverflow-questions) · [mcipriano/…](https://huggingface.co/datasets/mcipriano/stackoverflow-kubernetes-questions) — dépannage réel
- [keethu/kubernetes-documentation-dataset](https://huggingface.co/datasets/keethu/kubernetes-documentation-dataset) — doc K8s

## Volet ML — données de télémétrie labellisées

Le ML a besoin de vraies features **de ton cluster**. Deux voies :
1. **Répliquer la méthode d'injection** des benchmarks publics sur TON cluster (Chaos Mesh), collecter
   la télémétrie HELIOS, l'étiqueter → dataset réel des types **déclenchables** (~15-25). Puis
   ré-entraîner un **modèle candidat** (valider contre le pipeline prouvé avant tout déploiement live).
2. Datasets publics de télémétrie labellisée comme **référence / pré-entraînement** (schéma différent) :
   - [RCAEval (Zenodo)](https://zenodo.org/records/14590730) — 735 pannes, 3 systèmes K8s, 11 types de fautes
   - [PetShop](https://arxiv.org/html/2311.04806v2), [LO2](https://arxiv.org/html/2504.12067v1),
     [AIOps benchmark](https://arxiv.org/html/2407.14532v1), [LogHub](https://arxiv.org/pdf/2008.06448)

## Honnêteté (pour la soutenance)
- **LLM entraîné sur les 120** = ✅ atteignable (ce pipeline) : il **raisonne/décide** sur n'importe quel type.
- **ML classant les 120 sur signaux réels** = ❌ impossible ici : il n'existe pas de données réelles pour
  ~68 types (non-déclenchables). Le ML classe le réel (~15-25 après injection), le LLM raisonne sur le reste,
  l'heuristique/déterministe reste le filet pour l'inentraînable. C'est l'architecture **hybride** — l'état de l'art.
