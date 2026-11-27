import { ComponentFixture, TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { of, throwError } from 'rxjs';
import { ApiService } from '../../core/services/api.service';
import { AuthService } from '../../core/services/auth.service';
import { SupervisionService } from '../../core/services/supervision.service';
import { SupervisionComponent } from './supervision.component';

const pts = [{ t: 1795745000000, v: 10 }, { t: 1795745060000, v: 20 }];

const hosts = {
  available: true, error: null, generatedAt: '2026-11-27T02:00:00',
  hosts: [
    { instance: '192.168.148.153', name: 'k8s-master', up: true, cpuPercent: 16.5, memoryPercent: 75, diskPercent: 93 },
    { instance: '192.168.148.155', name: 'k8s-worker2', up: false, cpuPercent: null, memoryPercent: null, diskPercent: null }
  ],
  targets: [{ job: 'etcd', up: 5, total: 5 }, { job: 'kubelet', up: 2, total: 3 }]
};
const camundaOverview = {
  available: true, error: null, generatedAt: '', healthy: true, version: '8.9.0', clusterSize: 1,
  active: 142, completed: 6942, terminated: 0, incidents: 141, definitions: [{ key: '55', id: 'full-cycle', name: 'Cycle complet', version: 22 }]
};

describe('SupervisionComponent', () => {
  let api: jasmine.SpyObj<SupervisionService>;
  let fixture: ComponentFixture<SupervisionComponent>;
  let component: SupervisionComponent;

  function create(role = 'USER'): HTMLElement {
    api = jasmine.createSpyObj<SupervisionService>('SupervisionService',
      ['hosts', 'series', 'logStats', 'topErrors', 'meanings', 'correlation', 'camundaOverview', 'camundaInstances', 'camundaIncidents', 'camundaSteps', 'grafana']);
    api.hosts.and.returnValue(of(hosts));
    api.camundaOverview.and.returnValue(of(camundaOverview));
    api.correlation.and.returnValue(of({ available: true, error: null, minutes: 60, stepSeconds: 30, errorSeries: pts, cpuSeries: pts,
      incidents: [{ id: 7, type: 'NODE_HIGH_CPU', resource: 'worker1', severity: 'HIGH', status: 'OPEN', createdAt: '2026-11-27T02:00:00', errorLogsAround: 14 }] }));
    api.series.and.returnValue(of({ available: true, error: null, metric: 'CPU', unit: '%', minutes: 60, stepSeconds: 30,
      lines: [{ instance: 'a', name: 'k8s-master', points: pts }, { instance: 'b', name: 'k8s-worker2', points: pts }] }));
    api.logStats.and.returnValue(of({ available: true, error: null, minutes: 60, generatedAt: '', total: 1000, errors: 40, warnings: 10,
      byHost: [{ key: 'worker1', total: 100, errors: 7, warnings: 3 }], byApplication: [{ key: 'pfe/backend', total: 50, errors: 5, warnings: 2 }] }));
    api.topErrors.and.returnValue(of({ available: true, error: null, minutes: 60, patterns: [
      { pattern: 'connection refused <ip>', count: 12, host: 'worker1', category: 'RESEAU', label: 'Connexion refusée', meaning: 'Le service visé n’écoute pas.', advice: 'Vérifier le service.', example: 'connection refused 10.0.0.1' }] }));
    api.meanings.and.returnValue(of([{ category: 'RESEAU', label: 'Connexion refusée', meaning: 'm', advice: 'a' }]));
    api.camundaInstances.and.returnValue(of({ available: true, error: null, total: 7084, items: [
      { key: '1', definitionKey: '55', definitionId: 'full-cycle', definitionName: 'Cycle complet', version: 22, state: 'COMPLETED', hasIncident: false,
        startedAt: '2026-11-27T02:15:00', endedAt: '2026-11-27T02:15:02', durationMs: 1226 }] }));
    api.camundaIncidents.and.returnValue(of({ available: true, error: null, total: 141, items: [
      { key: '9', definitionId: 'incident-remediation', instanceKey: '7', errorType: 'JOB_NO_RETRIES', errorMessage: 'orphan', elementId: 'Task_Exec', createdAt: '2026-10-15T10:23:39' }] }));
    api.camundaSteps.and.returnValue(of({ available: true, error: null, definitionKey: '55', steps: [
      { id: 'S', type: 'startEvent', kind: 'Début', name: 'Incident détecté' }, { id: 'T', type: 'serviceTask', kind: 'Tâche automatique', name: 'Analyser' }] }));
    api.grafana.and.returnValue(of({ embedEnabled: false, dashboards: [] }));

    TestBed.configureTestingModule({
      imports: [SupervisionComponent],
      providers: [
        provideRouter([]),
        { provide: SupervisionService, useValue: api },
        { provide: ApiService, useValue: { getMonitoringLinks: () => of({ prometheus: 'http://p', grafana: 'http://g', loki: 'http://l', camunda: 'http://c' }) } },
        { provide: AuthService, useValue: { isAtLeastAdmin: () => role !== 'USER' } }
      ]
    });
    fixture = TestBed.createComponent(SupervisionComponent);
    component = fixture.componentInstance;
    fixture.detectChanges();
    return fixture.nativeElement as HTMLElement;
  }

  const text = () => (fixture.nativeElement as HTMLElement).textContent!.replace(/\s+/g, ' ');
  const click = (label: string) => {
    const b = Array.from((fixture.nativeElement as HTMLElement).querySelectorAll('button')).find(x => x.textContent!.includes(label))!;
    b.click();
    fixture.detectChanges();
  };

  it("ouvre la vue d'ensemble : machines, cibles, Camunda et incidents rapprochés des logs", () => {
    const el = create();

    expect(el.querySelectorAll('.sup-card').length).toBe(2);
    expect(text()).toContain('k8s-master');
    expect(el.querySelector('.sup-card.is-down')).not.toBeNull();
    expect(el.querySelectorAll('.sup-chip').length).toBe(2);
    expect(el.querySelector('.sup-chip.is-bad')!.textContent).toContain('kubelet');
    expect(text()).toContain('Instances actives');
    expect(text()).toContain('#7');
    expect(text()).toContain('14');
    expect(el.querySelector('canvas')).not.toBeNull();
  });

  it("colore les jauges selon le niveau : vert, orange, rouge", () => {
    create();
    expect(component.level(40)).toBe('ok');
    expect(component.level(75)).toBe('warn');
    expect(component.level(93)).toBe('crit');
    expect(component.level(null)).toBe('na');
  });

  it("ne charge que l'onglet ouvert : les logs, lents, ne sont demandés qu'à l'ouverture de l'onglet Logs", () => {
    create();
    expect(api.logStats).not.toHaveBeenCalled();
    expect(api.series).not.toHaveBeenCalled();

    click('Logs');

    expect(api.logStats).toHaveBeenCalledWith(60);
    expect(api.topErrors).toHaveBeenCalledWith(60, 10);
  });

  it("l'onglet Logs montre les totaux, la signification des erreurs et les tableaux par machine et application", () => {
    create();
    click('Logs');

    expect(text()).toContain('Connexion refusée');
    expect(text()).toContain('Le service visé');
    expect(text()).toContain('Vérifier le service.');
    expect(text()).toContain('worker1');
    expect(text()).toContain('pfe/backend');
    expect(component.errorRate({ total: 200, errors: 10 })).toBe(5);
  });

  it("change la période des logs et recharge", () => {
    create();
    click('Logs');
    api.logStats.calls.reset();

    component.setLogMinutes(15);

    expect(api.logStats).toHaveBeenCalledWith(15);
  });

  it("l'onglet Métriques trace une courbe par machine et change de métrique", () => {
    create();
    click('Métriques');

    expect(api.series).toHaveBeenCalledWith('CPU', 60);
    expect(component.metricChart!.datasets.length).toBe(2);
    expect(component.metricChart!.datasets[0].label).toBe('k8s-master');
    component.setMetric('DISK');
    expect(api.series).toHaveBeenCalledWith('DISK', 60);
  });

  it("l'onglet Camunda liste les processus, les instances, les incidents et les étapes d'un processus", () => {
    create();
    click('Camunda');

    expect(text()).toContain('Cycle complet');
    expect(text()).toContain('1 s');          // durée 1226 ms
    expect(text()).toContain('JOB_NO_RETRIES');
    click('Voir les étapes');
    expect(api.camundaSteps).toHaveBeenCalledWith('55');
    expect(text()).toContain('Incident détecté');
    expect(text()).toContain('Tâche automatique');
    click('Masquer les étapes');
    expect(text()).not.toContain('Incident détecté');
  });

  it("filtre les instances Camunda par état", () => {
    create();
    click('Camunda');
    component.setInstanceState('ACTIVE');

    expect(api.camundaInstances).toHaveBeenCalledWith('ACTIVE', 15);
  });

  it("affiche un message quand un outil est indisponible, sans casser le reste de la page", () => {
    api = undefined as any;
    create();
    api.logStats.and.returnValue(of({ available: false, error: 'Loki est injoignable', minutes: 60, generatedAt: '', total: 0, errors: 0, warnings: 0, byHost: [], byApplication: [] }));
    click('Logs');

    expect(text()).toContain('Loki indisponible');
    expect(text()).toContain('Loki est injoignable');
  });

  it("affiche un message quand le serveur ne répond pas", () => {
    create();
    api.series.and.returnValue(throwError(() => ({ status: 500 })));
    click('Métriques');

    expect(text()).toContain("Le serveur n'a pas pu répondre");
  });

  it("Grafana non intégré : explication claire, aucune iframe", () => {
    const el = create();
    click('Grafana');

    expect(text()).toContain('ne sont pas encore intégrés');
    expect(el.querySelector('iframe')).toBeNull();
  });

  it("Grafana intégré : une iframe par dashboard choisi, adresse http(s) seulement", () => {
    const el = create();
    api.grafana.and.returnValue(of({ embedEnabled: true, dashboards: [
      { title: 'Cluster', uid: 'k8s', embedUrl: 'http://graf.test:3000/d/k8s?orgId=1&kiosk' },
      { title: 'Piégé', uid: 'bad', embedUrl: 'javascript:alert(1)' }] }));
    click('Grafana');

    expect(el.querySelector('iframe')).not.toBeNull();
    expect(el.querySelectorAll('.sup-period').length).toBe(2);
    click('Piégé');
    expect(el.querySelector('iframe')).toBeNull();
  });

  it("les boutons directs vers les outils ne sont proposés qu'aux administrateurs", () => {
    expect(create('USER').querySelector('app-monitoring-links')).toBeNull();
    TestBed.resetTestingModule();
    expect(create('ADMIN').querySelector('app-monitoring-links')).not.toBeNull();
  });

  it("formate les durées", () => {
    create();
    expect(component.duration(null)).toBe('—');
    expect(component.duration(850)).toBe('850 ms');
    expect(component.duration(42_000)).toBe('42 s');
    expect(component.duration(125_000)).toBe('2 min 5 s');
    expect(component.periodLabel(15)).toBe('15 min');
    expect(component.periodLabel(180)).toBe('3 h');
  });
});
