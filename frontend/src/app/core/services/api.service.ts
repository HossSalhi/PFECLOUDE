import { Injectable } from '@angular/core';
import { HttpClient, HttpParams, HttpErrorResponse } from '@angular/common/http';
import { Observable, throwError } from 'rxjs';
import { catchError } from 'rxjs/operators';
import { environment } from '../../../environments/environment';

export interface DashboardSummary {
  totalIncidents: number;
  openIncidents: number;
  inProgressIncidents: number;
  resolvedIncidents: number;
  criticalIncidents: number;
  pendingApprovals: number;
  totalActions: number;
  resolutionRate: number;
  generatedAt: string;
}

export interface Incident {
  id: number;
  title: string;
  description: string;
  severity: string;
  status: string;
  source: string;
  createdAt: string;
  updatedAt: string;
  incidentType?: string;
  confidence?: number;
  explanation?: string;
  namespace?: string;
  app?: string;
}

export interface PagedResponse<T> {
  content: T[];
  totalElements: number;
  totalPages: number;
  number: number;
  size: number;
}

export interface Action {
  id: number;
  actionType?: string;
  type?: string;
  status: string;
  incidentId: number;
  incidentTitle?: string;
  executedAt: string;
  createdAt?: string;
  result?: string;
  executorResult?: string;
  automated: boolean;
  automationMode?: string;
  targetNamespace?: string;
  targetName?: string;
  failedReason?: string;
}

export interface Workflow {
  id: string;
  name: string;
  status: string;
  startedAt: string;
  incidentId?: number;
}

export interface Task {
  id: string;
  name: string;
  workflowId?: string;
  status: string;
  createdAt: string;
  variables?: any;
  incidentId?: number;
  incidentTitle?: string;
  incidentStatus?: string;
}

export interface RemediationSummary {
  totalActions: number;
  successfulActions: number;
  failedActions: number;
  successRate: number;
  autoActionRate: number;
  pendingActions: number;
  hoursWindow: number;
}

export interface FaultState {
  [key: string]: boolean;
}

export interface UserData {
  id: number;
  username: string;
  email: string;
  firstName: string;
  lastName: string;
  role: string;
  enabled: boolean;
  twoFactorEnabled: boolean;
  lastLoginAt: string | null;
  lastLoginIp: string | null;
  failedAttempts: number;
  lockedUntil: string | null;
  createdAt: string;
  blockedAt: string | null;
  blockedBy: string | null;
  blockReason: string | null;
  /** Badges calculés « Chef / Technicien <spécialité> » (liste des utilisateurs uniquement). */
  roleLabels?: string[];
}

export interface IncidentHistoryEntry {
  id: number;
  status: string;
  comment: string;
  createdAt: string;
}

export interface WorkflowLogEntry {
  id: number;
  message: string;
  createdAt: string;
}

// LOT HELIOS-INCIDENT-IDENTITY-CARD-01 — carte d'identité d'incident
export interface TerminalEvidence {
  id: number;
  incidentId: number;
  vm: string;
  phase: 'BEFORE' | 'AFTER';
  capturedAt: string;
  command: string;
  exitCode: number | null;
  stdout: string;
  stderr: string;
  measuresJson: string | null;
  available: boolean;
  source: string;
}

export interface IncidentVerification {
  id: number;
  incidentId: number;
  criteria: string;
  result: string;
  measuresJson: string | null;
  reason: string;
  verifiedAt: string;
  available: boolean;
}

export interface IncidentCard {
  identity: any;
  timeline: { id: number; status: string; comment: string; at: string }[];
  evidenceBefore: TerminalEvidence[];
  evidenceAfter: TerminalEvidence[];
  verification: IncidentVerification | null;
  completeness: {
    hasTimeline: boolean;
    hasEvidenceBefore: boolean;
    hasEvidenceAfter: boolean;
    hasVerification: boolean;
  };
}

@Injectable({
  providedIn: 'root'
})
export class ApiService {
  private baseUrl = environment.apiBaseUrl;

  constructor(private http: HttpClient) {}

  private handleError(error: HttpErrorResponse): Observable<never> {
    if (error.status === 0) {
      console.error('Network error — backend unreachable:', error.message);
    } else {
      console.error(`Backend returned HTTP ${error.status}:`, error.error);
    }
    return throwError(() => new Error(error.message || 'Unknown error'));
  }

  getDashboardSummary(): Observable<DashboardSummary> {
    return this.http.get<DashboardSummary>(`${this.baseUrl}/api/dashboard/summary`)
      .pipe(catchError(this.handleError));
  }

  getIncidents(page = 0, size = 20, status?: string): Observable<PagedResponse<Incident>> {
    let params = new HttpParams()
      .set('page', page.toString())
      .set('size', size.toString());
    if (status && status !== 'ALL') {
      params = params.set('status', status);
    }
    return this.http.get<PagedResponse<Incident>>(`${this.baseUrl}/api/incidents`, { params })
      .pipe(catchError(this.handleError));
  }

