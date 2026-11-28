# Haute disponibilité : arrêt d'un nœud (.155, .158 ou .153)

État au 28 nov. 2026. Ce qui est **mesuré** est distingué de ce qui est **déduit de la configuration**.

## Principe
Le cluster supporte **un seul nœud arrêté à la fois**. Les 3 nœuds sont control-plane (etcd, kube-apiserver, controller-manager, scheduler sur chacun) ; seuls `.155` et `.158` exécutent aussi les applications (le master `.153` garde son taint).

## Matrice (une panne à la fois)
| Composant | Placement actuel | Arrêt `.155` | Arrêt `.158` | Arrêt `.153` |
|---|---|---|---|---|
| Répartiteurs HAProxy (`.160`, `.162`) | 2 VM actives, **3 serveurs web chacun** (`.155`, `.158`, `.153`, santé `GET /ping` :8082) | inchangé | inchangé | inchangé |
| Ingress Traefik et ingress-nginx | **3 répliques chacun, une par nœud** (anti-affinité requise ; ajouté sur `.153` le 28 nov.) | 2 restent | 2 restent | 2 restent |
| Frontend | **3 répliques, une par nœud** | 2 restent | 2 restent | 2 restent |
| Backend | **3 répliques, une par nœud** (HPA min=max=3 ; ajouté sur `.153` le 28 nov.) | 2 restent | 2 restent | 2 restent |
| PostgreSQL (CNPG) | **3 instances, une par nœud** (`postgres-7` sur `.158`, `postgres-8` sur `.155`, `postgres-9` sur `.153`) ; réplication en direct | bascule si c'est le primaire ; 2 instances restent | idem | idem : 2 instances restent, bascule si le primaire était sur `.153` |
| **Opérateur CNPG** | **3 répliques, une par nœud, élection de leader** (avant : 1 réplique épinglée sur `.153`) | 2 restent, un survivant prend le bail en 60 s au plus | idem | idem |
| etcd (5 voix) et API Kubernetes | 3 nœuds control-plane | quorum conservé | quorum conservé | quorum conservé |

## Ce qui a changé le 28 nov. 2026 (production)
Opérateur CNPG : suppression de l'épinglage `kubernetes.io/hostname=k8s-master`, passage à 2 répliques, anti-affinité requise par nœud, stratégie RollingUpdate, image `1.28.4` ajoutée sur `.155`. Les pods de la base n'ont pas redémarré. Avant, arrêter `.153` pendant une bascule laissait la base sans primaire (incident du 18 nov.).

## Mesures disponibles
- Arrêt réel de `.155` le 6 nov. : **78,5 s** d'indisponibilité applicative (cause : `.155` portait le leader du controller-manager).
- Bascule de la base lors d'un redémarrage progressif : **≈ 55 s** mesurées.
- **Non remesuré** depuis les changements : les trois arrêts réels de `.158`, `.153` et `.155` n'ont pas été rejoués (autorisation nominative requise pour chacun).

