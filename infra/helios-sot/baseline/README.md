# Baseline de PRODUCTION (instantané du 5 nov. 2026, lecture seule)

Copies **byte-identiques** des artefacts réellement déployés, prises sans rien modifier en production, pour
(1) servir de référence de comparaison, (2) prouver par empreinte ce qui tourne (`MANIFEST.sha256`),
(3) permettre un retour arrière documenté (exports de tâches planifiées et de règles de pare-feu dans `host/`).

| Fichier du dépôt | Chemin en production | Remarque |
|---|---|---|
| `bridge/bridge.py`, `bridge/telemetrie.py` | `.161:/opt/helios-ssh-bridge/` | ≠ `camunda-direct/bridge/bridge.py` (deux lignées divergentes) |
| `detecteur/detecter.py` | `.160:/opt/helios/detecter.py` | sur-ensemble de la copie du dépôt (règles `TARGET_DOWN`, `BRIDGE_DOWN`, `lb2`) |
| `watchdog/helios-node-autoheal.py` | `C:\Users\houss\helios-node-autoheal.py` | machine d'état (verrou, backoff, maintenance) ; boucle autonome + branche master à retirer |
| `watchdog/helios-watchdog-http-only.py` | `C:\Users\houss\` | lanceur HTTP seul (tâche `HELIOS-Watchdog-HTTP`) |
| `watchdog/helios-vmpower-agent.py` | `C:\Users\houss\` | agent legacy `:9999` **non authentifié** (à retirer, décision D9) |
| `host/*` | Démarrage, `C:\Users\houss\`, Planificateur de tâches, Pare-feu Windows | état AVANT modification |

**Sécurité** : le dossier est ignoré par Git (voir `.gitignore`) car `watchdog/helios-node-autoheal.py` contient des
identifiants en clair hérités. Les versions CIBLES du plan (nouveau watchdog, nouveau Bridge) n'en contiennent pas.
**Vérification** : `cd infra/helios-sot/baseline && sha256sum -c MANIFEST.sha256` (ne modifie rien).
