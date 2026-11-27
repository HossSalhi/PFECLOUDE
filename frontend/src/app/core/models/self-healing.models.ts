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
