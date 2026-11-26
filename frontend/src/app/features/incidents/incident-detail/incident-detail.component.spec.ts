import { ComponentFixture, TestBed } from '@angular/core/testing';
import { CommonModule } from '@angular/common';
import { Router, ActivatedRoute } from '@angular/router';
import { of } from 'rxjs';
import { IncidentDetailComponent } from './incident-detail.component';
import { ApiService, Incident, IncidentVerification, TerminalEvidence } from '../../../core/services/api.service';

/**
 * LOT HELIOS-INCIDENT-IDENTITY-CARD-01 — Phase F (frontend).
 * Teste les helpers purs de la carte d'identité (preuves terminal + mesures), sans TestBed :
 * filtrage par phase, détection de fixture, parsing tolérant des mesures.
 */
describe("IncidentDetailComponent — helpers de la carte d'identité", () => {
  let component: IncidentDetailComponent;

  beforeEach(() => {
    const routeStub: any = { snapshot: { paramMap: { get: () => '1' } } };
    const routerStub: any = { navigate: () => {} };
    const apiStub: any = {};
    component = new IncidentDetailComponent(routeStub, routerStub, apiStub);
  });

  function ev(phase: 'BEFORE' | 'AFTER', source: string, available = true): TerminalEvidence {
    return {
      id: 1, incidentId: 1, vm: 'k8s-worker1', phase, capturedAt: '', command: 'cmd',
      exitCode: 0, stdout: 'out', stderr: '', measuresJson: null, available, source
    } as TerminalEvidence;
  }

  it('evidenceByPhase filtre les preuves par phase', () => {
    component.evidence = [ev('BEFORE', 'BRIDGE'), ev('AFTER', 'BRIDGE'), ev('BEFORE', 'FIXTURE')];
    expect(component.evidenceByPhase('BEFORE').length).toBe(2);
    expect(component.evidenceByPhase('AFTER').length).toBe(1);
  });

  it('isFixture distingue les données de test (FIXTURE) des preuves réelles', () => {
    expect(component.isFixture(ev('BEFORE', 'FIXTURE'))).toBeTrue();
    expect(component.isFixture(ev('BEFORE', 'BRIDGE'))).toBeFalse();
  });

  it("measurePairs parse le JSON des mesures et tolère l'absence/erreur", () => {
    expect(component.measurePairs('{"host_cpu":96.1,"ready":false}').length).toBe(2);
    expect(component.measurePairs(null).length).toBe(0);
    expect(component.measurePairs('pas-du-json').length).toBe(0);
  });

  it('displayTitle neutralise les titres hérités (sans altérer la donnée persistée)', () => {
    // Couche de PRÉSENTATION uniquement : la base n'est jamais réécrite.
    expect(component.displayTitle('Panne nœud (IA: NODE_NOT_READY)')).toBe('Panne nœud — NODE_NOT_READY');
    expect(component.displayTitle('IA: CPU élevé')).toBe('CPU élevé');
    expect(component.displayTitle('Incident normal')).toBe('Incident normal');
  });
});

/**
 * Rendu RÉEL de la carte dans le template Angular (TestBed, HTTP mocké — aucune requête réseau).
 * Vérifie que les champs fournis par le backend sont effectivement AFFICHÉS, et que les données absentes
 * apparaissent explicitement « NON COLLECTÉ » (jamais présentées comme un succès ou une valeur fictive).
 */
