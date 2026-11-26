import { ComponentFixture, TestBed } from '@angular/core/testing';
import { FormsModule } from '@angular/forms';
import { of, throwError } from 'rxjs';
import { AuditTrailService } from '../../core/services/audit-trail.service';
import { OrgPermissionsService } from '../../core/services/org-permissions.service';
import { AuditEntry, Page } from '../../core/models/org.models';
import { AuditTrailComponent } from './audit-trail.component';

class FakePerms {
  granted = new Set<string>();
  can(p: string): boolean { return this.granted.has(p); }
  canAny(...p: string[]): boolean { return p.some(x => this.granted.has(x)); }
  ensureLoaded() { return of(true); }
}

describe('AuditTrailComponent', () => {
  let fixture: ComponentFixture<AuditTrailComponent>;
  let component: AuditTrailComponent;
  let service: jasmine.SpyObj<AuditTrailService>;
  let perms: FakePerms;

  const entry = (over: Partial<AuditEntry> = {}): AuditEntry => ({
    id: 5, occurredAt: '2026-11-25T09:30:00', actorUserId: 1, actorUsername: 'admin', actorRole: 'ADMIN',
    module: 'USERS', action: 'UPDATE', objectType: 'AppUser', objectId: '12', summary: 'Modification de alice',
    result: 'SUCCESS', clientIp: '10.0.0.5', requestId: 'req-1', hasChanges: true, oldValues: null, newValues: null, ...over
  });

  const pageOf = (items: AuditEntry[]): Page<AuditEntry> =>
    ({ content: items, totalElements: items.length, totalPages: 1, number: 0, size: 20 });

  beforeEach(() => {
    service = jasmine.createSpyObj<AuditTrailService>('AuditTrailService', ['search', 'get', 'meta', 'exportCsv']);
    perms = new FakePerms();
    service.search.and.returnValue(of(pageOf([entry()])));
    service.meta.and.returnValue(of({ modules: ['AUTH', 'USERS'], actions: ['LOGIN', 'UPDATE'], results: ['SUCCESS', 'FAILURE', 'DENIED'] }));
    TestBed.configureTestingModule({
    imports: [FormsModule, AuditTrailComponent],
    providers: [
        { provide: AuditTrailService, useValue: service },
        { provide: OrgPermissionsService, useValue: perms }
    ]
});
  });

  function create(...granted: string[]): void {
    granted.forEach(g => perms.granted.add(g));
    fixture = TestBed.createComponent(AuditTrailComponent);
    component = fixture.componentInstance;
    fixture.detectChanges();
  }

  const el = (selector: string): HTMLElement | null => fixture.nativeElement.querySelector(selector);

  it('charge la liste et les valeurs de filtre ; aucune action de modification ou de suppression n\'existe', () => {
    create('AUDIT_VIEW_OWN', 'AUDIT_VIEW_ALL', 'AUDIT_EXPORT');
    expect(service.meta).toHaveBeenCalled();
    expect(service.search).toHaveBeenCalledWith(jasmine.objectContaining({ page: 0, size: 20 }));
    expect(fixture.nativeElement.querySelectorAll('tbody tr.clickable').length).toBe(1);
    expect(el('.btn-delete')).toBeNull();
    expect(el('.btn-edit')).toBeNull();
    expect(fixture.nativeElement.textContent).toContain('Modification de alice');
  });

  it('un utilisateur sans droit global ne voit que sa propre activité : pas de filtre par utilisateur ni d\'export', () => {
    create('AUDIT_VIEW_OWN');
    expect(fixture.nativeElement.textContent).toContain('propre activité');
    expect(el('input[placeholder="Utilisateur"]')).toBeNull();
    expect(el('.header-actions .btn-primary')).toBeNull();
    component.filters.username = 'bob';
    component.load();
    expect(service.search).toHaveBeenCalledWith(jasmine.objectContaining({ username: '' }));   // jamais envoyé sans droit global
  });

  it("un administrateur dispose de l'export et du filtre par utilisateur", () => {
    create('AUDIT_VIEW_OWN', 'AUDIT_VIEW_ALL', 'AUDIT_EXPORT');
    expect(el('input[placeholder="Utilisateur"]')).not.toBeNull();
    expect(el('.header-actions .btn-primary')).not.toBeNull();
    component.filters.username = ' bob ';
    component.filters.module = 'AUTH';
    component.load(true);
    expect(service.search).toHaveBeenCalledWith(jasmine.objectContaining({ username: 'bob', module: 'AUTH' }));
  });

  it("transmet la période en heure locale ISO et revient à la première page quand on filtre", () => {
    create('AUDIT_VIEW_OWN', 'AUDIT_VIEW_ALL');
    component.page = 3;
    component.filters.from = '2026-11-01T08:00';
    component.filters.to = '2026-11-30T18:30';
    component.load(true);
    expect(service.search).toHaveBeenCalledWith(jasmine.objectContaining({
      from: '2026-11-01T08:00:00', to: '2026-11-30T18:30:00', page: 0
    }));
  });

  it('compare les anciennes et nouvelles valeurs champ par champ', () => {
    create('AUDIT_VIEW_OWN');
    const diff = component.buildDiff(entry({
      oldValues: JSON.stringify({ role: 'USER', enabled: true, email: '***' }),
      newValues: JSON.stringify({ role: 'ADMIN', enabled: true, lastName: 'Martin' })
    }));
    const byField = (f: string) => diff.find(d => d.field === f)!;
    expect(byField('role')).toEqual({ field: 'role', before: 'USER', after: 'ADMIN', changed: true });
    expect(byField('enabled').changed).toBeFalse();
    expect(byField('lastName')).toEqual({ field: 'lastName', before: '—', after: 'Martin', changed: true });
    expect(byField('email').after).toBe('—');
  });

  it('tolère des valeurs absentes ou non JSON', () => {
    create('AUDIT_VIEW_OWN');
    expect(component.buildDiff(entry())).toEqual([]);
    const diff = component.buildDiff(entry({ oldValues: 'texte brut', newValues: null }));
    expect(diff).toEqual([{ field: 'valeur', before: 'texte brut', after: '—', changed: true }]);
  });

  it('ouvre le détail en le relisant depuis le Backend (les valeurs ne sont pas dans la liste)', () => {
    service.get.and.returnValue(of(entry({ oldValues: '{"a":1}', newValues: '{"a":2}' })));
    create('AUDIT_VIEW_OWN');
    component.openDetail(entry());
    expect(service.get).toHaveBeenCalledWith(5);
    expect(component.diff).toEqual([{ field: 'a', before: '1', after: '2', changed: true }]);
    expect(component.detailLoading).toBeFalse();
  });

  it("affiche l'erreur d'un export refusé sans télécharger quoi que ce soit", () => {
    service.exportCsv.and.returnValue(throwError(() => ({ status: 403, error: { error: 'Accès refusé' } })));
    create('AUDIT_VIEW_OWN', 'AUDIT_VIEW_ALL', 'AUDIT_EXPORT');
    component.exportCsv();
    expect(component.error).toContain('non autorisée');
    expect(component.exporting).toBeFalse();
  });

  it('traduit modules, actions et résultats pour l\'affichage', () => {
    create('AUDIT_VIEW_OWN');
    expect(component.moduleLabel('USERS')).toBe('Utilisateurs');
    expect(component.actionLabel('LOGIN_FAILED')).toBe('Échec de connexion');
    expect(component.resultLabel('DENIED')).toBe('Refusé');
    expect(component.actionLabel('NOUVELLE_ACTION')).toBe('NOUVELLE_ACTION');
    expect(component.moduleLabel(null)).toBe('—');
  });
});
