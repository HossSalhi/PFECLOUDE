# Runbook — déclencher un incident CPU sur `.158`, `.155` ou `.153` et être sûr qu'HELIOS le traite

*Rédigé le 12 nov. 2026 après les tests du 11-12 nov. (6 cycles CPU réels résolus en 60-79 s) et la recherche de toutes les causes de « rien ne se passe ».*

## 1. La commande à retenir
```
python scripts/helios-ops/helios_cpu_test.py            # PRÉ-VOL des 3 nœuds, lecture seule : vert / orange / rouge + pourquoi
python scripts/helios-ops/helios_cpu_test.py --go       # lance la charge sur les nœuds au vert/orange, puis suit jusqu'à RESOLVED
python scripts/helios-ops/helios_cpu_test.py --go 155   # un seul nœud
```
Manuel (équivalent) : `sudo systemctl start helios-cpuload.service` sur le nœud (charge de laboratoire : **2 workers à 100 % sur `.158` depuis le 17 nov. 2026**, 3 workers à 90 % avant et sur `.155`/`.153` non modifiés ; priorité basse, arrêt automatique à 45 min, 30 min sur `.153`). **Ne lancez jamais sans pré-vol vert : un test qui « ne fait rien » ne le dit pas.**

## 2. Ce qui doit se passer (chronologie mesurée)
| Temps | Événement |
|---|---|
| T+0 | `systemctl start` ; CPU du nœud ≈ 97 % après ~3 min |
| T+7 min 40 s à 8 min | le détecteur élit `NODE_HIGH_CPU` (règle : > 90 % en continu : `rate[3m]` + fenêtre `[5m:30s]`) ; incident créé (source `PROMETHEUS`) |
| +5 à +10 s | action `HOST_SERVICE_CPU_QUOTA` `COMPLETED` (`CPUQuota=25%`, plafond TEMPORAIRE posé par le Bridge) |
| +60 à +79 s | incident `RESOLVED` ; la charge s'arrête |
| +4 min | le plafond temporaire expire seul (≈ 240 s, mesuré le 12 nov. : posé 11:47:35, échéance 11:51:35 ; le défaut de 900 s du Bridge est surchargé par le backend) ; avant, une charge relancée est bridée à 25 % et ne déclenche rien |
| *Plusieurs nœuds* | HELIOS traite les incidents UN PAR UN (une anomalie élue par cycle, une remédiation à la fois) : le 12 nov., trois nœuds lancés ensemble ont donné deux fois des incidents à environ +8, +10 et +12 min (11:36 : +8:28 / +9:29 / +11:28 ; 13:10 : +7:45 / +9:47 / +11:47), chacun résolu en 60-82 s ⇒ compter ≈ 12 min pour trois nœuds |

### Ce que montre `top -d 1` (relevé réel du 17 nov. 2026 sur `.158`, charge lancée à 18:23:35 locale) et réglage du 17 nov.
**En-tête `%Cpu(s)`** pendant le plateau (18:24:30-18:31:30) : `us` 89,0 % (84,9-90,6), `sy` 5,3 %, `si` 1,5 %, `id` 3,6 % ⇒ **≈ 96 % occupé** (Prometheus, fenêtres de 30 s ; 89 % dès 25 s après la commande, > 90 % de 18:24:15 à 18:31:30 = 7 min 15 s). **Lignes `helios-cpuload`** (colonne `%CPU`) : ≈ **55 %** chacune avec 3 workers (le service a consommé 1,64 cœur en moyenne : 3 × 90 % sur 2 vCPU ne peut pas donner plus de ≈ 66 % chacun). À 18:31:27 HELIOS a créé l'incident #13190 `NODE_HIGH_CPU`, posé `CPUQuota=25%` (`top` retombe à ≈ 30 %) et résolu à 18:32:27 ; le service s'est arrêté.
**Réglage du 17 nov. (`.158` uniquement)** : `CPU_WORKERS=2`, `CPU_TARGET=100` (un worker par vCPU, cycle plein) pour que **chaque ligne `helios-cpuload` dépasse 80 %** (estimation ≈ 86 %) en gardant l'en-tête à ≈ 98 % ; détection (> 90 % en continu, ≈ 8 min) et remédiations inchangées ; sauvegarde `helios-cpuload.service.AVANT-CPU80-20261117T173705Z` (SHA `ac36f59e…` → `b8bce077…`) ; **à valider par un lancement** (non fait par l'assistant). Retour arrière : restaurer la sauvegarde puis `systemctl daemon-reload`. ⚠️ Relancer la charge **moins de ≈ 4 min après le plafond de HELIOS** la bride à 25 % (`top` ≈ 30 %) : vérifier `systemctl show helios-cpuload.service -p CPUQuotaPerSecUSec` = `infinity` avant de lancer.

