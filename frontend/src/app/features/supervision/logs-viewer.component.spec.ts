import { ComponentFixture, TestBed } from '@angular/core/testing';
import { of, throwError } from 'rxjs';
import { SupervisionService } from '../../core/services/supervision.service';
import { LogLines } from '../../core/models/supervision.models';
import { LogsViewerComponent } from './logs-viewer.component';

const sources = {
  available: true, error: null,
  sources: [
    { key: 'host:master', label: 'Machine master', kind: 'HOST' as const },
    { key: 'host:worker1', label: 'Machine worker1', kind: 'HOST' as const },
    { key: 'app:pfe/backend', label: 'Application pfe/backend', kind: 'APP' as const }
  ]
};

const lines = (over: Partial<LogLines> = {}): LogLines => ({
  available: true, error: null, source: 'app:pfe/backend', level: 'ALL', query: '', minutes: 30,
  from: '2026-11-27T10:00:00', to: '2026-11-27T10:30:00', truncated: false, count: 3,
  lines: [
    { at: '2026-11-27T10:29:03', text: 'kubelet: connection refused to 10.0.0.1', level: 'ERROR', category: 'RESEAU', label: 'Connexion refusée' },
    { at: '2026-11-27T10:28:10', text: 'level=warn msg=lent', level: 'WARN', category: null, label: null },
    { at: '2026-11-27T10:27:00', text: 'tout va bien', level: 'INFO', category: null, label: null }
  ], ...over
});

describe('LogsViewerComponent', () => {
  let api: jasmine.SpyObj<SupervisionService>;
  let fixture: ComponentFixture<LogsViewerComponent>;
  let component: LogsViewerComponent;

  function create(): HTMLElement {
    api = jasmine.createSpyObj<SupervisionService>('SupervisionService', ['logSources', 'logLines']);
    api.logSources.and.returnValue(of(sources));
    api.logLines.and.returnValue(of(lines()));
    TestBed.configureTestingModule({ imports: [LogsViewerComponent], providers: [{ provide: SupervisionService, useValue: api }] });
    fixture = TestBed.createComponent(LogsViewerComponent);
    component = fixture.componentInstance;
    fixture.detectChanges();
    return fixture.nativeElement as HTMLElement;
  }

  const text = () => (fixture.nativeElement as HTMLElement).textContent!.replace(/\s+/g, ' ');

  it("charge les sources puis les lignes de l'application du backend par défaut", () => {
    const el = create();

    expect(api.logSources).toHaveBeenCalledTimes(1);
    expect(api.logLines).toHaveBeenCalledWith('app:pfe/backend', 'ALL', '', 30, 100);
    expect(el.querySelectorAll('.lv-line').length).toBe(3);
    expect(el.querySelectorAll('optgroup').length).toBe(2);
  });

  it("colore les lignes par niveau et explique les erreurs connues", () => {
    const el = create();

    expect(el.querySelector('.lv-line.is-error')!.textContent).toContain('Connexion refusée');
    expect(el.querySelector('.lv-line.is-warn')).not.toBeNull();
    expect(el.querySelector('.lv-level[data-level="ERROR"]')!.textContent).toBe('ERR');
    expect(text()).toContain('1 erreur(s)');
    expect(text()).toContain('1 avertissement(s)');
  });

  it("recharge avec le niveau choisi", () => {
    create();
    component.setLevel('ERROR');

    expect(api.logLines).toHaveBeenCalledWith('app:pfe/backend', 'ERROR', '', 30, 100);
  });

  it("recherche un mot à la touche Entrée et le surligne sans HTML", () => {
    const el = create();
    component.query = 'refused';
    api.logLines.and.returnValue(of(lines({ query: 'refused', count: 1, lines: [lines().lines[0]] })));
    const input = el.querySelector('input[type="search"]') as HTMLInputElement;
    input.dispatchEvent(new KeyboardEvent('keyup', { key: 'Enter' }));
    fixture.detectChanges();

    expect(api.logLines).toHaveBeenCalledWith('app:pfe/backend', 'ALL', 'refused', 30, 100);
    const mark = el.querySelector('mark')!;
    expect(mark.textContent).toBe('refused');
    expect(el.querySelector('.lv-text')!.innerHTML).not.toContain('<script');
  });

  it("découpe une ligne autour du mot cherché, sans tenir compte de la casse", () => {
    create();
    component.result = lines({ query: 'CONNECTION' });

    const s = component.segments(lines().lines[0]);

    expect(s.map(x => x.text).join('')).toBe('kubelet: connection refused to 10.0.0.1');
    expect(s.filter(x => x.hit).map(x => x.text)).toEqual(['connection']);
  });

  it("n'interprète jamais une ligne comme du HTML", () => {
    const el = create();
    api.logLines.and.returnValue(of(lines({ lines: [{ at: '2026-11-27T10:00:00', text: '<img src=x onerror=alert(1)>', level: 'INFO', category: null, label: null }], count: 1 })));
    component.load();
    fixture.detectChanges();

    expect(el.querySelector('img')).toBeNull();
    expect(el.querySelector('.lv-text')!.textContent).toContain('<img src=x onerror=alert(1)>');
  });

  it("change de source, de période et de nombre de lignes", () => {
    create();
    component.source = 'host:worker1';
    component.minutes = 180;
    component.limit = 200;
    component.load();

    expect(api.logLines).toHaveBeenCalledWith('host:worker1', 'ALL', '', 180, 200);
  });

  it("indique quand Loki est indisponible", () => {
    const el = create();
    api.logLines.and.returnValue(of(lines({ available: false, error: 'Loki est injoignable', lines: [], count: 0 })));
    component.load();
    fixture.detectChanges();

    expect(text()).toContain('Loki indisponible');
    expect(el.querySelector('.lv-console')).toBeNull();
  });

  it("indique quand les sources de logs ne sont pas disponibles", () => {
    api = jasmine.createSpyObj<SupervisionService>('SupervisionService', ['logSources', 'logLines']);
    api.logSources.and.returnValue(of({ available: false, error: 'Loki est injoignable', sources: [] }));
    TestBed.configureTestingModule({ imports: [LogsViewerComponent], providers: [{ provide: SupervisionService, useValue: api }] });
    fixture = TestBed.createComponent(LogsViewerComponent);
    fixture.detectChanges();

    expect(text()).toContain('Les sources de logs ne sont pas disponibles');
    expect(api.logLines).not.toHaveBeenCalled();
  });

  it("affiche un message quand l'accès est refusé", () => {
    create();
    api.logLines.and.returnValue(throwError(() => ({ status: 403 })));
    component.load();
    fixture.detectChanges();

    expect(text()).toContain('Action non autorisée');
  });

  it("signale une liste tronquée et un résultat vide", () => {
    create();
    api.logLines.and.returnValue(of(lines({ truncated: true })));
    component.load();
    fixture.detectChanges();
    expect(text()).toContain('Limite atteinte');

    api.logLines.and.returnValue(of(lines({ lines: [], count: 0, truncated: false })));
    component.load();
    fixture.detectChanges();
    expect(text()).toContain('Aucune ligne ne correspond');
  });

  it("l'actualisation automatique se déclenche puis s'arrête avec le composant", () => {
    jasmine.clock().install();
    try {
      create();
      api.logLines.calls.reset();
      component.toggleAuto();
      jasmine.clock().tick(10_500);
      expect(api.logLines).toHaveBeenCalledTimes(1);

      fixture.destroy();
      jasmine.clock().tick(30_000);
      expect(api.logLines).toHaveBeenCalledTimes(1);
    } finally {
      jasmine.clock().uninstall();
    }
  });
});
