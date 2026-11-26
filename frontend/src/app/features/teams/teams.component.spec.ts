import { ComponentFixture, TestBed } from '@angular/core/testing';
import { NO_ERRORS_SCHEMA } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { provideRouter } from '@angular/router';
import { of, throwError } from 'rxjs';
import { ApiService, UserData } from '../../core/services/api.service';
import { TeamService } from '../../core/services/team.service';
import { OrgPermissionsService } from '../../core/services/org-permissions.service';
import { Team, TeamMember } from '../../core/models/org.models';
import { TeamsComponent } from './teams.component';

class FakePerms {
  granted = new Set<string>();
  can(p: string): boolean { return this.granted.has(p); }
  canAny(...p: string[]): boolean { return p.some(x => this.granted.has(x)); }
  ensureLoaded() { return of(true); }
}

describe('TeamsComponent', () => {
  let fixture: ComponentFixture<TeamsComponent>;
  let component: TeamsComponent;
  let service: jasmine.SpyObj<TeamService>;
  let api: jasmine.SpyObj<ApiService>;
  let perms: FakePerms;

  const team = (over: Partial<Team> = {}): Team => ({
    id: 7, name: 'Réseau', description: 'Équipe réseau', lead: { id: 2, username: 'alice', displayName: 'Alice Martin' },
    active: true, memberCount: 2, openTaskCount: 1, createdBy: null, createdAt: '2026-11-01T09:00:00', updatedAt: '2026-11-01T09:00:00', ...over
  });

  const member = (userId: number, username: string, lead = false): TeamMember => ({
    userId, username, displayName: username.toUpperCase(), role: 'USER', enabled: true, lead, addedAt: '2026-11-01T09:00:00', addedBy: 'admin'
  });

  const user = (id: number, username: string): UserData => ({ id, username, firstName: username, lastName: 'X', enabled: true } as UserData);

  beforeEach(() => {
    service = jasmine.createSpyObj<TeamService>('TeamService',
      ['listTeams', 'members', 'teamSummary', 'createTeam', 'updateTeam', 'addMember', 'removeMember', 'changeLead', 'deactivate', 'activate', 'deleteTeam']);
    api = jasmine.createSpyObj<ApiService>('ApiService', ['getUsers']);
    perms = new FakePerms();
    service.listTeams.and.returnValue(of([team()]));
    service.members.and.returnValue(of([member(2, 'alice', true), member(3, 'bob')]));
    service.teamSummary.and.returnValue(of({
      teamId: 7, teamName: 'Réseau', tasks: { total: 4, byStatus: { TODO: 2, DONE: 2 }, overdue: 1, completionRate: 50 }, members: []
    }));
    api.getUsers.and.returnValue(of([user(2, 'alice'), user(3, 'bob'), user(4, 'carol')]));
    TestBed.configureTestingModule({
    imports: [FormsModule, TeamsComponent],
    schemas: [NO_ERRORS_SCHEMA],
    providers: [
        provideRouter([]),
        { provide: TeamService, useValue: service },
        { provide: ApiService, useValue: api },
        { provide: OrgPermissionsService, useValue: perms }
    ]
});
  });

  function create(...granted: string[]): void {
    granted.forEach(g => perms.granted.add(g));
    fixture = TestBed.createComponent(TeamsComponent);
    component = fixture.componentInstance;
    fixture.detectChanges();
  }

  const el = (selector: string): HTMLElement | null => fixture.nativeElement.querySelector(selector);

  it("n'offre à un membre simple que la consultation", () => {
    create('TEAMS_VIEW');
    expect(fixture.nativeElement.textContent).toContain('Réseau');
    expect(el('.header-actions .btn-primary')).toBeNull();
    expect(el('.btn-icon.btn-edit')).toBeNull();
    expect(el('.btn-icon.btn-delete')).toBeNull();
    expect(api.getUsers).not.toHaveBeenCalled();
  });

  it('distingue gestion, désactivation et suppression (suppression réservée)', () => {
    create('TEAMS_VIEW', 'TEAMS_CREATE', 'TEAMS_UPDATE', 'TEAMS_DEACTIVATE', 'TEAMS_MANAGE_MEMBERS');
    expect(el('.header-actions .btn-primary')).not.toBeNull();
    expect(el('.btn-icon.btn-edit')).not.toBeNull();
    expect(el('.btn-icon.btn-warn')).not.toBeNull();
    expect(el('.btn-icon.btn-delete')).toBeNull();                 // TEAMS_DELETE absent
  });

  it("propose comme nouveaux membres les utilisateurs actifs qui n'en font pas déjà partie", () => {
    create('TEAMS_VIEW', 'TEAMS_MANAGE_MEMBERS');
    component.select(team());
    expect(component.members.length).toBe(2);
    expect(component.candidates.map(u => u.username)).toEqual(['carol']);
    expect(service.teamSummary).toHaveBeenCalledWith(7);
  });

  it('ajoute un membre puis recharge équipes et membres', () => {
    service.addMember.and.returnValue(of(member(4, 'carol')));
    create('TEAMS_VIEW', 'TEAMS_MANAGE_MEMBERS');
    component.select(team());
    component.addMemberId = 4;
    component.addMember();
    expect(service.addMember).toHaveBeenCalledWith(7, 4);
    expect(component.addMemberId).toBeNull();
    expect(component.success).toContain('Membre ajouté');
  });

  it("demande confirmation avant de retirer un membre, puis retire", () => {
    service.removeMember.and.returnValue(of(undefined));
    create('TEAMS_VIEW', 'TEAMS_MANAGE_MEMBERS');
    component.select(team());
    component.askRemoveMember(member(3, 'bob'));
    expect(component.confirm!.danger).toBeTrue();
    expect(service.removeMember).not.toHaveBeenCalled();
    component.runConfirm();
    expect(service.removeMember).toHaveBeenCalledWith(7, 3);
    expect(component.confirm).toBeNull();
  });

  it("affiche le refus du Backend (par exemple retirer le responsable)", () => {
    service.removeMember.and.returnValue(throwError(() => ({
      status: 409, error: { error: "Le responsable de l'équipe ne peut pas être retiré", status: 409 }
    })));
    create('TEAMS_VIEW', 'TEAMS_MANAGE_MEMBERS');
    component.select(team());
    component.askRemoveMember(member(2, 'alice', true));
    component.runConfirm();
    expect(component.error).toContain('responsable');
    expect(component.busy).toBeFalse();
  });

  it("exige un nom d'équipe et crée avec le responsable choisi", () => {
    service.createTeam.and.returnValue(of(team()));
    create('TEAMS_VIEW', 'TEAMS_CREATE');
    component.openCreate();
    component.submit();
    expect(component.modalError).toContain('nom');
    expect(service.createTeam).not.toHaveBeenCalled();
    component.form.name = '  Exploitation ';
    component.form.leadId = 2;
    component.submit();
    expect(service.createTeam).toHaveBeenCalledWith({ name: 'Exploitation', description: null, leadId: 2 });
    expect(component.showForm).toBeFalse();
  });

  it("affiche le message du Backend pour un nom déjà utilisé", () => {
    service.createTeam.and.returnValue(throwError(() => ({ status: 409, error: { error: 'Une équipe « Réseau » existe déjà' } })));
    create('TEAMS_VIEW', 'TEAMS_CREATE');
    component.openCreate();
    component.form.name = 'réseau';
    component.submit();
    expect(component.modalError).toContain('existe déjà');
    expect(component.showForm).toBeTrue();
  });

  it('désactive puis réactive après confirmation', () => {
    service.deactivate.and.returnValue(of(team({ active: false })));
    service.activate.and.returnValue(of(team()));
    create('TEAMS_VIEW', 'TEAMS_DEACTIVATE');
    component.askToggle(team());
    expect(component.confirm!.label).toBe('Désactiver');
    component.runConfirm();
    expect(service.deactivate).toHaveBeenCalledWith(7);
    component.askToggle(team({ active: false }));
    expect(component.confirm!.label).toBe('Réactiver');
    component.runConfirm();
    expect(service.activate).toHaveBeenCalledWith(7);
  });

  it('supprime une équipe après confirmation et referme le détail', () => {
    service.deleteTeam.and.returnValue(of(undefined));
    create('TEAMS_VIEW', 'TEAMS_DELETE');
    component.select(team());
    component.askDelete(team());
    component.runConfirm();
    expect(service.deleteTeam).toHaveBeenCalledWith(7);
    expect(component.selected).toBeNull();
  });
});
