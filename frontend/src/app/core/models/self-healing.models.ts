/** Trace d'auto-remédiation de l'Audit-trail : DÉRIVÉE du cycle réel (incident, actions, vérifications, relevés VM, logs Loki). */

export interface SelfHealingItem {
  incidentId: number; title: string; type: string | null; resource: string | null; status: string; createdAt: string;
  actions: number; failedActions: number; lastActionType: string | null; lastActionStatus: string | null; lastActionAt: string | null;
}
export interface SelfHealingListing { from: string; to: string; truncated: boolean; items: SelfHealingItem[]; }

export interface SelfHealingIncident {
  id: number; title: string; type: string | null; namespace: string | null; app: string | null; severity: string | null;
  status: string; createdAt: string;
}
export type TraceKind = 'INCIDENT' | 'HISTORY' | 'ACTION' | 'VERIFICATION' | 'EVIDENCE';
export interface TraceEvent { at: string | null; kind: TraceKind; title: string; detail: string | null; }
export interface TraceAction {
  id: number; type: string; status: string; mode: string; attempts: number; executedBy: string | null; createdAt: string;
  executedAt: string | null; target: string | null; failedReason: string | null; remediation: boolean; logSource: string | null;
}
export interface TraceVerification { criteria: string | null; result: string | null; available: boolean; at: string | null; reason: string | null; }
export interface TraceEvidence { vm: string | null; phase: string | null; command: string | null; exitCode: number | null; available: boolean; at: string | null; }
export interface SelfHealingTrace {
  incident: SelfHealingIncident; timeline: TraceEvent[]; actions: TraceAction[]; verifications: TraceVerification[]; evidence: TraceEvidence[];
}

export interface TraceLogLine { at: string; text: string; error: boolean; category: string | null; label: string | null; }
export interface ActionLogs {
  available: boolean; reason: string | null; source: string | null; from: string; to: string; truncated: boolean; lines: TraceLogLine[];
}

// ── Alertes, signaux de logs et trace Camunda de l'incident ──────────────────
export type ToolStatus = 'OK' | 'UNAVAILABLE' | 'NOT_CONFIGURED';
export interface AlertItem {
  name: string; severity: string | null; summary: string | null; labels: Record<string, string>;
  firstFiring: string | null; lastFiring: string | null; activeNow: boolean; ruleState: string | null;
  ruleIncidentType: string | null; typeMatches: boolean;
}
export interface ToolAlerts { tool: string; status: ToolStatus; message: string | null; alerts: AlertItem[]; }
export interface LokiRules { status: ToolStatus; message: string | null; rules: string[]; }
export interface IncidentAlerts {
  from: string; to: string; truncated: boolean; prometheus: ToolAlerts; alertmanager: ToolAlerts; grafana: ToolAlerts; loki: LokiRules;
}

export interface LogSignal {
  category: string; label: string; meaning: string; advice: string | null; count: number; first: string; last: string; example: string;
}
export interface LogSignals {
  available: boolean; reason: string | null; source: string | null; from: string; to: string; truncated: boolean; linesRead: number; signals: LogSignal[];
}

export interface CamundaStepInfo { name: string; type: string; state: string; start: string | null; end: string | null; durationMs: number | null; incident: boolean; }
export interface CamundaIncidentInfo { errorType: string; message: string; element: string; createdAt: string | null; }
export interface WorkflowLogLine { at: string; message: string; }
export interface CamundaRun {
  workflowId: number; workflowName: string | null; status: string | null; processInstanceKey: string | null; definitionId: string | null;
  state: string | null; start: string | null; end: string | null; hasIncident: boolean; note: string | null;
  steps: CamundaStepInfo[]; incidents: CamundaIncidentInfo[]; logs: WorkflowLogLine[];
}
export interface CamundaTrace { available: boolean; error: string | null; runs: CamundaRun[]; }
