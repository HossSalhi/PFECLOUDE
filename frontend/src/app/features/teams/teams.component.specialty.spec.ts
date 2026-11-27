import { ComponentFixture, TestBed } from '@angular/core/testing';
import { NO_ERRORS_SCHEMA } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { provideRouter } from '@angular/router';
import { of } from 'rxjs';
import { ApiService, UserData } from '../../core/services/api.service';
import { TeamService } from '../../core/services/team.service';
import { OrgPermissionsService } from '../../core/services/org-permissions.service';
import { Team } from '../../core/models/org.models';
import { TeamsComponent } from './teams.component';

class FakePerms {
  granted = new Set<string>();
  can(p: string): boolean { return this.granted.has(p); }
  canAny(...p: string[]): boolean { return p.some(x => this.granted.has(x)); }
  ensureLoaded() { return of(true); }
}

/** Spécialité d'équipe (étape 2) : formulaire, badge et contenu de la requête. */
describe('TeamsComponent — spécialité', () => {
  let fixture: ComponentFixture<TeamsComponent>;
  let component: TeamsComponent;
  let service: jasmine.SpyObj<TeamService>;
  let perms: FakePerms;

  const team = (over: Partial<Team> = {}): Team => ({
    id: 7, name: 'Réseau', description: 'Équipe réseau', lead: { id: 2, username: 'alice', displayName: 'Alice Martin' },
    active: true, memberCount: 2, openTaskCount: 1, createdBy: null, createdAt: '2026-11-01T09:00:00', updatedAt: '2026-11-01T09:00:00', ...over
  });

  beforeEach(() => {
    service = jasmine.createSpyObj<TeamService>('TeamService',
      ['listTeams', 'members', 'teamSummary', 'createTeam', 'updateTeam', 'addMember', 'removeMember', 'changeLead', 'deactivate', 'activate', 'deleteTeam']);
    const api = jasmine.createSpyObj<ApiService>('ApiService', ['getUsers']);
    perms = new FakePerms();
    service.listTeams.and.returnValue(of([team({ specialty: 'VM' }), team({ id: 8, name: 'Générale' })]));
    service.members.and.returnValue(of([]));
    api.getUsers.and.returnValue(of([{ id: 2, username: 'alice', firstName: 'Alice', lastName: 'X', enabled: true } as UserData]));
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

  it("affiche un badge de spécialité seulement pour l'équipe spécialisée", () => {
    create('TEAMS_VIEW');
    const badges = Array.from((fixture.nativeElement as HTMLElement).querySelectorAll('span.badge[title="Spécialité"]'));
    expect(badges.length).toBe(1);
    expect(badges[0].textContent).toContain('Machines virtuelles');
  });

  it("propose les quatre spécialités et « Aucune » par défaut à la création", () => {
    create('TEAMS_VIEW', 'TEAMS_CREATE');
    component.openCreate();
    expect(component.form.specialty).toBeNull();
    expect(component.scopes.map(s => s.value)).toEqual(['VM', 'CLUSTER', 'APPLICATION', 'SERVICE']);
  });

  it("envoie la spécialité choisie à la création", () => {
    service.createTeam.and.returnValue(of(team()));
    create('TEAMS_VIEW', 'TEAMS_CREATE');
    component.openCreate();
    component.form.name = 'Infra';
    component.form.specialty = 'CLUSTER';
    component.submit();
    expect(service.createTeam).toHaveBeenCalledWith({ name: 'Infra', description: null, leadId: null, specialty: 'CLUSTER' });
  });

  it("n'ajoute pas de champ specialty quand aucune spécialité n'est choisie (requête inchangée)", () => {
    service.createTeam.and.returnValue(of(team()));
    create('TEAMS_VIEW', 'TEAMS_CREATE');
    component.openCreate();
    component.form.name = 'Simple';
    component.submit();
    const body = service.createTeam.calls.mostRecent().args[0];
    expect('specialty' in body).toBeFalse();
  });

  it("préremplit la spécialité en modification et l'efface explicitement (null)", () => {
    service.updateTeam.and.returnValue(of(team()));
    create('TEAMS_VIEW', 'TEAMS_UPDATE');
    component.openEdit(team({ specialty: 'APPLICATION' }));
    expect(component.form.specialty).toBe('APPLICATION');
    component.form.specialty = null;
    component.submit();
    expect(service.updateTeam).toHaveBeenCalledWith(7, jasmine.objectContaining({ specialty: null }));
  });

  it("ne touche pas à la spécialité d'une équipe non spécialisée modifiée sans en choisir une", () => {
    service.updateTeam.and.returnValue(of(team()));
    create('TEAMS_VIEW', 'TEAMS_UPDATE');
    component.openEdit(team());
    component.submit();
    const body = service.updateTeam.calls.mostRecent().args[1];
    expect('specialty' in body).toBeFalse();
  });
});