## Ce qui n'est PAS couvert
Deux nœuds arrêtés ensemble, la perte de l'hôte physique (les 6 VM sont sur le même PC), le zéro seconde d'interruption (les délais de détection ne peuvent pas être réduits sans provoquer de faux « NotReady » lors des gels d'hôte). Capacité : workers à environ 80 % de CPU demandé et 700 à 900 Mo de RAM disponible ; ni 3 répliques backend, ni ingress sur le master.

## Avant tout arrêt réel
CNPG à 2/2 et stable, RAM libre de l'hôte ≥ 2 500 Mo, interface VMware fermée, un seul nœud à la fois.

## Retour arrière de l'opérateur
Sauvegarde de l'état précédent : `helios-state\ha-operateur-cnpg-20261128\deploy-operateur-AVANT.yaml`.
```
kubectl -n cnpg-system patch deploy cnpg-controller-manager --type=json -p='[
 {"op":"remove","path":"/spec/template/spec/affinity"},
 {"op":"replace","path":"/spec/replicas","value":1},
 {"op":"add","path":"/spec/template/spec/nodeSelector/kubernetes.io~1hostname","value":"k8s-master"},
 {"op":"replace","path":"/spec/strategy","value":{"type":"Recreate"}}]'
```

## Rôles identiques sur les 3 nœuds (28 nov. 2026, étape 2)
Demande : mêmes rôles sur `.153`, `.155` et `.158`, sans toucher aux ressources des VM.
- **Control-plane** : déjà sur les 3 (etcd, API, controller-manager, scheduler).
- **Ajouté sur `.153`** : Traefik, ingress-nginx et frontend (3 répliques, anti-affinité par nœud), opérateur CNPG (3 répliques), serveurs `node-153` dans les backends `ingress_http` et `ingress_https` des deux HAProxy. Le taint `control-plane` est conservé : seuls ces déploiements ont reçu la tolérance. Traefik avait une règle d'affinité qui l'excluait du master : retirée. Image Traefik `v3.7.17` copiée sur `.153`.
- **Non ajouté : le backend sur `.153`.** Mesuré : `.153` est à 90 % de mémoire (1 415 Mi), un pod backend en consomme environ 430 Mi et demande 512 Mi. Il faudrait augmenter la RAM de la VM (hors périmètre demandé) ou réduire l'empreinte du backend.
- **Vérifié après changement** : site et API en 200 sur `.153`, `.155` et `.158` ; 6 serveurs web UP sur les deux HAProxy ; base saine en 2/2 sans redémarrage ; RAM disponible de `.153` environ 630 Mo (700 Mo avant).
- **Seuil de sécurité** : si la RAM disponible de `.153` passe sous 350 Mo, retirer ces déploiements du master (retour arrière ci-dessous).

### Retour arrière des rôles sur `.153`
- Sauvegardes : `helios-state\ha-3-noeuds-20261128\deployments-AVANT.tgz` (Traefik, ingress-nginx, frontend, opérateur) ; `/etc/haproxy/haproxy.cfg.avant-ha3-20261128` sur `.160` et `.162`.
- HAProxy : restaurer la sauvegarde, `haproxy -c -f`, puis `systemctl reload haproxy`, sur chaque répartiteur, l'un après l'autre.
- Kubernetes : remettre les `replicas`, retirer la tolérance `node-role.kubernetes.io/control-plane` et rétablir l'affinité `hostname In (k8s-worker1, k8s-worker2)` de Traefik, d'après les YAML sauvegardés.

## PostgreSQL sur les 3 nœuds (28 nov. 2026, étape 3)
- **Fait :** `Cluster` CNPG passé de 2 à 3 instances, avec tolérance au taint `control-plane` dans `spec.affinity.tolerations` (anti-affinité requise : une instance par nœud). Sauvegarde préalable : `helios-state\ha-postgres-153-20261128` (dump et état du `Cluster`).
- **Mesuré pendant l'opération (168 s)** : sonde de l'API (`/api/test/health-readiness`) toutes les secondes, **185 requêtes, 0 erreur**. La base est restée disponible.
- **Effet de bord observé :** la tolérance a déclenché une mise à jour progressive du cluster (réplique puis bascule du primaire). **Le primaire est passé de `postgres-8` (`.155`) à `postgres-9` (`.153`).** Réplication vérifiée : 2 réplicas en `streaming`, retard de 1 à 28 ms ; données intactes (utilisateurs, incidents, migration Flyway 33).
- **Primaire ramené sur un worker (28 nov.)** : bascule planifiée `kubectl cnpg promote postgres postgres-8` ; en 16 s le primaire est redevenu `postgres-8` (`.155`), 3/3 sain, `postgres-9` (`.153`) et `postgres-7` (`.158`) en réplication `streaming`. Sonde de l'API pendant la bascule : 25 requêtes, 0 erreur. Données intactes.
- **Manifeste du dépôt :** `backend/backend/infra/k8s/postgres-cluster.yaml` aligné (3 instances et tolérance) pour qu'un futur `kubectl apply` ne supprime pas la 3ᵉ instance.
- **Retour arrière :** `kubectl -n pfe patch cluster.postgresql.cnpg.io postgres --type=merge -p '{"spec":{"instances":2}}'` (CNPG retire l'instance la plus récente) ; la PVC `postgres-9` sur `.153` ne se supprime qu'avec autorisation.

## RAM de `.153` augmentée, puis backend sur les 3 nœuds (28 nov. 2026, étape 4)
**Constat de départ :** `.153` avait 2 304 Mo configurés (2 169 Mo vus par la VM), pas de swap, environ 500 Mo disponibles, et le kubelet évince des pods sous `memory.available < 100Mi` : un backend (environ 430 Mo) y était impossible.
**Opération (GO nommé de l'utilisateur pour `.153`) :** pré-vol vert (interface VMware fermée, aucun `.vmx.lck`, RAM hôte 3 726 Mo, 3 nœuds Ready, etcd 5/5, base 3/3 retard nul, aucun incident) ; sauvegarde du `.vmx` (`helios-statemx-avant-20261128`, SHA-256) ; drapeau de maintenance `k8s-master.flag` (le watchdog aurait sinon rallumé la VM en 84 s avec l'ancienne RAM) ; arrêt propre `systemctl poweroff` à 18:15:56 ; VM éteinte à +115 s ; une ligne modifiée : **`memsize` 2 304 → 3 072 Mo** (les lignes `cleanShutdown` et `softPowerOff` sont réécrites par VMware à l'arrêt, normal) ; `vmrun start` à 18:18:15 ; nœud **Ready 49 s plus tard** ; drapeau archivé (`k8s-master.flag.maintenance-ended-20261128-181959`).
**Mesures pendant tout l'arrêt de `.153`** (sonde externe, 1 s, site et API par les deux HAProxy, 338 mesures) : **3 échecs isolés** (une 502 et deux délais, 2 secondes distinctes, jamais continues) ; le site est resté disponible. Le primaire de base (`postgres-8`, `.155`) n'a pas bougé. L'incident `NODE_NOT_READY` (#13550) a été créé automatiquement à +78 s puis s'est **résolu seul** à 18:19:57 au retour du nœud. Après redémarrage : etcd 5/5, base 3/3 (retard 1 à 4 ms), tous les pods du master revenus (opérateur, Traefik, ingress-nginx, frontend, `postgres-9`).
**Backend sur `.153` :** image `0.2.179` copiée, tolérance au taint ajoutée. **Piège découvert :** l'HPA `backend-hpa` était figé à `min=max=2` et remettait le nombre de répliques à 2 (le backend avait alors quitté `.158`) ; passé à `min=max=3`. Résultat : **3/3, un backend par nœud**, 0 erreur, 64 requêtes API sans échec pendant le changement ; RAM disponible de `.153` stable à environ 1,1 Go.
**Retour arrière :** HPA `min=max=2` (YAML sauvegardé : `/tmp/backend-hpa-AVANT.yaml` sur `.153`, éphémère) ; RAM : restaurer `helios-statemx-avant-20261128\k8s-master.vmx` VM éteinte proprement, avec le drapeau de maintenance.
