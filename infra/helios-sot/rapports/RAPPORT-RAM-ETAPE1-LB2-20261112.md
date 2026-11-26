# Rapport — Rééquilibrage mémoire, ÉTAPE 1 : `.162` (k8s-lb2), RAM 1 536 → 1 280 Mo

**Date : 12 novembre 2026 (heure locale = UTC+1), exécution 16:49 → 16:53, mesures jusqu'à 17:16.**
**Verdict : opération RÉUSSIE et sans incident durable ; gain de « mémoire disponible » de l'hôte NON concluant (+97 Mo à T+25 min, puis hausse lente jusqu'à +205 Mo à T+65 min ; la base de comparaison elle-même variait de ±150 Mo). Étape 2 (`.161`) NON autorisée et NON commencée.**

## 1. Périmètre

Autorisation écrite nommée de l'utilisateur : reconfigurer UNIQUEMENT la VM `.162` de 1 536 à 1 280 Mo (arrêt propre, édition de la seule ligne `memsize`, redémarrage `vmrun`), puis mesurer le gain réel. Interdits respectés : aucune action sur `.153`, `.155`, `.158`, `.160`, `.161` (lectures seules) ; aucun test CPU ; aucune modification de Kubernetes, CNPG, etcd, DNS, HAProxy ni de la configuration du watchdog ; aucune suppression de volume, PVC, PV ou sauvegarde ; aucun commit/push/nettoyage Git ; une seule VM arrêtée. Ni VIP, ni Keepalived, ni VRRP.

## 2. Pré-vol (lecture seule, 16:39 → 16:49) — tous les contrôles critiques verts

