import { ComponentFixture, TestBed } from '@angular/core/testing';
import { ActivatedRoute, provideRouter } from '@angular/router';
import { of, throwError } from 'rxjs';
import { SelfHealingService } from '../../core/services/self-healing.service';
import { IncidentAlerts } from '../../core/models/self-healing.models';
import { SelfHealingTraceComponent } from './self-healing-trace.component';

const trace = {
  incident: { id: 7, title: 'Crash backend', type: 'POD_CRASH_LOOP', namespace: 'pfe', app: 'backend', severity: 'HIGH', status: 'RESOLVED', createdAt: '2026-11-26T10:00:00' },
  timeline: [], actions: [], verifications: [], evidence: []
};

const alerts: IncidentAlerts = {
  from: '2026-11-26T09:45:00', to: '2026-11-26T10:30:00', truncated: false,
  prometheus: { tool: 'Prometheus', status: 'OK', message: null, alerts: [
    { name: 'HeliosCrashLoop', severity: 'high', summary: 'Pod en boucle', labels: { pod: 'backend-1' }, firstFiring: '2026-11-26T09:58:00',
      lastFiring: '2026-11-26T10:05:00', activeNow: true, ruleState: 'inactive', ruleIncidentType: 'POD_CRASH_LOOP', typeMatches: true }] },
  alertmanager: { tool: 'Alertmanager', status: 'NOT_CONFIGURED', message: "Alertmanager n'est pas configuré", alerts: [] },
  grafana: { tool: 'Grafana', status: 'UNAVAILABLE', message: 'jeton refusé', alerts: [] },
  loki: { status: 'NOT_CONFIGURED', message: 'Aucune règle d\'alerte Loki n\'est configurée', rules: [] }
};

const signals = {
  available: true, reason: null, source: 'pods pfe/backend', from: '2026-11-26T09:55:00', to: '2026-11-26T10:35:00', truncated: false, linesRead: 120,
  signals: [{ category: 'RESEAU', label: 'Connexion refusée', meaning: "Le service visé n'écoute pas.", advice: 'Vérifier le service.', count: 12,
    first: '2026-11-26T10:00:00', last: '2026-11-26T10:04:00', example: 'connection refused to 10.0.0.1' }]
};

const camunda = {
  available: true, error: null,
  runs: [{ workflowId: 3, workflowName: 'incident-remediation', status: 'COMPLETED', processInstanceKey: '2251799826972037', definitionId: 'incident-remediation',
    state: 'COMPLETED', start: '2026-11-26T10:00:00', end: '2026-11-26T10:00:09', hasIncident: true, note: null,
    steps: [{ name: 'Début', type: 'START_EVENT', state: 'COMPLETED', start: null, end: null, durationMs: 0, incident: false },
      { name: 'Exécuter la remédiation', type: 'SERVICE_TASK', state: 'COMPLETED', start: null, end: null, durationMs: 5000, incident: true }],
    incidents: [{ errorType: 'JOB_NO_RETRIES', message: 'plus de tentatives', element: 'Task_Exec', createdAt: null }],
    logs: [{ at: '2026-11-26T10:00:01', message: 'Étape exécutée' }] }]
};

