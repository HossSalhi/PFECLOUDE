import { ComponentFixture, TestBed } from '@angular/core/testing';
import { of, throwError } from 'rxjs';
import { SupervisionService } from '../../core/services/supervision.service';
import { ProcessDiagram } from '../../core/models/supervision.models';
import { CamundaPanelComponent } from './camunda-panel.component';

const overview = {
  available: true, error: null, generatedAt: '', healthy: true, version: '8.9.0', clusterSize: 1, active: 142, completed: 6942, terminated: 3, incidents: 141,
  definitions: [{ key: '55', id: 'helios-camunda-full-cycle', name: 'Cycle complet', version: 22 }, { key: '66', id: 'autre', name: 'Autre processus', version: 1 }]
};

const stats = (completed = 0, active = 0, incidents = 0) => ({ completed, active, canceled: 0, incidents });

const diagram: ProcessDiagram = {
  available: true, error: null, definitionKey: '55', name: 'Cycle complet', version: 22, autoLayout: true, width: 700, height: 240,
  nodes: [
    { id: 'S', type: 'startEvent', shape: 'EVENT_START', name: 'Début', x: 30, y: 100, w: 40, h: 40, stats: stats(100) },
    { id: 'T', type: 'serviceTask', shape: 'TASK', name: 'Exécuter la remédiation automatique', x: 120, y: 90, w: 150, h: 66, stats: stats(90, 2, 3) },
    { id: 'G', type: 'exclusiveGateway', shape: 'GATEWAY', name: 'Succès ?', x: 330, y: 95, w: 52, h: 52, stats: stats(90) },
    { id: 'E', type: 'endEvent', shape: 'EVENT_END', name: 'Fin', x: 440, y: 100, w: 40, h: 40, stats: stats() }
  ],
  edges: [
    { id: 'f1', from: 'S', to: 'T', label: null, points: [{ x: 70, y: 120 }, { x: 120, y: 120 }] },
    { id: 'f2', from: 'T', to: 'G', label: null, points: [{ x: 270, y: 123 }, { x: 330, y: 121 }] },
    { id: 'f3', from: 'G', to: 'E', label: 'oui', points: [{ x: 382, y: 121 }, { x: 440, y: 120 }] }
  ]
};