  getIncident(id: number): Observable<Incident> {
    return this.http.get<Incident>(`${this.baseUrl}/api/incidents/${id}`)
      .pipe(catchError(this.handleError));
  }

  updateIncidentStatus(id: number, status: string): Observable<Incident> {
    return this.http.put<Incident>(`${this.baseUrl}/api/incidents/${id}/status/${status}`, {})
      .pipe(catchError(this.handleError));
  }

  executeIncident(id: number): Observable<void> {
    return this.http.post<void>(`${this.baseUrl}/api/incidents/${id}/execute`, {})
      .pipe(catchError(this.handleError));
  }

  register(username: string, email: string, password: string, firstName: string, lastName: string): Observable<any> {
    return this.http.post<any>(`${this.baseUrl}/api/auth/register`, { username, email, password, firstName, lastName })
      .pipe(catchError(this.handleError));
  }

  activateAccount(token: string): Observable<any> {
    return this.http.get<any>(`${this.baseUrl}/api/auth/activate?token=${token}`)
      .pipe(catchError(this.handleError));
  }

  forgotPassword(email: string): Observable<any> {
    return this.http.post<any>(`${this.baseUrl}/api/auth/forgot-password`, { email })
      .pipe(catchError(this.handleError));
  }

  resetPassword(token: string, newPassword: string): Observable<any> {
    return this.http.post<any>(`${this.baseUrl}/api/auth/reset-password`, { token, newPassword })
      .pipe(catchError(this.handleError));
  }

  getActions(page = 0, size = 20): Observable<PagedResponse<Action>> {
    const params = new HttpParams()
      .set('page', page.toString())
      .set('size', size.toString());
    return this.http.get<PagedResponse<Action>>(`${this.baseUrl}/api/actions`, { params })
      .pipe(catchError(this.handleError));
  }

  getActionsForIncident(incidentId: number): Observable<Action[]> {
    return this.http.get<Action[]>(`${this.baseUrl}/api/actions/incident/${incidentId}`)
      .pipe(catchError(this.handleError));
  }

  getIncidentHistory(id: number): Observable<IncidentHistoryEntry[]> {
    return this.http.get<IncidentHistoryEntry[]>(`${this.baseUrl}/api/incidents/${id}/history`)
      .pipe(catchError(this.handleError));
  }

  getWorkflowLogs(incidentId: number): Observable<WorkflowLogEntry[]> {
    return this.http.get<WorkflowLogEntry[]>(`${this.baseUrl}/api/incidents/${incidentId}/workflow-logs`)
      .pipe(catchError(this.handleError));
  }

  // LOT HELIOS-INCIDENT-IDENTITY-CARD-01 — carte d'identité : preuves terminal + vérification finale
  getIncidentEvidence(id: number): Observable<TerminalEvidence[]> {
    return this.http.get<TerminalEvidence[]>(`${this.baseUrl}/api/incidents/${id}/evidence`)
      .pipe(catchError(this.handleError));
  }

  getIncidentVerification(id: number): Observable<IncidentVerification | null> {
    return this.http.get<IncidentVerification | null>(`${this.baseUrl}/api/incidents/${id}/verification`)
      .pipe(catchError(this.handleError));
  }

  getIncidentCard(id: number): Observable<IncidentCard> {
    return this.http.get<IncidentCard>(`${this.baseUrl}/api/incidents/${id}/card`)
      .pipe(catchError(this.handleError));
  }

  getActionsHistory(page = 0, size = 20): Observable<PagedResponse<Action>> {
    const params = new HttpParams()
      .set('page', page.toString())
      .set('size', size.toString());
    return this.http.get<PagedResponse<Action>>(`${this.baseUrl}/api/actions/history`, { params })
      .pipe(catchError(this.handleError));
  }

  getWorkflows(): Observable<Workflow[]> {
    return this.http.get<Workflow[]>(`${this.baseUrl}/api/workflows`)
      .pipe(catchError(this.handleError));
  }

  getTasks(): Observable<Task[]> {
    return this.http.get<Task[]>(`${this.baseUrl}/api/tasks`)
      .pipe(catchError(this.handleError));
  }

  getPendingTasks(): Observable<Task[]> {
    return this.http.get<Task[]>(`${this.baseUrl}/api/tasks/pending`)
      .pipe(catchError(this.handleError));
  }

  approveTask(id: string): Observable<any> {
    return this.http.post(`${this.baseUrl}/api/tasks/${id}/approve`, {})
      .pipe(catchError(this.handleError));
  }

  rejectTask(id: string): Observable<any> {
    return this.http.post(`${this.baseUrl}/api/tasks/${id}/reject`, {})
      .pipe(catchError(this.handleError));
  }

