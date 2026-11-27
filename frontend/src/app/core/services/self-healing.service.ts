import { Injectable } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable } from 'rxjs';
import { environment } from '../../../environments/environment';
import { toParams } from '../../shared/params.util';
import { ActionLogs, CamundaTrace, IncidentAlerts, LogSignals, SelfHealingListing, SelfHealingTrace } from '../models/self-healing.models';

/** Trace d'auto-remédiation (API {@code /api/audit-trail/self-healing}) : lecture seule, réservée à AUDIT_VIEW_ALL. */
@Injectable({ providedIn: 'root' })
export class SelfHealingService {

  private base = `${environment.apiBaseUrl}/api/audit-trail/self-healing`;

  constructor(private http: HttpClient) {}

  list(from: string, to: string, limit = 50): Observable<SelfHealingListing> {
    return this.http.get<SelfHealingListing>(this.base, { params: toParams({ from, to, limit }) });
  }

  trace(incidentId: number): Observable<SelfHealingTrace> {
    return this.http.get<SelfHealingTrace>(`${this.base}/${incidentId}`);
  }

  logs(incidentId: number, actionId: number): Observable<ActionLogs> {
    return this.http.get<ActionLogs>(`${this.base}/${incidentId}/actions/${actionId}/logs`);
  }

  alerts(incidentId: number): Observable<IncidentAlerts> {
    return this.http.get<IncidentAlerts>(`${this.base}/${incidentId}/alerts`);
  }

  logSignals(incidentId: number): Observable<LogSignals> {
    return this.http.get<LogSignals>(`${this.base}/${incidentId}/log-signals`);
  }

  camunda(incidentId: number): Observable<CamundaTrace> {
    return this.http.get<CamundaTrace>(`${this.base}/${incidentId}/camunda`);
  }
}