| Contrôle | Résultat |
|---|---|
| Kubernetes | 3/3 nœuds Ready |
| etcd | 5 membres sains (`/health`), un leader (`etcd_server_has_leader` = 1 sur 5 séries) |
| CNPG | 2/2 instances, « Cluster in healthy state », primaire `postgres-8` |
| Prometheus | 25/25 cibles up |
| HAProxy / API | `.160` et `.162` : `/readyz` 200, web :80 200, web :443 200 |
| DNS | `helios-dns-health` en mode `enforce` sur les deux LB, `.160` et `.162` UP, publication = `192.168.148.162` seule |
| Test CPU | aucun (`helios-cpuload.service` inactif, quota `infinity`, 0 processus de charge sur `.153/.155/.158`) |
| Incidents | aucun en remédiation (dernier : #11239 NODE_NOT_READY `.158`, RESOLVED à 16:38:37) |
| Watchdog | `/health` 200 ; `HELIOS_VMPOWER_ACTIVE_VMS = k8s-worker1,k8s-worker2,k8s-lb2` ; `k8s-lb2` mode AUTO |
| Mémoire hôte | disponible ≈ 1 140-1 470 Mo, pages lues/s médiane ≈ 630-880 (épisode antérieur : `.158` NotReady 16:36:40 → 16:38:37, disponible tombé à 599 Mo à 16:39) |

## 3. Chronologie

| Heure | Événement |
|---|---|
| 16:49:13 | Sauvegarde du VMX original dans `C:\Users\houss\helios-state\vmx-avant-20261112\` (copie vérifiée, lecture seule) |
| 16:49:14 | Drapeau `C:\Users\houss\helios-maintenance\k8s-lb2.flag` créé (fichier vide) |
| 16:49:19 | Watchdog : `k8s-lb2` `maintenance=True`, refus `STATE_RUNNING,MAINTENANCE,…` (lu depuis un pod backend, jeton non affiché) |
| 16:49:39 | Contrôle final VERT → `sudo systemctl poweroff` sur `.162` (seule commande envoyée) |
| 16:49:43 | VMware réécrit le VMX à l'extinction ; 16:49:49 `.162` absente de `vmrun list` ; 16:49:50 confirmé : aucun processus, aucun `.lck` |
| 16:50:31 | Incident #11240 `VM_DOWN k8s-lb2` créé (« arrêt confirmé par vmrun list, 3 observations sur 34 s ») |
| 16:50:39 | Édition de la seule ligne 35 : `memsize = "1536"` → `"1280"` (VM éteinte) |
| 16:50:59 | `vmrun -T ws start "C:\Users\houss\Documents\Virtual Machines\k8s-lb2\k8s-lb2.vmx" nogui` : code 0, 3,4 s, 2 955 Mo disponibles |
| 16:51:57 | Services de `.162` de retour (etcd, API, web, DNS) |
| 16:52:05 | Incident #11240 RESOLVED |
| 16:52:40 | Tous les contrôles de retour en service VERTS |
| 16:53:09 | Drapeau archivé : `k8s-lb2.flag.maintenance-ended-20261112-165309` ; `maintenance=False` constaté ensuite |
| 17:15:33 | Contrôles rejoués à T+24 min : TOUT VERT |

## 4. Empreintes SHA-256

| Fichier | SHA-256 |
|---|---|
| VMX ORIGINAL (sauvegarde, VM allumée) | `A58242AB53AE6F8DD1F7DC225DBC407335390D3E9FB0565A6711FC57E99F10C8` |
| VMX après arrêt propre (réécrit par VMware : `cleanShutdown` et `softPowerOff` FALSE→TRUE) | `83C3C932B37738183F5E91722C38267A954A401CE0AC0A0C7A97C48D50C06E41` |
| VMX après mon édition de `memsize` | `4E741BA3EB3C8C34B523D15922D1F6F12DA6131EFCC50BC181DDA495A251AA01` |
| VMX actuel (VM allumée, réécrit par VMware au démarrage 16:51:02) | `3BABE396CF31B7FC573B228752AFB17D1683DE4C8A004E13DF9248524429F35B` |

**Diff final ORIGINAL ↔ ACTUEL : exactement 1 ligne** (`memsize = "1536"` → `"1280"`), 115 lignes, 3 795 octets, CRLF conservés (115), sans BOM. Les deux lignes `cleanShutdown` / `softPowerOff` passées à TRUE par VMware à l'extinction sont revenues à FALSE au démarrage ; elles n'ont pas été touchées à la main. Le fichier `.vmem` de 1,5 Go a été supprimé par VMware à l'extinction (fichier transitoire) et recréé à 1 280 Mo (1 342 177 280 octets).

## 5. Effets observés pendant la maintenance (enregistreur `monitor_lb2.py`, un échantillon / 5 s)

- **`.162` muette de 16:49:44 à 16:51:57 (2 min 13 s)**, dont VM éteinte 1 min 20 s (16:49:49 → 16:51:09) : etcd, API `/readyz`, web :80, web :443 et DNS de `.162` en échec (27 échantillons).
- **DNS** : le dnsmasq de `.160` bascule sur `.160` seule **10 s** après le premier échec (16:49:54), puis revient sur `.162` à 16:51:57. Le dnsmasq de `.162` répond `162,160` / `160,162` en alternance de 16:51:57 à 16:52:32 puis se stabilise sur `.162`.
- **Aucun échec d'etcd `.153`, `.155`, `.160`, ni de DNS `.160`** pendant tout l'épisode ; HAProxy `.160` a servi API et web sans interruption pendant l'arrêt.
- **Battement bref à 16:51:34-16:51:40** (≈ 25 s après `vmrun start`) : etcd `.158` `/health` (2 échantillons), API `/readyz` via `.160` (1), web :80 via `.160` (1), web :443 via `.160` (2). Il coïncide avec une rafale de relectures disque (10 330 à 32 943 pages lues/s pendant ≈ 40 s) : gel d'E/S du démarrage de VM, déjà documenté sur cet hôte. Aucun nœud NotReady, aucun incident créé en dehors de #11240.
- **Incident #11240** : `VM_DOWN` créé 16:50:31, RESOLVED 16:52:05 ; **aucune action `RESTART_VM` créée, aucune ligne du journal du watchdog** : la demande de relance n'est jamais arrivée avant que la VM soit relancée. La reconnaissance du drapeau est donc prouvée par `/vm/state`, mais son effet (réponse 423) n'a pas été exercé en réel.

## 6. Mémoire — avant / après

| Fenêtre (heure locale) | Disponible Windows (Mo) | Pages lues/s |
|---|---|---|
| Base 16:45:06-16:49:38 (n = 51) | moy **1 404** (min 1 321, max 1 466) | médiane 732, p90 3 653 |
| `.162` éteinte 16:49:50-16:50:58 (n = 13) | moy **3 011** (2 968-3 056) | médiane 798 |
| Démarrage 16:51:00-16:52:30 (n = 18) | moy 1 849 (1 547-2 693) | médiane 5 449, p90 29 629, max 32 943 |
| T+5…10 min | moy 1 502 | médiane 795 |
| T+10…15 min | moy 1 499 | médiane 594 |
| T+15…20 min | moy 1 487 | médiane 567 |
| T+20…25 min | moy 1 516 | médiane 705 |
| **Stabilisé 16:55-17:15 (n = 246)** | **moy 1 501 (1 423-1 578)** | **médiane 674**, p90 2 480 |
| T+30…45 min (17:21-17:36, n = 180) | moy 1 572 (1 495-1 625) | médiane 584 |
| T+45…60 min (17:36-17:51, n = 180) | moy 1 593 (1 533-1 664) | médiane 487 |
| T+60…65 min (17:51-17:56, n = 68) | **moy 1 609 (1 573-1 652)** | médiane 498 |

Les pics de pages lues/s de la base et de la période stabilisée sont en partie causés par mes propres scripts de mesure (dizaines de processus `ssh`/`curl`/`bash`) ; la médiane est l'indicateur retenu.

| Mémoire résidente (WS, vmware-vmx) | 16:39 (avant) | 16:55 | 17:05 | 17:15 |
|---|---|---|---|---|
| `.162` k8s-lb2 | 2 294 | 1 937 | 1 955 | **1 998** (−296) |
| `.158` k8s-worker1 (paginée par Windows) | 2 004 | 1 320 | 1 378 | 1 353 |
| `.161` / `.155` / `.160` / `.153` | 5 553 / 4 701 / 3 277 / 3 074 | 5 554 / 4 703 / 3 277 / 3 075 | 5 554 / 4 703 / 3 277 / 3 076 | 5 555 / 4 703 / 3 277 / 3 076 |
| Somme des 6 VM | 20 903 | 19 866 | 19 943 | 19 962 |

Invité `.162` à T+4 min : `MemTotal` 1 164 Mo (avant 1 416), utilisé 546 Mo (47 %), cache 611 Mo, **aucun swap**, charge 0,10 ; plus gros processus : etcd 161 Mo, haproxy 43 Mo.

**Gain réel :** mémoire résidente de `.162` −296 à −339 Mo (≈ 1,2-1,3 × la baisse de `memsize`). **Mémoire disponible Windows : +97 Mo à T+5…25 min (1 501 contre 1 404), puis une lente hausse jusqu'à 1 609 Mo à T+60…65 min, soit +205 Mo par rapport à la base de 16:45-16:49 (rendement ≈ 0,8 Mo par Mo retiré).** Le résultat reste NON concluant : la base elle-même a varié de 1 681 à 1 404 Mo entre 15:46 et 16:49 sans intervention, et la hausse progressive peut avoir d'autres causes (cache, repagination de `.158`). Les 2,5 Go visés ne sont pas atteints.

**Plafond du plan A′ :** à partir des 1 609 Mo actuels, les −640 Mo de l'étape 2 (`.161` → 3 456) donnent au mieux 2 249 Mo (rendement 1,0) ; il faudrait un rendement de 1,39 pour atteindre 2 500 Mo. Le plan A′ seul ne peut donc pas tenir l'objectif de 2,5 Go ; il faudrait au minimum −896 Mo supplémentaires (par exemple `.161` → 3 328 et `.162` → 1 152, au mieux ≈ 2 505 Mo à rendement 1,0) ou des leviers hors VM.

## 7. Contrôles de santé à T+24 min (tous verts)

SSH `.162` ; haproxy, dnsmasq, helios-dns-health, helios-etcd actifs ; `MemTotal` 1 164 Mo ; etcd 5/5 ; API `/readyz`, web :80 et :443 = 200 via `.160` et `.162` ; DNS republié (publication = `.162` seule, `.160` et `.162` UP, mode enforce, dnsmasq `.160` et `.162` répondent `192.168.148.162`) ; Kubernetes 3/3 Ready ; CNPG 2/2 ; aucun incident actif ; bail du planificateur backend renouvelé ; watchdog `k8s-lb2` RUNNING, `maintenance=False`, refus `STATE_RUNNING` seulement ; Prometheus 25/25 ; `.161` : camunda8 actif, Bridge actif, sonde du Bridge OK, santé Camunda UP (aucune modification de `.161`).

## 8. Observations utiles pour la suite

- **Garde-fou RAM du watchdog** : il refuse tout redémarrage automatique si la mémoire disponible de l'hôte est < 1 500 Mo (`HOST_RAM_LOW`). Il était actif sur les 5 VM à 16:39 (669 Mo) et pendant la maintenance (1 344 Mo), puis levé à 17:15 (1 513-1 526 Mo) : avec un disponible qui oscille autour de 1,5 Go, la relance automatique d'une VM peut être refusée selon l'instant.
- `.162` n'a pas de swap : marge de 287 Mo au pic des 7 derniers jours (877 Mo utilisés pour 1 164 Mo).
- **L'enregistreur a été arrêté par Claude Code à 17:56:37 (≈ T+65 min)** pour « mémoire système critique » alors que la session était inactive ; il n'a pas été relancé (à ne relancer que sur demande). Son journal complet (855 échantillons) et tous les scripts utilisés sont dans `C:\Users\houss\helios-state\ram-rebalance-20261112\` (`monitor_lb2-complet-jusqu-a-17h56.log`). Sur l'ensemble du journal : **aucune sonde en échec après 16:51:57**, soit 64 minutes sans anomalie (etcd 5/5, API et web via `.160` et `.162`, DNS).

## 9. Retour arrière (non déclenché)

Arrêt propre de `.162` (`sudo systemctl poweroff`) ; copier `C:\Users\houss\helios-state\vmx-avant-20261112\k8s-lb2.vmx` à la place du VMX ; vérifier le SHA-256 `A58242AB…10C8` ; `vmrun -T ws start … nogui` ; créer puis archiver le drapeau de maintenance comme ci-dessus.
