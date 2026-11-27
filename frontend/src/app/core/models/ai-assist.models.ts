/** Assistance IA des modules de gestion : les chiffres viennent du Backend, seule la « narration » vient de l'IA. */

export interface AiFact { label: string; value: string; tone: 'neutral' | 'good' | 'warn' | 'bad'; }

export type AiNarrativeStatus = 'OK' | 'DISABLED' | 'NOT_CONFIGURED' | 'UNAVAILABLE' | 'NONE';
export interface AiNarrative {
  status: AiNarrativeStatus; message: string | null; text: string | null; model: string | null; generatedAt: string | null;
}

export interface AiFeatureState { enabled: boolean; ready: boolean; message: string | null; }
export interface AiLedTeam { id: number; name: string; }
export interface AiCapabilities {
  admin: boolean; ledTeams: AiLedTeam[]; allowed: boolean;
  reportsSummary: AiFeatureState; teamSummary: AiFeatureState; auditAnomalies: AiFeatureState;
  taskAssignment: AiFeatureState; maintenanceWindow: AiFeatureState; telemetryAnalysis: AiFeatureState;
}

export interface AiReportSummary { reportId: number; title: string | null; period: string | null; facts: AiFact[]; analysis: AiNarrative; }
export interface AiMemberLoad { userId: number; displayName: string; total: number; open: number; done: number; overdue: number; }
export interface AiTeamSummary { teamId: number; teamName: string; facts: AiFact[]; members: AiMemberLoad[]; analysis: AiNarrative; }

export interface AiAnomaly {
  key: string; rule: string; ruleLabel: string; severity: 'HIGH' | 'MEDIUM' | 'LOW'; actor: string; count: number;
  firstAt: string | null; lastAt: string | null; sentence: string; aiSentence: boolean;
}
export interface AiAnomalies { teamId: number | null; days: number; facts: AiFact[]; anomalies: AiAnomaly[]; analysis: AiNarrative; }

export interface AiCandidate {
  userId: number; displayName: string; score: number; openTasks: number; overdueTasks: number; similarDone: number; reasons: string[];
}
export interface AiAssignment {
  teamId: number; teamName: string; facts: AiFact[]; candidates: AiCandidate[]; recommendedUserId: number | null; analysis: AiNarrative;
}
export interface AiAssignmentRequest { teamId: number; title: string; description?: string | null; priority?: string | null; }

export interface AiWindow { start: string; end: string; averageLoad: number; peakLoad: number; score: number; notes: string[]; }
export interface AiWindowRequest { teamId: number | null; resourceType: string; resourceName: string; durationMinutes: number; horizonDays: number; }
export interface AiMaintenanceWindows { teamId: number | null; metricsAvailable: boolean; facts: AiFact[]; windows: AiWindow[]; analysis: AiNarrative; }

// ── (e) Analyse IA des logs et métriques (historique traçable) ───────────────────────────────────────────────
export type TelemetryVerdict = 'NO_ANOMALY' | 'ANOMALIES' | 'PARTIAL_COVERAGE' | 'INSUFFICIENT_DATA';
export type TelemetryDataQuality = 'COMPLETE' | 'PARTIAL' | 'INSUFFICIENT';
export type TelemetryScope = 'ALL' | 'VM' | 'CLUSTER' | 'APPLICATION' | 'SERVICE';
export interface TelemetryRequest { teamId: number | null; scope: TelemetryScope | null; minutes: number; }
export interface TelemetryFinding {
  scope: string; entity: string; signal: string; rule: string; severity: 'HIGH' | 'MEDIUM' | 'LOW';
  start: string; end: string; observed: number | null; baseline: number | null; unit: string | null; detail: string; evidence: string; queryRef: string | null;
}
export interface TelemetryQuality { source: string; available: boolean; coverage: number; note: string; }
/** Trace d'UNE requête : texte exact, plage, pas, état et motif d'un échec éventuel. */
export interface TelemetryQuery {
  id: string; source: string; signal: string; query: string; status: 'OK' | 'FAILED'; reason: string | null; stepSeconds: number | null;
  start: string | null; end: string | null; series: number | null; points: number | null;
}
export interface TelemetryCoverage { source: string; entity: string; signal: string; coverage: number; note: string; }
export interface TelemetryTrace {
  rulesVersion: string; windowStart: string; windowEnd: string; baselineStart: string | null; baselineEnd: string | null;
  metricsStepSeconds: number | null; logsWindowStart: string | null; logsBaselineStart: string | null; logsStepSeconds: number | null;
  thresholds: Record<string, string>; queries: TelemetryQuery[]; coverage: TelemetryCoverage[];
}
export interface TelemetryAnalysis {
  id: number; requestedAt: string; requestedBy: string | null; teamId: number | null; teamName: string | null; scope: string;
  minutes: number; windowStart: string; windowEnd: string; verdict: TelemetryVerdict; dataQuality: TelemetryDataQuality;
  anomalyCount: number; highCount: number; durationMs: number; facts: AiFact[]; findings: TelemetryFinding[]; quality: TelemetryQuality[];
  trace: TelemetryTrace; analysis: AiNarrative;
}
export interface TelemetryRunSummary {
  id: number; requestedAt: string; requestedBy: string | null; teamId: number | null; teamName: string | null; scope: string;
  minutes: number; verdict: TelemetryVerdict; dataQuality: TelemetryDataQuality; anomalyCount: number; highCount: number; narrativeStatus: string;
}
export interface TelemetryPage { content: TelemetryRunSummary[]; totalElements: number; totalPages: number; number: number; last: boolean; }
