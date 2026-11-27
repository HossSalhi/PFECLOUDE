import { ComponentFixture, TestBed } from '@angular/core/testing';
import { ActivatedRoute, provideRouter } from '@angular/router';
import { of, throwError } from 'rxjs';
import { SelfHealingService } from '../../core/services/self-healing.service';
import { SelfHealingTraceComponent } from './self-healing-trace.component';

const listing = {
  from: '2026-11-26T00:00:00', to: '2026-11-27T00:00:00', truncated: false,
  items: [{ incidentId: 7, title: 'CPU worker1', type: 'NODE_HIGH_CPU', resource: 'k8s-nodes/k8s-worker1', status: 'RESOLVED',
    createdAt: '2026-11-26T10:00:00', actions: 2, failedActions: 1, lastActionType: 'RESTART_POD', lastActionStatus: 'COMPLETED', lastActionAt: '2026-11-26T10:07:00' }]
};

const trace = {
  incident: { id: 7, title: 'CPU worker1', type: 'NODE_HIGH_CPU', namespace: 'k8s-nodes', app: 'k8s-worker1', severity: 'HIGH', status: 'RESOLVED', createdAt: '2026-11-26T10:00:00' },
  timeline: [
    { at: '2026-11-26T10:00:00', kind: 'INCIDENT', title: 'Incident détecté', detail: 'CPU worker1' },
    { at: '2026-11-26T10:05:00', kind: 'ACTION', title: 'Action lancée : HOST_SERVICE_CPU_QUOTA (AUTO)', detail: null },
    { at: '2026-11-26T10:10:00', kind: 'VERIFICATION', title: 'Vérification : PASSED', detail: 'CPU < 80 %' }
  ],
  actions: [
    { id: 11, type: 'HOST_SERVICE_CPU_QUOTA', status: 'COMPLETED', mode: 'AUTO', attempts: 1, executedBy: 'helios', createdAt: '2026-11-26T10:05:00',
      executedAt: '2026-11-26T10:06:00', target: 'k8s-worker1', failedReason: null, remediation: true, logSource: 'VM worker1' },
    { id: 12, type: 'NOTIFICATION', status: 'COMPLETED', mode: 'AUTO', attempts: 1, executedBy: null, createdAt: '2026-11-26T10:01:00',
      executedAt: null, target: null, failedReason: null, remediation: false, logSource: null }
  ],
  verifications: [{ criteria: 'CPU < 80 %', result: 'PASSED', available: true, at: '2026-11-26T10:10:00', reason: null }],
  evidence: [{ vm: 'k8s-worker1', phase: 'BEFORE', command: 'uptime', exitCode: 0, available: true, at: '2026-11-26T10:03:00' }]
};

const logs = {
  available: true, reason: null, source: 'VM worker1', from: '2026-11-26T10:01:00', to: '2026-11-26T10:11:00', truncated: false,
  lines: [
    { at: '2026-11-26T10:05:30', text: 'systemd[1]: ligne normale', error: false, category: null, label: null },
    { at: '2026-11-26T10:06:10', text: 'kubelet[1]: connection refused', error: true, category: 'RESEAU', label: 'Connexion refusée' }
  ]
};