describe('IncidentDetailComponent — rendu de la carte (TestBed, HTTP mocké)', () => {
  let fixture: ComponentFixture<IncidentDetailComponent>;
  let api: any;

  const incident: Incident = {
    id: 1, title: 'CPU élevé sur worker1', description: 'Cible: k8s-worker1 | Sévérité: HIGH',
    severity: 'HIGH', status: 'ESCALATED', source: 'PROMETHEUS',
    createdAt: '2026-10-15T10:00:00Z', updatedAt: '2026-10-15T10:05:00Z',
    incidentType: 'NODE_HIGH_CPU', namespace: 'k8s-nodes'
  };

  function configure(evidence: TerminalEvidence[], verification: IncidentVerification | null) {
    api = {
      getIncident: () => of(incident),
      getActionsForIncident: () => of([]),
      getIncidentHistory: () => of([]),
      getWorkflowLogs: () => of([]),
      getIncidentEvidence: () => of(evidence),
      getIncidentVerification: () => of(verification)
    };
    TestBed.configureTestingModule({
    imports: [CommonModule, IncidentDetailComponent],
    providers: [
        { provide: ApiService, useValue: api },
        { provide: Router, useValue: { navigate: () => { } } },
        { provide: ActivatedRoute, useValue: { snapshot: { paramMap: { get: () => '1' } } } }
    ]
});
    fixture = TestBed.createComponent(IncidentDetailComponent);
    fixture.detectChanges(false);  // mock ApiService synchrone : on saute le controle checkNoChanges (HTTP reel = asynchrone)   // déclenche ngOnInit -> loadAll -> rendu
  }

  afterEach(() => TestBed.resetTestingModule());

  it("affiche l'identité de l'incident (type, statut, sévérité)", () => {
    configure([], null);
    const text = (fixture.nativeElement as HTMLElement).textContent || '';
    expect(text).toContain('NODE_HIGH_CPU');
    expect(text).toContain('ESCALATED');
    expect(text).toContain('HIGH');
  });

  it("affiche « NON COLLECTÉ » quand preuves et vérification sont absentes (jamais un faux succès)", () => {
    configure([], null);
    const text = (fixture.nativeElement as HTMLElement).textContent || '';
    // présent à la fois pour les preuves terminal ET la vérification finale
    expect((text.match(/NON COLLECTÉ/g) || []).length).toBeGreaterThanOrEqual(2);
    expect(text).not.toContain('PASS');       // aucune vérification -> aucun verdict affiché
  });

  it('affiche le verdict RESOLVED(PASS), ses critères et ses preuves quand présents', () => {
    const verif: IncidentVerification = {
      id: 1, incidentId: 1, criteria: 'node Ready; host_cpu<90%', result: 'PASS',
      measuresJson: '{"cpu":12}', reason: 'rétabli', verifiedAt: '2026-10-15T10:06:00Z', available: true
    };
    configure([], verif);
    const text = (fixture.nativeElement as HTMLElement).textContent || '';
    expect(text).toContain('PASS');
    expect(text).toContain('node Ready; host_cpu<90%');
    expect(text).toContain('cpu=12');         // mesure réelle affichée
  });

  it("affiche le verdict ESCALATED et son motif", () => {
    const verif: IncidentVerification = {
      id: 2, incidentId: 1, criteria: '', result: 'ESCALATED',
      measuresJson: null, reason: 'aucune action sûre disponible', verifiedAt: '2026-10-15T10:06:00Z', available: true
    };
    configure([], verif);
    const text = (fixture.nativeElement as HTMLElement).textContent || '';
    expect(text).toContain('ESCALATED');
    expect(text).toContain('aucune action sûre disponible');
  });

  it("affiche une preuve terminal AVANT avec sa commande et marque FIXTURE/NON DISPONIBLE", () => {
    const before: TerminalEvidence = {
      id: 10, incidentId: 1, vm: 'k8s-worker1', phase: 'BEFORE', capturedAt: '2026-10-15T10:01:00Z',
      command: 'cat /proc/loadavg', exitCode: 0, stdout: '2.10 1.90 1.50', stderr: '',
      measuresJson: '{"load1":2.1}', available: true, source: 'FIXTURE'
    };
    const afterMissing: TerminalEvidence = {
      id: 11, incidentId: 1, vm: 'k8s-worker1', phase: 'AFTER', capturedAt: '', command: 'cat /proc/loadavg',
      exitCode: null, stdout: '', stderr: '', measuresJson: null, available: false, source: 'FIXTURE'
    };
    configure([before, afterMissing], null);
    const text = (fixture.nativeElement as HTMLElement).textContent || '';
    expect(text).toContain('cat /proc/loadavg');
    expect(text).toContain('load1=2.1');
    expect(text).toContain('FIXTURE (test)');          // donnée de test clairement marquée
    expect(text).toContain('NON DISPONIBLE');          // relevé APRÈS non disponible, pas inventé
  });
});

/**
 * HELIOS — FINALISATION INTERFACE & CARTES DÉTERMINISTES.
 * La carte affiche le SCÉNARIO métier et la DÉCISION DÉTERMINISTE (règle + action configurée) issue du
 * catalogue (miroir du backend), et NE présente plus aucune terminologie IA. Données simulées.
 */
