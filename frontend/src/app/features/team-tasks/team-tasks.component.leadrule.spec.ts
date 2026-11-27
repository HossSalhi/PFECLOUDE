import { ComponentFixture, TestBed } from '@angular/core/testing';
import { NO_ERRORS_SCHEMA } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { provideRouter } from '@angular/router';
import { of } from 'rxjs';
import { ApiService } from '../../core/services/api.service';
import { TeamService } from '../../core/services/team.service';
import { OrgPermissionsService } from '../../core/services/org-permissions.service';
import { Page, TeamTask } from '../../core/models/org.models';
import { TeamTasksComponent } from './team-tasks.component';

class FakePerms {
  granted = new Set<string>();
  user = 'chef';
  can(p: string): boolean { return this.granted.has(p); }
  canAny(...p: string[]): boolean { return p.some(x => this.granted.has(x)); }
  currentUsername(): string | null { return this.user; }
  ensureLoaded() { return of(true); }
}

/** Règle : seul le chef de l'équipe de la tâche choisit son statut (repli sans chef : droit de gestion). */
describe('TeamTasksComponent — statut réservé au chef d\'équipe', () => {
  let component: TeamTasksComponent;
  let perms: FakePerms;

  const task = (over: Partial<TeamTask> = {}): TeamTask => ({
    id: 10, title: 'Documenter', description: null, team: { id: 7, name: 'Réseau' },
    creator: { id: 1, username: 'admin', displayName: 'Admin' },
    assignee: { id: 2, username: 'alice', displayName: 'Alice Martin' },
    priority: 'MEDIUM', status: 'TODO', dueDate: '2030-01-15', overdue: false, completedAt: null,
    createdAt: '2026-11-20T09:00:00', updatedAt: '2026-11-20T09:00:00', teamLeadUsername: 'chef', ...over
  });
  const empty: Page<TeamTask> = { content: [], totalElements: 0, totalPages: 1, number: 0, size: 15 };

  function create(user: string, ...granted: string[]): void {
    const service = jasmine.createSpyObj<TeamService>('TeamService', ['listTasks', 'taskSummary', 'listTeams', 'changeTaskStatus']);
    service.listTasks.and.returnValue(of(empty));
    service.taskSummary.and.returnValue(of(null as any));
    service.listTeams.and.returnValue(of([]));
    const api = jasmine.createSpyObj<ApiService>('ApiService', ['getUsers']);
    api.getUsers.and.returnValue(of([]));
    perms = new FakePerms();
    perms.user = user;
    granted.forEach(g => perms.granted.add(g));
    TestBed.configureTestingModule({
      imports: [FormsModule, TeamTasksComponent], schemas: [NO_ERRORS_SCHEMA],
      providers: [provideRouter([]), { provide: TeamService, useValue: service }, { provide: ApiService, useValue: api },
        { provide: OrgPermissionsService, useValue: perms }]
    });
    const fixture: ComponentFixture<TeamTasksComponent> = TestBed.createComponent(TeamTasksComponent);
    component = fixture.componentInstance;
    fixture.detectChanges();
  }

  it("le chef de l'équipe choisit toutes les transitions, y compris annuler et rouvrir", () => {
    create('chef', 'TASKS_VIEW');
    expect(component.statusChoices(task({ status: 'TODO' }))).toEqual(['IN_PROGRESS', 'ON_HOLD', 'DONE', 'CANCELLED']);
    expect(component.statusChoices(task({ status: 'DONE' }))).toEqual(['IN_PROGRESS']);
  });

  it("la personne assignée n'a aucun choix de statut", () => {
    create('alice', 'TASKS_VIEW', 'TASKS_UPDATE_OWN_STATUS');
    expect(component.statusChoices(task())).toEqual([]);
  });

  it("un administrateur qui n'est pas le chef n'a aucun choix de statut", () => {
    create('admin', 'TASKS_VIEW', 'TASKS_UPDATE', 'TASKS_UPDATE_OWN_STATUS');
    expect(component.statusChoices(task())).toEqual([]);
  });

  it("sans chef d'équipe, le droit de gestion s'applique (repli) et un simple membre n'a rien", () => {
    create('admin', 'TASKS_VIEW', 'TASKS_UPDATE');
    expect(component.statusChoices(task({ teamLeadUsername: null, status: 'TODO' }))).toEqual(['IN_PROGRESS', 'ON_HOLD', 'DONE', 'CANCELLED']);
    TestBed.resetTestingModule();
    create('alice', 'TASKS_VIEW', 'TASKS_UPDATE_OWN_STATUS');
    expect(component.statusChoices(task({ teamLeadUsername: null }))).toEqual([]);
  });
});
