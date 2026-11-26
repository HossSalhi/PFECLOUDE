import { TestBed } from '@angular/core/testing';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { environment } from '../../../environments/environment';
import { MaintenanceService } from './maintenance.service';
import { TeamService } from './team.service';
import { AuditTrailService } from './audit-trail.service';
import { ReportService } from './report.service';
import { MaintenanceRequest } from '../models/org.models';
import { provideHttpClient, withInterceptorsFromDi, withXhr } from '@angular/common/http';

const base = environment.apiBaseUrl;

describe('Services des modules « organisation » (URL, méthode, paramètres, corps)', () => {
  let http: HttpTestingController;

  beforeEach(() => {
    TestBed.configureTestingModule({ imports: [], providers: [provideHttpClient(withXhr(), withInterceptorsFromDi()), provideHttpClientTesting()] });
    http = TestBed.inject(HttpTestingController);
  });

  afterEach(() => http.verify());

  describe('MaintenanceService', () => {
    let service: MaintenanceService;
    beforeEach(() => service = TestBed.inject(MaintenanceService));

    it('liste avec les filtres renseignés seulement, et une pagination par défaut', () => {
      service.list({ status: 'PLANNED', resourceType: '', resource: 'k8s-master', responsibleId: null, q: '' }).subscribe();
      const req = http.expectOne(r => r.url === `${base}/api/maintenance`);
      expect(req.request.method).toBe('GET');
      expect(req.request.params.get('status')).toBe('PLANNED');
      expect(req.request.params.get('resource')).toBe('k8s-master');
      expect(req.request.params.has('resourceType')).toBeFalse();
      expect(req.request.params.has('responsibleId')).toBeFalse();
      expect(req.request.params.get('page')).toBe('0');
      expect(req.request.params.get('size')).toBe('25');
      req.flush({ content: [], totalElements: 0, totalPages: 0, number: 0, size: 25 });
    });

    it('lit le calendrier sur un intervalle', () => {
      service.calendar('2026-10-26T00:00:00', '2026-12-07T00:00:00').subscribe();
      const req = http.expectOne(r => r.url === `${base}/api/maintenance/calendar`);
      expect(req.request.params.get('from')).toBe('2026-10-26T00:00:00');
      expect(req.request.params.get('to')).toBe('2026-12-07T00:00:00');
      req.flush([]);
    });

    it('crée, modifie, démarre, termine, annule et supprime sur les bons points d\'accès', () => {
      const body: MaintenanceRequest = {
        title: 'Mise à jour', description: null, reason: null, resourceType: 'VM', resourceName: 'k8s-worker1',
        startAt: '2026-12-01T22:00:00', endAt: '2026-12-02T02:00:00', responsibleId: 4
      };
      service.create(body).subscribe();
      let req = http.expectOne(`${base}/api/maintenance`);
      expect(req.request.method).toBe('POST');
      expect(req.request.body).toEqual(body);
      req.flush({});

      service.update(7, body).subscribe();
      req = http.expectOne(`${base}/api/maintenance/7`);
      expect(req.request.method).toBe('PUT');
      req.flush({});

      service.start(7).subscribe();
      expect(http.expectOne(`${base}/api/maintenance/7/start`).request.method).toBe('POST');

      service.complete(7).subscribe();
      expect(http.expectOne(`${base}/api/maintenance/7/complete`).request.method).toBe('POST');

      service.cancel(7, 'Reporté').subscribe();
      req = http.expectOne(`${base}/api/maintenance/7/cancel`);
      expect(req.request.body).toEqual({ reason: 'Reporté' });

      service.remove(7).subscribe();
      expect(http.expectOne(`${base}/api/maintenance/7`).request.method).toBe('DELETE');
    });

    it('lit l\'historique, les ressources et les maintenances en cours', () => {
      service.history(3).subscribe();
      service.resources().subscribe();
      service.inProgress().subscribe();
      http.expectOne(`${base}/api/maintenance/3/history`);
      http.expectOne(`${base}/api/maintenance/resources`);
      http.expectOne(`${base}/api/maintenance/active`);
    });
  });

  describe('TeamService', () => {
    let service: TeamService;
    beforeEach(() => service = TestBed.inject(TeamService));

    it('liste les équipes avec ou sans les désactivées', () => {
      service.listTeams(true, 'réseau').subscribe();
      let req = http.expectOne(r => r.url === `${base}/api/teams`);
      expect(req.request.params.get('includeInactive')).toBe('true');
      expect(req.request.params.get('q')).toBe('réseau');
      req.flush([]);

      service.listTeams(false).subscribe();
      req = http.expectOne(r => r.url === `${base}/api/teams`);
      expect(req.request.params.has('includeInactive')).toBeFalse();   // le Backend considère « faux » par défaut
      req.flush([]);
    });

    it('gère les membres et le responsable', () => {
      service.addMember(2, 9).subscribe();
      let req = http.expectOne(`${base}/api/teams/2/members`);
      expect(req.request.method).toBe('POST');
      expect(req.request.body).toEqual({ userId: 9 });

      service.removeMember(2, 9).subscribe();
      expect(http.expectOne(`${base}/api/teams/2/members/9`).request.method).toBe('DELETE');

      service.changeLead(2, 5).subscribe();
      req = http.expectOne(`${base}/api/teams/2/lead`);
      expect(req.request.method).toBe('PUT');
      expect(req.request.body).toEqual({ userId: 5 });
    });

    it('active, désactive et supprime une équipe', () => {
      service.deactivate(2).subscribe();
      service.activate(2).subscribe();
      service.deleteTeam(2).subscribe();
      expect(http.expectOne(`${base}/api/teams/2/deactivate`).request.method).toBe('POST');
      expect(http.expectOne(`${base}/api/teams/2/activate`).request.method).toBe('POST');
      expect(http.expectOne(`${base}/api/teams/2`).request.method).toBe('DELETE');
    });

    it('filtre les tâches et modifie statut et affectation avec PATCH', () => {
      service.listTasks({ teamId: 3, status: 'TODO', overdue: true, mine: false, q: '' }).subscribe();
      const list = http.expectOne(r => r.url === `${base}/api/team-tasks`);
      expect(list.request.params.get('teamId')).toBe('3');
      expect(list.request.params.get('status')).toBe('TODO');
      expect(list.request.params.get('overdue')).toBe('true');
      expect(list.request.params.has('mine')).toBeFalse();
      expect(list.request.params.has('q')).toBeFalse();
      list.flush({ content: [], totalElements: 0, totalPages: 0, number: 0, size: 25 });

      service.changeTaskStatus(8, 'DONE').subscribe();
      let req = http.expectOne(`${base}/api/team-tasks/8/status`);
      expect(req.request.method).toBe('PATCH');
      expect(req.request.body).toEqual({ status: 'DONE' });

      service.assignTask(8, null).subscribe();
      req = http.expectOne(`${base}/api/team-tasks/8/assignee`);
      expect(req.request.method).toBe('PATCH');
      expect(req.request.body).toEqual({ assigneeId: null });
    });

    it("demande l'avancement global ou celui d'une équipe", () => {
      service.taskSummary(3, true).subscribe();
      const req = http.expectOne(r => r.url === `${base}/api/team-tasks/summary`);
      expect(req.request.params.get('teamId')).toBe('3');
      expect(req.request.params.get('mine')).toBe('true');
      service.teamSummary(3).subscribe();
      http.expectOne(`${base}/api/teams/3/summary`);
    });
  });

  describe('AuditTrailService', () => {
    let service: AuditTrailService;
    beforeEach(() => service = TestBed.inject(AuditTrailService));

    it('recherche avec filtres et pagination, sans paramètres vides', () => {
      service.search({ username: 'alice', module: 'USERS', action: '', page: 2, size: 20 }).subscribe();
      const req = http.expectOne(r => r.url === `${base}/api/audit-trail`);
      expect(req.request.params.get('username')).toBe('alice');
      expect(req.request.params.get('module')).toBe('USERS');
      expect(req.request.params.has('action')).toBeFalse();
      expect(req.request.params.get('page')).toBe('2');
      req.flush({ content: [], totalElements: 0, totalPages: 0, number: 2, size: 20 });
    });

    it("exporte en CSV avec les mêmes filtres et reçoit un fichier (blob), jamais d'écriture", () => {
      service.exportCsv({ module: 'AUTH', result: 'FAILURE', page: 5, size: 50 }).subscribe();
      const req = http.expectOne(r => r.url === `${base}/api/audit-trail/export`);
      expect(req.request.method).toBe('GET');
      expect(req.request.responseType).toBe('blob');
      expect(req.request.params.get('module')).toBe('AUTH');
      expect(req.request.params.get('result')).toBe('FAILURE');
      expect(req.request.params.has('page')).toBeFalse();               // l'export ne se limite pas à une page
      req.flush(new Blob(['x']));
    });

    it('lit le détail et les valeurs de filtre', () => {
      service.get(12).subscribe();
      service.meta().subscribe();
      expect(http.expectOne(`${base}/api/audit-trail/12`).request.method).toBe('GET');
      http.expectOne(`${base}/api/audit-trail/meta`);
    });
  });

  describe('ReportService', () => {
    let service: ReportService;
    beforeEach(() => service = TestBed.inject(ReportService));

    it('génère un rapport sur une période, avec comparaison optionnelle', () => {
      service.generate({ type: 'MAINTENANCE', from: '2026-11-01T00:00:00', to: '2026-12-01T00:00:00', compare: true }).subscribe();
      const req = http.expectOne(`${base}/api/reports/generate`);
      expect(req.request.method).toBe('POST');
      expect(req.request.body).toEqual({ type: 'MAINTENANCE', from: '2026-11-01T00:00:00', to: '2026-12-01T00:00:00', compare: true });
    });

    it('liste les rapports, filtrés par type', () => {
      service.list('PERFORMANCE', 1, 10).subscribe();
      const req = http.expectOne(r => r.url === `${base}/api/reports`);
      expect(req.request.params.get('type')).toBe('PERFORMANCE');
      expect(req.request.params.get('page')).toBe('1');
      req.flush({ content: [], totalElements: 0, totalPages: 0, number: 1, size: 10 });
      service.list('').subscribe();
      const all = http.expectOne(r => r.url === `${base}/api/reports`);
      expect(all.request.params.has('type')).toBeFalse();
      all.flush({ content: [], totalElements: 0, totalPages: 0, number: 0, size: 20 });
    });

    it('exporte en PDF ou en CSV (fichier reçu en blob)', () => {
      service.export(5, 'pdf').subscribe();
      let req = http.expectOne(r => r.url === `${base}/api/reports/5/export`);
      expect(req.request.params.get('format')).toBe('pdf');
      expect(req.request.responseType).toBe('blob');
      req.flush(new Blob(['%PDF']));

      service.export(5, 'csv').subscribe();
      req = http.expectOne(r => r.url === `${base}/api/reports/5/export`);
      expect(req.request.params.get('format')).toBe('csv');
      req.flush(new Blob(['a;b']));
    });

    it('gère les plannings : création activée, bascule, suppression', () => {
      service.createSchedule('USER_ACTIVITY', 'WEEKLY').subscribe();
      let req = http.expectOne(`${base}/api/reports/schedules`);
      expect(req.request.method).toBe('POST');
      expect(req.request.body).toEqual({ type: 'USER_ACTIVITY', frequency: 'WEEKLY', enabled: true });

      service.toggleSchedule(4, false).subscribe();
      req = http.expectOne(`${base}/api/reports/schedules/4`);
      expect(req.request.method).toBe('PATCH');
      expect(req.request.body).toEqual({ enabled: false });

      service.deleteSchedule(4).subscribe();
      expect(http.expectOne(`${base}/api/reports/schedules/4`).request.method).toBe('DELETE');
    });

    it('calcule les statistiques de la plateforme à la demande', () => {
      service.statistics('2026-11-01T00:00:00', '2026-12-01T00:00:00', true).subscribe();
      const req = http.expectOne(r => r.url === `${base}/api/reports/statistics`);
      expect(req.request.params.get('compare')).toBe('true');
    });
  });
});
