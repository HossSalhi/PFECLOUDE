import { TestBed } from '@angular/core/testing';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { environment } from '../../../environments/environment';
import { AuthService } from './auth.service';
import { OrgPermissionsService } from './org-permissions.service';
import { provideHttpClient, withInterceptorsFromDi, withXhr } from '@angular/common/http';

class FakeAuth {
  logged = true;
  user: string | null = 'alice';
  role: string | null = 'ADMIN';
  isLoggedIn(): boolean { return this.logged; }
  getUsername(): string | null { return this.user; }
  getRole(): string | null { return this.role; }
}

describe('OrgPermissionsService', () => {
  let service: OrgPermissionsService;
  let http: HttpTestingController;
  let auth: FakeAuth;
  const url = `${environment.apiBaseUrl}/api/org/permissions/me`;

  beforeEach(() => {
    TestBed.configureTestingModule({
    imports: [],
    providers: [{ provide: AuthService, useClass: FakeAuth }, provideHttpClient(withXhr(), withInterceptorsFromDi()), provideHttpClientTesting()]
});
    service = TestBed.inject(OrgPermissionsService);
    http = TestBed.inject(HttpTestingController);
    auth = TestBed.inject(AuthService) as unknown as FakeAuth;
  });

  afterEach(() => http.verify());

  function load(body: { username: string; role: string; permissions: string[] }): boolean[] {
    const results: boolean[] = [];
    service.ensureLoaded().subscribe(v => results.push(v));
    http.expectOne(url).flush(body);
    return results;
  }

  it("n'accorde rien tant que les permissions ne sont pas chargées", () => {
    expect(service.can('MAINTENANCE_VIEW')).toBeFalse();
    expect(service.canAny('TEAMS_VIEW', 'TASKS_VIEW')).toBeFalse();
  });

  it("ne contacte pas le Backend quand personne n'est connecté", () => {
    auth.logged = false;
    let result: boolean | undefined;
    service.ensureLoaded().subscribe(v => result = v);
    http.expectNone(url);
    expect(result).toBeFalse();
    expect(service.currentUsername()).toBeNull();
  });

  it('charge les permissions du Backend et ne retient que celles accordées', () => {
    const results = load({ username: 'alice', role: 'ADMIN', permissions: ['MAINTENANCE_VIEW', 'TEAMS_VIEW'] });
    expect(results).toEqual([true]);
    expect(service.can('MAINTENANCE_VIEW')).toBeTrue();
    expect(service.can('TEAMS_VIEW')).toBeTrue();
    expect(service.can('MAINTENANCE_DELETE')).toBeFalse();
    expect(service.canAny('REPORTS_VIEW', 'TEAMS_VIEW')).toBeTrue();
    expect(service.canAny('REPORTS_VIEW', 'REPORTS_EXPORT')).toBeFalse();
    expect(service.currentUsername()).toBe('alice');
  });

  it('met les permissions en cache : aucun second appel pour la même identité', () => {
    load({ username: 'alice', role: 'ADMIN', permissions: ['TEAMS_VIEW'] });
    let again: boolean | undefined;
    service.ensureLoaded().subscribe(v => again = v);
    http.expectNone(url);
    expect(again).toBeTrue();
  });

  it('partage une seule requête entre des appels simultanés', () => {
    const seen: boolean[] = [];
    service.ensureLoaded().subscribe(v => seen.push(v));
    service.ensureLoaded().subscribe(v => seen.push(v));
    http.expectOne(url).flush({ username: 'alice', role: 'ADMIN', permissions: ['TEAMS_VIEW'] });
    expect(seen).toEqual([true, true]);
  });

  it('recharge quand une autre personne ou un autre rôle se connecte', () => {
    load({ username: 'alice', role: 'ADMIN', permissions: ['REPORTS_VIEW'] });
    expect(service.can('REPORTS_VIEW')).toBeTrue();

    auth.user = 'bob';
    auth.role = 'USER';
    expect(service.can('REPORTS_VIEW')).toBeFalse();      // plus valable pour une autre identité
    load({ username: 'bob', role: 'USER', permissions: ['MAINTENANCE_VIEW'] });
    expect(service.can('MAINTENANCE_VIEW')).toBeTrue();
    expect(service.can('REPORTS_VIEW')).toBeFalse();
  });

  it('ignore la casse du nom d\'utilisateur pour reconnaître la même identité', () => {
    load({ username: 'alice', role: 'ADMIN', permissions: ['TEAMS_VIEW'] });
    auth.user = 'ALICE';
    expect(service.can('TEAMS_VIEW')).toBeTrue();
  });

  it("n'accorde rien et permet une nouvelle tentative après une erreur du Backend", () => {
    let result: boolean | undefined;
    service.ensureLoaded().subscribe(v => result = v);
    http.expectOne(url).flush({ error: 'boom' }, { status: 500, statusText: 'Server Error' });
    expect(result).toBeFalse();
    expect(service.can('MAINTENANCE_VIEW')).toBeFalse();

    const retry = load({ username: 'alice', role: 'ADMIN', permissions: ['MAINTENANCE_VIEW'] });
    expect(retry).toEqual([true]);
    expect(service.can('MAINTENANCE_VIEW')).toBeTrue();
  });

  it('oublie les permissions après clear()', () => {
    load({ username: 'alice', role: 'ADMIN', permissions: ['TEAMS_VIEW'] });
    service.clear();
    expect(service.can('TEAMS_VIEW')).toBeFalse();
  });
});
