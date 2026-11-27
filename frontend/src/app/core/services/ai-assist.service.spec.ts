import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { environment } from '../../../environments/environment';
import { AiAssistService, aiErrorMessage } from './ai-assist.service';

describe('AiAssistService', () => {
  const base = `${environment.apiBaseUrl}/api/ai-assist`;

  describe('avec HttpClient', () => {
    let service: AiAssistService;
    let http: HttpTestingController;

    beforeEach(() => {
      TestBed.configureTestingModule({ providers: [provideHttpClient(), provideHttpClientTesting()] });
      service = TestBed.inject(AiAssistService);
      http = TestBed.inject(HttpTestingController);
    });

    afterEach(() => http.verify());

    it("n'émet aucune requête tant qu'on ne s'abonne pas (jamais d'appel automatique)", () => {
      service.capabilities();
      service.reportSummary(1);
      service.explainAnomalies(null, 7);
      http.expectNone(() => true);
    });

    it("met les capacités en cache et renvoie null si le serveur ne répond pas", () => {
      const seen: unknown[] = [];
      service.capabilities().subscribe(c => seen.push(c));
      service.capabilities().subscribe(c => seen.push(c));
      http.expectOne(`${base}/capabilities`).flush({ admin: true });
      expect(seen.length).toBe(2);

      service.reset();
      let failed: unknown = 'x';
      service.capabilities().subscribe(c => failed = c);
      http.expectOne(`${base}/capabilities`).flush('boom', { status: 500, statusText: 'KO' });
      expect(failed).toBeNull();
    });

    it("appelle les bons points d'entrée avec les bons paramètres", () => {
      service.reportSummary(5).subscribe();
      expect(http.expectOne(`${base}/reports/5/summary`).request.method).toBe('POST');

      service.teamSummary(3).subscribe();
      expect(http.expectOne(`${base}/teams/3/summary`).request.method).toBe('POST');

      service.anomalies(3, 14).subscribe();
      const detect = http.expectOne(r => r.url === `${base}/audit/anomalies`);
      expect(detect.request.method).toBe('GET');
      expect(detect.request.params.get('teamId')).toBe('3');
      expect(detect.request.params.get('days')).toBe('14');

      service.explainAnomalies(null, 7).subscribe();
      const explain = http.expectOne(r => r.url === `${base}/audit/anomalies/explain`);
      expect(explain.request.method).toBe('POST');
      expect(explain.request.params.has('teamId')).toBeFalse();

      service.recommendAssignment({ teamId: 1, title: 't' }).subscribe();
      expect(http.expectOne(`${base}/tasks/recommendation`).request.body).toEqual({ teamId: 1, title: 't' });

      service.assignmentFeedback(1, 2, true).subscribe();
      expect(http.expectOne(`${base}/tasks/recommendation/feedback`).request.body).toEqual({ teamId: 1, userId: 2, accepted: true });

      service.maintenanceWindows({ teamId: null, resourceType: 'VM', resourceName: 'x', durationMinutes: 60, horizonDays: 7 }).subscribe();
      expect(http.expectOne(`${base}/maintenance/window`).request.method).toBe('POST');
    });
  });

  it("traduit les erreurs HTTP en messages lisibles", () => {
    expect(aiErrorMessage({ status: 403 })).toContain('non autorisée');
    expect(aiErrorMessage({ status: 404 })).toContain('introuvable');
    expect(aiErrorMessage({ status: 400 })).toContain('invalide');
    expect(aiErrorMessage({ status: 500 })).toContain("n'a pas pu répondre");
    expect(aiErrorMessage(null)).toContain("n'a pas pu répondre");
  });
});
