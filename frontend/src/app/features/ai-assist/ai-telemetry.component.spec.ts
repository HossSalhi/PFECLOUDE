import { TestBed } from '@angular/core/testing';
import { of, throwError } from 'rxjs';
import { AiAssistService } from '../../core/services/ai-assist.service';
import { TelemetryAnalysis, TelemetryPage, TelemetryRunSummary } from '../../core/models/ai-assist.models';
import { AiTelemetryComponent } from './ai-telemetry.component';
import { OFF, caps, leaderCaps, narrative } from './ai-test.util';

type Api = jasmine.SpyObj<AiAssistService>;

const finding = {
  scope: 'VM', entity: 'k8s-worker1', signal: 'CPU', rule: 'SUSTAINED_HIGH', severity: 'HIGH' as const,
  start: '2026-11-27T10:00:00', end: '2026-11-27T10:40:00', observed: 96, baseline: 20, unit: '%',
  detail: 'CPU au-dessus de 85 % pendant 40 min (pic 96.0 %)', evidence: 'PromQL : 100-(avg by(instance)(rate(node_cpu_seconds_total...', queryRef: 'PROM_CPU'
};

const quality = [
  { source: 'Prometheus (CPU, mémoire, disque)', available: true, coverage: 0.92, note: 'Référence : 1 h précédentes.' },
  { source: 'Loki (erreurs des logs)', available: false, coverage: 0, note: 'Loki est indisponible ou trop lent : logs non analysés.' }
];

const trace = {
  rulesVersion: 'telemetry-rules-2', windowStart: '2026-11-27T10:00:00', windowEnd: '2026-11-27T11:00:00', baselineStart: '2026-11-27T09:00:00',
  baselineEnd: '2026-11-27T10:00:00', metricsStepSeconds: 60, logsWindowStart: '2026-11-27T10:00:00', logsBaselineStart: '2026-11-27T09:00:00', logsStepSeconds: 3600,
  thresholds: { 'cpu.high/critical': '85 / 95 %' },
  queries: [
    { id: 'PROM_CPU', source: 'Prometheus', signal: 'CPU', query: 'QL-CPU-EXACTE', status: 'OK' as const, reason: null, stepSeconds: 60, start: null, end: null, series: 6, points: 300 },
    { id: 'LOKI_ERRORS_PODS', source: 'Loki', signal: 'LOG_ERRORS', query: 'QL-LOKI-EXACTE', status: 'FAILED' as const, reason: 'Loki est injoignable', stepSeconds: 3600, start: null, end: null, series: null, points: null }
  ],
  coverage: [{ source: 'Prometheus', entity: 'k8s-worker1', signal: 'CPU', coverage: 0.6, note: '36 mesure(s) sur 60 attendues' }]
};

function analysis(over: Partial<TelemetryAnalysis> = {}): TelemetryAnalysis {
  return {
    id: 7, requestedAt: '2026-11-27T11:00:00', requestedBy: 'superadmin', teamId: null, teamName: null, scope: 'ALL', minutes: 60,
    windowStart: '2026-11-27T10:00:00', windowEnd: '2026-11-27T11:00:00', verdict: 'ANOMALIES', dataQuality: 'PARTIAL', anomalyCount: 1, highCount: 1, durationMs: 1234,
    facts: [{ label: 'Anomalies', value: '1', tone: 'warn' }], findings: [finding], quality, trace, analysis: narrative({ text: 'Une anomalie CPU est détectée sur k8s-worker1.' }), ...over
  };
}

function summary(id: number, over: Partial<TelemetryRunSummary> = {}): TelemetryRunSummary {
  return { id, requestedAt: '2026-11-27T11:00:00', requestedBy: 'superadmin', teamId: null, teamName: null, scope: 'ALL', minutes: 60,
    verdict: 'NO_ANOMALY', dataQuality: 'COMPLETE', anomalyCount: 0, highCount: 0, narrativeStatus: 'OK', ...over };
}

function page(content: TelemetryRunSummary[], last = true): TelemetryPage {
  return { content, totalElements: content.length, totalPages: 1, number: 0, last };
}

function setup(c: ReturnType<typeof caps> | null, history: TelemetryPage = page([summary(7), summary(6)])): Api {
  const api = jasmine.createSpyObj<AiAssistService>('AiAssistService', ['capabilities', 'analyseTelemetry', 'telemetryHistory', 'telemetryAnalysis']);
  api.capabilities.and.returnValue(of(c));
  api.telemetryHistory.and.returnValue(of(history));
  TestBed.configureTestingModule({ providers: [{ provide: AiAssistService, useValue: api }] });
  return api;
}

