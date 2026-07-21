# Camunda en accès direct — un seul process, tous les namespaces

Ce dossier contient le processus BPMN par lequel **Camunda seul** réalise la chaîne complète,
sans passer par le job-worker du backend :

```
timer 60s → Prometheus (.160) → Loki (.160) → Grafana (.160)
          → décision → stack IA (.161:8000) → exécution dans le cluster (API K8s .150:6443)
          → traçage de la décision en annotation sur la ressource
```

Camunda appelle le monitoring et l'API Kubernetes **directement**, via le connecteur
`io.camunda:http-json:1` du runtime de connecteurs embarqué dans `c8run`.

## Fichiers

| Fichier | Rôle |
|---|---|
| `camunda-full-cycle.bpmn` | le processus (`helios-camunda-full-cycle`) |
| `deploy_bpmn.py` | vérifie la portée RBAC puis déploie le BPMN |
| `preuve_deux_chemins.py` | prouve les 2 chemins de détection (replicas indisponibles, saturation CPU) |
| `preuve_namespaces.py` | prouve l'action sur un namespace arbitraire **et** le garde-fou système |

## Détection : deux chemins

Les requêtes Prometheus ne sont **pas** filtrées par namespace — un seul process couvre tout le cluster.

1. **Replicas indisponibles** — `kube_deployment_status_replicas_unavailable > 0`
2. **Saturation CPU** — `rate(container_cpu_usage_seconds_total[3m]) > 0.15`
   Détecte un pod « Ready » mais saturé, angle mort du chemin 1.

### Astuce FEEL : le contexte vide

Deux limites de FEEL/Zeebe ont dicté la structure :

- le `resultExpression` d'un connecteur **ne voit pas** les variables du process ;
- FEEL **refuse** un `if … then … else` inline dans une concaténation de chaînes.

D'où le montage : la tâche CPU s'exécute **en premier** et initialise toujours
`nbAnomalies / origine / ns / cible` ; la tâche replicas s'exécute ensuite et renvoie
`{ }` (contexte vide) quand elle ne trouve rien — la fusion ne réécrit alors aucune variable.
Plus aucun conditionnel n'est nécessaire en aval.

## Garde-fou : namespaces système

La `ServiceAccount camunda-direct` est liée par un **ClusterRoleBinding** : elle peut patcher
des déploiements partout, **y compris `kube-system`** (`kubectl auth can-i patch deployments
-n kube-system --as=system:serviceaccount:pfe:camunda-direct` → `yes`).

Sans garde-fou, une branche par défaut « rollout restart » pouvait donc redémarrer CoreDNS
ou l'opérateur CNPG de sa propre initiative. La passerelle `Routage` évalue en premier :

```feel
list contains(["kube-system","kube-public","kube-node-lease","cnpg-system",
               "calico-system","tigera-operator","falco","local-path-storage","monitoring"], ns)
```

→ sortie `FinProtege` : l'incident est **détecté et tracé dans l'historique Camunda**, mais
**aucune action automatique** n'est exécutée. C'est le principe déjà retenu ailleurs dans le
projet (Sécurité / Control-plane = escalade humaine, zéro action déterministe).

## Résultats mesurés

| Preuve | Résultat |
|---|---|
| Chemin CPU (saturation d'un pod pourtant `Ready`) | détecté à +160 s, action IA exécutée à +356 s |
| Namespace arbitraire `demo-camunda`, inconnu du backend | détecté et remédié en **75 s** (`POD_ERR_IMAGE_PULL` → `ROLLBACK_TO_PULLABLE_IMAGE`) |
| Garde-fou `kube-public` | 3 instances → `FinProtege`, 0 action, `coredns` 2/2 intact |

## Limite connue et assumée

Sur les applications que **le backend HELIOS surveille aussi**, le chemin job-worker répare
en ~45 s, soit **plus vite que le timer de 60 s** de ce process. La fenêtre d'anomalie se
referme avant le cycle Camunda suivant, qui conclut alors légitimement « cluster sain ».

Ce n'est pas un défaut du BPMN mais une course entre deux détecteurs concurrents. Mesure à
l'appui (`kube_deployment_status_replicas_unavailable` présent à +20 s et +40 s, absent
dès +60 s). La preuve du chemin replicas se fait donc sur un namespace hors périmètre du
backend — ce qui est aussi la démonstration de l'architecture « Camunda seul ».

## Exploitation

```bash
python camunda-direct/deploy_bpmn.py        # portée RBAC + déploiement
python camunda-direct/preuve_deux_chemins.py
python camunda-direct/preuve_namespaces.py
```

Les incidents sont déclenchés **depuis le cluster** (SSH sur le master `.153`), jamais depuis
le poste Windows.

### Redémarrer Camunda sans le casser

`c8run start -detached` échappe à systemd : un `systemctl restart` laisse des orphelins et
le port 8080 occupé. Procédure sûre :

```bash
sudo systemctl stop camunda8
ss -tln | grep -E ':8080|:26500|:8086'      # doit être vide
sudo pkill -9 -f 'c8run|zeebe|connector-runtime'   # si des orphelins subsistent
sudo systemctl start camunda8
```

Le token K8s et le truststore sont posés en drop-in sur l'unité `camunda8`, dont le runtime
de connecteurs hérite (ne pas lancer un second service de connecteurs : conflit sur 8086).
