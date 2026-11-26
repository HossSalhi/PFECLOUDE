# Runbook — redémarrage de `.161` pour appliquer le noyau 6.8.0-149 (PRÉPARÉ, NON EXÉCUTÉ)

> **Statut : préparé le 13 nov. 2026 (≈ 11:30 locale). NON EXÉCUTÉ. Rien ne se lance sans un GO explicite et nominatif de l'utilisateur.**
> Décision en vigueur : « PAS DE GO. Reporter la réduction de RAM de `.161`. Maintenir `memsize` à 4096 Mo. Ne pas arrêter de VM, ne modifier
> aucun VMX et ne changer aucune configuration GRUB. »
>
> **Périmètre** : UNE seule VM (`.161` k8s-aiCamunda), **sans changement de `memsize`** (4096 Mo), sans modification du VMX, sans changement de
> vCPU, réseau, disque, Kubernetes ou configuration Camunda. Cette procédure est **distincte** de la réduction de RAM (reportée).
> **Aucune modification de GRUB** n'est faite par ce runbook sans un GO distinct (§4). Aucune suppression de fichier, snapshot ou sauvegarde.
>
> Contexte et analyse : `infra/helios-sot/rapports/RAPPORT-ANALYSE-INSTABILITE-20261113.md`. Ligne de base de `.161` (avant redémarrage) :
> `C:\Users\houss\helios-state\kernel149-prep-20261113\baseline-161-avant-redemarrage-20261113.txt` (+ complément, + `.sha256`).

## 1. État constaté (lecture seule, 13 nov. 11:05)

| Élément | Valeur |
|---|---|
| Noyau en cours | **6.8.0-147-generic** (démarré le 11 nov. 15:43 UTC) |
| Noyaux installés | 6.8.0-147 et **6.8.0-149** (`linux-image`, `linux-modules`, `linux-modules-extra` des deux) |
| `reboot-required` | **oui** : `linux-image-6.8.0-149-generic`, `linux-base`, `libc6` (installés par unattended-upgrades les 12 et 13 nov.) |
| GRUB | `GRUB_DEFAULT=0` (entrée « Ubuntu » = noyau le plus récent = **149**), `GRUB_TIMEOUT_STYLE=hidden`, `GRUB_TIMEOUT=0` → **menu masqué, pas de filet de sécurité** ; `grubenv` vide |
| Menu GRUB (index) | 0 « Ubuntu » ; 1 « Advanced options » → 0 : 149, 1 : 149 (recovery), **2 : 147**, 3 : 147 (recovery) ; 2 « UEFI Firmware Settings » |
| Démarrage | probablement **BIOS hérité** (« BIOS 6.00 », `sda1` de 1 Mo, pas de partition EFI) — **à confirmer à la console** |
| `/boot` | 1,6 Go libres ; `initrd.img-6.8.0-149-generic` 76,6 Mo présent ; `vmlinuz-6.8.0-149` présent |
| Pilotes pour -149 | `e1000` (interface `ens33`), `mptspi`, `vmw_pvscsi`, `vmw_balloon`, `vmw_vmci`, `vmxnet3` présents ; l'initramfs contient `ahci`, `mptbase`, `mptscsih`, `mptspi`, `mptsas`, `nvme`, `vmw_pvscsi` (identique à -147) |
| DKMS | 0 module hors arbre ; `dpkg --audit` : 0 ; unités systemd en échec : 0 |
| Redémarrage automatique | `Automatic-Reboot` **non configuré** : `.161` ne redémarrera pas seule |
| needrestart | `$nrconf{restart} = 'i'` (défaut) : en mode non interactif il redémarre les services après mise à jour (cause du redémarrage de Camunda/Bridge à 07:11 le 13 nov.) |
| Services | tous les services HELIOS et `camunda8` sont `enabled` + `active` ; `pfe-pod-routes.timer` `enabled` (recrée les routes des pods au démarrage) |
| Camunda | `/actuator/health` UP (8 composants), connecteurs UP, Bridge `up=1` avec 6 nœuds routables ; démarrage observé le 13 nov. : **46 s** (06:11:11 → 06:11:57 UTC) |

## 2. Ce que le redémarrage change et ne change pas

