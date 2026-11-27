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
| `HELIOS_AI_ASSIST_PROVIDER` | `…provider` | `mistral` | `mistral` ou `groq` |
| `HELIOS_AI_ASSIST_API_KEY` | `…api-key` | vide | **Clé API (secret)** |
| `HELIOS_AI_ASSIST_BASE_URL` | `…base-url` | selon le fournisseur | Surcharge facultative |
| `HELIOS_AI_ASSIST_MODEL` | `…model` | `mistral-small-latest` / `llama-3.1-8b-instant` | Surcharge facultative |
| `HELIOS_AI_ASSIST_TIMEOUT_SECONDS` | `…timeout-seconds` | `20` | Délai d'un appel |

Une fonction n'est utilisable que si l'interrupteur global **et** son drapeau sont à `true` **et** si la clé est présente.
Désactivée, elle est invisible dans l'interface. Activée sans clé, elle s'affiche avec « non configurée » et son bouton est bloqué.

### Clé API : jamais en clair dans un fichier

La clé vient d'un **secret Kubernetes** créé par l'administrateur (la valeur ne transite jamais par le dépôt) :

```bash
kubectl -n pfe create secret generic helios-ai-assist --from-literal=HELIOS_AI_ASSIST_API_KEY='<la clé>'
kubectl -n pfe set env deployment/backend --from=secret/helios-ai-assist
kubectl -n pfe set env deployment/backend HELIOS_AI_ASSIST_ENABLED=true HELIOS_AI_ASSIST_REPORTS_SUMMARY=true
```

En développement, placer la clé dans `application-local.properties` (non versionné) ou dans la variable d'environnement.

### Activation progressive

1. Créer le secret, puis activer **une** fonction (par exemple `HELIOS_AI_ASSIST_REPORTS_SUMMARY=true`) avec l'interrupteur global.
2. Vérifier l'affichage, puis activer les autres une par une.
3. Retour arrière immédiat : `HELIOS_AI_ASSIST_ENABLED=false` (ou retirer un drapeau) ; l'interface masque aussitôt les fonctions.

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
