import { ComponentFixture, TestBed } from '@angular/core/testing';
import { FormsModule } from '@angular/forms';
import { provideRouter } from '@angular/router';
import { of, throwError } from 'rxjs';
import { ApiService } from '../../core/services/api.service';
import { MaintenanceService } from '../../core/services/maintenance.service';
import { OrgPermissionsService } from '../../core/services/org-permissions.service';
import { TeamService } from '../../core/services/team.service';
import { Maintenance, Page, Team } from '../../core/models/org.models';
import { MaintenanceComponent } from './maintenance.component';

class FakePerms {
  granted = new Set<string>();
  can(p: string): boolean { return this.granted.has(p); }
  canAny(...p: string[]): boolean { return p.some(x => this.granted.has(x)); }
  currentUsername(): string | null { return 'alice'; }
  ensureLoaded() { return of(true); }
}

/** Équipe porteuse, suspension et reprise (étape 3). */
describe('MaintenanceComponent — équipe et suspension', () => {
  let fixture: ComponentFixture<MaintenanceComponent>;
  let component: MaintenanceComponent;
  let service: jasmine.SpyObj<MaintenanceService>;
  let teamService: jasmine.SpyObj<TeamService>;
  let perms: FakePerms;

  const base: Maintenance = {
    id: 5, title: 'Mise à jour du noyau', description: null, reason: 'Sécurité', resourceType: 'NODE',
    resourceName: 'k8s-worker1', startAt: '2026-12-01T22:00:00', endAt: '2026-12-02T02:00:00', status: 'IN_PROGRESS',
    createdBy: { id: 1, username: 'admin', displayName: 'Admin' }, responsible: { id: 2, username: 'alice', displayName: 'Alice Martin' },
    actualStartAt: '2026-12-01T22:00:00', actualEndAt: null, cancelReason: null, startOverdue: false, endOverdue: false,
    createdAt: '2026-11-25T09:00:00', updatedAt: '2026-11-25T09:00:00'
  };
  const withTeam: Maintenance = { ...base, team: { id: 7, name: 'Équipe cluster', specialty: 'CLUSTER', lead: { id: 3, username: 'bob', displayName: 'Bob Durand' } } };
  const suspended: Maintenance = { ...withTeam, status: 'SUSPENDED', suspendedAt: '2026-12-01T23:30:00', suspendReason: 'Incident CPU', suspendIncidentId: 13497 };

  const team = (over: Partial<Team>): Team => ({
    id: 7, name: 'Équipe cluster', description: null, specialty: 'CLUSTER', lead: null, active: true, memberCount: 2, openTaskCount: 0,
    createdBy: null, createdAt: '2026-11-01T09:00:00', updatedAt: '2026-11-01T09:00:00', ...over
  });

  const pageOf = (items: Maintenance[]): Page<Maintenance> =>
    ({ content: items, totalElements: items.length, totalPages: 1, number: 0, size: 15 });

  beforeEach(() => {
    service = jasmine.createSpyObj<MaintenanceService>('MaintenanceService',
      ['list', 'calendar', 'inProgress', 'resources', 'get', 'history', 'create', 'update', 'start', 'complete', 'cancel', 'remove', 'pause', 'resume']);
    teamService = jasmine.createSpyObj<TeamService>('TeamService', ['listTeams']);
    const api = jasmine.createSpyObj<ApiService>('ApiService', ['getUsers']);
    perms = new FakePerms();
    service.resources.and.returnValue(of([]));
    service.history.and.returnValue(of([]));
    service.calendar.and.returnValue(of([]));
    api.getUsers.and.returnValue(of([]));
    teamService.listTeams.and.returnValue(of([
      team({ id: 7, name: 'Équipe cluster', specialty: 'CLUSTER' }),
      team({ id: 8, name: 'Équipe VM', specialty: 'VM' }),
      team({ id: 9, name: 'Équipe générale', specialty: null }),
      team({ id: 10, name: 'Équipe inactive', specialty: 'CLUSTER', active: false })
    ]));
    TestBed.configureTestingModule({
      imports: [FormsModule, MaintenanceComponent],
      providers: [
        provideRouter([]),
        { provide: MaintenanceService, useValue: service },
        { provide: ApiService, useValue: api },
        { provide: TeamService, useValue: teamService },
        { provide: OrgPermissionsService, useValue: perms }
      ]
    });
  });

  function create(items: Maintenance[], ...granted: string[]): void {
    service.list.and.returnValue(of(pageOf(items)));
    granted.forEach(g => perms.granted.add(g));
    fixture = TestBed.createComponent(MaintenanceComponent);
    component = fixture.componentInstance;
    fixture.detectChanges();
  }

  const el = (selector: string): HTMLElement | null => fixture.nativeElement.querySelector(selector);

  it("affiche l'équipe porteuse sous le responsable dans la liste", () => {
    create([withTeam], 'MAINTENANCE_VIEW');
    expect(el('tbody tr.clickable')?.textContent).toContain('Équipe cluster');
  });

  it("n'affiche aucune équipe pour une ancienne maintenance sans équipe", () => {
    create([base], 'MAINTENANCE_VIEW');
    expect(el('tbody tr.clickable')?.textContent).not.toContain('Équipe');
  });

  it("propose seulement les équipes actives compatibles avec le type de ressource", () => {
    create([], 'MAINTENANCE_VIEW', 'MAINTENANCE_CREATE');
    component.openCreate();
    component.form.resourceType = 'NODE';   // portée CLUSTER
    expect(component.teamsForType().map(t => t.name)).toEqual(['Équipe cluster', 'Équipe générale']);
    component.form.resourceType = 'VM';
    expect(component.teamsForType().map(t => t.name)).toEqual(['Équipe VM', 'Équipe générale']);
  });

  it("envoie l'équipe choisie à la création", () => {
    service.create.and.returnValue(of(withTeam));
    create([], 'MAINTENANCE_VIEW', 'MAINTENANCE_CREATE');
    component.openCreate();
    component.form.title = 'Avec équipe';
    component.form.resourceType = 'NODE';
    component.form.resourceName = 'k8s-worker2';
    component.form.startAt = '2026-12-01T22:00';
    component.form.endAt = '2026-12-02T02:00';
    component.form.teamId = 7;
    component.submit();
    expect(service.create).toHaveBeenCalledWith(jasmine.objectContaining({ teamId: 7 }));
  });

  it("n'ajoute pas de champ teamId sans équipe choisie (requête inchangée)", () => {
    service.create.and.returnValue(of(base));
    create([], 'MAINTENANCE_VIEW', 'MAINTENANCE_CREATE');
    component.openCreate();
    component.form.title = 'Sans équipe';
    component.form.resourceName = 'k8s-worker2';
    component.form.startAt = '2026-12-01T22:00';
    component.form.endAt = '2026-12-02T02:00';
    component.submit();
    expect('teamId' in service.create.calls.mostRecent().args[0]).toBeFalse();
  });

  it("retire l'équipe explicitement (teamId null) en modification", () => {
    service.update.and.returnValue(of(base));
    create([], 'MAINTENANCE_VIEW', 'MAINTENANCE_UPDATE');
    component.openEdit({ ...withTeam, status: 'PLANNED' });
    expect(component.form.teamId).toBe(7);
    component.form.teamId = null;
    component.submit();
    expect(service.update).toHaveBeenCalledWith(5, jasmine.objectContaining({ teamId: null }));
  });

  it("le formulaire reste utilisable si la liste des équipes est indisponible", () => {
    teamService.listTeams.and.returnValue(throwError(() => ({ status: 403 })));
    create([], 'MAINTENANCE_VIEW', 'MAINTENANCE_CREATE');
    component.openCreate();
    expect(component.teams).toEqual([]);
    expect(component.showForm).toBeTrue();
  });

  // ── Suspension et reprise ────────────────────────────────────────────────

  it("offre « Suspendre » sur une maintenance en cours et « Reprendre » sur une suspendue, selon les droits", () => {
    create([withTeam], 'MAINTENANCE_VIEW', 'MAINTENANCE_UPDATE');
    expect(el('.btn-icon[title="Suspendre"]')).not.toBeNull();
    expect(el('.btn-icon[title="Reprendre"]')).toBeNull();
    create([suspended], 'MAINTENANCE_VIEW', 'MAINTENANCE_UPDATE');
    expect(el('.btn-icon[title="Reprendre"]')).not.toBeNull();
    expect(el('.btn-icon[title="Suspendre"]')).toBeNull();
  });

  it("n'offre ni suspension ni reprise sans le droit de modification", () => {
    create([withTeam, suspended], 'MAINTENANCE_VIEW');
    expect(el('.btn-icon[title="Suspendre"]')).toBeNull();
    expect(el('.btn-icon[title="Reprendre"]')).toBeNull();
  });

  it("garde une maintenance suspendue modifiable et annulable (elle n'est pas terminée) mais verrouille sa ressource", () => {
    create([suspended], 'MAINTENANCE_VIEW', 'MAINTENANCE_UPDATE', 'MAINTENANCE_CANCEL');
    expect(component.isActive(suspended)).toBeTrue();
    expect(el('.btn-icon.btn-edit')).not.toBeNull();
    expect(el('.btn-icon.btn-warn[title="Annuler"]')).not.toBeNull();
    component.openEdit(suspended);
    expect(component.lockedFields).toBeTrue();
  });

  it("exige un motif ou un incident pour suspendre", () => {
    create([withTeam], 'MAINTENANCE_VIEW', 'MAINTENANCE_UPDATE');
    component.askPause(withTeam);
    component.submitPause();
    expect(component.modalError).toContain('motif');
    expect(service.pause).not.toHaveBeenCalled();
  });

  it("refuse un numéro d'incident invalide", () => {
    create([withTeam], 'MAINTENANCE_VIEW', 'MAINTENANCE_UPDATE');
    component.askPause(withTeam);
    component.pauseIncident = '12.5';
    component.submitPause();
    expect(component.modalError).toContain('entier positif');
    expect(service.pause).not.toHaveBeenCalled();
  });

  it("envoie le motif et l'incident à la suspension", () => {
    service.pause.and.returnValue(of(suspended));
    create([withTeam], 'MAINTENANCE_VIEW', 'MAINTENANCE_UPDATE');
    component.askPause(withTeam);
    component.pauseReason = '  Incident CPU ';
    component.pauseIncident = '13497';
    component.submitPause();
    expect(service.pause).toHaveBeenCalledWith(5, { incidentId: 13497, reason: 'Incident CPU' });
  });

  it("affiche clairement le motif et l'incident lié dans le détail d'une maintenance suspendue", () => {
    service.get.and.returnValue(of(suspended));
    create([suspended], 'MAINTENANCE_VIEW');
    component.openDetail(suspended);
    fixture.detectChanges();
    const text = (el('.modal') as HTMLElement).textContent || '';
    expect(text).toContain('Maintenance suspendue');
    expect(text).toContain('Incident CPU');
    expect(text).toContain('#13497');
    expect(text).toContain('Bob Durand');
    expect(el('.alert-warning a')?.getAttribute('href')).toContain('/incidents/13497');
  });

  it("reprend après confirmation", () => {
    service.resume.and.returnValue(of({ ...withTeam, status: 'IN_PROGRESS' }));
    create([suspended], 'MAINTENANCE_VIEW', 'MAINTENANCE_UPDATE');
    component.askResume(suspended);
    expect(component.confirm?.label).toBe('Reprendre');
    component.runConfirm();
    expect(service.resume).toHaveBeenCalledWith(5);
  });

  it("propose le statut « Suspendue » dans les filtres", () => {
    create([], 'MAINTENANCE_VIEW');
    expect(component.statuses).toContain('SUSPENDED');
    expect(component.statusLabels['SUSPENDED']).toBe('Suspendue');
  });
});
