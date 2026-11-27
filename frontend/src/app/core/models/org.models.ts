// Modèles des quatre modules « organisation » : maintenance, audit et activités, rapports, équipes et tâches.
// Les valeurs d'énumération sont celles du Backend ; les libellés français sont définis ici pour l'affichage.

export interface Page<T> {
  content: T[];
  totalElements: number;
  totalPages: number;
  number: number;
  size: number;
}

/** Référence légère vers un utilisateur HELIOS existant (le Backend n'expose ni e-mail ni donnée sensible). */
// ── Routage incident → équipe (maintenance en cours) ─────────────────────────
export type RoutingDecision = 'CONTINUE' | 'PAUSE';
export type RoutingState = 'NONE' | 'AWAITING_DECISION' | 'CONTINUED' | 'PAUSED' | 'AWAITING_RESUME' | 'RESUMED';
export type TaskSourceEvent = 'DECISION' | 'FOLLOW_UP' | 'RESUME';

export const ROUTING_STATE_LABELS: Record<RoutingState, string> = {
  NONE: 'Aucun routage',
  AWAITING_DECISION: 'Décision attendue',
  CONTINUED: 'Maintenance poursuivie',
  PAUSED: 'Maintenance suspendue',
  AWAITING_RESUME: 'Reprise proposée',
  RESUMED: 'Maintenance reprise'
};

export interface RoutingView {
  incidentId: number;
  incidentTitle: string;
  incidentType: string;
  incidentStatus: string;
  scope: string | null;
  team: { id: number; name: string; specialty: string | null; lead: UserRef | null } | null;
  maintenance: { id: number; title: string; resourceType: string; resourceName: string; status: string } | null;
  state: RoutingState;
  canDecide: boolean;
  canResume: boolean;
  tasks: { id: number; title: string; event: TaskSourceEvent; status: string }[];
}

export interface UserRef {
  id: number | null;
  username: string;
  displayName: string;
}

// ── Permissions ──────────────────────────────────────────────────────────────

export type OrgPermission =
  | 'MAINTENANCE_VIEW' | 'MAINTENANCE_CREATE' | 'MAINTENANCE_UPDATE' | 'MAINTENANCE_CANCEL' | 'MAINTENANCE_DELETE'
  | 'AUDIT_VIEW_OWN' | 'AUDIT_VIEW_ALL' | 'AUDIT_EXPORT'
  | 'REPORTS_VIEW' | 'REPORTS_GENERATE' | 'REPORTS_EXPORT' | 'REPORTS_SCHEDULE'
  | 'TEAMS_VIEW' | 'TEAMS_CREATE' | 'TEAMS_UPDATE' | 'TEAMS_DEACTIVATE' | 'TEAMS_DELETE' | 'TEAMS_MANAGE_MEMBERS'
  | 'TASKS_VIEW' | 'TASKS_CREATE' | 'TASKS_UPDATE' | 'TASKS_ASSIGN' | 'TASKS_UPDATE_OWN_STATUS';

export interface MyPermissions {
  username: string;
  role: string;
  permissions: OrgPermission[];
}

// ── Maintenance ──────────────────────────────────────────────────────────────

export type MaintenanceStatus = 'PLANNED' | 'IN_PROGRESS' | 'SUSPENDED' | 'COMPLETED' | 'CANCELLED';
export type MaintenanceResourceType = 'APPLICATION' | 'NODE' | 'VM' | 'SERVICE';

/** Équipe porteuse d'une maintenance, avec sa spécialité et son responsable. */
export interface MaintenanceTeam {
  id: number;
  name: string;
  specialty?: ResourceScope | null;
  lead: UserRef | null;
}

export interface Maintenance {
  id: number;
  title: string;
  description: string | null;
  reason: string | null;
  resourceType: MaintenanceResourceType;
  resourceName: string;
  startAt: string;
  endAt: string;
  status: MaintenanceStatus;
  createdBy: UserRef | null;
  responsible: UserRef | null;
  /** Équipe porteuse (optionnelle : absente des maintenances antérieures). */
  team?: MaintenanceTeam | null;
  actualStartAt: string | null;
  actualEndAt: string | null;
  cancelReason: string | null;
  /** Renseignés seulement si le statut est SUSPENDED. */
  suspendedAt?: string | null;
  suspendReason?: string | null;
  suspendIncidentId?: number | null;
  startOverdue: boolean;
  endOverdue: boolean;
  createdAt: string;
  updatedAt: string;
}

export interface MaintenanceRequest {
  title: string;
  description: string | null;
  reason: string | null;
  resourceType: MaintenanceResourceType;
  resourceName: string;
  startAt: string;
  endAt: string;
  responsibleId: number | null;
  /** Envoyée seulement si renseignée (ou null pour effacer en modification) : les anciens appels restent identiques. */
  teamId?: number | null;
}

