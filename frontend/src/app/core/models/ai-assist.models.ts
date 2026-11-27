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
  taskAssignment: AiFeatureState; maintenanceWindow: AiFeatureState;
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
