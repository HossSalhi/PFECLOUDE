# Rapport — stabilisation PostgreSQL / CNPG, archivage WAL et protection des sauvegardes (6 nov. 2026)

Mission §5 (archivage WAL) et §7 (suppressions automatiques de sauvegardes). Convention : `[MACHINE] [MODE : RO | MODIFIANT]`. Aucun secret n'est consigné.

## 1. État de PostgreSQL / CNPG (mesuré, 21:35-22:30)

| Élément | Constat (mesure directe) |
|---|---|
| Cluster | `postgres` (ns `pfe`), CNPG 1.28.4, image `ghcr.io/cloudnative-pg/postgresql:16`, **créé le 2026-06-06 17:07:35Z** (réinitialisé par `initdb`), 2 instances, `podAntiAffinityType: required`, stockage `local-path` 5 Gi |
| Santé | « Cluster in healthy state » 2/2, primaire `postgres-7` (`.158`), réplique `postgres-8` (`.155`), timeline 118 |
| Réplication | `pg_stat_replication` : `postgres-8 | streaming | async | retard 0 o` ; slot physique `_cnpg_postgres_8` actif (`wal_status=reserved`) ; `replicationSlots.highAvailability.enabled=true` |
| WAL | `wal_level=logical`, `wal_log_hints=on` (indispensable à `pg_rewind`, les checksums de données sont désactivés), `archive_mode=on`, `archive_timeout=300 s`, `wal_keep_size=512 MB`, `max_wal_size=1 GB`, `max_slot_wal_keep_size=-1` (illimité) ; `pg_wal` = 78 fichiers / 592 Mo sur le primaire, 31 fichiers / 480 Mo sur la réplique |
| `archive_command` / `restore_command` | `/controller/manager wal-archive … %p` / `/controller/manager wal-restore … %f %p` (gérés par CNPG) |
| `spec.backup` | **absent** (`null`) ; aucun plugin ; aucun objet `Backup`/`ScheduledBackup` ; CRD `ObjectStore` (plugin barman-cloud) absente ; `firstRecoverabilityPoint` et `lastSuccessfulBackup` vides ; secret `cnpg-backup-s3` **absent** |
| Archiveur | `pg_stat_archiver` : 101 « archivés », 1 échec (le 12:39:41Z : « Refusing to archive WAL when there is a switchover in progress », normal), dernier WAL `…E7` à 20:36Z. **Ces archivages ne sont PAS une sauvegarde** : sans `spec.backup`, `wal-archive` ne stocke rien (aucune destination configurée ; nulle part il n'existe d'objet correspondant) — ne jamais les interpréter comme des WAL conservés |
| Taille | `pfe_db` ≈ 13 Mo (12 107 lignes de données dans le dump de 19:14) |

## 2. Destination durable d'archivage : ce qui EXISTE, ce qui MANQUE (verdict : BLOQUÉ — autorisation et identifiants requis)

**Existe et documenté** : MinIO sur `.160` (`minio.service` actif, `/usr/local/bin/minio server /var/lib/minio/data`, API `:9000` et console `:9001` répondent HTTP 200, `health/live` et `health/ready` OK) ; manifestes du dépôt (`backend/backend/infra/k8s/postgres-cluster.yaml`, `postgres-scheduled-backup.yaml`, `postgres-restore-smoke.yaml`) qui décrivent exactement cette cible : `s3://cnpg-backups/postgres`, `http://192.168.148.160:9000`, secret `cnpg-backup-s3` (clés `ACCESS_KEY_ID`, `SECRET_ACCESS_KEY`, `AWS_REGION`), `target: prefer-standby`, sauvegarde de base toutes les 6 h.

**Constaté dans MinIO (lecture du système de fichiers de `.160`, noms et dates seulement)** : le bucket `cnpg-backups` contient **1 484 objets (147 Mo)** sous `postgres/postgres/` : 22 sauvegardes de base (la plus récente le 2026-05-16) et 1 418 fichiers WAL (du 2026-04-22 au **2026-06-06 02:20**, timeline 37). C'est l'archive de l'**incarnation précédente** du cluster, arrêtée 15 h avant la création du cluster actuel (2026-06-06 17:07Z) : historique de timelines et LSN sans rapport avec l'actuel (timeline 118, LSN `57/…`).

