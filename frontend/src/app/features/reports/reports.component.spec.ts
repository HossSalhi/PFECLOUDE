import { ComponentFixture, TestBed } from '@angular/core/testing';
import { NO_ERRORS_SCHEMA } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { of, throwError } from 'rxjs';
import { ReportService } from '../../core/services/report.service';
import { OrgPermissionsService } from '../../core/services/org-permissions.service';
import { Page, ReportItem, ReportTypeInfo } from '../../core/models/org.models';
import { ReportsComponent } from './reports.component';

class FakePerms {
  granted = new Set<string>();
  can(p: string): boolean { return this.granted.has(p); }
  canAny(...p: string[]): boolean { return p.some(x => this.granted.has(x)); }
  ensureLoaded() { return of(true); }
}

describe('ReportsComponent', () => {
  let fixture: ComponentFixture<ReportsComponent>;
  let component: ReportsComponent;
  let service: jasmine.SpyObj<ReportService>;
  let perms: FakePerms;

  const types: ReportTypeInfo[] = [
    { type: 'USER_ACTIVITY', label: 'Activité des utilisateurs', description: 'Connexions et actions' },
    { type: 'MAINTENANCE', label: 'Opérations de maintenance', description: 'Maintenances de la période' }
  ];

  const item = (over: Partial<ReportItem> = {}): ReportItem => ({
    id: 3, type: 'USER_ACTIVITY', typeLabel: 'Activité des utilisateurs',
    title: 'Activité des utilisateurs — du 01/11/2026 au 30/11/2026',
    periodStart: '2026-11-01T00:00:00', periodEnd: '2026-12-01T00:00:00', comparePrevious: true,
    trigger: 'MANUAL', status: 'COMPLETED', summary: 'Actions : 120', generatedBy: 'admin',
    generatedAt: '2026-12-01T08:00:00', ...over
  });

  const pageOf = (items: ReportItem[]): Page<ReportItem> =>
    ({ content: items, totalElements: items.length, totalPages: 1, number: 0, size: 10 });

  beforeEach(() => {
    service = jasmine.createSpyObj<ReportService>('ReportService',
      ['types', 'list', 'get', 'generate', 'statistics', 'export', 'schedules', 'createSchedule', 'toggleSchedule', 'deleteSchedule']);
    perms = new FakePerms();
    service.types.and.returnValue(of(types));
    service.list.and.returnValue(of(pageOf([item()])));
    service.schedules.and.returnValue(of([]));
    TestBed.configureTestingModule({
    imports: [FormsModule, ReportsComponent],
    schemas: [NO_ERRORS_SCHEMA], // <app-report-view> n'est pas l'objet de ces tests
    providers: [
        { provide: ReportService, useValue: service },
        { provide: OrgPermissionsService, useValue: perms }
    ]
});
  });

  function create(...granted: string[]): void {
    granted.forEach(g => perms.granted.add(g));
    fixture = TestBed.createComponent(ReportsComponent);
    component = fixture.componentInstance;
    fixture.detectChanges();
  }

  const el = (selector: string): HTMLElement | null => fixture.nativeElement.querySelector(selector);

  it('charge les types et la liste des rapports déjà générés', () => {
    create('REPORTS_VIEW');
    expect(service.types).toHaveBeenCalled();
    expect(service.list).toHaveBeenCalledWith('', 0, 10);
    expect(component.types.length).toBe(2);
    expect(fixture.nativeElement.textContent).toContain('Activité des utilisateurs — du 01/11/2026 au 30/11/2026');
  });

  it("sans droit de génération ni d'export, l'écran ne propose que la consultation", () => {
    create('REPORTS_VIEW');
    expect(el('.header-actions .btn-primary')).toBeNull();
    expect(el('.btn-icon.btn-edit')).toBeNull();                 // PDF
    expect(el('.btn-icon.btn-ok')).toBeNull();                   // CSV
    expect(el('.btn-icon.btn-view')).not.toBeNull();
  });

  it('avec les droits, propose génération et exports', () => {
    create('REPORTS_VIEW', 'REPORTS_GENERATE', 'REPORTS_EXPORT');
    expect(el('.header-actions .btn-primary')).not.toBeNull();
    expect(el('.btn-icon.btn-edit[title="Exporter en PDF"]')).not.toBeNull();
    expect(el('.btn-icon.btn-ok[title="Exporter en CSV"]')).not.toBeNull();
  });

  it("n'offre pas l'export d'un rapport en échec", () => {
    service.list.and.returnValue(of(pageOf([item({ status: 'FAILED' })])));
    create('REPORTS_VIEW', 'REPORTS_EXPORT');
    expect(el('.btn-icon.btn-edit')).toBeNull();
  });

  it("refuse une période invalide sans appeler le Backend", () => {
    create('REPORTS_VIEW', 'REPORTS_GENERATE');
    component.openGenerate();
    component.genFrom = '2026-11-30';
    component.genTo = '2026-11-01';
    component.generate();
    expect(component.modalError).toContain('postérieure');
    component.genFrom = '';
    component.generate();
    expect(component.modalError).toContain('obligatoires');
    component.genFrom = '2020-01-01';
    component.genTo = '2026-11-01';
    component.generate();
    expect(component.modalError).toContain('366');
    expect(service.generate).not.toHaveBeenCalled();
  });

  it('génère sur [début, lendemain de la fin[ avec comparaison, recharge puis ouvre le rapport', () => {
    const generated = item({ id: 9 });
    service.generate.and.returnValue(of(generated));
    service.get.and.returnValue(of({
      report: generated, errorMessage: null,
      content: JSON.stringify({ type: 'USER_ACTIVITY', typeLabel: 'x', title: 'x', periodFrom: '2026-11-01T00:00', periodTo: '2026-12-01T00:00',
        generatedAt: '2026-12-01T08:00', generatedBy: 'admin', summary: [], sections: [], charts: [] })
    }));
    create('REPORTS_VIEW', 'REPORTS_GENERATE');
    component.openGenerate();
    component.genType = 'MAINTENANCE';
    component.genFrom = '2026-11-01';
    component.genTo = '2026-11-30';
    component.genCompare = true;
    const listCalls = service.list.calls.count();
    component.generate();
    expect(service.generate).toHaveBeenCalledWith({
      type: 'MAINTENANCE', from: '2026-11-01T00:00:00', to: '2026-12-01T00:00:00', compare: true
    });
    expect(component.showGenerate).toBeFalse();
    expect(component.generating).toBeFalse();
    expect(service.list.calls.count()).toBe(listCalls + 1);
    expect(service.get).toHaveBeenCalledWith(9);
    expect(component.viewContent).not.toBeNull();
  });

  it('affiche le message du Backend quand la génération échoue', () => {
    service.generate.and.returnValue(throwError(() => ({ status: 500, error: { error: 'La génération du rapport a échoué : base indisponible' } })));
    create('REPORTS_VIEW', 'REPORTS_GENERATE');
    component.openGenerate();
    component.genFrom = '2026-11-01';
    component.genTo = '2026-11-30';
    component.generate();
    expect(component.modalError).toContain('base indisponible');
    expect(component.showGenerate).toBeTrue();
    expect(component.generating).toBeFalse();
  });

  it('lit le contenu JSON d\'un rapport et tolère un contenu absent ou illisible', () => {
    create('REPORTS_VIEW');
    service.get.and.returnValue(of({ report: item(), errorMessage: null, content: '{pas du json' }));
    component.open(item());
    expect(component.viewContent).toBeNull();
    service.get.and.returnValue(of({ report: item({ status: 'FAILED' }), errorMessage: 'Échec', content: null }));
    component.open(item({ status: 'FAILED' }));
    expect(component.viewContent).toBeNull();
    expect(component.viewing!.errorMessage).toBe('Échec');
  });

  it("affiche l'erreur d'un export refusé", () => {
    service.export.and.returnValue(throwError(() => ({ status: 403 })));
    create('REPORTS_VIEW', 'REPORTS_EXPORT');
    component.exportReport(item(), 'pdf');
    expect(service.export).toHaveBeenCalledWith(3, 'pdf');
    expect(component.error).toContain('non autorisée');
    expect(component.exportingId).toBeNull();
  });

  it('calcule les statistiques sur la période choisie, après validation', () => {
    service.statistics.and.returnValue(of({ type: 'PLATFORM_ACTIVITY' } as any));
    create('REPORTS_VIEW');
    component.statsFrom = '2026-11-30';
    component.statsTo = '2026-11-01';
    component.computeStats();
    expect(service.statistics).not.toHaveBeenCalled();
    expect(component.statsError).toContain('postérieure');
    component.statsFrom = '2026-11-01';
    component.statsTo = '2026-11-30';
    component.statsCompare = false;
    component.computeStats();
    expect(service.statistics).toHaveBeenCalledWith('2026-11-01T00:00:00', '2026-12-01T00:00:00', false);
    expect(component.statsContent).not.toBeNull();
  });

  it('gère les plannings : création, bascule, suppression confirmée', () => {
    service.createSchedule.and.returnValue(of({} as any));
    service.toggleSchedule.and.returnValue(of({} as any));
    service.deleteSchedule.and.returnValue(of(undefined));
    create('REPORTS_VIEW', 'REPORTS_SCHEDULE');
    component.newScheduleType = 'MAINTENANCE';
    component.newScheduleFrequency = 'MONTHLY';
    component.addSchedule();
    expect(service.createSchedule).toHaveBeenCalledWith('MAINTENANCE', 'MONTHLY');

    const schedule = { id: 4, type: 'MAINTENANCE', typeLabel: 'Opérations de maintenance', frequency: 'MONTHLY', enabled: true,
      createdBy: 'admin', createdAt: 'x', lastGeneratedAt: null } as any;
    component.toggleSchedule(schedule);
    expect(service.toggleSchedule).toHaveBeenCalledWith(4, false);

    spyOn(window, 'confirm').and.returnValue(false);
    component.removeSchedule(schedule);
    expect(service.deleteSchedule).not.toHaveBeenCalled();
    (window.confirm as jasmine.Spy).and.returnValue(true);
    component.removeSchedule(schedule);
    expect(service.deleteSchedule).toHaveBeenCalledWith(4);
  });

  it('applique les raccourcis de période', () => {
    create('REPORTS_VIEW', 'REPORTS_GENERATE');
    component.applyPreset('gen', 'last7');
    expect(component.genTo >= component.genFrom).toBeTrue();
    const from = new Date(component.genFrom + 'T00:00:00').getTime();
    const to = new Date(component.genTo + 'T00:00:00').getTime();
    expect(Math.round((to - from) / 86400000)).toBe(6);
  });
});
