# Laboratoire isolé de validation du self-healing (CPU / RAM / DISK / VM_DOWN)

**Statut au 26 nov. 2026 : NON RÉALISÉ.** Prérequis matériels absents : l'hôte (24 Go) est saturé par les 6 VM de production, et aucune VM jetable n'existe. Aucun résultat simulé ne doit être présenté comme une preuve réelle (voir `VALIDATION-SELF-HEALING-L10.md`).

## Architecture cible (rien de ce laboratoire ne touche la production)
| Élément | Choix | Isolation |
|---|---|---|
| Cible jetable | **1 VM worker clonée** (clone VMware *complet* d'un worker éteint, réseau host-only dédié `vmnet-lab`, 1 vCPU / 1,5 Go / 10 Go) | `.vmx` et disque propres ; jamais les VM de production |
| Backend | jar L10 sur le poste (`127.0.0.1:18090`) + PostgreSQL jetable (`:55441`) restauré depuis la copie chiffrée (`infra/backup/helios-offsite-encrypted-backup.py`) | clés/secrets de laboratoire, intégrations de production coupées |
| Camunda | Camunda 8 Run 8.9.0 **dédié** (ports décalés 28080/36500) sur la VM clonée ou sur le poste, BPMN du dépôt déployés | cluster Zeebe distinct de `.161` |
| Bridge | Bridge de test (même code que production) pointé **uniquement** sur la VM clonée (liste blanche = son IP) ; `vmrun` limité au `.vmx` du clone | jeton Bridge de laboratoire |
| Détection | Prometheus/détecteur de laboratoire sur la VM clonée (règles `infra/monitoring/rules`) | pas de `.160` |

## Matrice d'essais (chaque cas rejoué pour RESOLVED **et** ESCALATE)
| Fonction | Injection (sur la VM jetable uniquement) | Attendu RESOLVED | Attendu ESCALATE |
|---|---|---|---|
| CPU | `stress-ng --cpu N` borné dans le temps | détection → décision (allowlist) → action Bridge → métrique redescendue → incident RESOLVED | action refusée (hors allowlist) ou charge qui persiste → incident ESCALATED + notification |
| RAM | `stress-ng --vm` borné | idem avec libération de mémoire vérifiée | échec de libération → ESCALATED |
| DISK | fichier de remplissage borné (`fallocate`) sous le seuil 83 % | nettoyage ciblé, usage < 81 % vérifié | partition non nettoyable → ESCALATED |
| VM_DOWN | `vmrun stop hard` **du clone** | détection → redémarrage Bridge → nœud Ready → RESOLVED | refus (garde « 3+1 », cooldown) ou échec de redémarrage → ESCALATED |

## Critères de preuve
1. Chronologie horodatée détection → décision → action → vérification → statut final, extraite de la base de laboratoire (`incident`, `incident_action`) **et** des journaux Camunda/Bridge.
2. Jobs Camunda réels exécutés (pas de Mockito) ; appel Bridge réel visible dans le journal du Bridge de test.
3. Retour à l'état sain mesuré côté cible (métrique/`vmrun list`), pas seulement le statut en base.
4. Nettoyage : suppression du clone et de la base de laboratoire **après** archivage des preuves.

## Garde-fous
Aucune charge hors du clone ; arrêt du laboratoire si la RAM libre de l'hôte < 2 500 Mo ; un cas à la fois ; le clone ne partage ni disque ni réseau avec la production.