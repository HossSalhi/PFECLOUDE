# Assistance IA des modules de gestion

Couche **strictement additive** sur Maintenance, Équipes/Tâches, Audit-trail et Rapports. L'IA **propose, l'humain confirme** :
elle ne décide rien, n'exécute rien, et n'invente aucune donnée. Elle ne touche jamais au moteur de détection et de
remédiation (Camunda, routage d'équipe, cœur de `MaintenanceService`).

## Les 4 fonctions

| Fonction | Où | Qui | Calcul (sans IA) | Rôle de l'IA |
|---|---|---|---|---|
| Résumé de rapport | Rapports → détail d'un rapport | Administrateurs | Indicateurs du rapport déjà généré | Résumé exécutif en 4-6 phrases |
| Résumé d'équipe | Équipes → fiche d'équipe | Admin, ou chef **actif** de cette équipe | `TeamSummaryResponse` (avancement, retards, charge) | Synthèse narrative |
| Anomalies d'audit | Audit-trail (admin) et fiche d'équipe | Admin = tout ; chef = membres de SON équipe | Règles : accès refusés, échecs de connexion, suppressions en rafale, changements de rôle, connexions de nuit | Une phrase lisible par constat |
| Assignation de tâche | Formulaire de tâche et fiche d'équipe | Admin, ou chef actif de l'équipe | Score 0-100 : charge, retards, tâches similaires déjà terminées | Justification du classement |
| Fenêtre de maintenance | Page Maintenance | Admin ; chef = ressources de son équipe | Charge CPU par heure (Prometheus, 7 jours) + conflits avec les maintenances planifiées | Recommandation en langage naturel |

**Règle d'accès** : `OrgAccessService.canUseAiAssistFor(acteur, équipe)` / `requireAiAssistFor(...)`. Administrateur et
super-administrateur : toutes les équipes. Chef d'équipe : uniquement l'équipe dont il est le **responsable actif**
(même notion que `pauseAsTeamLead`). Technicien simple : aucun accès. Un refus est tracé (`ACCESS_DENIED`). La matrice
`OrgPermissionPolicy` n'est pas modifiée : un chef d'équipe n'obtient aucun droit supplémentaire en dehors de l'IA.

## Configuration (tout est désactivé par défaut)

| Variable d'environnement | Propriété | Défaut | Rôle |
|---|---|---|---|
| `HELIOS_AI_ASSIST_ENABLED` | `helios.ai.assist.enabled` | `false` | Interrupteur global |
| `HELIOS_AI_ASSIST_REPORTS_SUMMARY` | `…reports-summary.enabled` | `false` | Résumés de rapport et d'équipe |
| `HELIOS_AI_ASSIST_AUDIT_ANOMALIES` | `…audit-anomalies.enabled` | `false` | Anomalies d'audit |
| `HELIOS_AI_ASSIST_TASK_ASSIGNMENT` | `…task-assignment.enabled` | `false` | Assignation de tâche |
| `HELIOS_AI_ASSIST_MAINTENANCE_WINDOW` | `…maintenance-window.enabled` | `false` | Fenêtre de maintenance |
| `HELIOS_AI_ASSIST_PROVIDER` | `…provider` | `groq` | `groq` (gratuit, très rapide) ou `mistral` |
| `HELIOS_AI_ASSIST_API_KEY` | `…api-key` | vide | **Clé API (secret)** |
| `HELIOS_AI_ASSIST_BASE_URL` | `…base-url` | selon le fournisseur | Surcharge facultative |
| `HELIOS_AI_ASSIST_MODEL` | `…model` | `openai/gpt-oss-20b` (Groq) / `mistral-small-latest` | Surcharge facultative |
| `HELIOS_AI_ASSIST_TIMEOUT_SECONDS` | `…timeout-seconds` | `10` | Délai d'un appel |
| `HELIOS_AI_ASSIST_MAX_TOKENS` | `…max-tokens` | `400` | Longueur maximale de la réponse (économie de tokens) |

Une fonction n'est utilisable que si l'interrupteur global **et** son drapeau sont à `true` **et** si la clé est présente.
Désactivée, elle est invisible dans l'interface. Activée sans clé, elle s'affiche avec « non configurée » et son bouton est bloqué.

### Modèle gratuit retenu

`openai/gpt-oss-20b` sur Groq (gratuit : 30 req/min, 1 000 req/jour, 8 000 tokens/min, 200 000 tokens/jour sur le compte du projet). Un appel consomme environ 500-700 tokens : plus de 250 appels par jour. L'effort de raisonnement est réglé sur « low » pour garder la réponse courte et économique. Les modèles disponibles dépendent du compte : vérifier la liste dans la console Groq avant de changer `HELIOS_AI_ASSIST_MODEL`.

### Clé API : jamais en clair dans un fichier

La clé est **ajoutée au Secret existant `backend-secret`** (namespace `pfe`), que le Deployment charge déjà en entier par
`envFrom`. Le nom de la clé est identique à celui de la variable : `HELIOS_AI_ASSIST_API_KEY`. Aucun nouveau Secret n'est
nécessaire. La valeur est saisie masquée et transmise par l'entrée standard : elle n'apparaît ni dans l'historique du
shell ni dans la liste des processus.

```bash
read -rs GROQ_KEY
printf '{"stringData":{"HELIOS_AI_ASSIST_API_KEY":"%s"}}' "$GROQ_KEY" \
  | kubectl -n pfe patch secret backend-secret --type=merge --patch-file /dev/stdin
unset GROQ_KEY

# vérifier la présence de la clé sans afficher sa valeur
kubectl -n pfe get secret backend-secret -o json | grep -o '"HELIOS_AI_ASSIST_API_KEY"'
```

Le patch en fusion conserve toutes les autres clés du Secret. **N'appliquez jamais le dossier Kustomize en bloc**
(`kubectl apply -k`) : `backend-secret.yaml` écraserait les valeurs réelles. `scripts/generate-production-secrets.sh`
préserve cette clé et exige `--rotate` avec `--apply`, car il génère de nouveaux mots de passe de production.

En développement, placer la clé dans `application-local.properties` (non versionné) ou dans la variable d'environnement.

### Activation progressive

La clé est lue au démarrage des pods : tout `set env` ci-dessous redémarre progressivement le backend (2 réplicas).
Attendre `rollout status` et contrôler la consommation dans la console Groq entre deux étapes.

1. **Résumés de rapports et d'équipe** (en premier) :
   `kubectl -n pfe set env deployment/backend HELIOS_AI_ASSIST_ENABLED=true HELIOS_AI_ASSIST_REPORTS_SUMMARY=true`
   puis `kubectl -n pfe rollout status deployment/backend`.
2. **Anomalies d'audit** : `kubectl -n pfe set env deployment/backend HELIOS_AI_ASSIST_AUDIT_ANOMALIES=true`
3. **Assignation de tâche** : `kubectl -n pfe set env deployment/backend HELIOS_AI_ASSIST_TASK_ASSIGNMENT=true`
4. **Fenêtre de maintenance** : `kubectl -n pfe set env deployment/backend HELIOS_AI_ASSIST_MAINTENANCE_WINDOW=true`

### Retour arrière (ne supprime aucune clé préexistante)

1. Une fonction : `kubectl -n pfe set env deployment/backend HELIOS_AI_ASSIST_<FONCTION>=false`.
2. Toute l'IA : `kubectl -n pfe set env deployment/backend HELIOS_AI_ASSIST_ENABLED=false` ; l'interface masque aussitôt les fonctions.
3. Retirer les variables posées : `kubectl -n pfe set env deployment/backend HELIOS_AI_ASSIST_ENABLED-` (et les autres
   `HELIOS_AI_ASSIST_…-`). Cela ne retire que des variables du Deployment, jamais une clé du Secret ni du ConfigMap.
4. Images : `kubectl -n pfe rollout undo deployment/backend` (et `frontend`).
5. Optionnel, uniquement la clé IA :
   `kubectl -n pfe patch secret backend-secret --type=json -p='[{"op":"remove","path":"/data/HELIOS_AI_ASSIST_API_KEY"}]'`.

## Garanties

- **Aucun appel automatique.** Chaque appel IA part d'un bouton (« Résumé IA », « Analyser avec l'IA », « Suggérer un
  responsable », « Recommander une fenêtre »). Aucun planificateur, aucun `@EventListener`, aucun `@PostConstruct`
  (vérifié par test). La détection d'anomalies seule (« Détection seule ») n'appelle pas l'IA.
