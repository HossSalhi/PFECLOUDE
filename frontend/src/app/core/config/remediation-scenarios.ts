/**
 * HELIOS — Catalogue déterministe des scénarios de remédiation (couche de PRÉSENTATION).
 *
 * ⚠️ SOURCE DE VÉRITÉ : la décision opérationnelle est prise par le backend déterministe —
 *   `camunda-direct/bridge/bridge.py` (`ACTION_PAR_TYPE_MESURE`) et le ConfigMap
 *   `infra/remediation-action-catalog.yaml`. Ce fichier NE DÉCIDE RIEN : il ne fait que
 *   REFLÉTER, pour l'affichage des Incident Cards et de la page « Décisions de remédiation »,
 *   le mapping réellement implémenté côté backend. À type d'incident identique, il renvoie
 *   toujours la même règle (déterministe et reproductible). Aucun modèle, aucune heuristique.
 *
 * Mapping reflété (bridge.py ACTION_PAR_TYPE_MESURE) :
 *   NODE_HIGH_CPU -> HOST_SERVICE_CPU_QUOTA (quota labo temporaire; validation runtime a confirmer)
 *   NODE_DISK_FULL -> HOST_RECLAIM_DISK (nettoyage borne; E2E a demontrer)
 *   NODE_NOT_READY -> HOST_RESTART_KUBELET ; VM_POWERED_OFF -> RESTART_NODE_VM
 *
 * Toute évolution du mapping backend doit être répercutée ici (et inversement jamais l'inverse).
 *
 * ⚠️ IMPORTANT (cohérence des fiches) : ce catalogue est une **CONFIGURATION DE RÉFÉRENCE par TYPE**
 * d'incident. Il n'indique PAS ce qui a été réellement décidé/exécuté pour un incident donné. La
 * décision et l'exécution réelles d'un incident sont portées par ses enregistrements
 * `incident_action` / `incident_decision` (API backend) — et peuvent DIFFÉRER de cette référence
 * (p. ex. un `NODE_NOT_READY` traité par le chemin Java/Camunda est enregistré en `ESCALATION`, pas en
 * `RESTART_NODE_VM`). L'UI doit donc présenter cette règle comme une référence, et l'action réellement
 * exécutée séparément. Le `validationStatus` ci-dessous qualifie le SCÉNARIO (historique), jamais un
 * incident précis.
 */

export type BusinessScenario = 'CPU' | 'VM_DOWN' | 'DISK';

/** Niveau de validation réel, affiché honnêtement (jamais « validé » sans preuve). */
export type ValidationStatus =
  | 'E2E_VALIDATED_HISTORICAL'   // E2E réussi (daté) ; revalidation sur l'état courant à refaire
  | 'ESCALATION_BY_DESIGN'       // aucune remédiation sûre validée -> escalade assumée
  | 'NOT_DEMONSTRATED_E2E';      // action mappée mais bout-en-bout non démontré

export interface KeyValue { k: string; v: string; }

export interface ScenarioRule {
  scenario: BusinessScenario;
  label: string;
  icon: string;                       // Font Awesome (cohérent avec le reste de l'UI)
  incidentTypes: string[];            // types de détection couverts par ce scénario

  // ── Décision déterministe (règle + configuration) ──
  ruleId: string;
  detectionCondition: string;
  thresholdNote: string;
  observationWindow: string;
  compatibleTargets: string;
  contextPreconditions: string[];

  actionId: string;                   // identifiant d'action RÉEL (jamais inventé)
  actionLabel: string;
  actionParams: KeyValue[];
  executionMode: string;
  guards: string[];
  retryPolicy: string;
  maxDelay: string;
  rollback: string;

  // ── Vérification & escalade ──
  verificationCriteria: string[];
  escalationStrategy: string;

  // ── Honnêteté sur l'état de validation ──
  validationStatus: ValidationStatus;
  validationNote: string;
  configSource: string;               // où vit la décision réelle
}

/** Version de la configuration déterministe affichée (gérée avec ce fichier). */
export const REMEDIATION_CONFIG_VERSION = 'helios-scenarios-2026-11-06';

