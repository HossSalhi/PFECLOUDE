import { ComponentFixture, TestBed } from '@angular/core/testing';
import { of, throwError } from 'rxjs';
import { SupervisionService } from '../../core/services/supervision.service';
import { GrafanaConfig } from '../../core/models/supervision.models';
import { GrafanaPanelComponent } from './grafana-panel.component';

const config: GrafanaConfig = {
  embedEnabled: true,
  dashboards: [
    { title: 'HELIOS - Alarmes', uid: 'helios-alarmes', embedUrl: 'http://graf.test:3000/d/helios-alarmes?orgId=1&kiosk' },
    { title: 'HELIOS - Incidents hôte', uid: 'helios-host-incidents', embedUrl: 'http://graf.test:3000/d/helios-host-incidents?orgId=1&kiosk' }
  ]
};

describe('GrafanaPanelComponent', () => {
  let api: jasmine.SpyObj<SupervisionService>;
  let fixture: ComponentFixture<GrafanaPanelComponent>;
  let component: GrafanaPanelComponent;

  function create(cfg: GrafanaConfig | 'erreur'): HTMLElement {
    api = jasmine.createSpyObj<SupervisionService>('SupervisionService', ['grafana']);
    api.grafana.and.returnValue(cfg === 'erreur' ? throwError(() => ({ status: 500 })) : of(cfg));
    TestBed.configureTestingModule({ imports: [GrafanaPanelComponent], providers: [{ provide: SupervisionService, useValue: api }] });
    fixture = TestBed.createComponent(GrafanaPanelComponent);
    component = fixture.componentInstance;
    fixture.detectChanges();
    return fixture.nativeElement as HTMLElement;
  }

  const text = () => (fixture.nativeElement as HTMLElement).textContent!.replace(/\s+/g, ' ');
  const frameSrc = () => (fixture.nativeElement as HTMLElement).querySelector('iframe')?.getAttribute('src') ?? null;

  it("intègre le premier dashboard avec la période et l'actualisation par défaut", () => {
    const el = create(config);

    expect(el.querySelectorAll('[role="tab"]').length).toBe(2);
    expect(el.querySelector('[role="tab"].active')!.textContent).toContain('Alarmes');
    expect(frameSrc()).toContain('/d/helios-alarmes');
    expect(frameSrc()).toContain('from=now-6h');
    expect(frameSrc()).toContain('refresh=30s');
    expect(frameSrc()).toContain('kiosk');
  });

  it("change de dashboard et de période", () => {
    create(config);
    component.choose(config.dashboards[1]);
    fixture.detectChanges();
    expect(frameSrc()).toContain('/d/helios-host-incidents');

    component.setRange('24h');
    fixture.detectChanges();
    expect(frameSrc()).toContain('from=now-24h');
  });

  it("désactive l'actualisation automatique à la demande", () => {
    create(config);
    component.toggleRefresh();
    fixture.detectChanges();

    expect(frameSrc()).not.toContain('refresh=30s');
  });

  it("propose d'ouvrir le dashboard dans Grafana, sans le mode kiosque", () => {
    const el = create(config);

    const link = Array.from(el.querySelectorAll('a')).find(a => a.textContent!.includes('Ouvrir dans Grafana'))!;
    expect(link.getAttribute('target')).toBe('_blank');
    expect(link.getAttribute('rel')).toContain('noopener');
    expect(link.getAttribute('href')).toContain('/d/helios-alarmes');
    expect(link.getAttribute('href')).not.toContain('kiosk');
  });

  it("n'intègre jamais une adresse qui n'est pas http(s)", () => {
    create({ embedEnabled: true, dashboards: [{ title: 'Piégé', uid: 'x', embedUrl: 'javascript:alert(1)' }] });

    expect(frameSrc()).toBeNull();
    expect(component.openUrl).toBeNull();
  });

  it("explique quand l'intégration n'est pas activée", () => {
    const el = create({ embedEnabled: false, dashboards: [] });

    expect(text()).toContain('ne sont pas encore intégrés');
    expect(el.querySelector('iframe')).toBeNull();
  });

  it("explique quand aucun dashboard n'est configuré", () => {
    const el = create({ embedEnabled: true, dashboards: [] });

    expect(text()).toContain('aucun dashboard');
    expect(el.querySelector('iframe')).toBeNull();
  });

  it("affiche un message quand le serveur ne répond pas", () => {
    create('erreur');

    expect(text()).toContain("Le serveur n'a pas pu répondre");
  });
});
