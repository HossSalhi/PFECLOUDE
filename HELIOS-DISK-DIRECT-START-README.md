# HELIOS DISK — correctif « démarrage direct »

Date de préparation : 15 novembre 2026

> **ADDENDUM du 17 novembre 2026 — décision de l'exploitant, À LIRE EN PREMIER.**
> `sudo systemctl start helios-diskfill.service` doit suffire **directement dans la VM, comme `helios-cpuload.service`**, sans dépendre de l'hôte Windows.
> L'attestation de l'agent Windows décrite plus bas est donc **OPTIONNELLE et DÉSACTIVÉE par défaut** : constante `ATTESTATION_HOTE_REQUISE = False` dans
> `infra/helios-sot/diskfill/diskfill.py`. Agent absent ou arrêté = **aucun effet**, aucune requête vers Windows ; les contrôles du nœud (identité, empreintes
> des unités, montage, espace, inodes, plan, cooldown, Prometheus, garde indépendante) s'exécutent à chaque démarrage et refusent sans rien allouer.
> **Tout le code de l'agent, le contrat HMAC et les tests sont conservés** : passer la constante à `True` (nouvelle version, `SHA256SUMS` régénéré,
> réinstallation) rend l'agent de nouveau **obligatoire**, en échec fermé, avec la politique décrite ci-dessous (VERT, ou ORANGE limité à `GEL_HOTE_48H` et
> `E2E_INCIDENTS_DISQUE_ANCIENS`). Les passages ci-dessous décrivent ce **mode optionnel « requis »**.
> Ce que le mode autonome ne vérifie plus : RAM/pagination et gels VMware de l'hôte, état du Backend/Bridge/Camunda ; le diagnostic
> `helios_diskfill_service.py --preflight` les montre, à titre **facultatif**. Voir `infra/helios-sot/RUNBOOK-DISKFILL-SERVICE-158.md` § 18.

## Objectif

Sur `.158`, la commande de lancement reste :

```bash
sudo systemctl start helios-diskfill.service
```

Aucun armement manuel n'est demandé. `ExecStartPre` exécute à chaque appel les contrôles locaux de `.158`, puis demande une attestation à l'agent Windows DISK. L'agent réutilise les collecteurs existants du pré-vol DISK et la porte `DISK_E2E_158`.

## Sécurité intégrée

- Agent Windows indépendant du watchdog VM_DOWN sur `192.168.148.1:9099`.
- Agent DISK lié uniquement à `192.168.148.1:19158`, appelant autorisé uniquement `192.168.148.158`.
- Route unique `POST /v1/preflight`; aucune route de commande, redémarrage VM ou injection.
- HMAC-SHA256 dans les deux sens, horodatage, nonce anti-rejeu en mémoire, réponse bornée et refus si l'agent est absent.
- `.158` vérifie les permissions `root:root 0700` du répertoire de clé et `root:root 0600` du fichier, la signature, la cible, le scénario, l'image Backend et la fraîcheur (30 s).
- Uniquement `VERT` strict avec des raisons `INFO` est accepté. `ORANGE`, `ROUGE`, agent absent, horloge incohérente, réponse périmée ou signature invalide = refus avant création de l'état du lancement et avant toute allocation.
- Les contrôles locaux existants (identité, SHA des unités, montage, espace, inodes, kubelet, Prometheus, incidents, cooldown, garde indépendante, nettoyage) sont conservés.
- `helios-vm-watchdog.py`, son port `9099` et ses règles ne sont pas modifiés.

Le transport utilise HTTP sur le réseau HELIOS privé ; l'authenticité et l'intégrité sont assurées par HMAC, mais le contenu n'est pas chiffré. Le pare-feu restreint le port au seul appelant `.158`.

## Fichiers inclus

Les fichiers sont placés sous leurs chemins du dépôt (`infra/helios-sot/diskfill` et `scripts/helios-ops`). Le paquet ajoute :

- `scripts/helios-ops/helios_disk_host_agent.py` — agent Windows en lecture seule.
- `scripts/helios-ops/install_helios_disk_host_agent.ps1` — installation Windows + provisionnement de la clé sur `.158` + pare-feu + tâche planifiée.
- `scripts/helios-ops/test_helios_disk_host_agent.py` — tests de protocole HMAC et de validation côté Linux.

Le fichier `SHA256SUMS` couvre les trois fichiers SoT du service (`diskfill.py` et les deux unités). L'empreinte du fichier systemd principal est également épinglée dans `diskfill.py`.