* **Change** : noyau 6.8.0-147 → 6.8.0-149 ; `libc6` neuve réellement chargée par tous les processus ; disparition du fichier `reboot-required`.
* **Ne change pas** : `memsize`/vCPU/VMX, réseau, disque, Kubernetes, configuration Camunda, paquets (**aucun `apt upgrade`** supplémentaire), autres VM.
* **Indisponibilité** attendue de `.161` ≈ **4 à 5 min** (estimation : arrêt < 90 s, démarrage système 60–90 s, Camunda ≈ 46 s) ; limite dure **10 min**. Pendant ce temps : Camunda, Bridge, proxy :8100, sentinelle, relais SMTP, mailhog et le minuteur de sauvegarde hors site ne répondent pas ; **aucune remédiation automatique** ; HELIOS peut ouvrir des incidents « infrastructure injoignable » (Camunda/Bridge) qui doivent se **résoudre seuls** au retour.
* `.161` n'est pas dans la table du watchdog Windows (jamais relancée automatiquement). Un **redémarrage invité** (la VM reste allumée côté VMware) ne déclenche pas VM_DOWN et n'utilise **aucun `vmrun`**.

## 3. Portes de sécurité — toutes obligatoires, rejouées juste avant d'agir

| Porte | Condition | Comment la vérifier (lecture seule) |
|---|---|---|
| G0 | **GO nominatif** de l'utilisateur (texte §8) **et** choix du filet GRUB (§4) | message de l'utilisateur |
| G1 | RAM disponible Windows ≥ 1 500 Mo sur 5 échantillons de 10 s (**tous**), pages lues < 5 000/s | `python -I -X utf8 outils-lecture-seule\ram_control.py` ; `powershell -File outils-lecture-seule\host_perf.ps1` |
| G2 | **Aucun gel d'hôte récent** : 0 gel VMware ≥ 60 s dans les 60 min précédentes et 0 épisode multi-VM ; critère strict avant de reconsidérer : 48 h sans gel | `python -I -X utf8 outils-lecture-seule\vmware_log_freezes.py` ; `prom_episodes.py` |
| G3 | Cluster sain : Kubernetes 3/3 Ready, etcd 5/5, CNPG 2/2, Prometheus 25/25, `/readyz` 200 via `.160` et `.162`, 0 incident OPEN/IN_PROGRESS/NEW, 0 workflow RUNNING/CREATED ; incident #12315 (RESOLUTION_FAILED) toléré s'il est le seul | `bash outils-lecture-seule\ctrl_rapide.sh` |
| G4 | Sauvegarde hors site < 24 h et intègre (`gzip -t`, sha256) | `remote_161_backup.sh` (lecture) |
| G5 | `.161` fonctionnelle : Camunda UP, connecteurs UP, Bridge `up=1`/6 nœuds, workers connectés (long polling ≥ 1 sur 2 min pour les 5 types de job) | `ctrl_rapide.sh` |
| G6 | **Créneau** hors des moments à risque : compactions Prometheus (heures paires locales ±10 min), `pg_dump` 03:00 et 03:30, sauvegarde hors site 04:01, mises à jour automatiques 07:00–07:30 ; aucun gel dans l'heure | horloge |
| G7 | **Utilisateur présent**, console VMware de `.161` ouverte et fenêtre active (le clavier doit pouvoir envoyer Shift/Esc au démarrage) | utilisateur |
| G8 | Ligne de base **fraîche** capturée et identique à celle du 13 nov. (hors lignes volatiles) | `verif_post_redemarrage_161.sh --essai-a-blanc` (diff vide attendu) |

Une seule porte en échec = **STOP**, aucune action, rapport à l'utilisateur. Ne jamais abaisser un seuil ni contourner un contrôle.

## 4. Filet de sécurité GRUB — décision à prendre AVANT le GO (aucune option n'est exécutée ici)

Contexte : le menu est masqué (`TIMEOUT=0`). Pour atteindre « Advanced options » il faut maintenir **Shift** (ou appuyer sur **Esc**) pendant le démarrage ; c'est possible mais serré. Si -149 ne démarre pas, la **restauration du VMX n'y change rien** : seul un autre noyau dans GRUB règle le problème.

