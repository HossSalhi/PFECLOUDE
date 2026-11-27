import { Injectable } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable } from 'rxjs';
import { environment } from '../../../environments/environment';
import { RoutingDecision, RoutingView } from '../models/org.models';

/** Routage incident → équipe (API {@code /api/incident-routing}) : décision du responsable face à une maintenance en cours. */
@Injectable({ providedIn: 'root' })
export class IncidentRoutingService {

  private base = `${environment.apiBaseUrl}/api/incident-routing/incidents`;

  constructor(private http: HttpClient) {}

  view(incidentId: number): Observable<RoutingView> {
    return this.http.get<RoutingView>(`${this.base}/${incidentId}`);
  }

  decide(incidentId: number, maintenanceId: number, decision: RoutingDecision): Observable<RoutingView> {
    return this.http.post<RoutingView>(`${this.base}/${incidentId}/decision`, { maintenanceId, decision });
  }

  resume(incidentId: number, maintenanceId: number): Observable<RoutingView> {
    return this.http.post<RoutingView>(`${this.base}/${incidentId}/resume`, { maintenanceId });
  }
}