export const REMEDIATION_SCENARIOS: Record<BusinessScenario, ScenarioRule> = {
  VM_DOWN: {
    scenario: 'VM_DOWN',
    label: "VM eteinte confirmee",
    icon: 'fa-power-off',
    incidentTypes: [
      "VM_POWERED_OFF",
      "VM_DOWN"
    ],
    ruleId: 'RULE_VM_DOWN_RESTART_NODE',
    detectionCondition: "Etat powered-off confirme par la supervision hyperviseur; NODE_NOT_READY seul ne suffit pas",
    thresholdNote: "Une VM doit etre confirmee eteinte avant le choix RESTART_NODE_VM",
    observationWindow: 'Scrutation périodique de l\'état des nœuds',
    compatibleTargets: 'k8s-worker1 (.158), k8s-worker2 (.155), k8s-lb2 (.162) et k8s-master (.153, depuis la consigne du 2026-11-17) : redémarrage automatique (chaîne Camunda) — k8s-monitoring (.160, VM de supervision) : DÉTECTION SEULE (incident + interface, ni e-mail, ni Camunda, aucun redémarrage : rallumage par l\'administrateur)',
    contextPreconditions: [
      'La cible figure dans la table fermée de la politique VM_DOWN (noms exacts, pas d\'alias ni d\'IP)',
      'L\'arrêt est prouvé par vmrun list (2 observations espacées de 30 s) et les sondes réseau échouent',
      'Hors maintenance, hors cooldown (5 min depuis le 18 nov. 2026 ; 30 min auparavant), un seul démarrage par incident'
    ],
    actionId: 'RESTART_NODE_VM',
    actionLabel: 'Redémarrage (power-on) de la VM du nœud',
    actionParams: [
      { k: 'target', v: 'nom du nœud (ex. k8s-worker1)' },
      { k: 'mécanisme', v: 'watchdog hôte -> vmrun start (power-on seulement)' }
    ],
    executionMode: 'Automatique — watchdog hôte (mécanisme natif, sans dépendance externe)',
    guards: [
      'Power-on uniquement : la VM n\'est jamais arrêtée par cette action',
      'Refus de toute cible hors politique ; .153 : cible AUTO depuis le 2026-11-17 (et toujours appelante du watchdog) ; ni `vmrun reset` ni arrêt forcé',
      'Un seul acteur de redémarrage : le backend via le watchdog hôte (le Bridge n\'a aucune capacité de power-on) ; .160 : détection seule, jamais de redémarrage automatique',
      'Idempotent : demande persistée avant l\'appel, un seul POST jamais rejoué, cooldown 5 min (30 min avant le 18 nov. 2026), aucune boucle de démarrage'
    ],
    retryPolicy: "Verification bornee du demarrage reel; une reponse HTTP seule ne valide pas la recuperation",
    maxDelay: 'Délai de vérification configuré (retour Ready attendu)',
    rollback: 'Aucun — l\'action ne fait que rallumer la VM ; échec -> escalade',
    verificationCriteria: [
      'VM allumée selon vmrun (preuve fraîche)',
      'Nœuds Kubernetes (workers .155/.158 ; plan de contrôle .153) : nœud Ready, kubelet actif (Lease), SSH, apiserver 6443 (.153 seulement), etcd sain avec leader, cluster CNPG sain (primaire + réplica), backend disponible',
      'k8s-lb2 : SSH, HAProxy (serveurs UP), chemin API Kubernetes via HAProxy, DNS, membre etcd sain + quorum etcd',
      'k8s-monitoring (rallumée par l\'administrateur, jamais automatiquement) : clôture de l\'incident après SSH, Prometheus, Grafana, Loki, entrées du détecteur, HAProxy, DNS, membre etcd sain',
      'Deux passes consécutives persistées, espacées d\'au moins 15 s — jamais de RESOLVED sur un simple ping ou « Powered On »'
    ],
    escalationStrategy: "Si les criteres echouent, enregistrer FAILED avec les preuves; ne jamais declarer RESOLVED",
    validationStatus: 'NOT_DEMONSTRATED_E2E',
    validationNote: 'Mise à jour 2026-11-17 : .153 devient une 4ᵉ cible AUTO dans le dépôt (HA + auto-rallumage) — déployée et armée côté backend/watchdog le 17 nov. 2026 (ce catalogue de l\'interface ne sera à jour qu\'au prochain build du frontend), essai réel du 17 nov. 2026 : rallumée automatiquement, résolue en 2 min 37 s (VALIDÉE). Politique VM_DOWN du 2026-11-06 (3 cibles automatiques : .155 .158 .162 ; .160 en détection seule) : validée hors production (tests unitaires et d\'intégration). Essais réels du 2026-11-06 : .158 et .162 rallumées automatiquement avec preuves de récupération ; .155 rallumée mais vérification de santé non confirmée (réplique PostgreSQL à reconstruire). L\'E2E du 2026-10-08 concernait l\'ancien mécanisme.',
    configSource: "infra/helios-vm-policy.yaml : AUTO k8s-worker1/k8s-worker2/k8s-lb2/k8s-master (Camunda ; k8s-master depuis le 2026-11-17), DETECT_ONLY k8s-monitoring (interface seulement) ; DENY_RESTART conservé (aucune VM)"
  },

  DISK: {
    scenario: 'DISK',
    label: "Saturation disque - nettoyage hote cible",
    icon: 'fa-hard-drive',
    incidentTypes: ['NODE_DISK_FULL', 'NODE_DISK_PRESSURE'],
    ruleId: "RULE_DISK_RECLAIM",
    detectionCondition: "Seuil disque depasse ou DiskPressure observe sur le noeud",
    thresholdNote: 'Seuil d\'occupation disque configuré (déclencheur NODE_DISK_FULL)',
    observationWindow: 'Scrutation périodique des métriques disque du nœud',
    compatibleTargets: 'Nœud du cluster (commande hôte in-cluster)',
    contextPreconditions: [
      "La cible est un vrai noeud Kubernetes autorise",
      "Le nettoyage est borne aux journaux et fichiers de test explicitement autorises",
      "Aucune suppression generale d images ou de volumes"
    ],
    actionId: "HOST_RECLAIM_DISK",
    actionLabel: "Nettoyage hote cible des journaux et artefacts autorises",
    actionParams: [
      { k: "target", v: "nom du noeud autorise" },
      { k: "cleanup", v: "journaux et artefacts de test explicitement autorises" },
      { k: "forbidden", v: "crictl rmi --prune et suppression generale de fichiers" }
    ],
    executionMode: "Automatique uniquement sur une cible autorisee et avec un nettoyage borne",
    guards: [
      "Ne supprimer aucun volume ni fichier metier",
      "Ne pas executer crictl rmi --prune sur le cluster air-gap",
      "Ne jamais declarer RESOLVED sans mesures apres nettoyage"
    ],
    retryPolicy: "Mesurer avant/apres; echec ou gain insuffisant => FAILED avec preuves",
    maxDelay: 'Délai de vérification configuré',
    rollback: "Les suppressions ne sont pas reversibles; les chemins autorises doivent rester explicites",
    verificationCriteria: [
      "Espace libre mesure avant/apres avec df -B1 /",
      "Occupation des inodes mesuree avant/apres avec df -i /",
      "DiskPressure leve si cette condition etait presente"
    ],
    escalationStrategy: "Si le nettoyage ne corrige pas le probleme, enregistrer FAILED avec les preuves",
    validationStatus: 'NOT_DEMONSTRATED_E2E',
    validationNote: "Mapping configure; execution et gain disque restent a demontrer de bout en bout",
    configSource: "Action canonique HOST_RECLAIM_DISK; aucune commande crictl rmi --prune"
  },

  CPU: {
    scenario: 'CPU',
    label: "CPU eleve - quota temporaire du service de charge controle",
    icon: 'fa-gauge-high',
    incidentTypes: ['NODE_HIGH_CPU', 'NODE_CPU_SATURATION'],
    ruleId: "RULE_CPU_SERVICE_QUOTA",
    detectionCondition: 'CPU d\'un nœud au-dessus du seuil pendant la fenêtre d\'observation',
    thresholdNote: 'Seuil CPU nœud configuré (déclencheur NODE_HIGH_CPU)',
    observationWindow: 'Fenêtre d\'observation configurée',
    compatibleTargets: "Uniquement la cible autorisee hebergeant helios-cpuload.service",
    contextPreconditions: [
      "helios-cpuload.service est present et actif sur la cible autorisee",
      "La valeur cpu.max initiale est capturee avant toute modification",
      "Le scenario et la cible correspondent au laboratoire controle"
    ],
    actionId: "HOST_SERVICE_CPU_QUOTA",
    actionLabel: "Limitation temporaire du quota CPU du service controle",
    actionParams: [
      { k: "service", v: "helios-cpuload.service" },
      { k: "cpu.max", v: "25000 100000 (25% d un coeur)" },
      { k: "lease", v: "240 secondes" },
      { k: "restauration", v: "restaurer exactement la valeur initiale" }
    ],
    executionMode: "Automatique uniquement pour le scenario de laboratoire preconfigure",
    guards: [
      "Limiter uniquement helios-cpuload.service sur la cible autorisee",
      "Capturer puis restaurer exactement le quota initial apres 240 secondes",
      "Ne pas remplacer cette action par pkill ou redemarrage du pod"
    ],
    retryPolicy: "Tentatives bornees; si action ou restauration echoue, FAILED avec preuves",
    maxDelay: "240 secondes de limitation, puis verification de restauration du quota",
    rollback: "Restaurer exactement la valeur cpu.max initiale capturee avant action",
    verificationCriteria: [
      "Mesures CPU avant/apres prises sur des fenetres comparables",
      "cpu.max passe temporairement a 25000 100000",
      "helios-cpuload.service reste actif",
      "Apres 240 secondes, cpu.max revient exactement a sa valeur initiale"
    ],
    escalationStrategy: "Si les criteres echouent, enregistrer FAILED avec les preuves; ne jamais declarer RESOLVED",
    validationStatus: 'NOT_DEMONSTRATED_E2E',
    validationNote: "Collection-03 EP007 constitue une preuve historique de laboratoire; le raccordement runtime actuel reste a valider",
    configSource: "Collection-03 EP007; action canonique HOST_SERVICE_CPU_QUOTA; cette carte seule ne prouve pas l execution runtime"
  }
};