describe('CamundaPanelComponent', () => {
  let api: jasmine.SpyObj<SupervisionService>;
  let fixture: ComponentFixture<CamundaPanelComponent>;
  let component: CamundaPanelComponent;

  function create(): HTMLElement {
    api = jasmine.createSpyObj<SupervisionService>('SupervisionService', ['camundaOverview', 'camundaInstances', 'camundaIncidents', 'camundaDiagram']);
    api.camundaOverview.and.returnValue(of(overview));
    api.camundaDiagram.and.returnValue(of(diagram));
    api.camundaInstances.and.returnValue(of({ available: true, error: null, total: 7084, items: [
      { key: '1', definitionKey: '55', definitionId: 'x', definitionName: 'Cycle complet', version: 22, state: 'COMPLETED', hasIncident: false, startedAt: '2026-11-27T02:15:00', endedAt: '', durationMs: 1226 },
      { key: '2', definitionKey: '55', definitionId: 'x', definitionName: 'Cycle complet', version: 22, state: 'ACTIVE', hasIncident: true, startedAt: '2026-11-27T02:20:00', endedAt: null, durationMs: null }] }));
    api.camundaIncidents.and.returnValue(of({ available: true, error: null, total: 141, items: [
      { key: 'a', definitionId: 'incident-remediation', instanceKey: '7', errorType: 'JOB_NO_RETRIES', errorMessage: 'orphan: workflow purged', elementId: 'Task_Exec', createdAt: '2026-10-15T10:23:39' },
      { key: 'b', definitionId: 'incident-remediation', instanceKey: '8', errorType: 'JOB_NO_RETRIES', errorMessage: 'orphan: workflow purged', elementId: 'Task_Exec', createdAt: '2026-10-15T10:24:39' },
      { key: 'c', definitionId: 'p', instanceKey: '9', errorType: 'CONDITION_ERROR', errorMessage: 'condition', elementId: 'Gw', createdAt: null }] }));
    TestBed.configureTestingModule({ imports: [CamundaPanelComponent], providers: [{ provide: SupervisionService, useValue: api }] });
    fixture = TestBed.createComponent(CamundaPanelComponent);
    component = fixture.componentInstance;
    fixture.detectChanges();
    return fixture.nativeElement as HTMLElement;
  }

  const text = () => (fixture.nativeElement as HTMLElement).textContent!.replace(/\s+/g, ' ');

  it("affiche les indicateurs du moteur", () => {
    const el = create();

    expect(el.querySelectorAll('.cp-kpi').length).toBe(5);
    expect(text()).toContain('Sain');
    expect(text()).toContain('Camunda 8.9.0');
    expect(text()).toContain('6,942');
    expect(el.querySelector('.cp-kpi.is-bad')!.textContent).toContain('141');
  });

  it("charge le diagramme du processus HELIOS par défaut et le dessine", () => {
    const el = create();

    expect(api.camundaDiagram).toHaveBeenCalledWith('55');
    expect(el.querySelectorAll('.cp-node').length).toBe(4);
    expect(el.querySelectorAll('path.cp-edge').length).toBe(3);
    expect(el.querySelector('polygon')).not.toBeNull();          // passerelle
    expect(el.querySelectorAll('circle').length).toBe(2);        // début et fin
    expect(el.querySelector('rect')).not.toBeNull();             // tâche
    expect(text()).toContain('oui');                             // étiquette de flèche
  });

  it("colore chaque étape selon son état et affiche ses compteurs", () => {
    const el = create();

    const task = el.querySelector('.cp-node[data-shape="TASK"]')!;
    expect(task.getAttribute('data-state')).toBe('incident');
    expect(task.textContent).toContain('✓ 90');
    expect(task.textContent).toContain('● 2');
    expect(task.textContent).toContain('⚠ 3');
    expect(el.querySelector('.cp-node[data-shape="EVENT_START"]')!.getAttribute('data-state')).toBe('done');
    expect(el.querySelector('.cp-node[data-shape="EVENT_END"]')!.getAttribute('data-state')).toBe('idle');
    expect(task.querySelector('title')!.textContent).toContain('3 incident(s)');
  });

  it("coupe les libellés trop longs sur trois lignes au plus", () => {
    create();

    expect(component.wrap('Exécuter la remédiation automatique des incidents critiques', 20).length).toBeLessThanOrEqual(3);
    expect(component.wrap('Court', 20)).toEqual(['Court']);
    expect(component.wrap('Mot' + 'x'.repeat(40), 10)[0].endsWith('…')).toBeTrue();
  });

  it("change de processus et recharge son diagramme", () => {
    create();
    api.camundaDiagram.calls.reset();

    component.chooseProcess('66');

    expect(api.camundaDiagram).toHaveBeenCalledWith('66');
  });

  it("zoome et dézoome dans des bornes", () => {
    const el = create();

    component.zoomIn();
    expect(component.zoom).toBe(1.25);
    fixture.detectChanges();
    expect(el.querySelector('svg')!.getAttribute('width')).toBe(String(700 * 1.25));
    for (let i = 0; i < 10; i++) { component.zoomOut(); }
    expect(component.zoom).toBe(0.6);
    for (let i = 0; i < 10; i++) { component.zoomIn(); }
    expect(component.zoom).toBe(1.5);
  });

  it("liste les instances avec leur état et leur durée, et filtre par état", () => {
    const el = create();

    expect(el.querySelectorAll('.cp-table tbody tr').length).toBe(2);
    expect(text()).toContain('Terminée');
    expect(text()).toContain('1 s');
    api.camundaInstances.calls.reset();
    component.setInstanceState('ACTIVE');
    expect(api.camundaInstances).toHaveBeenCalledWith('ACTIVE', 15);
    expect(component.duration(125_000)).toBe('2 min 5 s');
    expect(component.duration(null)).toBe('—');
  });

  it("regroupe les incidents par type avec leur signification", () => {
    const el = create();

    expect(el.querySelectorAll('.cp-group').length).toBe(2);
    expect(component.groups[0].type).toBe('JOB_NO_RETRIES');
    expect(component.groups[0].count).toBe(2);
    expect(text()).toContain('a épuisé ses tentatives');
    expect(text()).toContain('orphan: workflow purged');
    expect(text()).toContain('141');
  });

  it("explique un diagramme indisponible sans casser le reste de la page", () => {
    create();
    api.camundaDiagram.and.returnValue(of({ ...diagram, available: false, error: 'Instance introuvable', nodes: [], edges: [] }));
    component.loadDiagram();
    fixture.detectChanges();

    expect(text()).toContain('Diagramme indisponible : Instance introuvable');
    expect(fixture.nativeElement.querySelectorAll('.cp-kpi').length).toBe(5);
  });

  it("indique quand Camunda est indisponible", () => {
    api = jasmine.createSpyObj<SupervisionService>('SupervisionService', ['camundaOverview', 'camundaInstances', 'camundaIncidents', 'camundaDiagram']);
    api.camundaOverview.and.returnValue(of({ ...overview, available: false, error: 'Camunda est injoignable', definitions: [] }));
    api.camundaInstances.and.returnValue(of({ available: false, error: 'x', total: 0, items: [] }));
    api.camundaIncidents.and.returnValue(of({ available: false, error: 'x', total: 0, items: [] }));
    TestBed.configureTestingModule({ imports: [CamundaPanelComponent], providers: [{ provide: SupervisionService, useValue: api }] });
    fixture = TestBed.createComponent(CamundaPanelComponent);
    fixture.detectChanges();

    expect(text()).toContain('Camunda indisponible : Camunda est injoignable');
    expect(api.camundaDiagram).not.toHaveBeenCalled();
  });

  it("affiche un message quand le serveur ne répond pas", () => {
    api = jasmine.createSpyObj<SupervisionService>('SupervisionService', ['camundaOverview', 'camundaInstances', 'camundaIncidents', 'camundaDiagram']);
    api.camundaOverview.and.returnValue(throwError(() => ({ status: 500 })));
    api.camundaInstances.and.returnValue(throwError(() => ({ status: 500 })));
    api.camundaIncidents.and.returnValue(throwError(() => ({ status: 500 })));
    TestBed.configureTestingModule({ imports: [CamundaPanelComponent], providers: [{ provide: SupervisionService, useValue: api }] });
    fixture = TestBed.createComponent(CamundaPanelComponent);
    fixture.detectChanges();

    expect(text()).toContain("Le serveur n'a pas pu répondre");
  });
});
