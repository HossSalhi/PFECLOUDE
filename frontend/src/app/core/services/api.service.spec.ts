import { TestBed } from '@angular/core/testing';
import { HttpClientTestingModule, HttpTestingController } from '@angular/common/http/testing';
import { HTTP_INTERCEPTORS } from '@angular/common/http';
import { ApiService } from './api.service';
import { AuthInterceptor } from '../interceptors/auth.interceptor';
import { AuthService } from './auth.service';
import { environment } from '../../../environments/environment';

// Le service préfixe toutes les routes par environment.apiBaseUrl (config dev/prod volontaire).
// Les assertions ci-dessous vérifient donc l'URL COMPLÈTE réellement émise (base + /api/...).
const BASE = environment.apiBaseUrl;

describe('ApiService', () => {
  let service: ApiService;
  let httpMock: HttpTestingController;

  beforeEach(() => {
    TestBed.configureTestingModule({
      imports: [HttpClientTestingModule],
      providers: [
        ApiService,
        { provide: HTTP_INTERCEPTORS, useClass: AuthInterceptor, multi: true }
      ]
    });
    service = TestBed.inject(ApiService);
    httpMock = TestBed.inject(HttpTestingController);
    // L'intercepteur n'ajoute `Authorization` que si un token existe : on simule un utilisateur
    // authentifié pour vérifier réellement ce comportement (précondition manquante à l'origine).
    spyOn(TestBed.inject(AuthService), 'getAccessToken').and.returnValue('test-token');
  });

  afterEach(() => httpMock.verify());

  it('should be created', () => {
    expect(service).toBeTruthy();
  });

  it('getDashboardSummary should call correct endpoint', () => {
    const mockSummary = {
      totalIncidents: 5, openIncidents: 2, inProgressIncidents: 1,
      resolvedIncidents: 2, criticalIncidents: 1, pendingApprovals: 0,
      totalActions: 3, resolutionRate: 0.4, generatedAt: '2026-05-16T10:00:00Z'
    };

    service.getDashboardSummary().subscribe(data => {
      expect(data.totalIncidents).toBe(5);
      expect(data.openIncidents).toBe(2);
    });

    const req = httpMock.expectOne(`${BASE}/api/dashboard/summary`);
    expect(req.request.method).toBe('GET');
    expect(req.request.headers.has('Authorization')).toBeTrue();
    req.flush(mockSummary);
  });

  it('getIncidents should pass pagination params', () => {
    service.getIncidents(2, 10, 'OPEN').subscribe();

    const req = httpMock.expectOne(r => r.url === `${BASE}/api/incidents`);
    expect(req.request.params.get('page')).toBe('2');
    expect(req.request.params.get('size')).toBe('10');
    expect(req.request.params.get('status')).toBe('OPEN');
    req.flush({ content: [], totalElements: 0, totalPages: 0, number: 0, size: 10 });
  });

  it('getIncidents should not pass status param when ALL', () => {
    service.getIncidents(0, 20, 'ALL').subscribe();

    const req = httpMock.expectOne(r => r.url === `${BASE}/api/incidents`);
    expect(req.request.params.has('status')).toBeFalse();
    req.flush({ content: [], totalElements: 0, totalPages: 0, number: 0, size: 20 });
  });

  it('updateIncidentStatus should use PUT method', () => {
    service.updateIncidentStatus(42, 'RESOLVED').subscribe();

    const req = httpMock.expectOne(`${BASE}/api/incidents/42/status/RESOLVED`);
    expect(req.request.method).toBe('PUT');
    req.flush({ id: 42, status: 'RESOLVED' });
  });

  it('submitFeedback should POST with correct body', () => {
    service.submitFeedback(1, true, 'Correct decision').subscribe();

    const req = httpMock.expectOne(`${BASE}/api/feedback/incident/1`);
    expect(req.request.method).toBe('POST');
    expect(req.request.body.correct).toBeTrue();
    expect(req.request.body.comment).toBe('Correct decision');
    req.flush({ recorded: true });
  });

  it('handleError should return observable error on HTTP failure', (done) => {
    service.getIncident(999).subscribe({
      error: (err) => {
        expect(err).toBeTruthy();
        done();
      }
    });

    const req = httpMock.expectOne(`${BASE}/api/incidents/999`);
    req.flush('Not found', { status: 404, statusText: 'Not Found' });
  });
});