function create() {
  const f = TestBed.createComponent(AiTelemetryComponent);
  f.detectChanges();
  return f;
}

const text = (f: { nativeElement: HTMLElement }) => f.nativeElement.textContent!.replace(/\s+/g, ' ');
const runButton = (f: { nativeElement: HTMLElement }) => f.nativeElement.querySelector('button.ai-run') as HTMLButtonElement;

describe('AiTelemetryComponent', () => {

  it("reste invisible si la fonction est désactivée, refusée ou si les droits sont inconnus", () => {
    for (const c of [null, caps({ telemetryAnalysis: OFF })]) {
      TestBed.resetTestingModule();
      const api = setup(c);
      expect(create().nativeElement.querySelector('.ai-card')).toBeNull();
      expect(api.telemetryHistory).not.toHaveBeenCalled();
    }
  });

  it("charge l'historique à l'ouverture mais ne lance JAMAIS d'analyse toute seule", () => {
    const api = setup(caps());
    const f = create();

    expect(api.telemetryHistory).toHaveBeenCalledWith(null, 0, 10);
    expect(api.analyseTelemetry).not.toHaveBeenCalled();
    expect(f.nativeElement.querySelectorAll('.ai-history tbody tr').length).toBe(2);
    expect(f.nativeElement.querySelector('.ai-verdict')).toBeNull();
  });

  it("un administrateur choisit le périmètre et la période ; la demande part avec ces valeurs", () => {
    const api = setup(caps());
    api.analyseTelemetry.and.returnValue(of(analysis()));
    const f = create();
    f.componentInstance.scope = 'APPLICATION';
    f.componentInstance.minutes = 1440;

    runButton(f).click();
    f.detectChanges();

    expect(api.analyseTelemetry).toHaveBeenCalledOnceWith({ teamId: null, scope: 'APPLICATION', minutes: 1440 });
    expect(f.nativeElement.querySelector('select[aria-label="Périmètre"]')).not.toBeNull();
    expect(f.nativeElement.querySelector('select[aria-label="Équipe"]')).toBeNull();
  });

  it("un chef d'équipe choisit une de SES équipes et n'envoie jamais de périmètre", () => {
    const api = setup(leaderCaps(4));
    api.analyseTelemetry.and.returnValue(of(analysis({ teamId: 4, teamName: 'Réseau', scope: 'VM' })));
    const f = create();

    expect(f.componentInstance.teamId).toBe(4);
    expect(f.nativeElement.querySelector('select[aria-label="Périmètre"]')).toBeNull();
    expect(text(f)).toContain('spécialité de votre équipe');
    runButton(f).click();

    expect(api.analyseTelemetry).toHaveBeenCalledOnceWith({ teamId: 4, scope: null, minutes: 60 });
  });

  it("affiche le verdict, les constats avec leur requête, la qualité des données et la synthèse IA", () => {
    const api = setup(caps());
    api.analyseTelemetry.and.returnValue(of(analysis()));
    const f = create();
    runButton(f).click();
    f.detectChanges();

    const verdict = f.nativeElement.querySelector('.ai-verdict')!;
    expect(verdict.getAttribute('data-verdict')).toBe('ANOMALIES');
    expect(verdict.textContent).toContain('1 anomalie(s) détectée(s) dont 1 de gravité haute');
    expect(verdict.textContent).toContain('Analyse #7');
    expect(text(f)).toContain('par superadmin');

    const row = f.nativeElement.querySelector('app-ai-card .ai-table tbody tr')!;
    expect(row.textContent).toContain('k8s-worker1');
    expect(row.textContent).toContain('Haute');
    expect(row.textContent).toContain('Dépassement soutenu');
    expect(row.textContent).toContain('96 %');
    expect(row.textContent).toContain('20 %');
    expect(row.querySelector('.ai-evidence code')!.textContent).toContain('PromQL');

    const q = f.nativeElement.querySelectorAll('.ai-quality li');
    expect(q.length).toBe(2);
    expect(q[0].textContent).toContain('couverture 92 %');
    expect(q[1].classList).toContain('is-off');
    expect(q[1].textContent).toContain('indisponible');

    expect(f.nativeElement.querySelector('.ai-badge')!.textContent).toContain('Analyse IA');
    expect(text(f)).toContain('Une anomalie CPU est détectée');
  });

  it("annonce clairement l'absence d'anomalie et les données insuffisantes", () => {
    const api = setup(caps());
    api.analyseTelemetry.and.returnValue(of(analysis({ verdict: 'NO_ANOMALY', anomalyCount: 0, highCount: 0, findings: [] })));
    const f = create();
    runButton(f).click();
    f.detectChanges();

    expect(f.nativeElement.querySelector('.ai-verdict')!.getAttribute('data-verdict')).toBe('NO_ANOMALY');
    expect(text(f)).toContain('Aucune anomalie détectée');
    expect(f.nativeElement.querySelector('app-ai-card .ai-table')).toBeNull();          // aucun tableau de constats (celui de l'historique est ailleurs)

    api.analyseTelemetry.and.returnValue(of(analysis({ verdict: 'INSUFFICIENT_DATA', anomalyCount: 0, findings: [] })));
    runButton(f).click();
    f.detectChanges();
    expect(text(f)).toContain('Données insuffisantes pour conclure');
  });

  it("couverture partielle : jamais présentée comme « aucune anomalie »", () => {
    const api = setup(caps(), page([]));
    api.analyseTelemetry.and.returnValue(of(analysis({ verdict: 'PARTIAL_COVERAGE', dataQuality: 'PARTIAL', anomalyCount: 0, highCount: 0, findings: [] })));
    const f = create();
    runButton(f).click();
    f.detectChanges();
    expect(f.nativeElement.querySelector('.ai-verdict')!.getAttribute('data-verdict')).toBe('PARTIAL_COVERAGE');
    expect(text(f)).toContain('Couverture partielle');
    expect(text(f)).not.toContain('Aucune anomalie détectée');
  });

  it("anomalies avec données partielles : avertissement, requêtes en échec nommées et traçabilité", () => {
    const api = setup(caps(), page([]));
    api.analyseTelemetry.and.returnValue(of(analysis()));
    const f = create();
    runButton(f).click();
    f.detectChanges();
    expect(f.nativeElement.querySelector('.ai-warn')!.textContent).toContain('partielles');
    const failed = f.nativeElement.querySelector('.ai-failed')!;
    expect(failed.textContent).toContain('1 requête(s) en échec');
    expect(failed.textContent).toContain('Loki est injoignable');
    expect(failed.textContent).toContain('QL-LOKI-EXACTE');
    const traceText = f.nativeElement.querySelector('.ai-trace')!.textContent;
    expect(traceText).toContain('telemetry-rules-2');
    expect(traceText).toContain('QL-CPU-EXACTE');
    expect(traceText).toContain('cpu.high/critical : 85 / 95 %');
    expect(traceText).toContain('k8s-worker1');
    expect(f.nativeElement.querySelector('.ai-trace li.ai-low')).not.toBeNull();       // couverture < 80 % signalée
  });

  it("sépare les constats côté application et côté infrastructure, avec des libellés lisibles", () => {
    const api = setup(caps(), page([]));
    const app = { ...finding, scope: 'APPLICATION', entity: 'pfe/backend', signal: 'WORKLOAD', rule: 'POD_RESTARTS', detail: 'Le conteneur a redémarré 7 fois', queryRef: 'PROBE_RESTARTS' };
    const infra = { ...finding, scope: 'SERVICE', entity: 'service Loki', signal: 'MONITORING', rule: 'MONITORING_NOT_READY', detail: "Loki n'est pas prêt : HTTP 503", queryRef: 'PROBE_TOOLS_READY' };
    api.analyseTelemetry.and.returnValue(of(analysis({ findings: [app, infra], anomalyCount: 2 })));
    const f = create();
    runButton(f).click();
    f.detectChanges();
    const sides = Array.from(f.nativeElement.querySelectorAll('app-ai-card .ai-side') as NodeListOf<HTMLElement>).map(e => e.textContent!.trim());
    expect(sides).toEqual(['Application', 'Infrastructure']);
    expect(text(f)).toContain('Redémarrages');
    expect(text(f)).toContain('Outil non prêt');
    expect(text(f)).toContain('Charge de travail');
  });

  it("l'IA indisponible garde le verdict et les constats calculés", () => {
    const api = setup(caps());
    api.analyseTelemetry.and.returnValue(of(analysis({ analysis: narrative({ status: 'UNAVAILABLE', message: 'Le quota du fournisseur IA est dépassé.', text: null }) })));
    const f = create();
    runButton(f).click();
    f.detectChanges();

    expect(text(f)).toContain('1 anomalie(s) détectée(s)');
    expect(text(f)).toContain('quota');
    expect(f.nativeElement.querySelectorAll('app-ai-card .ai-table tbody tr').length).toBe(1);
  });

  it("recharge l'historique après une analyse pour que la trace apparaisse", () => {
    const api = setup(caps());
    api.analyseTelemetry.and.returnValue(of(analysis()));
    const f = create();
    api.telemetryHistory.calls.reset();

    runButton(f).click();

    expect(api.telemetryHistory).toHaveBeenCalledWith(null, 0, 10);
  });

  // ── historique ───────────────────────────────────────────────────────────────────────────────────────────────

  it("rouvre une analyse enregistrée à l'identique, sans la recalculer", () => {
    const api = setup(caps(), page([summary(7, { verdict: 'ANOMALIES', anomalyCount: 1, highCount: 1 })]));
    api.telemetryAnalysis.and.returnValue(of(analysis({ id: 7 })));
    const f = create();

    (f.nativeElement.querySelector('.ai-history tbody button') as HTMLButtonElement).click();
    f.detectChanges();

    expect(api.telemetryAnalysis).toHaveBeenCalledOnceWith(7);
    expect(api.analyseTelemetry).not.toHaveBeenCalled();
    expect(text(f)).toContain('Analyse #7');
    expect(f.nativeElement.querySelector('.ai-history tr.is-open')).not.toBeNull();
  });

  it("affiche l'historique avec verdict, auteur, équipe et gravité haute", () => {
    setup(caps(), page([summary(9, { verdict: 'ANOMALIES', anomalyCount: 3, highCount: 2, requestedBy: 'chef.alpha', teamName: 'Réseau', scope: 'VM', minutes: 1440 })]));
    const f = create();

    const row = f.nativeElement.querySelector('.ai-history tbody tr')!;
    expect(row.textContent).toContain('chef.alpha');
    expect(row.textContent).toContain('Réseau');
    expect(row.textContent).toContain('Machines virtuelles');
    expect(row.textContent).toContain('24 heures');
    expect(row.textContent).toContain('2 haute(s)');
    expect(row.querySelector('.ai-verdict-pill')!.getAttribute('data-verdict')).toBe('ANOMALIES');
  });

  it("pagine : « plus anciennes » charge la page suivante et l'ajoute", () => {
    const api = setup(caps(), page([summary(10)], false));
    const f = create();
    api.telemetryHistory.and.returnValue(of(page([summary(9)], true)));

    (Array.from(f.nativeElement.querySelectorAll('button')) as HTMLButtonElement[]).find(b => b.textContent!.includes('plus anciennes'))!.click();
    f.detectChanges();

    expect(api.telemetryHistory).toHaveBeenCalledWith(null, 1, 10);
    expect(f.nativeElement.querySelectorAll('.ai-history tbody tr').length).toBe(2);
    expect(text(f)).not.toContain('plus anciennes');
  });

  it("historique vide et erreurs : messages clairs, la page reste utilisable", () => {
    const api = setup(caps(), page([]));
    const f = create();
    expect(text(f)).toContain('Aucune analyse enregistrée');

    api.telemetryHistory.and.returnValue(throwError(() => ({ status: 403 })));
    (Array.from(f.nativeElement.querySelectorAll('button')) as HTMLButtonElement[]).find(b => b.textContent!.includes('Actualiser'))!.click();
    f.detectChanges();
    expect(text(f)).toContain('Action non autorisée');
    expect(runButton(f)).not.toBeNull();
  });

  it("affiche l'erreur d'une analyse refusée et reste cliquable", () => {
    const api = setup(caps());
    api.analyseTelemetry.and.returnValue(throwError(() => ({ status: 400 })));
    const f = create();
    runButton(f).click();
    f.detectChanges();

    expect(text(f)).toContain('Demande invalide');
    expect(runButton(f).disabled).toBeFalse();
  });

  it("explique une fonction activée mais non configurée et bloque le bouton", () => {
    setup(caps({ telemetryAnalysis: { enabled: true, ready: false, message: "L'assistance IA n'est pas configurée (clé API manquante ou refusée)." } }));
    const f = create();

    expect(runButton(f).disabled).toBeTrue();
    expect(text(f)).toContain('clé API manquante');
  });

  it("un chef sans équipe active ne peut pas lancer d'analyse", () => {
    setup(caps({ admin: false, ledTeams: [], allowed: true }));
    const f = create();

    expect(f.componentInstance.teamId).toBeNull();
    expect(runButton(f).disabled).toBeTrue();
  });

  it("libellés de signaux, règles et périodes", () => {
    setup(caps());
    const c = create().componentInstance;

    expect(c.signalLabel('LOG_ERRORS')).toBe('Erreurs des logs');
    expect(c.ruleLabel('FILLING_UP')).toBe('Saturation prévue');
    expect(c.ruleLabel('INCONNUE')).toBe('INCONNUE');
    expect(c.periodLabel(10080)).toBe('7 jours');
    expect(c.periodLabel(45)).toBe('45 min');
    expect(c.valueOf({ ...finding, unit: 'erreurs/h' }, 40)).toBe('40 erreurs/h');
    expect(c.valueOf(finding, null)).toBe('—');
  });
});
