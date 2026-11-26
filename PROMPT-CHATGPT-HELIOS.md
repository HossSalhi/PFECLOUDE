# Prompt à copier-coller dans ChatGPT

---

Tu es un architecte DevOps/SRE/Kubernetes senior. Tu vas m'aider à terminer un projet de fin d'études (PFE) : HELIOS, une infrastructure Kubernetes auto-réparatrice (self-healing) pour Orange Tunisie, basée sur Kubernetes, Prometheus/Loki/Grafana, Camunda/Zeebe, du Machine Learning et un LLM.

Je vais te donner l'état exact et vérifié de mon infrastructure, ce qui a déjà été fait, ce qui reste à faire, et je veux que tu m'aides à écrire le code manquant et à produire les commandes exactes à exécuter sur chaque machine. **Ne remets pas en cause les choix d'architecture déjà faits et vérifiés ci-dessous** (pas de VIP, pas de Keepalived, pas de MetalLB comme chemin principal) — mon encadrant a validé ces choix explicitement.

## 1. Infrastructure — 6 VMs (réseau 192.168.148.0/24)

| VM | Nom | IP | Rôle |
|---|---|---|---|
| VM1 | k8s-master | .153 | Control-plane + Worker + membre etcd |
| VM2 | k8s-worker1 | .158 | Control-plane + Worker + membre etcd |
| VM3 | k8s-worker2 | .155 | Control-plane + Worker + membre etcd |
| VM4 | k8s-monitoring | .160 | Monitoring hors cluster + HAProxy actif + membre etcd |
| VM5 | k8s-aiCamunda | .161 | Camunda/Zeebe + ML + LLM (Ollama) + ChromaDB (PAS membre etcd) |
| VM6 | k8s-lb2 | .162 | HAProxy actif (pas standby) + membre etcd |

## 2. Ce qui est DÉJÀ fait et vérifié — ne pas reproposer