describe('SelfHealingTraceComponent — alertes, signaux et Camunda', () => {
  let api: jasmine.SpyObj<SelfHealingService>;
  let fixture: ComponentFixture<SelfHealingTraceComponent>;

  function open(): HTMLElement {
    api = jasmine.createSpyObj<SelfHealingService>('SelfHealingService', ['list', 'trace', 'logs', 'alerts', 'logSignals', 'camunda']);
    api.list.and.returnValue(of({ from: '', to: '', truncated: false, items: [] }));
    api.trace.and.returnValue(of(trace as any));
    api.alerts.and.returnValue(of(alerts));
    api.logSignals.and.returnValue(of(signals));
    api.camunda.and.returnValue(of(camunda));
    TestBed.configureTestingModule({
      imports: [SelfHealingTraceComponent],
      providers: [provideRouter([]), { provide: SelfHealingService, useValue: api },
        { provide: ActivatedRoute, useValue: { snapshot: { queryParamMap: { get: () => '7' } } } }]
    });
    fixture = TestBed.createComponent(SelfHealingTraceComponent);
    fixture.detectChanges();
    return fixture.nativeElement as HTMLElement;
  }

  const text = () => (fixture.nativeElement as HTMLElement).textContent!.replace(/\s+/g, ' ');
  const click = (label: string) => {
    Array.from((fixture.nativeElement as HTMLElement).querySelectorAll('button')).find(b => b.textContent!.includes(label))!.click();
    fixture.detectChanges();
  };

  it("ne charge aucune des trois sources à l'ouverture de la trace", () => {
    open();

    expect(api.trace).toHaveBeenCalledWith(7);
    expect(api.alerts).not.toHaveBeenCalled();
    expect(api.logSignals).not.toHaveBeenCalled();
    expect(api.camunda).not.toHaveBeenCalled();
    expect(text()).toContain('Charger les alertes');
    expect(text()).toContain('Charger les signaux de logs');
    expect(text()).toContain('Charger la trace Camunda');
  });

  it("charge les alertes à la demande et distingue disponible, non configuré et indisponible", () => {
    const el = open();
    click('Charger les alertes');

    expect(api.alerts).toHaveBeenCalledOnceWith(7);
    expect(text()).toContain('HeliosCrashLoop');
    expect(text()).toContain('active maintenant');
    expect(text()).toContain("type d'incident concordant");
    expect(text()).toContain('Disponible');
    expect(text()).toContain('Non configuré');
    expect(text()).toContain('Indisponible');
    expect(text()).toContain('jeton refusé');
    expect(text()).toContain("Règles d'alerte Loki");
    expect(el.querySelectorAll('.sup-alert').length).toBe(1);
  });

  it("charge les signaux de logs avec leur signification et leur conseil", () => {
    open();
    click('Charger les signaux de logs');

    expect(api.logSignals).toHaveBeenCalledOnceWith(7);
    expect(text()).toContain('Connexion refusée');
    expect(text()).toContain('× 12');
    expect(text()).toContain('Vérifier le service.');
    expect(text()).toContain('connection refused to 10.0.0.1');
    expect(text()).toContain('120 lignes lues');
  });

  it("explique quand les logs de la ressource ne sont pas collectés", () => {
    open();
    api.logSignals.and.returnValue(of({ ...signals, available: false, reason: 'Les logs de cette ressource ne sont pas collectés par Loki', signals: [] }));
    click('Charger les signaux de logs');

    expect(text()).toContain('ne sont pas collectés');
  });

  it("charge la trace Camunda : étapes en ordre, étape en incident, incident Camunda et journal du workflow", () => {
    const el = open();
    click('Charger la trace Camunda');

    expect(api.camunda).toHaveBeenCalledOnceWith(7);
    const steps = Array.from(el.querySelectorAll('.sup-steps li'));
    expect(steps.map(s => s.textContent!.trim().split(' ')[0])).toEqual(['Début', 'Exécuter']);
    expect(el.querySelector('.sup-steps li.is-error')!.textContent).toContain('Exécuter la remédiation');
    expect(text()).toContain('5 s');
    expect(text()).toContain('JOB_NO_RETRIES');
    expect(text()).toContain('Journal HELIOS du workflow');
    expect(text()).toContain('Les variables du processus ne sont jamais lues');
  });

  it("affiche un message quand Camunda est indisponible, sans perdre la chronologie", () => {
    open();
    api.camunda.and.returnValue(of({ available: false, error: 'Camunda est injoignable', runs: [] }));
    click('Charger la trace Camunda');

    expect(text()).toContain('Camunda est injoignable');
  });

  it("affiche un message quand le serveur refuse ou ne répond pas", () => {
    open();
    api.alerts.and.returnValue(throwError(() => ({ status: 403 })));
    click('Charger les alertes');

    expect(text()).toContain('Action non autorisée');
  });

  it("réinitialise les trois sections quand on ouvre un autre incident", () => {
    open();
    click('Charger les alertes');
    expect(text()).toContain('HeliosCrashLoop');

    fixture.componentInstance.open(8);
    fixture.detectChanges();

    expect(text()).toContain('Charger les alertes');
    expect(text()).not.toContain('HeliosCrashLoop');
  });
});
