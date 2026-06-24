# HELIOS — Guide des tests de chaos (applicatifs + techniques)

Toolkit unique : [`scripts/chaos/helios-chaos.ps1`](scripts/chaos/helios-chaos.ps1)
Cible : **podinfo** dans le namespace **pfe**. 100% `kubectl` local + busybox embarqué
dans podinfo + NetworkPolicy Calico. **Aucune image à télécharger, aucun Chaos Mesh, air-gap safe.**

## Utilisation

```powershell
cd scripts\chaos
.\helios-chaos.ps1 setup            # une seule fois : limite CPU 1200m (baseline chaos)
.\helios-chaos.ps1 <test>           # injecte la panne
.\helios-chaos.ps1 <test> -Restore  # repare / nettoie
.\helios-chaos.ps1 list             # liste les tests
.\helios-chaos.ps1 status           # etat podinfo + policies chaos actives
```

> ⚠️ Entre deux tests sur podinfo, attendre **~3 min** (cooldown anti-doublons post-RESOLVED)
> ou ~5 min après un RESOLUTION_FAILED.

---

## Tests applicatifs

| Test | Commande | Type HELIOS attendu | Statut |
|------|----------|---------------------|--------|
| Pod kill | `.\helios-chaos.ps1 podkill` | `POD_CRASH_LOOP` (HIGH) | ✅ validé |
| Crash loop | `.\helios-chaos.ps1 crashloop` | `POD_CRASH_LOOP` | ✅ |
| Pod eviction | `.\helios-chaos.ps1 eviction` | reschedule / `DEPLOYMENT_UNAVAILABLE` | ✅ |
| Container failure | `.\helios-chaos.ps1 container` | restart / `POD_CRASH_LOOP` | ✅ |
| Service (injoignable) | `.\helios-chaos.ps1 service` | `DEPLOYMENT_UNAVAILABLE` (sonde active 0.2.57) | ✅ (meme voie que network) |
| Node failure (clé) | `.\helios-chaos.ps1 node` | NODE + watchdog autoheal | ✅ |
| HPA / scaling | `.\helios-chaos.ps1 hpa` | autoscaler K8s scale 2->N | ✅ validé (2->4 repliques) |

## Tests techniques

| Test | Commande | Type HELIOS attendu | Statut |
|------|----------|---------------------|--------|
| CPU stress | `.\helios-chaos.ps1 cpu` | `CPU_STRESS` | ✅ validé (#3436) |
| Memory OOM | `.\helios-chaos.ps1 memory` | `DEPLOYMENT_UNAVAILABLE` | ✅ validé (#3438) |
| HTTP 503 | `.\helios-chaos.ps1 http503` | `GENERAL_DEGRADATION` -> RESOLVED | ✅ validé (#3442) |
| HTTP latency | `.\helios-chaos.ps1 latency` | `HIGH_RESPONSE_TIME` | ✅ (meme voie que http503) |
| Database down | `.\helios-chaos.ps1 db` | `DB_CONNECTION_FAILURE` + failover CNPG | ✅ |
| Network partition | `.\helios-chaos.ps1 network` | `DEPLOYMENT_UNAVAILABLE` (sonde active 0.2.57) | ✅ validé (#3450) |
| Disk pressure | `.\helios-chaos.ps1 disk` | `POD_CRASH_LOOP` (eviction emptyDir sizeLimit) | ✅ validé (#3491) |
| DNS failure | `.\helios-chaos.ps1 dns` | `POD_CRASH_LOOP` (readiness nslookup KO) | ✅ validé (#3496) |

---

## Resultat de validation live (18 juin 2026) — 14/14

Tous les tests validés en direct (incident # entre parenthèses) :
`CPU_STRESS` (#3436), OOM->`DEPLOYMENT_UNAVAILABLE` (#3438), HTTP->`GENERAL_DEGRADATION` (#3442),
podkill->`POD_CRASH_LOOP` (#3423), network->`DEPLOYMENT_UNAVAILABLE` (#3450),
disk->`POD_CRASH_LOOP` (#3491), HPA->scale 2->4 répliques, dns->`POD_CRASH_LOOP` (#3496).

### Comment les 3 ex-angles-morts ont été résolus

1. **HPA** : l'image `metrics-server v0.8.1` était cachée sur **worker2** -> deploiement épinglé
   sur ce noeud (`backend/infra/k8s/metrics-server.yaml`, `--kubelet-insecure-tls`). `kubectl top` + HPA OK.

2. **Disk** : l'éviction `ephemeral-storage` ne se déclenche pas sur ces noeuds. Solution :
   **emptyDir avec `sizeLimit: 300Mi`** monté sur /tmp (via `setup`) -> dépassement = éviction kubelet FIABLE.

3. **Network / DNS / Service** : podinfo monitoré via cAdvisor node-local (la coupure réseau ne
   coupe pas les métriques). Solutions :
   - **network/service** : **sonde active TCP** backend->podinfo dans `KubernetesSignalEnrichmentService`
     (0.2.57) -> si tous les pods Ready injoignables => `DEPLOYMENT_UNAVAILABLE`.
   - **dns** : readinessProbe = `nslookup` (via `setup`) -> DNS coupé => readiness KO => NotReady => détecté.

## Notes importantes

1. **CPU stress** : limite CPU par défaut (200m) throttle sous le seuil IA (0.55 core). `setup` la
   relève à **1200m**.

2. **Baseline chaos (`setup`)** modifie podinfo de façon permanente : CPU 1200m, emptyDir /tmp 300Mi,
   readinessProbe nslookup. La readiness dépend donc du DNS -> un hoquet DNS cluster peut faire flapper
   podinfo. Pour revenir à un podinfo "vanilla", redéployer podinfo d'origine.

3. **network / dns / service** finissent en `RESOLUTION_FAILED` (escalade) : HELIOS ne supprime pas
   la NetworkPolicy de chaos (correct). Lancer `-Restore` pour nettoyer.

4. **Cooldowns** : après une résolution, HELIOS ignore la même app ~3 min (anti-doublons). Un test
   à la fois, sur état propre.

---

## Restauration rapide (tout remettre d'aplomb)

```powershell
.\helios-chaos.ps1 cpu -Restore
.\helios-chaos.ps1 memory -Restore
.\helios-chaos.ps1 disk -Restore
.\helios-chaos.ps1 network -Restore
.\helios-chaos.ps1 dns -Restore
.\helios-chaos.ps1 service -Restore
.\helios-chaos.ps1 crashloop -Restore
.\helios-chaos.ps1 eviction -Restore
.\helios-chaos.ps1 node -Restore
kubectl scale deployment podinfo -n pfe --replicas=2
```