- **Cluster Kubernetes HA à 3 control-planes** (kubeadm, stacked etcd) : `.153`, `.158`, `.155` sont chacun control-plane ET worker.
- **Aucun VIP, aucun Keepalived** : supprimés sur demande explicite de l'encadrant (« un VIP n'est pas une notion Kubernetes »).
- **Pas de SPOF sur le control-plane** : chaque `kubelet.conf` pointe vers l'API server **local de son propre nœud** (`.153→.153:6443`, etc.). Si `.160` tombe, les 3 nœuds K8s restent `Ready`.
- **HAProxy actif/actif** sur `.160` ET `.162` (pas actif/passif) : `helios.orange.tn` résout vers les deux IP (2 A-records), le navigateur bascule côté client si l'une ne répond pas. Backend = NodePort de l'ingress-nginx des 3 nœuds (`:31756` http, `:31411` https).
- **etcd à 5 membres votants** : `.153`, `.155`, `.158` (nœuds K8s) + `.160`, `.162` (VMs plateforme, ajoutées en `--learner` puis `promote`). Quorum = 3, tolère la perte de **2 machines simultanément**. `.161` a été retirée d'etcd (aucun rôle dans le maintien du cluster).
- **Gardien de quorum** (`helios-quorum-guard`, systemd sur les 3 nœuds K8s) : reconstitue automatiquement etcd sur le dernier survivant après validation de 5 garde-fous anti-split-brain (patience 90s, isolement ICMP+port 2380, témoin visible, avis de l'arbitre, élection par plus petite IP).
- **Monitoring 100% hors cluster** (demande encadrant) : Prometheus, Grafana, Loki, node_exporter tournent en **systemd sur `.160`**, zéro pod de monitoring dans le cluster.
- **kube-state-metrics externalisé** sur `.160` (binaire systemd, scrape `127.0.0.1:8080`, kubeconfig avec jeton lecture-seule pointant sur `.160:6443`). Le KSM in-cluster a été supprimé.
- **metrics-server** reste dans le cluster (API agrégée `metrics.k8s.io`, requis par le HPA, non externalisable).
- **RBAC corrigé** : le verbe `pods: create` a été retiré du Role `backend-remediator` (namespace `pfe`) — il servait à créer un pod privilégié éphémère avec `nsenter` sur l'hôte, ce qui équivaut à un accès root sur le nœud. Les actions hôte doivent maintenant passer exclusivement par le bridge SSH avec liste blanche stricte.

## 3. Fichiers déjà créés cette session (à réutiliser, pas à recréer)

Dans `infra/` du repo :
- `docs/ARCHITECTURE-HELIOS-PRO.md` — document d'architecture complet (13 parties)
- `docs/RUNBOOK-ETCD-QUORUM.md` — procédure de récupération perte de quorum
- `infra/monitoring/prometheus-6vms.yml` — scrape des 6 VMs + etcd (5 membres) + scheduler + controller-manager + apiserver + kubelet/cAdvisor + blackbox
- `infra/monitoring/rules-infrastructure.yml` — 66 règles d'alerte Prometheus (host, control-plane, nœuds, pods, réseau, plateforme, application), avec labels `severity`, `helios_type`, `helios_target`, `auto_remediate`
- `infra/monitoring/alertmanager-camunda.yml` — routage Alertmanager avec groupement, inhibition en cascade, et un webhook vers `http://192.168.148.161:8099/alertmanager` (auth HTTP Basic + token fichier)
- `infra/monitoring/promtail-vms-hors-cluster.yml` — collecte journald/syslog/kern.log/auth.log des VMs hors cluster
- `infra/remediation-action-catalog.yaml` — catalogue de 14 actions de remédiation avec 9 portes de validation (namespace autorisé, cible protégée, paramètres validés par pattern, seuil de confiance, cohérence IA/cluster, circuit breaker, approbation humaine pour risque HIGH/CRITICAL)
- `infra/09-fix-monitoring-vm.sh` — installe Alertmanager + Registry Docker privé sur `.160`
- `infra/11-install-monitoring-6vms.sh <nom-vm>` — installe node_exporter (avec `--collector.systemd`) + Promtail + (sur `.160` seulement) blackbox_exporter

**Fichiers explicitement obsolètes, à ne PAS exécuter** : `infra/10-local-apiserver-lb.sh` (redondant, le problème est déjà résolu autrement), `infra/ha-upgrade/01` à `04` (migration HA et Keepalived déjà faits), `infra/08-metallb-ingress-pro.sh` (changerait un chemin de trafic qui fonctionne déjà, à n'exécuter que sur décision délibérée).

## 4. Le vrai blocage — ce que je veux que tu m'aides à coder

L'architecture ci-dessus est en place, mais la chaîne d'alerte end-to-end n'est PAS branchée. J'ai vérifié dans le code :

1. **Le bridge SSH (`camunda-direct/bridge/bridge.py`, tourne sur `.161:8099`) n'a AUCUN endpoint `/alertmanager`.** Il expose actuellement `POST /exec`, `POST /action`, `POST /detecter`, `POST /trace`, `GET /sante`, `GET /actions`. Le webhook Alertmanager pointe vers un endpoint qui n'existe pas.

2. **Le BPMN (`camunda-direct/camunda-full-cycle.bpmn`) démarre encore sur un `Timer` (`R/PT60S`, polling toutes les 60s), pas sur un Message Start Event.** Il contient une tâche `Detecter` qui fait du SSH vers `.160` pour interroger Prometheus/Loki — cette tâche doit disparaître puisqu'Alertmanager fournira déjà le contexte de l'incident.

### Ce que je veux exactement :

**A. Code Python à ajouter dans `bridge.py`** (je vais te coller le fichier existant, ou son architecture, dans mon prochain message) :
- Un endpoint `POST /alertmanager` qui reçoit le payload webhook standard d'Alertmanager (format `{version, groupKey, status, receiver, groupLabels, commonLabels, alerts[]}`)
- Vérifie l'authentification HTTP Basic (utilisateur `alertmanager`, mot de passe = contenu d'un fichier token)
- Extrait `groupKey`, `helios_type` (label), `helios_target` (label), `status` (`firing`/`resolved`)
- Publie un message Zeebe corrélé (`PublishMessage`, nom du message = `AlerteRecue`, `correlationKey` = `groupKey`) avec en variables le contexte de l'incident
- Si `status == "resolved"`, publie un message `IncidentResolu` avec la même `correlationKey` pour fermer le workflow en cours
- Journalise dans `/var/log/helios-ssh-bridge.log` avant traitement, retourne `200 OK`
- Respecte le style du fichier existant (commentaires en français, sécurité par token, pas de root inutile)

**B. Modifications BPMN** (je vais coller le XML existant) :
- Remplacer le `startEvent` `Timer` par un Message Start Event nommé `AlerteRecue`, avec `zeebe:subscription` et `correlationKey` lié à une variable de processus (ex: `= groupKey`)
- Supprimer la tâche de service `Detecter` (SSH vers `.160`) et la brancher directement sur la tâche de classification ML, en utilisant les variables reçues du message (`helios_type`, `helios_target`, labels et annotations de l'alerte) comme contexte initial
- Ajouter un Message End Event ou une branche qui écoute `IncidentResolu` pour clore le workflow si l'alerte se résout avant la fin de la remédiation
- Garder tous les garde-fous existants (namespaces système protégés, vérification de cohérence IA vs cluster) — je te collerai le XML complet pour que tu bases tes modifications dessus

**C. Une checklist de déploiement dans l'ordre**, VM par VM, avec les commandes exactes (`scp`, `systemctl`, `curl -X POST .../reload`, etc.) pour :
1. `.160` : exécuter `09-fix-monitoring-vm.sh`, déployer `prometheus-6vms.yml`, `rules-infrastructure.yml`, `alertmanager-camunda.yml`, générer le token bridge
2. Les 6 VMs : exécuter `11-install-monitoring-6vms.sh <nom>` (node_exporter + Promtail, + blackbox sur `.160` uniquement)
3. `.161` : déployer le nouveau `bridge.py` avec l'endpoint `/alertmanager`, redémarrer le service, reporter le token généré sur `.160` dans une variable d'environnement `HELIOS_ALERTMANAGER_TOKEN`, déployer le nouveau BPMN
4. Tests de bout en bout : provoquer un incident (`kubectl -n pfe run casse --image=busybox --restart=Always -- /bin/false`), vérifier que Prometheus détecte → Alertmanager groupe → webhook `.161:8099/alertmanager` reçoit → Camunda démarre le workflow → ML classifie → LLM diagnostique → action exécutée → vérification → incident résolu

## 5. Contraintes à respecter

- Commentaires de code en **français**, dans le style déjà utilisé (direct, technique, explique le "pourquoi" pas juste le "quoi")
- Sécurité : le bridge écoute en `127.0.0.1` uniquement pour `/exec` et `/action` — pour `/alertmanager`, il doit écouter sur l'IP réseau `.161` puisqu'Alertmanager vient de `.160`, mais avec authentification obligatoire
- Le catalogue d'actions (`remediation-action-catalog.yaml`) est la seule source de vérité pour les actions autorisées — le LLM ne génère jamais de commande shell, seulement un `action_id`
- Le namespace `pfe` est le seul namespace applicatif autorisé pour la remédiation automatique
- Ne propose pas de VIP, Keepalived, VRRP, ni de migration vers MetalLB comme solution — ces sujets sont fermés

## 6. Ce que je vais te fournir ensuite

Dans mon prochain message, je colle le contenu actuel de `camunda-direct/bridge/bridge.py` (environ 560 lignes) et `camunda-direct/camunda-full-cycle.bpmn`. Attends ces fichiers avant de proposer du code — base-toi sur leur structure réelle, ne réinvente pas l'architecture existante.

Réponds d'abord en confirmant que tu as compris le contexte et liste les questions bloquantes que tu as, si il y en a, avant que je colle les deux fichiers.