## 2 bis. Pourquoi « rien ne se passe » : toutes les causes connues et leur parade
| # | Cause | Parade |
|---|---|---|
| 1 | **Le détecteur n'élit qu'UNE anomalie par cycle** (la 1re dans l'ordre des règles) : une anomalie de rang < 11 qui dure (disque de `.160`, mémoire, nœud cordonné, VM éteinte…) masquait le CPU. Cause du « rien ne se passe » du 11 nov. | `DETECTOR-PRIORITY-02` (déployé 11 nov. 22:21) : disque de `.160` < 90 % toléré. `DETECTOR-PRIORITY-03` (**déployé le 12 nov. 2026 à 09:43**) : TOUTE anomalie présente depuis ≥ 10 min passe « de fond ». Pré-vol : contrôle « et si » exécuté par le détecteur déployé |
| 2 | Disque de `.160` ≥ 90 % (urgence disque : prioritaire sur le CPU) ; WAL de Prometheus corrompu ⇒ le disque monte sans fin | `repair_prometheus_wal_160.py` (prêt, autorisation requise) ; pré-vol orange dès 85 % |
| 3 | **Plafond temporaire de HELIOS encore posé** (≈ 4 min) | pré-vol rouge avec les minutes restantes |
| 4 | **Incident CPU encore actif** sur le nœud (`NEW/OPEN/IN_PROGRESS/WAITING_FOR_APPROVAL`) : le backend corrèle la nouvelle détection à lui au lieu d'en créer un (`HostIncidentIngestionService`) | pré-vol rouge ; un incident bloqué se clôt avec l'autorisation de l'exploitant |
| 5 | Anti-flapping du backend (5 min) | **sans effet pratique** : la détection n'a lieu que ≥ 8 min après le démarrage de la charge ; le pré-vol l'affiche en simple information |
| 6 | Supervision non fiable (Prometheus/Grafana KO ⇒ le détecteur répond `NO_INCIDENT`), Bridge inactif, backend absent, cluster dégradé | pré-vol rouge |
| 7 | Délai de détection de ~8 min jugé « trop long » | affiché par le lanceur ; seuil calibré (ne pas le baisser : bruit) |
| 8 | RAM de l'hôte basse (< 1 Go) : gels de VM | pré-vol orange |

Faits établis (code `HostIncidentIngestionService`, base `pfe_db`, historique) : la déduplication backend est par (namespace, app, source) **et** type ; les incidents OPEN d'AUTRES types sur le même nœud ne bloquent pas ; aucun refroidissement de 30 min n'est appliqué à `HOST_SERVICE_CPU_QUOTA` (paires d'actions à 12-13 min d'écart `COMPLETED` en octobre) ; 32 incidents `IN_PROGRESS` anciens existent en base.

## 3. Si le test échoue malgré un pré-vol vert
1. `ssh kube@192.168.148.160 "python3 /opt/helios/detecter.py --diag"` (après PRIORITY-03) : qui est élu, depuis quand ; sinon `python3 /opt/helios/detecter.py` et lire `type` / `matches`.
2. `systemctl show helios-cpuload.service -p ActiveState -p CPUQuotaPerSecUSec -p DropInPaths` sur le nœud.
3. Base : `select id, status, created_at from incident where incident_type='NODE_HIGH_CPU' and app='<nœud>' order by id desc limit 5;`
4. Arrêt manuel de la charge : `sudo systemctl stop helios-cpuload.service`.

## 4. Fichiers
`scripts/helios-ops/helios_cpu_test.py` (+ `test_helios_cpu_test.py`) · `detecter.candidate.py` / `deploy-detecteur-160.sh` / `test_detecter_persistance.py` (PRIORITY-03) · `repair_prometheus_wal_160.py` (+ test) · `infra/helios-sot/baseline/detecteur/detecter.py` (= détecteur déployé) · `rapports/PROGRESSION-MISSION-CLOTURE.md` §12 à §12 septies.