/** Index type d'incident -> scénario métier (déterministe). */
const TYPE_INDEX: Record<string, BusinessScenario> = (() => {
  const idx: Record<string, BusinessScenario> = {};
  (Object.keys(REMEDIATION_SCENARIOS) as BusinessScenario[]).forEach(s => {
    REMEDIATION_SCENARIOS[s].incidentTypes.forEach(t => { idx[t.toUpperCase()] = s; });
  });
  return idx;
})();

/** Scénario métier d'un type d'incident, ou null si aucun scénario déterministe ne le couvre. */
export function scenarioForIncidentType(incidentType: string | null | undefined): ScenarioRule | null {
  if (!incidentType) { return null; }
  const s = TYPE_INDEX[incidentType.trim().toUpperCase()];
  return s ? REMEDIATION_SCENARIOS[s] : null;
}

/**
 * Présente un libellé hérité (titre d'incident ancien) SANS terminologie IA, sans modifier la donnée
 * persistée. Ex. « Panne nœud (IA: NODE_NOT_READY) » -> « Panne nœud — NODE_NOT_READY ».
 * Purement cosmétique (couche de présentation) ; la base de données n'est jamais réécrite.
 */
export function neutralizeAiText(s: string | null | undefined): string {
  if (!s) { return s || ''; }
  return s
    .replace(/\s*\(\s*(?:IA|AI)\s*:\s*([^)]*)\)/gi, ' — $1')   // "(IA: X)" -> " — X"
    .replace(/\b(?:IA|AI)\s*:\s*/gi, '')                        // préfixe "IA:" résiduel
    .replace(/\bAIOps\b/gi, 'supervision')
    .replace(/\s{2,}/g, ' ')
    .trim();
}

/** Libellé lisible du niveau de validation (pour l'affichage). */
export function validationLabel(status: ValidationStatus): string {
  switch (status) {
    case 'E2E_VALIDATED_HISTORICAL': return 'E2E validé (historique)';
    case 'ESCALATION_BY_DESIGN':     return 'Escalade par conception';
    case 'NOT_DEMONSTRATED_E2E':     return 'E2E non démontré';
  }
}
