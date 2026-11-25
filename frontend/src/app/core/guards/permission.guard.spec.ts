import { TestBed } from '@angular/core/testing';
import { ActivatedRouteSnapshot, Router, UrlTree } from '@angular/router';
import { Observable, of } from 'rxjs';
import { AuthService } from '../services/auth.service';
import { OrgPermissionsService } from '../services/org-permissions.service';
import { OrgPermission } from '../models/org.models';
import { PermissionGuard } from './permission.guard';

describe('PermissionGuard', () => {
  let guard: PermissionGuard;
  let logged: boolean;
  let loaded: boolean;
  let granted: Set<string>;
  const router = jasmine.createSpyObj<Router>('Router', ['createUrlTree']);

  const route = (permissions?: OrgPermission[]) =>
    ({ data: permissions ? { permissions } : {} } as unknown as ActivatedRouteSnapshot);

  /** Résultat sous forme lisible : true, ou le chemin de redirection. */
  function run(r: ActivatedRouteSnapshot): boolean | string {
    let out: boolean | string = '';
    (guard.canActivate(r) as Observable<boolean | UrlTree>).subscribe(v => {
      out = typeof v === 'boolean' ? v : (v as any).path;
    });
    return out;
  }

  beforeEach(() => {
    logged = true;
    loaded = true;
    granted = new Set<string>();
    router.createUrlTree.and.callFake((commands: any[]) => ({ path: commands.join('/') } as unknown as UrlTree));
    TestBed.configureTestingModule({
      providers: [
        { provide: Router, useValue: router },
        { provide: AuthService, useValue: { isLoggedIn: () => logged } },
        {
          provide: OrgPermissionsService,
          useValue: {
            ensureLoaded: () => of(loaded),
            canAny: (...p: string[]) => p.some(x => granted.has(x))
          }
        }
      ]
    });
    guard = TestBed.inject(PermissionGuard);
  });

  it('ouvre la route quand au moins une permission requise est accordée', () => {
    granted.add('AUDIT_VIEW_OWN');
    expect(run(route(['AUDIT_VIEW_OWN', 'AUDIT_VIEW_ALL']))).toBeTrue();
  });

  it('renvoie vers le tableau de bord quand aucune permission requise n\'est accordée', () => {
    granted.add('TEAMS_VIEW');
    expect(run(route(['REPORTS_VIEW']))).toBe('/dashboard');
  });

  it('renvoie vers la connexion quand la session a disparu', () => {
    logged = false;
    loaded = false;
    expect(run(route(['TEAMS_VIEW']))).toBe('/login');
  });

  it("n'ouvre pas la page, sans déconnecter, quand les permissions sont indisponibles", () => {
    loaded = false;
    expect(run(route(['TEAMS_VIEW']))).toBe('/dashboard');
  });

  it('laisse passer une route sans permission déclarée', () => {
    expect(run(route())).toBeTrue();
  });
});