/** Pause d'une maintenance : un motif et/ou l'incident déclencheur (au moins l'un des deux). */
export interface PauseRequest {
  incidentId?: number | null;
  reason?: string | null;
}

export interface MaintenanceHistoryEntry {
  id: number;
  changedAt: string;
  changedBy: string | null;
  changeType: string;
  details: string | null;
}

export interface MaintenanceResource {
  type: MaintenanceResourceType;
  name: string;
  label: string;
}

export interface MaintenanceQuery {
  status?: MaintenanceStatus | '';
  resourceType?: MaintenanceResourceType | '';
  resource?: string;
  responsibleId?: number | null;
  from?: string;
  to?: string;
  q?: string;
  page?: number;
  size?: number;
}

// ── Équipes et tâches ────────────────────────────────────────────────────────

export type TaskStatus = 'TODO' | 'IN_PROGRESS' | 'ON_HOLD' | 'DONE' | 'CANCELLED';
export type TaskPriority = 'LOW' | 'MEDIUM' | 'HIGH' | 'CRITICAL';

/** Spécialité d'une équipe (enum Java ResourceScope). */
export type ResourceScope = 'VM' | 'CLUSTER' | 'APPLICATION' | 'SERVICE';

export interface Team {
  id: number;
  name: string;
  description: string | null;
  /** Optionnelle : absente ou null = équipe non spécialisée. */
  specialty?: ResourceScope | null;
  lead: UserRef | null;
  active: boolean;
  memberCount: number;
  openTaskCount: number;
  createdBy: UserRef | null;
  createdAt: string;
  updatedAt: string;
}

export interface TeamRequest {
  name: string;
  description: string | null;
  leadId: number | null;
  /** Envoyée seulement si renseignée (ou null pour effacer en modification) : les anciens appels restent identiques. */
  specialty?: ResourceScope | null;
}

export interface TeamMember {
  userId: number;
  username: string;
  displayName: string;
  role: string | null;
  enabled: boolean;
  lead: boolean;
  addedAt: string;
  addedBy: string | null;
  /** Badge calculé : « Chef <spécialité> » ou « Technicien <spécialité> ». */
  roleLabel?: string;
}

export interface TaskSummary {
  total: number;
  byStatus: Record<string, number>;
  overdue: number;
  completionRate: number;
}

export interface MemberTaskStats {
  userId: number | null;
  displayName: string;
  total: number;
  open: number;
  done: number;
  overdue: number;
}

export interface TeamSummary {
  teamId: number;
  teamName: string;
  tasks: TaskSummary;
  members: MemberTaskStats[];
}

export interface TeamTask {
  /** Renseignés pour une tâche créée automatiquement par le routage incident → équipe. */
  sourceIncidentId?: number | null;
  sourceEvent?: TaskSourceEvent | null;
  id: number;
  title: string;
  description: string | null;
  team: { id: number; name: string } | null;
  creator: UserRef | null;
  assignee: UserRef | null;
  priority: TaskPriority;
  status: TaskStatus;
  dueDate: string | null;
  overdue: boolean;
  completedAt: string | null;
  createdAt: string;
  updatedAt: string;
}

export interface TaskRequest {
  title: string;
  description: string | null;
  teamId: number | null;
  assigneeId: number | null;
  priority: TaskPriority;
  dueDate: string | null;
}

export interface TaskQuery {
  teamId?: number | null;
  assigneeId?: number | null;
  status?: TaskStatus | '';
  priority?: TaskPriority | '';
  overdue?: boolean;
  mine?: boolean;
  q?: string;
  page?: number;
  size?: number;
}

// ── Audit et activités ───────────────────────────────────────────────────────

export interface AuditEntry {
  id: number;
  occurredAt: string;
  actorUserId: number | null;
  actorUsername: string | null;
  actorRole: string | null;
  module: string;
  action: string;
  objectType: string | null;
  objectId: string | null;
  summary: string | null;
  result: string;
  clientIp: string | null;
  requestId: string | null;
  hasChanges: boolean;
  oldValues: string | null;
  newValues: string | null;
}

export interface AuditMeta {
  modules: string[];
  actions: string[];
  results: string[];
}

export interface AuditQuery {
  username?: string;
  module?: string;
  action?: string;
  result?: string;
  from?: string;
  to?: string;
  q?: string;
  page?: number;
  size?: number;
}

// ── Rapports ─────────────────────────────────────────────────────────────────

export type ReportType = 'USER_ACTIVITY' | 'MAINTENANCE' | 'TEAMS_TASKS' | 'PLATFORM_ACTIVITY' | 'PERFORMANCE';
export type ReportFrequency = 'DAILY' | 'WEEKLY' | 'MONTHLY';

export interface ReportTypeInfo {
  type: ReportType;
  label: string;
  description: string;
}

