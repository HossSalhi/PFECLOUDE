import { ComponentFixture, TestBed } from '@angular/core/testing';
import { NO_ERRORS_SCHEMA } from '@angular/core';
import { ActivatedRoute, convertToParamMap } from '@angular/router';
import { FormsModule } from '@angular/forms';
import { of, throwError } from 'rxjs';
import { ApiService } from '../../core/services/api.service';
import { TeamService } from '../../core/services/team.service';
import { OrgPermissionsService } from '../../core/services/org-permissions.service';
import { Page, TeamTask } from '../../core/models/org.models';
import { toDateValue } from '../../shared/date.util';
import { TeamTasksComponent } from './team-tasks.component';

class FakePerms {
  granted = new Set<string>();
  user: string | null = 'alice';
  can(p: string): boolean { return this.granted.has(p); }
  canAny(...p: string[]): boolean { return p.some(x => this.granted.has(x)); }
  currentUsername(): string | null { return this.user; }
  ensureLoaded() { return of(true); }
}

describe('TeamTasksComponent', () => {
  let fixture: ComponentFixture<TeamTasksComponent>;
  let component: TeamTasksComponent;
  let service: jasmine.SpyObj<TeamService>;
  let perms: FakePerms;

  const task = (over: Partial<TeamTask> = {}): TeamTask => ({
    id: 10, title: 'Documenter le plan de reprise', description: null, team: { id: 7, name: 'Réseau' },
    creator: { id: 1, username: 'admin', displayName: 'Admin' },
    assignee: { id: 2, username: 'alice', displayName: 'Alice Martin' },
    priority: 'MEDIUM', status: 'TODO', dueDate: '2030-01-15', overdue: false, completedAt: null,
    createdAt: '2026-11-20T09:00:00', updatedAt: '2026-11-20T09:00:00', ...over
  });

  const pageOf = (items: TeamTask[]): Page<TeamTask> =>
    ({ content: items, totalElements: items.length, totalPages: 1, number: 0, size: 15 });

  beforeEach(() => {
    service = jasmine.createSpyObj<TeamService>('TeamService',
      ['listTeams', 'listTasks', 'taskSummary', 'members', 'createTask', 'updateTask', 'assignTask', 'changeTaskStatus']);
    perms = new FakePerms();
    service.listTeams.and.returnValue(of([]));
    service.listTasks.and.returnValue(of(pageOf([task()])));
    service.taskSummary.and.returnValue(of({ total: 1, byStatus: { TODO: 1 }, overdue: 0, completionRate: 0 }));
    service.members.and.returnValue(of([]));
    TestBed.configureTestingModule({
    imports: [FormsModule, TeamTasksComponent],
    schemas: [NO_ERRORS_SCHEMA], // routerLink : sans importance pour ces tests
    providers: [
        { provide: TeamService, useValue: service },
        { provide: ApiService, useValue: jasmine.createSpyObj<ApiService>('ApiService', { getUsers: of([]) }) },
        { provide: OrgPermissionsService, useValue: perms },
        { provide: ActivatedRoute, useValue: { snapshot: { queryParamMap: convertToParamMap({ teamId: '7' }) } } }
    ]
});
  });

  function create(...granted: string[]): void {
    granted.forEach(g => perms.granted.add(g));
    fixture = TestBed.createComponent(TeamTasksComponent);
    component = fixture.componentInstance;
    fixture.detectChanges();
  }

  const el = (selector: string): HTMLElement | null => fixture.nativeElement.querySelector(selector);

  it("reprend l'équipe passée dans l'adresse et filtre la liste et l'avancement dessus", () => {
    create('TASKS_VIEW');
    expect(component.filters.teamId).toBe(7);
    expect(service.listTasks).toHaveBeenCalledWith(jasmine.objectContaining({ teamId: 7, page: 0, size: 15 }));
    expect(service.taskSummary).toHaveBeenCalledWith(7, false);
  });

  it('un gestionnaire peut choisir toutes les transitions, y compris annuler', () => {
    create('TASKS_VIEW', 'TASKS_UPDATE', 'TASKS_UPDATE_OWN_STATUS');
    expect(component.statusChoices(task({ status: 'TODO' }))).toEqual(['IN_PROGRESS', 'ON_HOLD', 'DONE', 'CANCELLED']);
    expect(component.statusChoices(task({ status: 'DONE' }))).toEqual(['IN_PROGRESS']);
  });

  it("le responsable d'une tâche peut la faire avancer mais pas l'annuler", () => {
    create('TASKS_VIEW', 'TASKS_UPDATE_OWN_STATUS');
    expect(component.statusChoices(task({ status: 'TODO' }))).toEqual(['IN_PROGRESS', 'ON_HOLD', 'DONE']);
    expect(component.statusChoices(task({ status: 'DONE' }))).toEqual([]);          // pas de réouverture
  });

  it("un membre n'a aucun choix de statut sur la tâche d'un autre", () => {
    create('TASKS_VIEW', 'TASKS_UPDATE_OWN_STATUS');
    const others = task({ assignee: { id: 3, username: 'bob', displayName: 'Bob' } });
    expect(component.statusChoices(others)).toEqual([]);
    expect(component.statusChoices(task({ assignee: null }))).toEqual([]);
  });

  it('un lecteur simple ne voit ni création ni modification ni affectation', () => {
    create('TASKS_VIEW');
    expect(el('.header-actions .btn-primary')).toBeNull();
    expect(el('.btn-icon.btn-edit')).toBeNull();
    expect(el('.btn-icon.btn-warn')).toBeNull();
    expect(component.statusChoices(task())).toEqual([]);
  });

  it('change le statut via le Backend puis recharge la liste', () => {
    service.changeTaskStatus.and.returnValue(of(task({ status: 'DONE' })));
    create('TASKS_VIEW', 'TASKS_UPDATE');
    const before = service.listTasks.calls.count();
    component.changeStatus(task(), 'DONE');
    expect(service.changeTaskStatus).toHaveBeenCalledWith(10, 'DONE');
    expect(service.listTasks.calls.count()).toBe(before + 1);
    expect(component.success).toContain('Terminée');
  });

  it('affiche le refus du Backend et resynchronise la liste', () => {
    service.changeTaskStatus.and.returnValue(throwError(() => ({
      status: 409, error: { error: 'Transition interdite : DONE → CANCELLED', status: 409 }
    })));
    create('TASKS_VIEW', 'TASKS_UPDATE');
    const before = service.listTasks.calls.count();
    component.changeStatus(task({ status: 'DONE' }), 'CANCELLED');
    expect(component.error).toContain('Transition interdite');
    expect(service.listTasks.calls.count()).toBe(before + 1);
    expect(component.busy).toBeFalse();
  });

  it("ignore un changement de statut sans valeur choisie", () => {
    create('TASKS_VIEW', 'TASKS_UPDATE');
    component.changeStatus(task(), '');
    expect(service.changeTaskStatus).not.toHaveBeenCalled();
  });

  it('exige un titre et refuse une échéance passée à la création', () => {
    create('TASKS_VIEW', 'TASKS_CREATE');
    component.openCreate();
    component.submit();
    expect(component.modalError).toContain('titre');
    component.form.title = 'Contrôle des sauvegardes';
    component.form.dueDate = '2020-01-01';
    component.submit();
    expect(component.modalError).toContain('passé');
    expect(service.createTask).not.toHaveBeenCalled();
  });

  it('crée la tâche avec les valeurs saisies', () => {
    service.createTask.and.returnValue(of(task()));
    create('TASKS_VIEW', 'TASKS_CREATE');
    component.openCreate();
    component.form.title = '  Contrôle des sauvegardes ';
    component.form.teamId = 7;
    component.form.assigneeId = 2;
    component.form.priority = 'HIGH';
    component.form.dueDate = toDateValue(new Date(Date.now() + 5 * 86400000));
    component.submit();
    expect(service.createTask).toHaveBeenCalledWith(jasmine.objectContaining({
      title: 'Contrôle des sauvegardes', teamId: 7, assigneeId: 2, priority: 'HIGH', description: null
    }));
    expect(component.showForm).toBeFalse();
  });

  it("propose comme responsables les membres de l'équipe choisie, sinon tous les utilisateurs actifs", () => {
    service.members.and.returnValue(of([
      { userId: 2, username: 'alice', displayName: 'Alice Martin', role: 'USER', enabled: true, lead: true, addedAt: 'x', addedBy: null },
      { userId: 5, username: 'old', displayName: 'Ancien', role: 'USER', enabled: false, lead: false, addedAt: 'x', addedBy: null }
    ]));
    create('TASKS_VIEW', 'TASKS_CREATE');
    component.openCreate();                                  // l'équipe 7 est préfiltrée dans l'adresse
    expect(component.assigneeChoices.map(c => c.id)).toEqual([2]);   // le membre désactivé est écarté
    component.form.teamId = null;
    component.users = [{ id: 9, firstName: 'Zoé', lastName: 'Durand', username: 'zoe' } as any];
    expect(component.assigneeChoices).toEqual([{ id: 9, label: 'Zoé Durand (@zoe)' }]);
  });

  it('retire les filtres et recharge', () => {
    create('TASKS_VIEW');
    component.filters.status = 'DONE';
    component.filters.mine = true;
    component.resetFilters();
    expect(component.filters.status).toBe('');
    expect(component.filters.mine).toBeFalse();
    expect(component.filters.teamId).toBeNull();
    expect(component.page).toBe(0);
  });
});