## Dépendances déjà présentes dans le dépôt, non incluses dans l'archive source reçue

Le ZIP fourni pour analyse ne contenait pas :

- `infra/helios-sot/bridge/bridge.py` — requis par l'agent/les suites de tests et contrôlé par l'installateur Windows ;
- `camunda-direct/detecteur/detecter.py` — requis par la suite de cohérence.

Ne les remplacez pas par des fichiers inventés. Ils doivent rester présents dans le dépôt HELIOS original. Les suites historiques complètes n'ont pas pu être exécutées dans l'environnement d'analyse faute de ces deux dépendances.

## Vérifications déjà exécutées sur cette copie du paquet

- Compilation Python : 11 fichiers Python compilés sans erreur.
- Tests isolés du nouvel agent : 10/10 réussis (HMAC aller/retour, IP autorisée, méthode/route, rejeu, VERT, ORANGE, signature, cible et fraîcheur).
- `SHA256SUMS` recalculé et comparé au calcul du module hôte : cohérent.
- SHA-256 des deux unités comparés aux constantes `SHA_UNITES` : cohérents.
- Les suites historiques `test_helios_diskfill_service.py` et `test_helios_diskfill_coherence.py` n'ont pas pu démarrer : `infra/helios-sot/bridge/bridge.py` n'était pas dans l'archive.
- Aucun service n'a été démarré ; aucune charge disque n'a été injectée ; aucun hôte/VM n'a été modifié.

## Ordre d'installation dans le dépôt Windows

1. **Sauvegarder** les fichiers existants concernés avant extraction. Le ZIP contient les chemins relatifs au dépôt ; extraire à la racine ne remplace que les chemins contenus dans ce paquet.
2. Vérifier que `infra\helios-sot\bridge\bridge.py` et `camunda-direct\detecteur\detecter.py` existent toujours dans le dépôt original.
3. Depuis `scripts\helios-ops`, lancer le test isolé :

   ```powershell
   python -I -B .\test_helios_disk_host_agent.py
   ```

4. Dans **PowerShell administrateur**, depuis la racine du dépôt, exécuter une seule fois :

   ```powershell
   .\scripts\helios-ops\install_helios_disk_host_agent.ps1
   ```

   Ce script installe la tâche Windows au logon de l'utilisateur courant, crée les règles pare-feu pour `.158` seulement et provisionne une clé aléatoire sur `.158`. Il ne démarre pas le service de remplissage. La session Windows doit rester ouverte pour que la tâche au logon reste disponible ; si l'agent s'arrête, `.158` refusera le démarrage.

5. Vérifier le plan d'installation Linux (lecture seule) :

   ```powershell
   python .\scripts\helios-ops\deploy_diskfill_service_158.py --installer
   ```

6. Après revue du plan, l'installation réelle des trois fichiers sur `.158` exige la commande explicitement confirmée suivante :

   ```powershell
   python .\scripts\helios-ops\deploy_diskfill_service_158.py --installer --confirm INSTALLER-DISKFILL-158
   ```

   Elle sauvegarde les fichiers précédents, installe les trois fichiers SoT, fait `systemd-analyze verify` et `daemon-reload`, puis vérifie l'installation. Elle ne fait ni `enable`, ni `start`, ni injection.

7. Vérifier en lecture seule :

   ```powershell
   python .\scripts\helios-ops\deploy_diskfill_service_158.py --verifier
   ```

8. **Ne lancer la commande `systemctl start` qu'après un diagnostic VERT strict récent.** Le service interrogera de toute façon l'agent Windows à chaque démarrage et refusera toute réponse non VERT.

## État constaté dans la session du 15 novembre 2026

Le pré-vol Windows réel présenté dans la conversation était `ORANGE`, avec 29 gels dans les 48 dernières heures (`GEL_HOTE_48H`) et deux incidents `NODE_DISK_PRESSURE` encore `OPEN` : #1344 sur `.158` et #1339 sur `.155` (créés le 11 septembre 2026). Le nouvel agent n'acquitte aucun ORANGE automatiquement. **Dans cet état, la commande directe doit refuser de lancer le remplissage.** Les incidents ne sont ni fermés ni supprimés par ce correctif ; ils nécessitent une décision et un traitement séparés.

## Limite de validation

Ce paquet est un correctif source préparé et testé partiellement. Il n'est pas déployé. L'installateur PowerShell n'a pas été exécuté dans l'environnement réel. Il reste à exécuter les suites historiques complètes dans le dépôt qui contient le Bridge SoT et le détecteur, puis à réaliser l'installation sans démarrer le service.