export interface ReportItem {
  id: number;
  type: ReportType;
  typeLabel: string;
  title: string;
  periodStart: string;
  periodEnd: string;
  comparePrevious: boolean;
  trigger: 'MANUAL' | 'SCHEDULED';
  status: 'COMPLETED' | 'FAILED';
  summary: string | null;
  generatedBy: string | null;
  generatedAt: string;
}

export interface ReportDetail {
  report: ReportItem;
  errorMessage: string | null;
  content: string | null;
}

export interface ReportMetric {
  label: string;
  value: number;
  unit?: string;
  previous?: number;
  delta?: number;
  deltaPercent?: number | null;
}

export interface ReportSection {
  title: string;
  columns: string[];
  rows: Array<Array<string | number | null>>;
}

export interface ReportChartDef {
  type: 'bar' | 'line' | 'doughnut';
  title: string;
  labels: string[];
  datasets: Array<{ label: string; data: number[] }>;
}

/** Contenu d'un rapport (JSON conservé avec le rapport). */
export interface ReportContent {
  type: string;
  typeLabel: string;
  title: string;
  periodFrom: string;
  periodTo: string;
  previousFrom?: string;
  previousTo?: string;
  generatedAt: string;
  generatedBy: string;
  summary: ReportMetric[];
  sections: ReportSection[];
  charts: ReportChartDef[];
}

export interface GenerateReportRequest {
  type: ReportType;
  from: string;
  to: string;
  compare: boolean;
}

export interface ReportSchedule {
  id: number;
  type: ReportType;
  typeLabel: string;
  frequency: ReportFrequency;
  enabled: boolean;
  createdBy: string | null;
  createdAt: string;
  lastGeneratedAt: string | null;
}

// ── Libellés d'affichage ─────────────────────────────────────────────────────

export const MAINTENANCE_STATUS_LABELS: Record<MaintenanceStatus, string> = {
  PLANNED: 'Planifiée', IN_PROGRESS: 'En cours', SUSPENDED: 'Suspendue', COMPLETED: 'Terminée', CANCELLED: 'Annulée'
};

export const RESOURCE_TYPE_LABELS: Record<MaintenanceResourceType, string> = {
  APPLICATION: 'Application', NODE: 'Nœud Kubernetes', VM: 'Machine virtuelle', SERVICE: 'Service'
};

export const TASK_STATUS_LABELS: Record<TaskStatus, string> = {
  TODO: 'À faire', IN_PROGRESS: 'En cours', ON_HOLD: 'En attente', DONE: 'Terminée', CANCELLED: 'Annulée'
};

export const TASK_PRIORITY_LABELS: Record<TaskPriority, string> = {
  LOW: 'Basse', MEDIUM: 'Moyenne', HIGH: 'Haute', CRITICAL: 'Critique'
};

export const FREQUENCY_LABELS: Record<ReportFrequency, string> = {
  DAILY: 'Quotidien', WEEKLY: 'Hebdomadaire', MONTHLY: 'Mensuel'
};

export const AUDIT_MODULE_LABELS: Record<string, string> = {
  AUTH: 'Authentification', USERS: 'Utilisateurs', MAINTENANCE: 'Maintenance', AUDIT: 'Audit',
  REPORTS: 'Rapports', TEAMS: 'Équipes', TASKS: 'Tâches'
};

export const AUDIT_ACTION_LABELS: Record<string, string> = {
  LOGIN: 'Connexion', LOGIN_2FA: 'Connexion (2FA)', LOGIN_FAILED: 'Échec de connexion', LOGOUT: 'Déconnexion',
  PASSWORD_CHANGE: 'Changement de mot de passe', EMAIL_CHANGE: "Changement d'e-mail", CREATE: 'Création',
  UPDATE: 'Modification', DELETE: 'Suppression', ENABLE: 'Activation du compte', DISABLE: 'Désactivation du compte',
  ROLE_CHANGE: 'Changement de rôle', PASSWORD_RESET: 'Réinitialisation du mot de passe', BLOCK: 'Blocage',
  UNBLOCK: 'Déblocage', TWO_FACTOR_CHANGE: 'Modification 2FA', MEMBER_ADD: 'Ajout de membre',
  MEMBER_REMOVE: 'Retrait de membre', LEAD_CHANGE: 'Changement de responsable', ASSIGN: 'Affectation',
  STATUS_CHANGE: 'Changement de statut', START: 'Démarrage', PAUSE: 'Suspension', RESUME: 'Reprise', COMPLETE: 'Fin', CANCEL: 'Annulation',
  ACTIVATE: 'Réactivation', DEACTIVATE: 'Désactivation', GENERATE: 'Génération', EXPORT: 'Export',
  SCHEDULE: 'Planification', ACCESS_DENIED: 'Accès refusé'
};