describe('SelfHealingTraceComponent', () => {
  let api: jasmine.SpyObj<SelfHealingService>;
  let fixture: ComponentFixture<SelfHealingTraceComponent>;
  let component: SelfHealingTraceComponent;

  function create(query: string | null = null): HTMLElement {
    api = jasmine.createSpyObj<SelfHealingService>('SelfHealingService', ['list', 'trace', 'logs']);
    api.list.and.returnValue(of(listing));
    api.trace.and.returnValue(of(trace as any));
    api.logs.and.returnValue(of(logs));
    TestBed.configureTestingModule({
      imports: [SelfHealingTraceComponent],
      providers: [provideRouter([]), { provide: SelfHealingService, useValue: api },
        { provide: ActivatedRoute, useValue: { snapshot: { queryParamMap: { get: () => query } } } }]
    });
    fixture = TestBed.createComponent(SelfHealingTraceComponent);
    component = fixture.componentInstance;
    fixture.detectChanges();
    return fixture.nativeElement as HTMLElement;
  }

  const text = () => (fixture.nativeElement as HTMLElement).textContent!.replace(/\s+/g, ' ');
  const click = (label: string) => {
    Array.from((fixture.nativeElement as HTMLElement).querySelectorAll('button')).find(b => b.textContent!.includes(label))!.click();
    fixture.detectChanges();
  };

  it('liste les incidents ayant déclenché une remédiation, avec les échecs', () => {
    const el = create();

    expect(api.list).toHaveBeenCalledTimes(1);
    expect(el.querySelectorAll('tbody tr').length).toBe(1);
    expect(text()).toContain('CPU worker1');
    expect(text()).toContain('1 en échec');
    expect(text()).toContain('RESTART_POD');
  });

  it('propose les deux onglets, dont celui de la page courante', () => {
    const el = create();

    const tabs = Array.from(el.querySelectorAll('.sup-tab')).map(t => t.textContent!.trim());
    expect(tabs).toEqual(['Activités', 'Auto-remédiation']);
    expect(el.querySelector('.sup-tab.active')!.textContent).toContain('Auto-remédiation');
  });

  it('change la période et recharge la liste', () => {
    create();
    api.list.calls.reset();

    component.setPeriod(168);

    expect(api.list).toHaveBeenCalledTimes(1);
    expect(api.list.calls.mostRecent().args[2]).toBe(50);
  });

  it("ouvre la trace d'un incident : chronologie, actions, vérifications et relevés VM", () => {
    const el = create();
    click('Voir la trace');

    expect(api.trace).toHaveBeenCalledWith(7);
    expect(el.querySelectorAll('.sup-timeline li').length).toBe(3);
    expect(el.querySelector('.sup-timeline li[data-kind="VERIFICATION"]')).not.toBeNull();
    expect(text()).toContain('HOST_SERVICE_CPU_QUOTA');
    expect(text()).toContain('CPU < 80 %');
    expect(text()).toContain('uptime');
    expect(text()).toContain("La sortie complète des commandes n'est pas reprise ici");
  });

  it("ne demande les logs à Loki qu'à l'ouverture d'une action, une seule fois", () => {
    const el = create();
    click('Voir la trace');
    expect(api.logs).not.toHaveBeenCalled();

    click('Voir les logs');
    expect(api.logs).toHaveBeenCalledOnceWith(7, 11);
    expect(el.querySelectorAll('.sup-log').length).toBe(2);
    expect(el.querySelector('.sup-log.is-error')!.textContent).toContain('Connexion refusée');

    click('Masquer les logs');
    expect(el.querySelectorAll('.sup-log').length).toBe(0);
    click('Voir les logs');
    expect(api.logs).toHaveBeenCalledTimes(1);   // déjà chargés : pas de nouvel appel
  });

  it("n'offre pas de logs pour une action sans ressource collectée", () => {
    create();
    click('Voir la trace');

    expect(text()).toContain('Pas de logs Loki pour cette ressource');
  });

  it("affiche la raison quand les logs sont indisponibles, sans casser la chronologie", () => {
    const el = create();
    api.logs.and.returnValue(of({ ...logs, available: false, reason: 'Loki est injoignable', lines: [] }));
    click('Voir la trace');
    click('Voir les logs');

    expect(text()).toContain('Loki est injoignable');
    expect(el.querySelectorAll('.sup-timeline li').length).toBe(3);
  });

  it("affiche un message quand le serveur refuse ou ne répond pas", () => {
    create();
    api.trace.and.returnValue(throwError(() => ({ status: 403 })));
    click('Voir la trace');

    expect(text()).toContain('Action non autorisée');
  });

  it("revient à la liste et peut s'ouvrir directement sur un incident (?incident=)", () => {
    create('7');

    expect(api.trace).toHaveBeenCalledWith(7);
    click('Retour à la liste');
    expect(component.selectedId).toBeNull();
    expect(text()).toContain('Voir la trace');
  });

  it("signale une liste tronquée et l'absence de remédiation", () => {
    create();
    api.list.and.returnValue(of({ ...listing, truncated: true }));
    component.loadList();
    fixture.detectChanges();
    expect(text()).toContain('Liste limitée aux incidents les plus récents');

    api.list.and.returnValue(of({ ...listing, truncated: false, items: [] }));
    component.loadList();
    fixture.detectChanges();
    expect(text()).toContain('Aucune remédiation lancée');
  });
});