  getRemediationSummary(hours = 24): Observable<RemediationSummary> {
    const params = new HttpParams().set('hours', hours.toString());
    return this.http.get<RemediationSummary>(`${this.baseUrl}/api/operations/remediation/summary`, { params })
      .pipe(catchError(this.handleError));
  }

  getFaults(): Observable<FaultState> {
    return this.http.get<FaultState>(`${this.baseUrl}/api/test/faults`)
      .pipe(catchError(this.handleError));
  }

  toggleFault(body: any): Observable<any> {
    return this.http.post(`${this.baseUrl}/api/test/faults`, body)
      .pipe(catchError(this.handleError));
  }

  resetFaults(): Observable<any> {
    return this.http.post(`${this.baseUrl}/api/test/faults/reset`, {})
      .pipe(catchError(this.handleError));
  }

  // ── User Management ───────────────────────────────────────────────────────

  getUsers(): Observable<UserData[]> {
    return this.http.get<UserData[]>(`${this.baseUrl}/api/users`)
      .pipe(catchError(this.handleError));
  }

  getUserById(id: number): Observable<UserData> {
    return this.http.get<UserData>(`${this.baseUrl}/api/users/${id}`)
      .pipe(catchError(this.handleError));
  }

  createUser(data: any): Observable<UserData> {
    return this.http.post<UserData>(`${this.baseUrl}/api/users`, data)
      .pipe(catchError(this.handleError));
  }

  updateUser(id: number, data: any): Observable<UserData> {
    return this.http.put<UserData>(`${this.baseUrl}/api/users/${id}`, data)
      .pipe(catchError(this.handleError));
  }

  deleteUser(id: number): Observable<void> {
    return this.http.delete<void>(`${this.baseUrl}/api/users/${id}`)
      .pipe(catchError(this.handleError));
  }

  enableUser(id: number): Observable<UserData> {
    return this.http.patch<UserData>(`${this.baseUrl}/api/users/${id}/enable`, {})
      .pipe(catchError(this.handleError));
  }

  disableUser(id: number): Observable<UserData> {
    return this.http.patch<UserData>(`${this.baseUrl}/api/users/${id}/disable`, {})
      .pipe(catchError(this.handleError));
  }

  changeUserRole(id: number, role: string): Observable<UserData> {
    return this.http.patch<UserData>(`${this.baseUrl}/api/users/${id}/role?role=${role}`, {})
      .pipe(catchError(this.handleError));
  }

  blockUser(id: number, reason: string): Observable<UserData> {
    return this.http.post<UserData>(`${this.baseUrl}/api/users/${id}/block`, { reason })
      .pipe(catchError(this.handleError));
  }

  unblockUser(id: number): Observable<UserData> {
    return this.http.post<UserData>(`${this.baseUrl}/api/users/${id}/unblock`, {})
      .pipe(catchError(this.handleError));
  }

  resetUserPassword(id: number, newPassword: string): Observable<any> {
    return this.http.post(`${this.baseUrl}/api/users/${id}/reset-password`, { newPassword })
      .pipe(catchError(this.handleError));
  }

  getMe(): Observable<any> {
    return this.http.get<any>(`${this.baseUrl}/api/auth/me`);
  }

  // ── Account self-service ──────────────────────────────────────────────────

  changePassword(currentPassword: string, newPassword: string): Observable<any> {
    return this.http.post(`${this.baseUrl}/api/auth/change-password`, { currentPassword, newPassword });
  }

  changeEmail(currentPassword: string, newEmail: string): Observable<any> {
    return this.http.post(`${this.baseUrl}/api/auth/change-email`, { currentPassword, newEmail });
  }

  getInfrastructureHealth(): Observable<any> {
    return this.http.get<any>(`${this.baseUrl}/api/infrastructure/health`)
      .pipe(catchError(this.handleError));
  }

  /** Liens Prometheus / Grafana / Loki / Camunda (ADMIN et SUPER_ADMIN seulement, refusé sinon par le Backend). */
  getMonitoringLinks(): Observable<{ prometheus: string; grafana: string; loki: string; camunda: string }> {
    return this.http.get<{ prometheus: string; grafana: string; loki: string; camunda: string }>(
      `${this.baseUrl}/api/infrastructure/monitoring-links`);
  }

  getMonitoredApps(): Observable<any> {
    return this.http.get<any>(`${this.baseUrl}/api/infrastructure/apps`)
      .pipe(catchError(this.handleError));
  }

  submitFeedback(incidentId: number, correct: boolean, comment: string, operator: string = 'operator'): Observable<any> {
    return this.http.post(`${this.baseUrl}/api/feedback/incident/${incidentId}`, {
      correct,
      comment,
      operator
    }).pipe(catchError(this.handleError));
  }
}