**Ce qui manque** :
1. les **identifiants S3** : le secret `cnpg-backup-s3` n'existe pas ; MinIO n'a que `MINIO_ROOT_USER`/`MINIO_ROOT_PASSWORD` dans `/etc/default/minio` (variables lues par leur NOM seulement) ; aucun utilisateur dédié n'est connu. Je n'invente rien et n'utilise pas les identifiants root pour le cluster ;
2. la **non-collision avec l'archive historique** : activer l'archivage avec le chemin et le `serverName` du manifeste (`postgres` / `postgres`) viserait un dossier NON VIDE d'une autre histoire. CNPG exécute `barman-cloud-check-wal-archive` avant d'archiver et **refuse** (« archive non vide ») : `archive_command` échouerait en boucle et les WAL s'accumuleraient dans `pg_wal` (volume local-path de 5 Gi, `max_slot_wal_keep_size=-1`) jusqu'à la saturation et l'arrêt de PostgreSQL. **Le manifeste du dépôt ne doit donc PAS être appliqué tel quel** ; il faut un `serverName` neuf (ex. `postgres-t118-20261106`) — l'archive historique reste intacte (aucune suppression) ;
3. l'autorisation : modifier le `Cluster` peut, selon la version, déclencher un redémarrage progressif (réplique puis bascule du primaire ≈ 55 s mesurées) — **non établi sans essai** ; c'est une « modification pouvant interrompre la production » ⇒ autorisation spécifique.
Capacité de MinIO : système de fichiers de `.160` à **77 %** (4,2 Go libres) ; les WAL quasi vides compressés (`gzip`) pèsent quelques Ko chacun : suffisant, à surveiller.

## 3. Correction PRÉPARÉE (non appliquée) — procédure d'activation avec contrôles et retour arrière

*Pré-conditions (lecture seule, à rejouer juste avant)* : `kubectl get cluster postgres -n pfe` = healthy 2/2, réplique `streaming` retard 0 ; `curl http://192.168.148.160:9000/minio/health/ready` = 200 ; espace libre `.160` ≥ 2 Go ; sauvegardes pg_dump A/B récentes et restaurées (§4) ; export du Cluster (`helios-state\snapshots\cnpg-cluster-AVANT-20261106.yaml`, déjà pris).

