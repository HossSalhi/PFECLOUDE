import { Injectable } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable } from 'rxjs';
import { environment } from '../../../environments/environment';
import { toParams } from '../../shared/params.util';
import {
  Page, TaskQuery, TaskRequest, TaskStatus, TaskSummary, Team, TeamMember, TeamRequest, TeamSummary, TeamTask
} from '../models/org.models';

/** Équipes, membres et tâches (API {@code /api/teams} et {@code /api/team-tasks}). */
@Injectable({ providedIn: 'root' })
export class TeamService {

  private teamsUrl = `${environment.apiBaseUrl}/api/teams`;
  private tasksUrl = `${environment.apiBaseUrl}/api/team-tasks`;

  constructor(private http: HttpClient) {}

  // ── Équipes ────────────────────────────────────────────────────────────────

  listTeams(includeInactive: boolean, q?: string): Observable<Team[]> {
    return this.http.get<Team[]>(this.teamsUrl, { params: toParams({ includeInactive, q }) });
  }

  getTeam(id: number): Observable<Team> {
    return this.http.get<Team>(`${this.teamsUrl}/${id}`);
  }

  createTeam(body: TeamRequest): Observable<Team> {
    return this.http.post<Team>(this.teamsUrl, body);
  }

  updateTeam(id: number, body: TeamRequest): Observable<Team> {
    return this.http.put<Team>(`${this.teamsUrl}/${id}`, body);
  }

  changeLead(id: number, userId: number): Observable<Team> {
    return this.http.put<Team>(`${this.teamsUrl}/${id}/lead`, { userId });
  }

  deactivate(id: number): Observable<Team> {
    return this.http.post<Team>(`${this.teamsUrl}/${id}/deactivate`, {});
  }

  activate(id: number): Observable<Team> {
    return this.http.post<Team>(`${this.teamsUrl}/${id}/activate`, {});
  }

  deleteTeam(id: number): Observable<void> {
    return this.http.delete<void>(`${this.teamsUrl}/${id}`);
  }

  members(id: number): Observable<TeamMember[]> {
    return this.http.get<TeamMember[]>(`${this.teamsUrl}/${id}/members`);
  }

  addMember(id: number, userId: number): Observable<TeamMember> {
    return this.http.post<TeamMember>(`${this.teamsUrl}/${id}/members`, { userId });
  }

  removeMember(id: number, userId: number): Observable<void> {
    return this.http.delete<void>(`${this.teamsUrl}/${id}/members/${userId}`);
  }

  teamSummary(id: number): Observable<TeamSummary> {
    return this.http.get<TeamSummary>(`${this.teamsUrl}/${id}/summary`);
  }

  // ── Tâches ─────────────────────────────────────────────────────────────────

  listTasks(q: TaskQuery): Observable<Page<TeamTask>> {
    return this.http.get<Page<TeamTask>>(this.tasksUrl, { params: toParams({
      teamId: q.teamId, assigneeId: q.assigneeId, status: q.status, priority: q.priority,
      overdue: q.overdue, mine: q.mine, q: q.q, page: q.page ?? 0, size: q.size ?? 25
    }) });
  }

  taskSummary(teamId?: number | null, mine?: boolean): Observable<TaskSummary> {
    return this.http.get<TaskSummary>(`${this.tasksUrl}/summary`, { params: toParams({ teamId, mine }) });
  }

  getTask(id: number): Observable<TeamTask> {
    return this.http.get<TeamTask>(`${this.tasksUrl}/${id}`);
  }

  createTask(body: TaskRequest): Observable<TeamTask> {
    return this.http.post<TeamTask>(this.tasksUrl, body);
  }

  updateTask(id: number, body: TaskRequest): Observable<TeamTask> {
    return this.http.put<TeamTask>(`${this.tasksUrl}/${id}`, body);
  }

  assignTask(id: number, assigneeId: number | null): Observable<TeamTask> {
    return this.http.patch<TeamTask>(`${this.tasksUrl}/${id}/assignee`, { assigneeId });
  }

  changeTaskStatus(id: number, status: TaskStatus): Observable<TeamTask> {
    return this.http.patch<TeamTask>(`${this.tasksUrl}/${id}/status`, { status });
  }
}
