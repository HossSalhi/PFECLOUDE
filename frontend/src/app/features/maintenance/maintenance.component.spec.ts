import { ComponentFixture, TestBed } from '@angular/core/testing';
import { FormsModule } from '@angular/forms';
import { of, throwError } from 'rxjs';
import { ApiService } from '../../core/services/api.service';
import { MaintenanceService } from '../../core/services/maintenance.service';
import { OrgPermissionsService } from '../../core/services/org-permissions.service';
import { Maintenance, Page } from '../../core/models/org.models';
import { MaintenanceComponent } from './maintenance.component';

class FakePerms {
  granted = new Set<string>();
  can(p: string): boolean { return this.granted.has(p); }
  canAny(...p: string[]): boolean { return p.some(x => this.granted.has(x)); }
  currentUsername(): string | null { return 'alice'; }
  ensureLoaded() { return of(true); }
}

describe('MaintenanceComponent', () => {
  let fixture: ComponentFixture<MaintenanceComponent>;
  let component: MaintenanceComponent;
  let service: jasmine.SpyObj<MaintenanceService>;
  let api: jasmine.SpyObj<ApiService>;
  let perms: FakePerms;

  const planned: Maintenance = {
    id: 1, title: 'Mise à jour du noyau', description: null, reason: 'Sécurité', resourceType: 'VM',
    resourceName: 'k8s-worker1', startAt: '2026-12-01T22:00:00', endAt: '2026-12-02T02:00:00', status: 'PLANNED',
    createdBy: { id: 1, username: 'admin', displayName: 'Admin' }, responsible: null,
    actualStartAt: null, actualEndAt: null, cancelReason: null, startOverdue: false, endOverdue: false,
    createdAt: '2026-11-25T09:00:00', updatedAt: '2026-11-25T09:00:00'
  };

  const pageOf = (items: Maintenance[]): Page<Maintenance> =>
    ({ content: items, totalElements: items.length, totalPages: 1, number: 0, size: 15 });

  beforeEach(() => {
    service = jasmine.createSpyObj<MaintenanceService>('MaintenanceService',
      ['list', 'calendar', 'inProgress', 'resources', 'get', 'history', 'create', 'update', 'start', 'complete', 'cancel', 'remove']);
    api = jasmine.createSpyObj<ApiService>('ApiService', ['getUsers']);
    perms = new FakePerms();
    service.list.and.returnValue(of(pageOf([planned])));
    service.resources.and.returnValue(of([]));
    service.history.and.returnValue(of([]));
    service.calendar.and.returnValue(of([]));
    api.getUsers.and.returnValue(of([]));
    TestBed.configureTestingModule({
    imports: [FormsModule, MaintenanceComponent],
    providers: [
        { provide: MaintenanceService, useValue: service },
        { provide: ApiService, useValue: api },
        { provide: OrgPermissionsService, useValue: perms }
    ]
});
  });

  function create(...granted: string[]): void {
    granted.forEach(g => perms.granted.add(g));
    fixture = TestBed.createComponent(MaintenanceComponent);
    component = fixture.componentInstance;
    fixture.detectChanges();
  }

  const el = (selector: string): HTMLElement | null => fixture.nativeElement.querySelector(selector);
  const all = (selector: string): HTMLElement[] => Array.from(fixture.nativeElement.querySelectorAll(selector));

  function fillValidForm(): void {
    component.openCreate();
    component.form.title = 'Remplacement de disque';
    component.form.resourceType = 'VM';
    component.form.resourceName = 'k8s-worker2';
    component.form.startAt = '2026-12-01T22:00';
    component.form.endAt = '2026-12-02T02:00';
  }

  it("charge la liste et n'offre aucune action d'écriture à un utilisateur en lecture seule", () => {
    create('MAINTENANCE_VIEW');
    expect(service.list).toHaveBeenCalled();
    expect(all('tbody tr.clickable').length).toBe(1);
    expect(el('.header-actions .btn-primary')).toBeNull();                 // pas de « Planifier »
    expect(el('.btn-icon.btn-edit')).toBeNull();
    expect(el('.btn-icon.btn-ok')).toBeNull();                             // ni démarrer ni terminer
    expect(el('.btn-icon.btn-warn')).toBeNull();                           // ni annuler
    expect(el('.btn-icon.btn-view')).not.toBeNull();                       // la consultation reste possible
    expect(api.getUsers).not.toHaveBeenCalled();                           // pas de liste d'utilisateurs sans droit de création
  });

  it('propose planifier, modifier, démarrer et annuler à un gestionnaire', () => {
    create('MAINTENANCE_VIEW', 'MAINTENANCE_CREATE', 'MAINTENANCE_UPDATE', 'MAINTENANCE_CANCEL');
    expect(el('.header-actions .btn-primary')).not.toBeNull();
    expect(el('.btn-icon.btn-edit')).not.toBeNull();
    expect(el('.btn-icon.btn-ok[title="Démarrer"]')).not.toBeNull();
    expect(el('.btn-icon.btn-warn[title="Annuler"]')).not.toBeNull();
    expect(el('.btn-icon.btn-delete')).toBeNull();                         // la suppression n'est pas dans la liste
    expect(api.getUsers).toHaveBeenCalled();
  });

  it("n'offre ni modification ni démarrage pour une maintenance terminée", () => {
    service.list.and.returnValue(of(pageOf([{ ...planned, status: 'COMPLETED' }])));
    create('MAINTENANCE_VIEW', 'MAINTENANCE_CREATE', 'MAINTENANCE_UPDATE', 'MAINTENANCE_CANCEL');
    expect(el('.btn-icon.btn-edit')).toBeNull();
    expect(el('.btn-icon.btn-ok')).toBeNull();
    expect(el('.btn-icon.btn-warn')).toBeNull();
  });

  it('signale les dépassements d\'horaire à titre indicatif', () => {
    service.list.and.returnValue(of(pageOf([{ ...planned, startOverdue: true }])));
    create('MAINTENANCE_VIEW');
    expect(fixture.nativeElement.textContent).toContain('Début dépassé');
  });

  it('refuse une fin antérieure au début sans appeler le Backend', () => {
    create('MAINTENANCE_VIEW', 'MAINTENANCE_CREATE');
    fillValidForm();
    component.form.endAt = '2026-12-01T20:00';
    component.submit();
    expect(component.modalError).toContain('postérieure');
    expect(service.create).not.toHaveBeenCalled();
  });

  it('refuse une maintenance de plus de 31 jours', () => {
    create('MAINTENANCE_VIEW', 'MAINTENANCE_CREATE');
    fillValidForm();
    component.form.endAt = '2027-01-15T02:00';
    component.submit();
    expect(component.modalError).toContain('31 jours');
    expect(service.create).not.toHaveBeenCalled();
  });

  it('exige un titre, une ressource et des dates', () => {
    create('MAINTENANCE_VIEW', 'MAINTENANCE_CREATE');
    component.openCreate();
    component.submit();
    expect(component.modalError).toContain('titre');
    component.form.title = 'T';
    component.submit();
    expect(component.modalError).toContain('ressource');
    component.form.resourceName = 'k8s-master';
    component.submit();
    expect(component.modalError).toContain('dates');
    expect(service.create).not.toHaveBeenCalled();
  });

  it('crée la maintenance avec des dates ISO locales puis recharge la liste', () => {
    service.create.and.returnValue(of(planned));
    create('MAINTENANCE_VIEW', 'MAINTENANCE_CREATE');
    fillValidForm();
    component.form.responsibleId = 3;
    const callsBefore = service.list.calls.count();
    component.submit();
    expect(service.create).toHaveBeenCalledWith(jasmine.objectContaining({
      title: 'Remplacement de disque', resourceType: 'VM', resourceName: 'k8s-worker2',
      startAt: '2026-12-01T22:00:00', endAt: '2026-12-02T02:00:00', responsibleId: 3, description: null, reason: null
    }));
    expect(component.showForm).toBeFalse();
    expect(component.saving).toBeFalse();
    expect(service.list.calls.count()).toBe(callsBefore + 1);
    expect(component.success).toContain('planifiée');
  });

  it('affiche le message du Backend en cas de conflit et garde le formulaire ouvert', () => {
    service.create.and.returnValue(throwError(() => ({
      status: 409, error: { error: 'Conflit : « k8s-worker2 » est déjà en maintenance sur ce créneau', status: 409 }
    })));
    create('MAINTENANCE_VIEW', 'MAINTENANCE_CREATE');
    fillValidForm();
    component.submit();
    expect(component.modalError).toContain('déjà en maintenance');
    expect(component.showForm).toBeTrue();
    expect(component.saving).toBeFalse();
  });

  it('verrouille le début et la ressource d\'une maintenance déjà démarrée', () => {
    create('MAINTENANCE_VIEW', 'MAINTENANCE_UPDATE');
    component.openEdit({ ...planned, status: 'IN_PROGRESS' });
    expect(component.lockedFields).toBeTrue();
    component.openEdit(planned);
    expect(component.lockedFields).toBeFalse();
  });

  it('affiche le calendrier du mois avec les maintenances qui chevauchent chaque jour', () => {
    service.calendar.and.returnValue(of([{ ...planned, startAt: '2026-11-10T22:00:00', endAt: '2026-11-11T02:00:00' }]));
    create('MAINTENANCE_VIEW');
    component.calYear = 2026;
    component.calMonth = 10;
    component.setView('calendar');
    fixture.detectChanges();
    expect(service.calendar).toHaveBeenCalled();
    expect(component.weeks.length).toBe(6);
    const withEvents = component.weeks.reduce((all2, w) => all2.concat(w), []).filter(d => d.events.length > 0).map(d => d.iso);
    expect(withEvents).toEqual(['2026-11-10', '2026-11-11']);
    expect(all('.cal-ev').length).toBe(2);
  });

  it('annule avec un motif facultatif', () => {
    service.cancel.and.returnValue(of({ ...planned, status: 'CANCELLED' }));
    create('MAINTENANCE_VIEW', 'MAINTENANCE_CANCEL');
    component.askCancel(planned);
    component.cancelReason = '  Reporté à janvier ';
    component.submitCancel();
    expect(service.cancel).toHaveBeenCalledWith(1, 'Reporté à janvier');
    expect(component.showCancel).toBeFalse();
  });

  it('résume l\'historique des changements en lignes lisibles', () => {
    create('MAINTENANCE_VIEW');
    const lines = component.historyLines({
      id: 1, changedAt: '2026-11-25T10:00:00', changedBy: 'admin', changeType: 'UPDATE',
      details: JSON.stringify({ old: { startAt: '2026-12-01T22:00:00' }, new: { startAt: '2026-12-02T22:00:00', title: 'Nouveau' } })
    });
    expect(lines).toContain('Début : 01/12/2026 22:00 → 02/12/2026 22:00');
    expect(lines).toContain('Titre : Nouveau');
    expect(component.historyLines({ id: 2, changedAt: 'x', changedBy: null, changeType: 'CREATE', details: null })).toEqual([]);
  });
});