describe('IncidentDetailComponent — décision déterministe & neutralité IA (TestBed)', () => {
  let fixture: ComponentFixture<IncidentDetailComponent>;

  function render(incidentType: string): string {
    const inc: Incident = {
      id: 1, title: 'Incident ' + incidentType, description: 'Cible: k8s-worker1',
      severity: 'HIGH', status: 'OPEN', source: 'AI',
      createdAt: '2026-10-15T10:00:00Z', updatedAt: '2026-10-15T10:00:00Z',
      incidentType, namespace: 'k8s-nodes'
    };
    const api: any = {
      getIncident: () => of(inc),
      getActionsForIncident: () => of([]),
      getIncidentHistory: () => of([]),
      getWorkflowLogs: () => of([]),
      getIncidentEvidence: () => of([]),
      getIncidentVerification: () => of(null)
    };
    TestBed.configureTestingModule({
    imports: [CommonModule, IncidentDetailComponent],
    providers: [
        { provide: ApiService, useValue: api },
        { provide: Router, useValue: { navigate: () => { } } },
        { provide: ActivatedRoute, useValue: { snapshot: { paramMap: { get: () => '1' } } } }
    ]
});
    fixture = TestBed.createComponent(IncidentDetailComponent);
    fixture.detectChanges(false);  // mock ApiService synchrone : on saute le controle checkNoChanges (HTTP reel = asynchrone)   // ngOnInit -> loadAll -> rendu
    return (fixture.nativeElement as HTMLElement).textContent || '';
  }

  afterEach(() => TestBed.resetTestingModule());

  it('CPU (NODE_HIGH_CPU) : scénario + action configurée + E2E non démontré', () => {
    const text = render('NODE_HIGH_CPU');
    expect(text).toContain('Règle de remédiation configurée');
    expect(text).toContain('CPU');
    expect(text).toContain('RULE_CPU_SERVICE_QUOTA');
    expect(text).toContain('HOST_SERVICE_CPU_QUOTA');           // action RÉELLE du mapping (bridge.py), jamais inventée
    expect(text).toContain('E2E non démontré');                 // statut honnête du catalogue (aucune validation E2E déclarée)
  });

  it('VM_DOWN (type VM_DOWN) : RESTART_NODE_VM + E2E non démontré (statut du catalogue)', () => {
    const text = render('VM_DOWN');                             // NODE_NOT_READY seul n'est plus couvert (catalogue du 18 nov. 2026)
    expect(text).toContain('VM_DOWN');
    expect(text).toContain('RESTART_NODE_VM');
    expect(text).toContain('E2E non démontré');
  });

  it('DISK (NODE_DISK_FULL) : HOST_RECLAIM_DISK + E2E non démontré', () => {
    const text = render('NODE_DISK_FULL');
    expect(text).toContain('DISK');
    expect(text).toContain('HOST_RECLAIM_DISK');
    expect(text).toContain('E2E non démontré');                 // limite affichée honnêtement
  });

  it('Type non couvert : hors catalogue déterministe -> escalade', () => {
    const text = render('POD_IMAGE_PULL_BACKOFF');
    expect(text).toContain('hors catalogue déterministe');
    expect(text.toLowerCase()).toContain('escalade');
  });

  it('aucune terminologie IA visible sur la carte', () => {
    const text = render('NODE_HIGH_CPU');
    expect(text).not.toContain('Type IA');
    expect(text).not.toContain('Confiance IA');
    expect(text).not.toContain('Explication IA');
    expect(text).not.toContain("l'IA");
  });
});

/**
 * HELIOS — COHÉRENCE DES FICHES (régression #3229 anonymisé + cas généraux).
 * La carte ne présente JAMAIS l'action configurée (référence par type) comme exécutée : l'action
 * réellement enregistrée et l'action recommandée (détection) sont distinctes et explicites.
 * Données SYNTHÉTIQUES (aucune donnée de production ; l'enregistrement réel n'est pas modifié).
 */
