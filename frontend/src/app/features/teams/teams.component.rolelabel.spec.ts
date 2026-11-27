import { TestBed } from '@angular/core/testing';
import { NO_ERRORS_SCHEMA } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { provideRouter } from '@angular/router';
import { of } from 'rxjs';
import { ApiService } from '../../core/services/api.service';
import { TeamService } from '../../core/services/team.service';
import { OrgPermissionsService } from '../../core/services/org-permissions.service';
import { Team, TeamMember } from '../../core/models/org.models';
import { TeamsComponent } from './teams.component';

class FakePerms {
  granted = new Set<string>(['TEAMS_VIEW']);
  can(p: string): boolean { return this.granted.has(p); }
  canAny(...p: string[]): boolean { return p.some(x => this.granted.has(x)); }
  ensureLoaded() { return of(true); }
}

/** Badges de rôle calculés (étape 5) dans le détail d'une équipe. */
describe('TeamsComponent — badge de rôle', () => {
  const team: Team = {
    id: 7, name: 'Réseau', description: null, lead: { id: 2, username: 'alice', displayName: 'Alice' }, specialty: 'CLUSTER',
    active: true, memberCount: 2, openTaskCount: 0, createdBy: null, createdAt: '2026-11-01T09:00:00', updatedAt: '2026-11-01T09:00:00'
  };
  const member = (over: Partial<TeamMember>): TeamMember => ({
    userId: 1, username: 'bob', displayName: 'Bob', role: 'USER', enabled: true, lead: false,
    addedAt: '2026-11-02T09:00:00', addedBy: 'admin', ...over
  });

  function open(members: TeamMember[]) {
    const service = jasmine.createSpyObj<TeamService>('TeamService', ['listTeams', 'members', 'teamSummary']);
    service.listTeams.and.returnValue(of([team]));
    service.members.and.returnValue(of(members));
    service.teamSummary.and.returnValue(of({ teamId: 7, teamName: 'Réseau', tasks: { total: 0, byStatus: {}, overdue: 0, completionRate: 0 }, members: [] }));
    const api = jasmine.createSpyObj<ApiService>('ApiService', ['getUsers']);
    api.getUsers.and.returnValue(of([]));
    TestBed.configureTestingModule({
      imports: [FormsModule, TeamsComponent], schemas: [NO_ERRORS_SCHEMA],
      providers: [provideRouter([]), { provide: TeamService, useValue: service }, { provide: ApiService, useValue: api },
        { provide: OrgPermissionsService, useValue: new FakePerms() }]
    });
    const fixture = TestBed.createComponent(TeamsComponent);
    fixture.detectChanges();
    fixture.componentInstance.select(team);
    fixture.detectChanges();
    return fixture.nativeElement as HTMLElement;
  }

  it('affiche « Chef … » pour le responsable et « Technicien … » pour un membre', () => {
    const el = open([member({ userId: 2, username: 'alice', displayName: 'Alice', lead: true, roleLabel: 'Chef Cluster' }),
      member({ roleLabel: 'Technicien Cluster' })]);
    const badges = Array.from(el.querySelectorAll('.team-role-label')).map(b => b.textContent!.trim());

    expect(badges).toEqual(['Chef Cluster', 'Technicien Cluster']);
    expect(el.querySelectorAll('.team-role-label.is-lead').length).toBe(1);
  });

  it("n'affiche aucun badge quand le serveur n'en fournit pas, et garde le rôle HELIOS", () => {
    const el = open([member({})]);

    expect(el.querySelectorAll('.team-role-label').length).toBe(0);
    expect(el.textContent).toContain('USER');
  });
});
