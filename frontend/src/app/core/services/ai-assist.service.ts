import { HttpClient, HttpParams } from '@angular/common/http';
import { Injectable, Injector, inject } from '@angular/core';
import { Observable, catchError, defer, map, of, shareReplay } from 'rxjs';
import { environment } from '../../../environments/environment';
import {
  AiAnomalies, AiAssignment, AiAssignmentRequest, AiCapabilities, AiMaintenanceWindows, AiReportSummary, AiTeamSummary,
  AiWindowRequest
} from '../models/ai-assist.models';

/**
 * Assistance IA (API {@code /api/ai-assist}) : toujours à la demande, jamais en continu. Les droits sont décidés par le
 * Backend ; les capacités ne servent qu'à ne montrer que ce qui est utilisable. {@code HttpClient} est récupéré à l'appel
 * (et non à la construction) : une page qui n'utilise pas l'IA ne dépend donc jamais d'elle.
 */
@Injectable({ providedIn: 'root' })
export class AiAssistService {

  private injector = inject(Injector);
  private base = `${environment.apiBaseUrl}/api/ai-assist`;
  private caps: Observable<AiCapabilities | null> | null = null;
  private capsAt = 0;

  private http(): HttpClient { return this.injector.get(HttpClient); }

  /** Capacités de l'utilisateur, mises en cache une minute ; {@code null} si le serveur ne répond pas (rien n'est alors affiché). */
  capabilities(): Observable<AiCapabilities | null> {
    if (!this.caps || Date.now() - this.capsAt > 60_000) {
      this.capsAt = Date.now();
      this.caps = defer(() => this.http().get<AiCapabilities>(`${this.base}/capabilities`)).pipe(
        map(c => c as AiCapabilities | null), catchError(() => of(null)), shareReplay(1));
    }
    return this.caps;
  }

  /** À appeler à la déconnexion : les droits d'un autre utilisateur ne doivent pas être réutilisés. */
  reset(): void { this.caps = null; }

  reportSummary(reportId: number): Observable<AiReportSummary> {
    return defer(() => this.http().post<AiReportSummary>(`${this.base}/reports/${reportId}/summary`, {}));
  }

  teamSummary(teamId: number): Observable<AiTeamSummary> {
    return defer(() => this.http().post<AiTeamSummary>(`${this.base}/teams/${teamId}/summary`, {}));
  }

  /** Détection par règles seulement (aucun appel IA). */
  anomalies(teamId: number | null, days: number): Observable<AiAnomalies> {
    return defer(() => this.http().get<AiAnomalies>(`${this.base}/audit/anomalies`, { params: this.anomalyParams(teamId, days) }));
  }

  /** Détection puis une phrase IA par constat. */
  explainAnomalies(teamId: number | null, days: number): Observable<AiAnomalies> {
    return defer(() => this.http().post<AiAnomalies>(`${this.base}/audit/anomalies/explain`, {}, { params: this.anomalyParams(teamId, days) }));
  }

  recommendAssignment(request: AiAssignmentRequest): Observable<AiAssignment> {
    return defer(() => this.http().post<AiAssignment>(`${this.base}/tasks/recommendation`, request));
  }

  /** Trace l'acceptation (la suggestion a été suivie) ou le refus : base du taux d'acceptation. */
  assignmentFeedback(teamId: number, userId: number, accepted: boolean): Observable<void> {
    return defer(() => this.http().post<void>(`${this.base}/tasks/recommendation/feedback`, { teamId, userId, accepted }));
  }

  maintenanceWindows(request: AiWindowRequest): Observable<AiMaintenanceWindows> {
    return defer(() => this.http().post<AiMaintenanceWindows>(`${this.base}/maintenance/window`, request));
  }

  private anomalyParams(teamId: number | null, days: number): HttpParams {
    let p = new HttpParams().set('days', days);
    if (teamId !== null) { p = p.set('teamId', teamId); }
    return p;
  }
}

/** Message d'erreur lisible pour une réponse HTTP refusée ou en panne. */
export function aiErrorMessage(e: { status?: number } | null | undefined): string {
  if (e?.status === 403) { return 'Action non autorisée pour votre rôle ou votre équipe.'; }
  if (e?.status === 404) { return 'Élément introuvable.'; }
  if (e?.status === 400) { return 'Demande invalide : vérifiez les champs saisis.'; }
  return "Le serveur n'a pas pu répondre.";
}