| Option | Contenu | Filet | Modification de GRUB |
|---|---|---|---|
| **0 — aucune** | On redémarre tel quel. Repli : console, Shift/Esc, « Advanced options » → 6.8.0-147 | faible (dépend du geste) | **aucune** |
| **1 — menu visible 5 s** | `GRUB_TIMEOUT_STYLE=menu`, `GRUB_TIMEOUT=5` | bon (menu à l'écran pendant 5 s) | oui (`/etc/default/grub`, `update-grub`) |
| **2 — essai unique (recommandée si une modification de GRUB est acceptée)** | `GRUB_DEFAULT=saved` ; défaut persistant = 147 ; **un seul** démarrage sur 149 via `grub-reboot` ; promotion de 149 après validation | **très bon** : si -149 plante, un simple « Reset » de la VM redémarre sur -147 | oui (2 modifications, réversibles) |

### Commandes (À NE PAS EXÉCUTER sans GO distinct — elles modifient la configuration d'amorçage de l'invité)

```bash
# Option 1
sudo cp -a /etc/default/grub /etc/default/grub.avant-noyau149-20261113          # copie de sécurité (rien n'est supprimé)
sudo sed -i 's/^GRUB_TIMEOUT_STYLE=.*/GRUB_TIMEOUT_STYLE=menu/; s/^GRUB_TIMEOUT=.*/GRUB_TIMEOUT=5/' /etc/default/grub
sudo update-grub
# retour : sudo cp -a /etc/default/grub.avant-noyau149-20261113 /etc/default/grub && sudo update-grub

# Option 2
sudo cp -a /etc/default/grub /etc/default/grub.avant-noyau149-20261113
sudo sed -i 's/^GRUB_DEFAULT=.*/GRUB_DEFAULT=saved/' /etc/default/grub
sudo update-grub
sudo awk -F"'" '/^[[:space:]]*(menuentry|submenu) /{print NR": "$2}' /boot/grub/grub.cfg    # VÉRIFIER les titres avant d'utiliser les index
sudo grub-set-default "1>2"        # défaut persistant = « Advanced options » > entrée 2 = 6.8.0-147 (index confirmés le 13 nov.)
sudo grub-reboot 0                 # UN SEUL démarrage sur l'entrée 0 « Ubuntu » = 6.8.0-149
sudo grub-editenv list             # attendu : saved_entry=1>2 et next_entry=0
# promotion de 149 seulement après validation (§6, T+30 min stable) : sudo grub-set-default 0
# annulation complète : sudo cp -a /etc/default/grub.avant-noyau149-20261113 /etc/default/grub && sudo update-grub && sudo grub-editenv - unset saved_entry next_entry
```

## 5. Procédure (uniquement après GO)

| Étape | Action | Critère / sortie |
|---|---|---|
| P0 | Rejouer les portes G1 à G8 (§3). Capturer l'heure de début. | toutes vertes, sinon STOP |
| P1 | L'utilisateur ouvre la console VMware de `.161`, fenêtre active. | présent |
| P2 | (Option GRUB choisie, §4 — seulement si 1 ou 2 est autorisée.) | `grub-editenv list` conforme |
| P3 | Quiescence : 0 workflow RUNNING/CREATED ; `sudo sync`. | confirmé |
| P4 | **Redémarrage invité** : `ssh kube@192.168.148.161 'sudo systemctl reboot'`. **Pas de `vmrun`, pas de reset VMware, pas de VMX.** | la connexion SSH tombe |
| P5 | Surveiller : réponse SSH, puis `uname -r`. Limite dure **10 min**. Si Option 0/1 et la VM ne revient pas : voir §7. | SSH revenu |
| P6 | Lancer `bash outils-lecture-seule\verif_post_redemarrage_161.sh --jalon T+5min` (§6). | tous les contrôles OK |
| P7 | Option 2 : **promouvoir** 149 (`sudo grub-set-default 0`) seulement après T+30 min stables. | `grub-editenv list` conforme |
| P8 | Suivre les jalons T+10 min, T+30 min, T+3 h, T+24 h (§6) et consigner le journal (§9). | — |

## 6. Protocole de vérification de stabilité après redémarrage (tâche 5 — à exécuter seulement après le redémarrage)

Outil : `outils-lecture-seule\verif_post_redemarrage_161.sh --jalon <T+…>` (lecture seule ; compare à la ligne de base ; produit un fichier horodaté ; code retour 0 si tout est OK). **Validé le 13 nov. par un essai à blanc avant redémarrage** (tous les contrôles OK, diff vide).

| Jalon | Contrôles | Critère de réussite |
|---|---|---|
| T+0 (SSH revenu) | `uname -r` ; `/var/run/reboot-required` ; `systemctl --failed` ; heure de démarrage | noyau = **6.8.0-149-generic** ; `reboot-required` absent ; 0 unité en échec |
| T+5 min | services de la ligne de base `active` ; Camunda UP (8 composants), connecteurs UP ; Bridge `up=1` et 6 nœuds routables ; workers connectés (long polling ≥ 1 sur 2 min × 5 types) ; routes des pods = base ; pare-feu (empreintes `f354e6e375a0` / `a5345f3e5867`) ; ports en écoute ; modules VMware chargés ; `MemTotal` 3 868 Mo ; swap 4 095 Mo | identique à la base ; 0 message noyau (soft lockup / hung task / OOM / erreur d'E/S) depuis ce démarrage |
| T+10 min | Kubernetes 3/3, etcd 5/5, CNPG 2/2, Prometheus 25/25 (cible `.161` UP), `/readyz` 200 ×2 ; incidents créés depuis le redémarrage (comparer `created_at`, **pas** l'`id`) ; workflows | 0 incident persistant ; incidents « infrastructure injoignable » de la coupure **RESOLVED** ; 0 workflow FAILED depuis le redémarrage |
| T+30 min | `journalctl -k -b` ; `NRestarts` de `camunda8` et `helios-ssh-bridge` ; `helios_bridge_up` ; Zeebe `banned` (base 1) et `pending_incidents` (base 0) ; charge, PSI, mémoire disponible (≥ 1 400 Mo) | 0 lockup ; `NRestarts=0` ; pas **3 zéros consécutifs** de `helios_bridge_up` (les zéros isolés sont connus : 95 / 24 h) |
| T+3 h | idem ; `vmware_log_freezes.py` et `prom_episodes.py` sur la période | aucun gel de `.161` ; si un gel apparaît, le classer (ci-dessous) |
| T+24 h | idem ; passage du minuteur de sauvegarde hors site (03:01 UTC) et d'`apt-daily-upgrade` (≈ 06:10 UTC) | sauvegarde OK ; mise à jour sans régression |

**Règle d'attribution (noyau ou hôte)** — un incident de stabilité est imputé au **noyau -149** seulement si **`.161` est seule touchée** (les autres VM et les compteurs VMware ne montrent rien au même instant) **et** que `journalctl -k` de `.161` signale un défaut de pilote/module, un hung task ou un échec de démarrage. Un gel **simultané sur ≥ 2 VM** (`vmware_log_freezes.py`, `prom_episodes.py`) est un **gel de l'hôte** : il est consigné, il n'invalide pas le noyau. Référence avant changement : 1 épisode de 4 messages en 41 h, attribué à l'hôte.

## 7. Retour arrière (aucune modification du VMX dans aucun cas)

| Cas | Constat | Action (toute modification de GRUB exige un GO distinct) |
|---|---|---|
| A | SSH revenu mais `.161` instable avec -149 (critère §6 non tenu et imputable au noyau) | Option 2 : `grub-set-default "1>2"` déjà en place → `sudo systemctl reboot` (reste sur 147). Option 0/1 : `sudo grub-reboot "1>2"` puis `sudo systemctl reboot` (demande `GRUB_DEFAULT=saved`, donc un GO GRUB) ; sinon console + Shift/Esc → « Advanced options » → 6.8.0-147 |
| B | La VM **ne répond pas** après 10 min | Console : observer l'écran. Option 2 : **Power → Reset** (redémarrage dur, **GO explicite requis**) → GRUB démarre 147. Option 1 : menu visible, choisir 147. Option 0 : Shift/Esc au démarrage → 147 |
| C | Le noyau démarre mais Camunda ne revient pas | Ne **pas** redémarrer en boucle. `journalctl -u camunda8 -b` ; comparer à la ligne de base ; rapport à l'utilisateur ; aucune action sans GO |
| D | Gel d'hôte pendant l'opération | Attendre la reprise (≤ 5 min observés), ne rien relancer ; consigner ; refaire le contrôle G2 avant toute reprise |

## 8. Texte du GO à copier (nominatif)

« **GO : redémarrer l'invité de `.161` (k8s-aiCamunda) pour appliquer le noyau 6.8.0-149 — redémarrage invité (`sudo systemctl reboot`) de cette seule VM, sans changer `memsize` ni le VMX, filet GRUB : [option 0 / option 1 / option 2], à [heure], console VMware ouverte ; un NotReady auto-résolu en moins de 5 min ne déclenche pas de retour arrière.** »

## 9. Journal d'exécution (à remplir)

| Heure | Étape | Résultat | Preuve |
|---|---|---|---|
| | | | |

## 10. Ce que ce runbook ne fait pas

Pas de changement de `memsize` ; pas d'arrêt de VM par `vmrun` ; pas de modification de VMX ; pas de GRUB sans GO distinct ; pas d'`apt upgrade` ni de `needrestart` manuel ; pas de modification de Camunda, du Bridge, de Kubernetes, du réseau ou du disque ; pas d'action sur `.153` (jamais arrêtée), `.160` (`DETECT_ONLY`) ni les autres VM ; ni VIP, Keepalived ou VRRP ; aucune suppression de PVC, PV, données PostgreSQL, snapshot ou sauvegarde ; aucun commit/push.