describe('IncidentDetailComponent — cohérence configuré vs recommandé vs exécuté (TestBed)', () => {
  let fixture: ComponentFixture<IncidentDetailComponent>;

  function render(opts: { type?: string; status?: string; explanation?: string; actions?: any[] }): string {
    const inc: any = {
      id: 3229, title: 'Panne nœud (IA: ' + (opts.type ?? 'NODE_NOT_READY') + ') — k8s-worker1',
      description: 'Node k8s-worker1 NotReady', severity: 'HIGH', status: opts.status || 'RESOLVED',
      source: 'AI', createdAt: '2026-10-15T12:27:19Z', updatedAt: '2026-10-15T13:39:33Z',
      incidentType: opts.type ?? 'NODE_NOT_READY', namespace: 'k8s-nodes', app: 'k8s-worker1',
      explanation: opts.explanation
    };
    const api: any = {
      getIncident: () => of(inc),
      getActionsForIncident: () => of(opts.actions || []),
      getIncidentHistory: () => of([]),
      getWorkflowLogs: () => of([]),
      getIncidentEvidence: () => of([]),
      getIncidentVerification: () => of(null)
    };
    TestBed.configureTestingModule({
    imports: [CommonModule, IncidentDetailComponent],
    providers: [
        { provide: ApiService, useValue: api },
        { provide: Router, useValue: { navigate: () => { } } },
        { provide: ActivatedRoute, useValue: { snapshot: { paramMap: { get: () => '3229' } } } }
    ]
});
    fixture = TestBed.createComponent(IncidentDetailComponent);
    fixture.detectChanges(false);  // mock ApiService synchrone : on saute le controle checkNoChanges (HTTP reel = asynchrone)
    return (fixture.nativeElement as HTMLElement).textContent || '';
  }

  afterEach(() => TestBed.resetTestingModule());

  const EXPL_3229 = 'NODE_NOT_READY (severity HIGH, confidence 0.72). Measured evidence: 1 node(s) NotReady; K8s root cause: node(s): k8s-worker1 unreachable. Recommended action: ESCALATE_NODE_OUTAGE_AND_REBALANCE. Why: node down.';
  const ESC = { id: 389, actionType: 'ESCALATION', type: 'ESCALATION', status: 'COMPLETED', automated: true, targetName: 'k8s-worker1' };

  it('#3229 (type VM_DOWN) : ESCALATION exécutée — RESTART_NODE_VM seulement en RÉFÉRENCE, jamais présenté comme exécuté', () => {
    const text = render({ type: 'VM_DOWN', status: 'RESOLVED', explanation: EXPL_3229, actions: [ESC] });   // type couvert (voir le test suivant pour NODE_NOT_READY)
    expect(text).toContain('ESCALATION · COMPLETED');                 // action réellement enregistrée (autorité)
    expect(text).toContain('ESCALATE_NODE_OUTAGE_AND_REBALANCE');      // action recommandée (détection), distincte
    expect(text).toContain('RESTART_NODE_VM');                         // présent SEULEMENT comme référence…
    expect(text).toContain('Configuration de référence pour le TYPE'); // …avec caveat explicite
    expect(text).toContain('Action configurée (référence, ≠ exécution)');
    expect(text).toContain('k8s-worker1 unreachable');                 // cause racine réelle
    expect(text).toContain('RESOLVED');                               // statut incident distinct de la remédiation
    expect(text).not.toContain('(IA:');                               // titre hérité neutralisé
  });

  it('#3229 (type NODE_NOT_READY) : ESCALATION exécutée, aucune règle de référence affirmée (type hors catalogue)', () => {
    // Depuis la mise à jour du catalogue (18 nov. 2026), NODE_NOT_READY seul n'est plus rattaché au scénario VM_DOWN.
    const text = render({ type: 'NODE_NOT_READY', status: 'RESOLVED', explanation: EXPL_3229, actions: [ESC] });
    expect(text).toContain('ESCALATION · COMPLETED');                 // action réellement enregistrée (autorité)
    expect(text).toContain('ESCALATE_NODE_OUTAGE_AND_REBALANCE');      // action recommandée (détection)
    expect(text).toContain('hors catalogue déterministe');             // le catalogue ne couvre plus ce type
    expect(text).not.toContain('RESTART_NODE_VM');                     // aucune action de référence affirmée pour ce type
    expect(text).not.toContain('Action configurée (référence, ≠ exécution)');
    expect(text).toContain('k8s-worker1 unreachable');                 // cause racine réelle
    expect(text).toContain('RESOLVED');
    expect(text).not.toContain('(IA:');                               // titre hérité neutralisé
  });

  it('cause racine absente -> « Cause racine non déterminée » (jamais une hypothèse)', () => {
    const text = render({ type: 'NODE_NOT_READY', status: 'OPEN', explanation: undefined, actions: [] });
    expect(text).toContain('Cause racine non déterminée');
  });

  it('DISK réel NODE_DISK_PRESSURE est reconnu comme scénario DISK', () => {
    const text = render({ type: 'NODE_DISK_PRESSURE', status: 'OPEN', actions: [] });
    expect(text).toContain('DISK');
    expect(text).toContain('Règle de remédiation configurée');
  });

  it('type hors catalogue avec action réelle -> montre l\'action exécutée (pas d\'escalade fictive)', () => {
    const scale = { id: 5, actionType: 'SCALE_DEPLOYMENT', type: 'SCALE_DEPLOYMENT', status: 'COMPLETED', automated: true };
    const text = render({ type: 'DATABASE_POOL_SATURATION', status: 'RESOLVED', actions: [scale] });
    expect(text).toContain('hors catalogue déterministe');
    expect(text).toContain('SCALE_DEPLOYMENT · COMPLETED');
  });

  it('type non renseigné -> fiche gérée (hors catalogue, « (non renseigné) »)', () => {
    const text = render({ type: '', status: 'OPEN', actions: [] });
    expect(text).toContain('hors catalogue déterministe');
    expect(text).toContain('(non renseigné)');
  });
});
