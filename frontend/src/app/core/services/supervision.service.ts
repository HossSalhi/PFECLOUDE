import { Injectable } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable } from 'rxjs';
import { environment } from '../../../environments/environment';
import {
  CamundaIncidents, CamundaInstances, CamundaOverview, CamundaSteps, Correlation, GrafanaConfig, HostsOverview, LogStats,
  LogLevelFilter, LogLines, LogSources, MeaningEntry, ProcessDiagram, SeriesResponse, SupervisionMetric, TopErrors
} from '../models/supervision.models';

/** Supervision en lecture seule (API {@code /api/supervision}) : ouverte à tout utilisateur connecté. */
@Injectable({ providedIn: 'root' })
export class SupervisionService {

  private base = `${environment.apiBaseUrl}/api/supervision`;

  constructor(private http: HttpClient) {}

  hosts(): Observable<HostsOverview> { return this.http.get<HostsOverview>(`${this.base}/hosts`); }

  series(metric: SupervisionMetric, minutes: number): Observable<SeriesResponse> {
    return this.http.get<SeriesResponse>(`${this.base}/series`, { params: { metric, minutes } });
  }

  logStats(minutes: number): Observable<LogStats> {
    return this.http.get<LogStats>(`${this.base}/logs/stats`, { params: { minutes } });
  }

  topErrors(minutes: number, limit = 10): Observable<TopErrors> {
    return this.http.get<TopErrors>(`${this.base}/logs/top-errors`, { params: { minutes, limit } });
  }

  meanings(): Observable<MeaningEntry[]> { return this.http.get<MeaningEntry[]>(`${this.base}/logs/meanings`); }

  correlation(minutes: number): Observable<Correlation> {
    return this.http.get<Correlation>(`${this.base}/correlation`, { params: { minutes } });
  }

  camundaOverview(): Observable<CamundaOverview> { return this.http.get<CamundaOverview>(`${this.base}/camunda/overview`); }

  camundaInstances(state: string, limit = 20): Observable<CamundaInstances> {
    return this.http.get<CamundaInstances>(`${this.base}/camunda/instances`, { params: state ? { state, limit } : { limit } });
  }

  camundaIncidents(limit = 20): Observable<CamundaIncidents> {
    return this.http.get<CamundaIncidents>(`${this.base}/camunda/incidents`, { params: { limit } });
  }

  camundaSteps(definitionKey: string): Observable<CamundaSteps> {
    return this.http.get<CamundaSteps>(`${this.base}/camunda/definitions/${encodeURIComponent(definitionKey)}/steps`);
  }

  grafana(): Observable<GrafanaConfig> { return this.http.get<GrafanaConfig>(`${this.base}/grafana`); }

  camundaDiagram(definitionKey: string): Observable<ProcessDiagram> {
    return this.http.get<ProcessDiagram>(`${this.base}/camunda/definitions/${encodeURIComponent(definitionKey)}/diagram`);
  }

  /** Machines et applications dont les logs sont consultables (administrateurs). */
  logSources(): Observable<LogSources> { return this.http.get<LogSources>(`${this.base}/logs/sources`); }

  /** Lignes de logs d'une source (administrateurs) ; le mot cherché est toujours pris littéralement par le serveur. */
  logLines(source: string, level: LogLevelFilter, q: string, minutes: number, limit: number): Observable<LogLines> {
    const params: Record<string, string | number> = { source, level, minutes, limit };
    if (q.trim()) { params['q'] = q.trim(); }
    return this.http.get<LogLines>(`${this.base}/logs/lines`, { params });
  }
}
