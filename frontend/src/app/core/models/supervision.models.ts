/** Modèles de la supervision (lecture seule) : métriques Prometheus, logs Loki, Camunda, Grafana. */

export type SupervisionMetric = 'CPU' | 'MEMORY' | 'DISK';

export interface SupPoint { t: number; v: number; }

export interface HostStat {
  instance: string; name: string; up: boolean;
  cpuPercent: number | null; memoryPercent: number | null; diskPercent: number | null;
}
export interface TargetStat { job: string; up: number; total: number; }
export interface HostsOverview { available: boolean; error: string | null; generatedAt: string; hosts: HostStat[]; targets: TargetStat[]; }

export interface SeriesLine { instance: string; name: string; points: SupPoint[]; }
export interface SeriesResponse {
  available: boolean; error: string | null; metric: SupervisionMetric; unit: string; minutes: number; stepSeconds: number; lines: SeriesLine[];
}

export interface LogGroup { key: string; total: number; errors: number; warnings: number; }
export interface LogStats {
  available: boolean; error: string | null; minutes: number; generatedAt: string;
  total: number; errors: number; warnings: number; byHost: LogGroup[]; byApplication: LogGroup[];
}
export interface ErrorPattern {
  pattern: string; count: number; host: string; category: string; label: string; meaning: string; advice: string | null; example: string;
}
export interface TopErrors { available: boolean; error: string | null; minutes: number; patterns: ErrorPattern[]; }
export interface MeaningEntry { category: string; label: string; meaning: string; advice: string | null; }

export interface IncidentMark {
  id: number; type: string; resource: string | null; severity: string | null; status: string | null; createdAt: string; errorLogsAround: number;
}
export interface Correlation {
  available: boolean; error: string | null; minutes: number; stepSeconds: number;
  errorSeries: SupPoint[]; cpuSeries: SupPoint[]; incidents: IncidentMark[];
}

export interface CamundaDefinition { key: string; id: string; name: string; version: number; }
export interface CamundaOverview {
  available: boolean; error: string | null; generatedAt: string; healthy: boolean; version: string | null; clusterSize: number;
  active: number; completed: number; terminated: number; incidents: number; definitions: CamundaDefinition[];
}
export interface CamundaInstance {
  key: string; definitionKey: string; definitionId: string; definitionName: string; version: number; state: string;
  hasIncident: boolean; startedAt: string | null; endedAt: string | null; durationMs: number | null;
}
export interface CamundaInstances { available: boolean; error: string | null; total: number; items: CamundaInstance[]; }
export interface CamundaIncident {
  key: string; definitionId: string; instanceKey: string; errorType: string; errorMessage: string; elementId: string; createdAt: string | null;
}
export interface CamundaIncidents { available: boolean; error: string | null; total: number; items: CamundaIncident[]; }
export interface StepInfo { id: string; type: string; kind: string; name: string; }
export interface CamundaSteps { available: boolean; error: string | null; definitionKey: string; steps: StepInfo[]; }

export interface GrafanaDashboard { title: string; uid: string; embedUrl: string | null; }
export interface GrafanaConfig { embedEnabled: boolean; dashboards: GrafanaDashboard[]; }
