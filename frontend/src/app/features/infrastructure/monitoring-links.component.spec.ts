import { TestBed } from '@angular/core/testing';
import { of, throwError } from 'rxjs';
import { ApiService } from '../../core/services/api.service';
import { AuthService } from '../../core/services/auth.service';
import { InfrastructureComponent } from './infrastructure.component';
import { MonitoringLinksComponent, safeUrl } from './monitoring-links.component';

const LINKS = { prometheus: 'http://prom.test:9090', grafana: 'http://graf.test:3000', loki: 'http://loki.test:3100', camunda: 'http://camunda.test:8080' };

describe('MonitoringLinksComponent', () => {
  function render(api: Partial<ApiService>) {
    TestBed.configureTestingModule({ imports: [MonitoringLinksComponent], providers: [{ provide: ApiService, useValue: api }] });
    const f = TestBed.createComponent(MonitoringLinksComponent);
    f.detectChanges();
    return f.nativeElement as HTMLElement;
  }

  it("affiche un bouton par outil, ouvert dans un nouvel onglet, sans iframe", () => {
    const el = render({ getMonitoringLinks: () => of(LINKS) });
    const anchors = Array.from(el.querySelectorAll('a')) as HTMLAnchorElement[];

    expect(anchors.map(a => a.textContent!.trim().split(/\s+/)[0])).toEqual(['Prometheus', 'Grafana', 'Loki', 'Camunda']);
    expect(anchors.map(a => a.getAttribute('href'))).toEqual(Object.values(LINKS));
    anchors.forEach(a => {
      expect(a.target).toBe('_blank');
      expect(a.rel).toContain('noopener');
    });
    expect(el.querySelector('iframe')).toBeNull();
  });

  it("n'affiche pas un lien dont l'adresse n'est pas http(s)", () => {
    const el = render({ getMonitoringLinks: () => of({ ...LINKS, loki: 'javascript:alert(1)', camunda: '' }) });

    expect(Array.from(el.querySelectorAll('a')).map(a => a.getAttribute('href'))).toEqual([LINKS.prometheus, LINKS.grafana]);
  });

  it('affiche un message quand les liens ne sont pas disponibles (refus ou panne)', () => {
    const el = render({ getMonitoringLinks: () => throwError(() => ({ status: 403 })) });

    expect(el.querySelectorAll('a').length).toBe(0);
    expect(el.textContent).toContain('ne sont pas disponibles');
  });

  it('safeUrl ne garde que http et https', () => {
    expect(safeUrl('https://grafana.test')).toBe('https://grafana.test');
    expect(safeUrl(' http://x ')).toBe('http://x');
    expect(safeUrl('javascript:alert(1)')).toBeNull();
    expect(safeUrl('data:text/html,x')).toBeNull();
    expect(safeUrl(null)).toBeNull();
  });
});

describe('InfrastructureComponent — section Supervision', () => {
  function page(role: string | null) {
    const api = jasmine.createSpyObj<ApiService>('ApiService', ['getInfrastructureHealth', 'getMonitoringLinks']);
    api.getInfrastructureHealth.and.returnValue(of({ total: 0, up: 0, down: 0, components: [], vmPolicy: [] }));
    api.getMonitoringLinks.and.returnValue(of(LINKS));
    const providers: any[] = [{ provide: ApiService, useValue: api }];
    if (role) {
      providers.push({ provide: AuthService, useValue: { isAdmin: () => role === 'ADMIN', isSuperAdmin: () => role === 'SUPER_ADMIN' } });
    }
    TestBed.configureTestingModule({ imports: [InfrastructureComponent], providers });
    const f = TestBed.createComponent(InfrastructureComponent);
    f.detectChanges();
    return { el: f.nativeElement as HTMLElement, api };
  }

  it('est visible pour un ADMIN', () => {
    const { el, api } = page('ADMIN');

    expect(el.querySelector('app-monitoring-links')).not.toBeNull();
    expect(api.getMonitoringLinks).toHaveBeenCalledTimes(1);
  });

  it('est visible pour un SUPER_ADMIN', () => {
    expect(page('SUPER_ADMIN').el.querySelector('app-monitoring-links')).not.toBeNull();
  });

  it("n'est ni affichée ni demandée à l'API pour un utilisateur ordinaire", () => {
    const { el, api } = page('USER');

    expect(el.querySelector('app-monitoring-links')).toBeNull();
    expect(api.getMonitoringLinks).not.toHaveBeenCalled();
  });
});