- **Données minimales.** Le prompt ne contient que des agrégats déjà calculés. Les personnes y sont anonymisées
  (« Technicien A/B… ») puis rétablies à l'affichage ; aucun e-mail, identifiant, mot de passe ni ligne brute de tableau n'est envoyé.
- **Dégradation gracieuse.** Clé absente, quota dépassé (HTTP 429), délai dépassé, fournisseur injoignable ou réponse
  inexploitable donnent un état « Analyse IA indisponible » avec la raison ; les chiffres restent affichés. Jamais d'erreur 500.
- **Aucune action automatique.** Pas d'assignation, pas de planification, pas de pause de maintenance. Le choix humain est tracé.
- **Mesure du taux d'acceptation.** Chaque choix (suggestion suivie, autre membre choisi, suggestion ignorée) est inscrit au
  journal d'audit (module `TASKS`, action `ASSIGN`, « Suggestion IA d'assignation ACCEPTÉE/IGNORÉE »), sans nouvelle table.

## API (`/api/ai-assist`, utilisateur connecté ; le droit est vérifié par les services)

`GET /capabilities` · `POST /reports/{id}/summary` · `POST /teams/{id}/summary` · `GET /audit/anomalies?teamId&days` ·
`POST /audit/anomalies/explain?teamId&days` · `POST /tasks/recommendation` · `POST /tasks/recommendation/feedback` ·
`POST /maintenance/window`.
