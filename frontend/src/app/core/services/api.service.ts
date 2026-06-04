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

export interface FineTuningStatus {
  running: boolean;
  status: string;
  startedAt: string | null;
  lastLog: string | null;
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
}

export interface IncidentHistoryEntry {
  id: number;
  status: string;
  comment: string;
  createdAt: string;
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

  getFineTuningStatus(): Observable<FineTuningStatus> {
    return this.http.get<FineTuningStatus>(`${this.baseUrl}/api/finetuning/status`)
      .pipe(catchError(this.handleError));
  }

  triggerFineTuning(force = false, minIncidents = 20): Observable<any> {
    const params = new HttpParams()
      .set('force', force.toString())
      .set('minIncidents', minIncidents.toString());
    return this.http.post(`${this.baseUrl}/api/finetuning/trigger`, {}, { params })
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
