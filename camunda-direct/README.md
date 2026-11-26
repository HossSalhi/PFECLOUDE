# Camunda en accès direct — un seul process, tous les namespaces

> **Mise à jour du 11 nov. 2026 — retrait de l'IA.** La stack IA (service `:8000`, Ollama, collecteur ML, pilote de campagne ML) a été retirée du dépôt
> (archive de sécurité : `helios-state\snapshots\ai-removal-20261106`). Les passages ci-dessous qui décrivent des décisions ou des diagnostics « de l'IA »
> sont **historiques** : la chaîne actuelle est déterministe (détecteur → politique → Bridge/watchdog → vérification). Le BPMN n'a pas été modifié.

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

## Garde-fou : le cluster doit confirmer le diagnostic de l'IA

Mesure : sur un **blocage DNS réel** (NetworkPolicy coupant l'egress), le modèle répond
`POD_IMAGE_PULL_BACKOFF` → `ROLLBACK_TO_PULLABLE_IMAGE` avec **0,7188 de confiance**.
Il s'effondre sur la classe la plus fréquente. Sans contrôle, Camunda exécuterait une
remédiation dont la prémisse est fausse.

La tâche `Verifier` interroge donc kubelet via Prometheus **avant** de router :

```promql
kube_pod_container_status_waiting_reason{namespace="<ns>",
  reason=~"ImagePullBackOff|ErrImagePull|InvalidImageName"} > 0
```

Si l'IA annonce une action « image » que le cluster ne confirme pas
(`imageConfirmee = false`), la passerelle route vers **`FinIncoherent`** : escalade, aucune
action. Même principe que `groundTruthContainerType()` côté backend.

## Accès de Camunda — ce qu'il atteint vraiment

| Cible | Moyen | Vérifié |
|---|---|---|
| Prometheus `.160:9090` | HTTP | 200 |
| Loki `.160:3100` | HTTP | 200 (`/ready` = `ready`) |
| Grafana `.160:3000` | HTTP `/api/health` | 200 |
| Stack IA `.161:8000` | HTTP `/predict` | 200 |
| API Kubernetes `.150:6443` | HTTPS + token SA | v1.29.15 |
| VMs master / worker1 / worker2 | passerelle SSH (ci-dessous) | exit=0 sur les 3 |

Droits de la ServiceAccount `camunda-direct` (vérifiés par `kubectl auth can-i`) :

| Autorisé | Refusé |
|---|---|
| patcher / scaler des deployments (tous namespaces) | supprimer un deployment |
| supprimer des pods, supprimer des networkpolicies | lire les Secrets |
| **lire** les nœuds (`get`/`list`) | créer un pod, **modifier/supprimer/drainer** un nœud |

## Exécution : tout passe par SSH, sans limite RBAC

Depuis la v13, **toutes** les actions passent par un exécuteur unique. La passerelle
traduit l'action de l'IA en commande réelle et la lance au bon endroit :

| Type d'action | Où | Pourquoi |
|---|---|---|
| pods, deployments, scale, netpol, cordon, drain | `kubectl` sur le **master** | le kubeconfig y est **admin** : aucune limite RBAC |
| kubelet, containerd, disque, horloge | `systemctl` sur le **nœud** concerné | hors périmètre de l'API Kubernetes |
| VM éteinte | `vmrun` via le watchdog hôte | SSH impossible sans OS |

Vérifié : ce que la ServiceAccount refusait, la voie SSH l'exécute.

```
lire les Secrets      RBAC=no  -> SSH exit=0
créer un pod          RBAC=no  -> SSH : pod/test-ssh created
supprimer un deploy   RBAC=no  -> SSH : deployment "cible" deleted
```

Une action hors catalogue n'est **pas** exécutée : elle est signalée
(`execute=false`, `action hors catalogue`) plutôt que traduite au hasard.

### Le traçage dit la vérité

Une commande qui échoue est tracée comme un échec, jamais comme un succès :

```
reussi = false
result = ECHEC(exit=1) error: no rollout history found for deployment "ssh-demo"
```

Ce cas est réel : `rollout undo` ne peut rien faire sur un déploiement sans révision
antérieure. Le système le dit, au lieu d'annoter une remédiation fantôme.

## Capacité et jugement : deux choses différentes

Camunda **peut** techniquement tout faire (admin par SSH). Il **refuse** pourtant deux cas,
délibérément :

- **`FinProtege`** — namespaces système. Avec les droits admin, une erreur de diagnostic
  redémarrerait CoreDNS ou l'opérateur CNPG.
- **`FinIncoherent`** — le cluster contredit l'IA. Mesuré : sur un blocage DNS réel, le
  modèle répond « problème d'image » à **0,7188** de confiance.

Ce n'est pas une limite technique, c'est une règle de sûreté. Plus les droits sont larges,
plus le garde-fou compte : le rayon d'impact d'une erreur est désormais le cluster entier.

## Actions au niveau des VMs : la passerelle SSH

L'API Kubernetes ne sait pas relancer un kubelet, purger un journal ni rallumer une VM.
Le bundle de connecteurs Camunda n'a **pas** de connecteur SSH. D'où le service
`helios-ssh-bridge` sur la VM de Camunda, que le connecteur HTTP sait appeler :

```
POST http://127.0.0.1:8099/exec
X-Helios-Token: <jeton>
{"host": "master|worker1|worker2", "command": "..."}
  -> {"exitCode": 0, "stdout": "...", "ok": true}
```

**Périmètre de sécurité** (vérifié) :

| Contrôle | État |
|---|---|
| écoute | `127.0.0.1:8099` uniquement — depuis `.160` : **injoignable (000)** |
| sans jeton | **HTTP 401** |
| cibles | table fixe de 3 nœuds, rien d'autre |
| audit | chaque commande écrite dans `/var/log/helios-ssh-bridge.log` **avant** exécution |
| jeton | posé en drop-in systemd `camunda8` (chmod 600), lu via `{{secrets.HELIOS_BRIDGE_TOKEN}}` |

### Recouvrement avec le watchdog hôte (à connaître)

Le watchdog `helios-node-autoheal.py` (hôte Windows) expose **déjà** une API sur `:9099`
prévue pour Camunda : `POST /restart/<node>` (`vmrun start`) et `POST /host-exec/<node>`
(liste blanche : `restart-kubelet`, `restart-containerd`, `sync-time`, `image-gc`, `reboot`).
Son auto-réparation autonome est **désactivée par choix** — il détecte, Camunda décide.

Les deux voies sont complémentaires, pas redondantes :

- **passerelle SSH `:8099`** — commande libre, mais exige que la VM soit **allumée** ;
- **watchdog `:9099`** — seule voie possible quand la VM est **éteinte** (`vmrun` agit sur
  l'hyperviseur, pas dans l'OS invité).

## Résultats mesurés

| Preuve | Résultat |
|---|---|
| Chemin CPU (saturation d'un pod pourtant `Ready`) | détecté à +160 s, action IA exécutée à +356 s |
| Namespace arbitraire `demo-camunda`, inconnu du backend | détecté et remédié en **75 s** (`POD_ERR_IMAGE_PULL` → `ROLLBACK_TO_PULLABLE_IMAGE`) |
| Garde-fou système (`kube-public`) | 3 instances → `FinProtege`, 0 action, `coredns` 2/2 intact |
| Garde-fou de cohérence (blocage DNS) | 2 instances → `FinIncoherent` (`imageConfirmee=false`), 0 action |
| Non-régression du garde-fou (vrai ImagePull) | prémisse confirmée → action exécutée en 75 s |
| **Panne de VM** (kubelet arrêté sur worker2) | NotReady à +40 s → `systemctl restart kubelet` par SSH à +60 s → **Ready à +60 s** |

## Limites connues et assumées

**1. Le modèle se trompe de diagnostic.** Mesuré : blocage DNS → `POD_IMAGE_PULL_BACKOFF`
à 0,7188. Le garde-fou empêche d'agir à tort, mais **ne produit pas** le bon diagnostic.
Conséquence directe : les branches `NETWORK` et `SCALE` sont câblées et prouvées
exécutables (via le process de test `helios-test-branches`), mais **jamais empruntées dans
le cycle réel** — l'IA ne les demande jamais. Le vrai correctif est un réentraînement, hors
périmètre ici. Ne pas maquiller ce point : il est mesuré, pas supposé.

**2. Une application sans sonde de readiness est invisible.** Vérifié : un deny-all
ingress+egress sur `helios-latency` (aucune `readinessProbe`) laisse le déploiement
`ready=1`. Ce n'est pas propre à HELIOS — Kubernetes lui-même déclare l'app saine. Dès
qu'une sonde dépendante du réseau existe, l'incident devient visible en ~40 s (mesuré sur
`net-probe`).

**3. Course entre les deux détecteurs.** Sur les applications que le backend surveille
aussi, le job-worker répare en ~45 s, plus vite que le timer de 60 s. Mesure :
`kube_deployment_status_replicas_unavailable` présent à +20 s et +40 s, **absent dès +60 s**.
Camunda conclut alors légitimement « cluster sain ». Ce n'est pas un défaut du BPMN. Les
preuves du chemin replicas se font donc hors périmètre du backend.

**4. Loki n'est pas câblé comme déclencheur, par choix.** Il sait attribuer une erreur à un
pod (`filename=/var/log/containers/<pod>_<namespace>_<container>.log`), mais le bruit de
fond est élevé et variable (mesuré : `cnpg-controller-manager` ≈ 105 erreurs/5 min contre 16
pour le backend), sans label `namespace` exploitable. Un seuil fixe serait fragile et
risquerait de masquer un vrai incident. Loki reste donc un signal de **corroboration**
(`nbErreursLogs`), pas de déclenchement.

**5. Délai de propagation.** `kubectl` voit l'indisponibilité ~40 s avant que
kube-state-metrics ne l'expose à Prometheus. Tout test doit **attendre le verdict Camunda**,
pas un délai fixe — sinon on observe un `FinSain` légitime et on conclut à tort à un échec.

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