1. **MinIO (autorisation requise)** : créer un utilisateur et une clé dédiés limités au préfixe `cnpg-backups/postgres-t118-20261106/*` (`mc admin user svcacct add` ou équivalent, depuis `.160`, avec les droits root de l'administrateur) ; ne jamais réutiliser les identifiants root.
2. **Secret** (valeurs saisies par l'administrateur, jamais enregistrées dans un fichier du dépôt) : `kubectl -n pfe create secret generic cnpg-backup-s3 --from-literal=ACCESS_KEY_ID=… --from-literal=SECRET_ACCESS_KEY=… --from-literal=AWS_REGION=us-east-1`.
3. **Cluster** (correctif fusionné ; `serverName` NEUF, rétention prudente) :
```yaml
spec:
  backup:
    retentionPolicy: "30d"
    target: prefer-standby
    barmanObjectStore:
      destinationPath: s3://cnpg-backups/postgres-t118-20261106
      endpointURL: http://192.168.148.160:9000
      serverName: postgres-t118
      s3Credentials:
        accessKeyId:     {name: cnpg-backup-s3, key: ACCESS_KEY_ID}
        secretAccessKey: {name: cnpg-backup-s3, key: SECRET_ACCESS_KEY}
        region:          {name: cnpg-backup-s3, key: AWS_REGION}
      wal:  {compression: gzip, maxParallel: 1}
      data: {compression: gzip}
```
   Avant application : `kubectl apply --dry-run=server`, **relire la note de version 1.28.4 sur le redémarrage** ; surveiller `kubectl get pods -n pfe -w` (si un redémarrage progressif démarre, l'opération reste sûre : la réplique redémarre d'abord).
4. **Vérifications (lecture seule)** dans les 10 min : `pg_stat_archiver` (`failed_count` stable, `last_archived_time` récent) ; `kubectl exec postgres-7 -- barman-cloud-check-wal-archive --endpoint-url http://192.168.148.160:9000 s3://cnpg-backups/postgres-t118-20261106 postgres-t118` ; présence d'objets dans le nouveau préfixe côté MinIO ; `pg_wal` qui NE croît PAS ; `kubectl get cluster` toujours healthy 2/2 ; retard de réplication < 1 Mo.
5. **Sauvegarde de base initiale** : `ScheduledBackup` (`immediate: true`, toutes les 6 h, `method: barmanObjectStore`, `target: prefer-standby`) puis `kubectl get backup -n pfe` = `completed` ; `status.firstRecoverabilityPoint` renseigné.
6. **Preuve de restauration** : `barman-cloud-wal-restore` d'un segment archivé vers `/tmp` d'un pod, puis restauration complète dans un cluster jetable (`postgres-restore-smoke.yaml` adapté au nouveau `serverName`) **seulement hors fenêtre d'essai** (RAM des nœuds ≈ 2,5-2,9 Go : un second PostgreSQL ne tient pas à côté d'un essai de VM).
7. **Retour arrière** : `kubectl patch cluster postgres -n pfe --type merge -p '{"spec":{"backup":null}}'` (l'archivage cesse, `pg_wal` se résorbe) ; supprimer le secret et le `ScheduledBackup` ; **le préfixe MinIO n'est jamais supprimé automatiquement**.
8. **Garde-fou à poser en même temps** : alerte sur `pg_stat_archiver.failed_count` croissant et sur la taille de `pg_wal` > 2 Go (déjà relevés par `passive_observe.py` : archiveur et `pg_wal` du primaire), et `max_slot_wal_keep_size=2GB` pour qu'un slot ne puisse pas remplir le volume.

**Risques résiduels** : (a) tant que l'archivage n'est pas activé, la reprise d'un ancien primaire après arrêt brutal reste impossible par `pg_rewind` (segment local absent) : la reconstruction de la réplique par `pg_basebackup` (`rebuild_replica.py`) reste la procédure ; (b) l'archive est sur le même ordinateur physique que le cluster ; (c) saturation de `.160` (77 %).

## 4. Sauvegardes `pg_dump` : inventaire, intégrité, restaurations (mesurés)

| Emplacement | Contenu | Intégrité / restauration |
|---|---|---|
| Volume A (`postgres-backups`, nœud `.158`) | 8 dumps (3 nov. → 6 nov. 20:44Z), 247-278 Ko | `gzip -t` 8/8 OK ; SHA-256 posé pour le nouveau dump (`pfe_db-20261106-204446.sql.gz.sha256` vérifié OK) ; **restaurés pour de vrai** : `…113036` (17:53 local), `…170741` (18:07) |
| Volume B (`postgres-backups-b`, nœud `.155`) | 8 dumps (3 nov. → 6 nov. 20:45Z) | `gzip -t` 8/8 OK ; SHA-256 du nouveau dump vérifié OK ; **restaurés pour de vrai** : `…113034` (18:07), `…170801` (18:08) |
| Hors cluster `.161:/opt/helios-backups/ha01/offsite` | 9 dumps `pg/` (3 nov. 23:15Z → 6 nov. 03:00Z), 9 snapshots etcd (23,3 Mo), `cronjob-a/` 10 fichiers (copie additive des dumps du CronJob A) | `gzip -t` 9/9 OK ; `sha256sum -c` 9/9 OK ; dernier passage du timer : succès 03:00:40Z ; FS 69 % |
| Hôte Windows `helios-state\backups` | `pfe_db-hostcopy-20261106-181423.sql.gz` (276 737 o, sha256 `16065ad4…`), exports des CronJobs et du Cluster (`helios-state\snapshots`) | `gzip -t` OK, marqueur « dump complete », 16 blocs COPY / 12 107 lignes ; pas de restauration jetable de cette copie |
| MinIO `.160` `cnpg-backups` | archive HISTORIQUE (avr.-juin 2026, ancien cluster) : 22 bases + 1 418 WAL | non utilisable pour le cluster actuel (histoire différente) ; conservée |

## 5. Suppressions automatiques de sauvegardes : mécanismes trouvés et protection posée

| Mécanisme (avant) | Défaut | Correction (6 nov.) | Preuve |
|---|---|---|---|
| CronJob A `postgres-backup` (02:00) : `ls -1t … | tail -n +8 | xargs -r rm -f` | supprimait le plus ancien dump dès 7 fichiers, sans condition d'espace ; dump écrit sous son nom définitif (un dump tronqué pouvait masquer un bon dump) | script réécrit (`infra/helios-sot/tools/protect_backups.py`) : `.partial` + `gzip -t` + taille mini + `mv` + SHA-256, **aucune suppression sous 90 % d'occupation du FS** (alerte à 80 %), au-delà jamais en dessous de 30 dumps, jamais ceux de `/backups/.protected`, seulement si les 5 plus récents passent `gzip -t` | export `cronjob-postgres-backup-AVANT-protection-20261106-214424.json` ; **Job de test `bk-prot-a-214438` : 8e dump ajouté, 0 suppression, `gzip -t` 8/8, SHA-256 OK** (contrôle indépendant par le nœud) |
| CronJob B `postgres-backup-b` (02:30) : même rotation | idem (`.partial` déjà présent) | même bloc de rotation sûre | **Job de test `bk-prot-b-214438` : 8e dump ajouté, 0 suppression** |
| Script hors cluster `.161:/usr/local/bin/helios-offsite-backup.sh` (03:00 UTC) : `tail -n +31 | xargs -r rm -f` (pg/ et etcd/) | suppression sans contrôle d'intégrité ni liste de protection | rétention sûre : au-delà de 30, jamais ceux de `.protected`, seulement si les 5 plus récents passent `gzip -t` (pg) / `sha256sum -c` (etcd) ; original conservé en `.bak-20261106T204948Z` sur `.161` et dans `helios-state\snapshots\prechange-20261106` | exécution du bloc sur les données réelles avec `HELIOS_OFFSITE_KEEP=1000` : rc 0, 9 + 9 fichiers inchangés ; effet réel au prochain passage du timer (07 nov. 03:00 UTC) |
| Listes `.protected` | n'existaient pas | créées sur chaque volume : dumps dont la restauration a été VALIDÉE (A : `…113036`, `…170741` ; B : `…113034`, `…170801`) | contenu relu |

**Politique de conservation** : tous les dumps des volumes A/B sont conservés tant que le système de fichiers du nœud reste sous 90 % ; au-delà, seuls les dumps au-delà des 30 plus récents, non protégés, peuvent être supprimés, après contrôle d'intégrité de leurs 5 successeurs ; copie additive hors cluster (`.161/cronjob-a`, 30 dumps + 30 snapshots etcd de rétention sûre) ; copie hors cluster de l'hôte Windows conservée sans rotation. **Limites** : les sauvegardes d'avant le 3 nov. (A/B) ont déjà été éliminées par l'ancienne rotation de 7 ; toutes les copies sont sur le même ordinateur physique ; aucune archive WAL durable n'est active (§2).

## 6. Verdicts

| Tâche | Verdict |
|---|---|
| État CNPG, réplication, lag, streaming | **VALIDÉ** (mesuré à 21:35 et relevé toutes les 15 min par `passive_observe.py`) |
| Inventaire et intégrité des sauvegardes | **VALIDÉ** |
| Protection contre les suppressions automatiques (CronJobs A/B, script hors cluster, listes protégées) | **CORRIGÉ ET REVÉRIFIÉ** (tests unitaires bash 14 + 7 OK ; Jobs de test en production ; contrôle indépendant des nœuds) |
| Archivage WAL durable | **BLOQUÉ / AUTORISATION REQUISE** : destination MinIO `.160` existante mais identifiants S3 absents, archive historique incompatible (nouveau `serverName` obligatoire), impact sur le Cluster non établi |

## 7. Mise à jour du 11 nov. 2026 (`[MODE : RO]` : kubectl get, SELECT, ls/gzip -t en SSH ; aucune action sur CNPG, aucun PVC supprimé, aucune promotion)

| Élément | Constat (11 nov., ≈ 13:00-13:20 locale) |
|---|---|
| Cluster | « Waiting for the instances to become active », **1/2** ; primaire `postgres-8` (`.155`) depuis le 9 nov. 10:55:42Z ; **timeline 127** (118 le 6 nov. : **9 changements de timeline en 5 jours**, conséquences des arrêts de VM et des gels d'hôte) |
| Réplique | `postgres-7` (`.158`) `Pending` (le nœud `.158` est NotReady : hôte sans mémoire) ; `pg_stat_replication` vide ; slot `_cnpg_postgres_7` inactif mais `wal_status=reserved` ; **réplique absente ≈ 6,5 h le 7 nov. (14:22 → 20:53 locale)**, puis de nouveau aujourd'hui |
| WAL | `pg_wal` 47 fichiers / **592 Mo** ; `max_slot_wal_keep_size=-1` : la rétention est illimitée tant que la réplique est absente (alerte de l'observateur à 2 Go ; volume de 5 Gi) |
| Archiveur | 464 « archivés », 0 échec : **toujours une archive nulle** (pas de `spec.backup`) — jamais une sauvegarde |
| PVC | 5 `Bound` : `postgres-5` (112 j, ancien primaire conservé), `postgres-7`, `postgres-8`, `postgres-backups`, `postgres-backups-b` : **aucune suppression** |
| Dumps A/B | volume A (`.158`) **12** dumps (le plus récent 11 nov. 03:51), volume B (`.155`) **12** dumps (11 nov. 03:49) ; FS 65 % / 69 % (< 90 % : aucune suppression possible) ; `.protected` présents |
| Hors cluster `.161` | 13 dumps, le plus récent **11 nov. 09:00:14Z** ; `gzip -t` OK sur les 3 plus récents ; dernier passage du timer `rc=0` |
| Jobs `Error` du 9 nov. | 3 tentatives × 2 CronJobs : « `pg_dump: … the database system is shutting down` » : conséquence de l'arrêt des VM, **pas** de la garde de rotation |

**Ce que cela change.**
1. Le risque principal est la **perte de redondance** : une seule copie vive des données pendant des heures. En cas de perte du disque de `.155` dans ces fenêtres, le point de reprise est le dernier dump (plusieurs heures). L'archivage WAL durable (§2-3) est donc **plus utile qu'estimé le 6 nov.** ; il reste BLOQUÉ (identifiants S3, `serverName` neuf, autorisation). Pour le `serverName` neuf, utiliser un nom du jour (ex. `postgres-t127-20261111`) et non l'exemple du §3.
2. Au retour de `.158`, `postgres-7` doit rejoindre en réplique. S'il reste bloqué comme le 6 nov. (segment WAL absent, aucune archive), la procédure reste `rebuild_replica.py` **sans suppression de PVC**, sur autorisation spécifique : **non lancée**.
3. Surveiller `pg_wal` pendant l'absence de la réplique (592 Mo aujourd'hui).

| Tâche | Verdict au 11 nov. |
|---|---|
| État CNPG | **DÉGRADÉ (1/2), qualifié** : cause = `.158` NotReady (pression mémoire de l'hôte) ; aucune action sans autorisation ; pas « VALIDÉ » |
| Sauvegardes (inventaire, intégrité, garde de rotation) | **VALIDÉ** (12 + 12 + 13 dumps, aucun supprimé, garde en place) |
| Archivage WAL durable | **BLOQUÉ / AUTORISATION REQUISE** (inchangé ; priorité relevée) |
