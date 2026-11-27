import { TestBed } from '@angular/core/testing';
import { NO_ERRORS_SCHEMA } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { of } from 'rxjs';
import { ApiService, UserData } from '../../core/services/api.service';
import { AuthService } from '../../core/services/auth.service';
import { UsersComponent } from './users.component';

/** Badges de rôle calculés (étape 5) dans la liste des utilisateurs : en plus du rôle HELIOS, jamais à sa place. */
describe('UsersComponent — badges d\'équipe', () => {
  const user = (over: Partial<UserData>): UserData => ({
    id: 1, username: 'bob', email: 'bob@test.local', firstName: 'Bob', lastName: 'Martin', role: 'USER', enabled: true,
    twoFactorEnabled: false, lastLoginAt: null, lastLoginIp: null, failedAttempts: 0, lockedUntil: null,
    createdAt: '2026-11-01T09:00:00', blockedAt: null, blockedBy: null, blockReason: null, ...over
  } as UserData);

  function render(users: UserData[]): HTMLElement {
    const api = jasmine.createSpyObj<ApiService>('ApiService', ['getUsers']);
    api.getUsers.and.returnValue(of(users));
    const auth = { isSuperAdmin: () => true, isAdmin: () => true } as unknown as AuthService;
    TestBed.configureTestingModule({
      imports: [FormsModule, UsersComponent], schemas: [NO_ERRORS_SCHEMA],
      providers: [{ provide: ApiService, useValue: api }, { provide: AuthService, useValue: auth }]
    });
    const fixture = TestBed.createComponent(UsersComponent);
    fixture.detectChanges();
    return fixture.nativeElement as HTMLElement;
  }

  it('affiche chaque badge calculé à côté du rôle HELIOS, le responsable en évidence', () => {
    const el = render([user({ roleLabels: ['Chef VM', 'Technicien Application'] })]);
    const badges = Array.from(el.querySelectorAll('.team-role-label'));

    expect(badges.map(b => b.textContent!.trim())).toEqual(['Chef VM', 'Technicien Application']);
    expect(badges[0].classList).toContain('is-lead');
    expect(badges[1].classList).not.toContain('is-lead');
    expect(el.querySelector('.role-badge')!.textContent).toContain('Utilisateur');
  });

  it("n'affiche aucun badge pour un utilisateur sans équipe ou si le champ est absent", () => {
    const el = render([user({ roleLabels: [] }), user({ id: 2, username: 'carol', email: 'c@test.local', roleLabels: undefined })]);

    expect(el.querySelectorAll('.team-role-label').length).toBe(0);
    expect(el.querySelectorAll('.role-badge').length).toBe(2);
  });
});
