import { TestBed } from '@angular/core/testing';
import { of, throwError } from 'rxjs';
import { AiAssistService } from '../../core/services/ai-assist.service';
import { AiAnomalies, AiAssignment, AiMaintenanceWindows, AiReportSummary, AiTeamSummary } from '../../core/models/ai-assist.models';
import { AiAuditAnomaliesComponent } from './ai-audit-anomalies.component';
import { AiMaintenanceWindowComponent } from './ai-maintenance-window.component';
import { AiReportSummaryComponent } from './ai-report-summary.component';
import { AiTaskAdvisorComponent } from './ai-task-advisor.component';
import { AiTeamPanelComponent } from './ai-team-panel.component';
import { OFF, ON, caps, leaderCaps, narrative } from './ai-test.util';

type Api = jasmine.SpyObj<AiAssistService>;

function setup(capabilities: ReturnType<typeof caps> | null): Api {
  const api = jasmine.createSpyObj<AiAssistService>('AiAssistService', [
    'capabilities', 'reportSummary', 'teamSummary', 'anomalies', 'explainAnomalies', 'recommendAssignment', 'assignmentFeedback', 'maintenanceWindows']);
  api.capabilities.and.returnValue(of(capabilities));
  api.assignmentFeedback.and.returnValue(of(undefined));
  TestBed.configureTestingModule({ providers: [{ provide: AiAssistService, useValue: api }] });
  return api;
}

const text = (f: { nativeElement: HTMLElement }) => f.nativeElement.textContent!.replace(/\s+/g, ' ');

// ── (b) résumé de rapport ───────────────────────────────────────────────────────────────────────────────────────────

describe('AiReportSummaryComponent', () => {
  const summary: AiReportSummary = {
    reportId: 5, title: 'Rapport', period: 'a → b', analysis: narrative({ text: 'Résumé exécutif du rapport.' }),
    facts: [{ label: 'Incidents', value: '42', tone: 'neutral' }]
  };

  function create(reportId = 5) {
    const f = TestBed.createComponent(AiReportSummaryComponent);
    f.componentRef.setInput('reportId', reportId);
    f.detectChanges();
    return f;
  }

  it("ne montre rien tant que la fonction est désactivée, indisponible ou refusée à l'utilisateur", () => {
    for (const c of [null, caps({ reportsSummary: OFF }), leaderCaps(1)]) {
      TestBed.resetTestingModule();
      setup(c);
      expect(create().nativeElement.querySelector('.ai-card')).toBeNull();
    }
  });

  it("génère le résumé à la demande seulement (aucun appel à l'affichage)", () => {
    const api = setup(caps());
    api.reportSummary.and.returnValue(of(summary));
    const f = create();

    expect(api.reportSummary).not.toHaveBeenCalled();
    (f.nativeElement.querySelector('button.ai-run') as HTMLButtonElement).click();
    f.detectChanges();

    expect(api.reportSummary).toHaveBeenCalledOnceWith(5);
    expect(text(f)).toContain('Incidents');
    expect(text(f)).toContain('Résumé exécutif du rapport.');
    expect(f.nativeElement.querySelector('.ai-fresh')).not.toBeNull();
    expect(f.nativeElement.querySelector('button.ai-run')!.textContent).toContain('Régénérer');
  });

  it("explique une fonction activée mais non configurée et bloque le bouton", () => {
    setup(caps({ reportsSummary: { enabled: true, ready: false, message: "L'assistance IA n'est pas configurée." } }));
    const f = create();

    expect((f.nativeElement.querySelector('button.ai-run') as HTMLButtonElement).disabled).toBeTrue();
    expect(text(f)).toContain("n'est pas configurée");
  });

  it("affiche l'erreur d'un accès refusé sans casser la page", () => {
    const api = setup(caps());
    api.reportSummary.and.returnValue(throwError(() => ({ status: 403 })));
    const f = create();
    (f.nativeElement.querySelector('button.ai-run') as HTMLButtonElement).click();
    f.detectChanges();

    expect(text(f)).toContain('Action non autorisée');
    expect(f.nativeElement.querySelector('button.ai-run')).not.toBeNull();
  });
});

// ── (c) anomalies ───────────────────────────────────────────────────────────────────────────────────────────────────

describe('AiAuditAnomaliesComponent', () => {
  const anomalies: AiAnomalies = {
    teamId: null, days: 7, analysis: narrative(),
    facts: [{ label: 'Anomalies', value: '2', tone: 'warn' }],
    anomalies: [
      { key: 'a', rule: 'ACCESS_DENIED_BURST', ruleLabel: 'Accès refusés répétés', severity: 'HIGH', actor: 'amine', count: 12,
        firstAt: '2026-11-26T10:00:00', lastAt: '2026-11-26T10:20:00', sentence: 'Amine a été refusé 12 fois.', aiSentence: true },
      { key: 'b', rule: 'OFF_HOURS_LOGIN', ruleLabel: 'Connexions hors horaires', severity: 'LOW', actor: 'bilel', count: 1,
        firstAt: '2026-11-26T23:10:00', lastAt: '2026-11-26T23:10:00', sentence: '1 connexion la nuit.', aiSentence: false }]
  };

  function create(teamId: number | null = null) {
    const f = TestBed.createComponent(AiAuditAnomaliesComponent);
    f.componentRef.setInput('teamId', teamId);
    f.detectChanges();
    return f;
  }

  it("reste caché si la fonction est désactivée", () => {
    setup(caps({ auditAnomalies: OFF }));
    expect(create().nativeElement.querySelector('.ai-card')).toBeNull();
  });

  it("l'analyse IA appelle l'explication et affiche gravité, phrase IA et phrase déterministe", () => {
    const api = setup(caps());
    api.explainAnomalies.and.returnValue(of(anomalies));
    const f = create();

    (f.nativeElement.querySelector('button.ai-run') as HTMLButtonElement).click();
    f.detectChanges();

    expect(api.explainAnomalies).toHaveBeenCalledOnceWith(null, 7);
    const items = f.nativeElement.querySelectorAll('.ai-item');
    expect(items.length).toBe(2);
    expect(items[0].getAttribute('data-sev')).toBe('HIGH');
    expect(items[0].querySelector('.ai-mini')).not.toBeNull();       // phrase rédigée par l'IA, signalée comme telle
    expect(items[1].querySelector('.ai-mini')).toBeNull();           // phrase déterministe
    expect(text(f)).toContain('Haute');
    expect(text(f)).toContain('Faible');
  });

  it("la détection seule n'appelle jamais l'IA et respecte période et équipe", () => {
    const api = setup(leaderCaps(3));
    api.anomalies.and.returnValue(of({ ...anomalies, teamId: 3, analysis: narrative({ status: 'NONE', text: null }) }));
    const f = create(3);
    f.componentInstance.days = 30;

    (Array.from(f.nativeElement.querySelectorAll('button')) as HTMLButtonElement[]).find(b => b.textContent!.includes('Détection seule'))!.click();
    f.detectChanges();

    expect(api.anomalies).toHaveBeenCalledOnceWith(3, 30);
    expect(api.explainAnomalies).not.toHaveBeenCalled();
    expect(f.nativeElement.querySelector('.ai-analysis')).toBeNull();
  });

  it("annonce l'absence d'anomalie", () => {
    const api = setup(caps());
    api.explainAnomalies.and.returnValue(of({ ...anomalies, anomalies: [] }));
    const f = create();
    (f.nativeElement.querySelector('button.ai-run') as HTMLButtonElement).click();
    f.detectChanges();

    expect(text(f)).toContain('Aucune anomalie détectée');
  });
});

// ── (a) assignation ─────────────────────────────────────────────────────────────────────────────────────────────────

describe('AiTaskAdvisorComponent', () => {
  const reco: AiAssignment = {
    teamId: 4, teamName: 'Réseau', recommendedUserId: 12, analysis: narrative({ text: 'Karim est le moins chargé.' }),
    facts: [{ label: 'Meilleur score', value: '88 / 100', tone: 'good' }],
    candidates: [
      { userId: 12, displayName: 'Karim', score: 88, openTasks: 1, overdueTasks: 0, similarDone: 2, reasons: ['1 tâche(s) ouverte(s)', '2 tâche(s) similaire(s) déjà terminée(s)'] },
      { userId: 11, displayName: 'Sami', score: 46, openTasks: 2, overdueTasks: 1, similarDone: 0, reasons: ['2 tâche(s) ouverte(s)'] }]
  };

  function create(inputs: Record<string, unknown>) {
    const f = TestBed.createComponent(AiTaskAdvisorComponent);
    Object.entries(inputs).forEach(([k, v]) => f.componentRef.setInput(k, v));
    f.detectChanges();
    return f;
  }

  it("n'apparaît pas sans équipe ni si la fonction est désactivée", () => {
    setup(caps());
    expect(create({ teamId: null, title: 'x' }).nativeElement.querySelector('.ai-card')).toBeNull();
    TestBed.resetTestingModule();
    setup(caps({ taskAssignment: OFF }));
    expect(create({ teamId: 4, title: 'x' }).nativeElement.querySelector('.ai-card')).toBeNull();
  });

  it("dans le formulaire, il faut un titre pour suggérer ; la demande part avec équipe, titre et priorité", () => {
    const api = setup(caps());
    api.recommendAssignment.and.returnValue(of(reco));
    const f = create({ teamId: 4, title: '', description: 'câblage', priority: 'HIGH' });

    expect(text(f)).toContain("Saisissez d'abord le titre");
    expect((f.nativeElement.querySelector('button.ai-run') as HTMLButtonElement).disabled).toBeTrue();

    f.componentRef.setInput('title', 'Remplacer le switch');
    f.detectChanges();
    (f.nativeElement.querySelector('button.ai-run') as HTMLButtonElement).click();
    f.detectChanges();

    expect(api.recommendAssignment).toHaveBeenCalledOnceWith({ teamId: 4, title: 'Remplacer le switch', description: 'câblage', priority: 'HIGH' });
    expect(f.nativeElement.querySelectorAll('.ai-cand').length).toBe(2);
    expect(f.nativeElement.querySelector('.ai-cand.is-top')!.textContent).toContain('Recommandé');
    expect(text(f)).toContain('Karim est le moins chargé.');
  });

  it("choisir le membre recommandé trace une acceptation et prévient le formulaire", () => {
    const api = setup(caps());
    api.recommendAssignment.and.returnValue(of(reco));
    const f = create({ teamId: 4, title: 'Tâche' });
    const chosen = jasmine.createSpy('chosen');
    f.componentInstance.chosen.subscribe(chosen);
    (f.nativeElement.querySelector('button.ai-run') as HTMLButtonElement).click();
    f.detectChanges();

    (f.nativeElement.querySelectorAll('.ai-cand button')[0] as HTMLButtonElement).click();
    f.detectChanges();

    expect(api.assignmentFeedback).toHaveBeenCalledOnceWith(4, 12, true);
    expect(chosen).toHaveBeenCalledWith(12);
    expect(text(f)).toContain('Suggestion retenue : Karim');
    expect(f.nativeElement.querySelector('.ai-candidates')).toBeNull();
  });

  it("choisir un autre membre, ou ignorer, compte comme une suggestion non suivie", () => {
    const api = setup(caps());
    api.recommendAssignment.and.returnValue(of(reco));
    const f = create({ teamId: 4, title: 'Tâche' });
    (f.nativeElement.querySelector('button.ai-run') as HTMLButtonElement).click();
    f.detectChanges();

    (f.nativeElement.querySelectorAll('.ai-cand button')[1] as HTMLButtonElement).click();
    expect(api.assignmentFeedback).toHaveBeenCalledWith(4, 11, false);

    (f.nativeElement.querySelector('button.ai-run') as HTMLButtonElement).click();
    f.detectChanges();
    api.assignmentFeedback.calls.reset();
    (f.nativeElement.querySelector('.btn-link') as HTMLButtonElement).click();
    expect(api.assignmentFeedback).toHaveBeenCalledWith(4, 12, false);
    f.detectChanges();
    expect(text(f)).toContain('Suggestion ignorée');
  });

  it("en mode autonome, le titre est saisi dans le composant ; une équipe vide est expliquée", () => {
    const api = setup(caps());
    api.recommendAssignment.and.returnValue(of({ ...reco, candidates: [], recommendedUserId: null }));
    const f = create({ teamId: 4, manual: true });
    const input = f.nativeElement.querySelector('input[type="text"]') as HTMLInputElement;
    input.value = 'Audit des sauvegardes';
    input.dispatchEvent(new Event('input'));
    f.detectChanges();

    (f.nativeElement.querySelector('button.ai-run') as HTMLButtonElement).click();
    f.detectChanges();

    expect(api.recommendAssignment).toHaveBeenCalledWith(jasmine.objectContaining({ title: 'Audit des sauvegardes' }));
    expect(text(f)).toContain('Aucun membre actif');
  });

  it("l'échec du suivi d'acceptation ne bloque jamais l'utilisateur", () => {
    const api = setup(caps());
    api.recommendAssignment.and.returnValue(of(reco));
    api.assignmentFeedback.and.returnValue(throwError(() => ({ status: 500 })));
    const f = create({ teamId: 4, title: 'Tâche' });
    (f.nativeElement.querySelector('button.ai-run') as HTMLButtonElement).click();
    f.detectChanges();

    (f.nativeElement.querySelectorAll('.ai-cand button')[0] as HTMLButtonElement).click();
    f.detectChanges();

    expect(text(f)).toContain('Suggestion retenue');
  });
});

// ── (d) fenêtre de maintenance ──────────────────────────────────────────────────────────────────────────────────────

describe('AiMaintenanceWindowComponent', () => {
  const windows: AiMaintenanceWindows = {
    teamId: 1, metricsAvailable: true, analysis: narrative({ text: 'Privilégiez la nuit de mardi.' }),
    facts: [{ label: 'Source de charge', value: 'Prometheus (7 j)', tone: 'good' }],
    windows: [
      { start: '2026-12-01T02:00:00', end: '2026-12-01T04:00:00', averageLoad: 12, peakLoad: 15, score: 88, notes: ['Heures creuses de nuit'] },
      { start: '2026-12-02T03:00:00', end: '2026-12-02T05:00:00', averageLoad: 45, peakLoad: 50, score: 55, notes: [] }]
  };

  function create() {
    const f = TestBed.createComponent(AiMaintenanceWindowComponent);
    f.detectChanges();
    return f;
  }

  function fill(f: ReturnType<typeof create>, resource: string) {
    f.componentInstance.resourceName = resource;
    f.detectChanges();
  }

  it("reste caché si la fonction est désactivée", () => {
    setup(caps({ maintenanceWindow: OFF }));
    expect(create().nativeElement.querySelector('.ai-card')).toBeNull();
  });

  it("un chef d'équipe vise l'une de SES équipes ; un administrateur n'en cible aucune", () => {
    const api = setup(leaderCaps(1));
    api.maintenanceWindows.and.returnValue(of(windows));
    const lead = create();
    expect(lead.componentInstance.teamId).toBe(1);
    expect(lead.nativeElement.querySelector('select[aria-label="Équipe"]')).not.toBeNull();

    TestBed.resetTestingModule();
    setup(caps());
    const admin = create();
    expect(admin.componentInstance.teamId).toBeNull();
    expect(admin.nativeElement.querySelector('select[aria-label="Équipe"]')).toBeNull();
  });

  it("exige une ressource, envoie la demande puis affiche les créneaux classés avec leur charge", () => {
    const api = setup(leaderCaps(1));
    api.maintenanceWindows.and.returnValue(of(windows));
    const f = create();
    expect((f.nativeElement.querySelector('button.ai-run') as HTMLButtonElement).disabled).toBeTrue();

    fill(f, ' k8s-worker1 ');
    (f.nativeElement.querySelector('button.ai-run') as HTMLButtonElement).click();
    f.detectChanges();

    expect(api.maintenanceWindows).toHaveBeenCalledOnceWith({ teamId: 1, resourceType: 'VM', resourceName: 'k8s-worker1', durationMinutes: 120, horizonDays: 14 });
    const cands = f.nativeElement.querySelectorAll('.ai-cand');
    expect(cands.length).toBe(2);
    expect(cands[0].classList).toContain('is-top');
    expect(cands[0].textContent).toContain('Meilleur créneau');
    expect(cands[0].querySelector('.ai-score')!.getAttribute('data-level')).toBe('good');
    expect(cands[1].querySelector('.ai-score')!.getAttribute('data-level')).toBe('warn');
    expect(text(f)).toContain('Privilégiez la nuit de mardi.');
  });

  it("signale l'absence de métriques et l'absence de créneau", () => {
    const api = setup(caps());
    api.maintenanceWindows.and.returnValue(of({ ...windows, metricsAvailable: false }));
    const f = create();
    fill(f, 'vm1');
    (f.nativeElement.querySelector('button.ai-run') as HTMLButtonElement).click();
    f.detectChanges();
    expect(text(f)).toContain('Prometheus étant indisponible');

    api.maintenanceWindows.and.returnValue(of({ ...windows, windows: [], analysis: narrative({ status: 'NONE', text: null }) }));
    (f.nativeElement.querySelector('button.ai-run') as HTMLButtonElement).click();
    f.detectChanges();
    expect(text(f)).toContain('Aucun créneau libre');
  });

  it("affiche un refus de périmètre renvoyé par le serveur", () => {
    const api = setup(leaderCaps(1));
    api.maintenanceWindows.and.returnValue(throwError(() => ({ status: 400 })));
    const f = create();
    fill(f, 'app1');
    (f.nativeElement.querySelector('button.ai-run') as HTMLButtonElement).click();
    f.detectChanges();

    expect(text(f)).toContain('Demande invalide');
  });
});

// ── fiche d'équipe ─────────────────────────────────────────────────────────────────────────────────────────────────

describe('AiTeamPanelComponent', () => {
  const summary: AiTeamSummary = {
    teamId: 1, teamName: 'Réseau', analysis: narrative({ text: 'Équipe en bonne santé.' }),
    facts: [{ label: 'En retard', value: '2', tone: 'bad' }],
    members: [{ userId: 11, displayName: 'Sami', total: 6, open: 3, done: 3, overdue: 2 }]
  };

  function create(teamId: number) {
    const f = TestBed.createComponent(AiTeamPanelComponent);
    f.componentRef.setInput('teamId', teamId);
    f.detectChanges();
    return f;
  }

  it("un administrateur voit l'assistant de n'importe quelle équipe", () => {
    setup(caps());
    const f = create(99);
    expect(f.nativeElement.querySelector('.ai-panel')).not.toBeNull();
    expect(text(f)).toContain("Assistant IA de l'équipe");
  });

  it("un chef d'équipe ne voit l'assistant que de SON équipe", () => {
    setup(leaderCaps(1));
    expect(create(1).nativeElement.querySelector('.ai-panel')).not.toBeNull();

    TestBed.resetTestingModule();
    setup(leaderCaps(1));
    expect(create(2).nativeElement.querySelector('.ai-panel')).toBeNull();
  });

  it("un technicien simple, ou une IA entièrement désactivée, n'affichent rien", () => {
    setup(caps({ admin: false, allowed: false, ledTeams: [] }));
    expect(create(1).nativeElement.querySelector('.ai-panel')).toBeNull();

    TestBed.resetTestingModule();
    setup(caps({ teamSummary: OFF, auditAnomalies: OFF, taskAssignment: OFF }));
    expect(create(1).nativeElement.querySelector('.ai-panel')).toBeNull();

    TestBed.resetTestingModule();
    setup(null);
    expect(create(1).nativeElement.querySelector('.ai-panel')).toBeNull();
  });

  it("génère le résumé d'équipe à la demande avec le tableau de charge", () => {
    const api = setup(leaderCaps(1));
    api.teamSummary.and.returnValue(of(summary));
    const f = create(1);
    expect(api.teamSummary).not.toHaveBeenCalled();

    (f.nativeElement.querySelector('button.ai-run') as HTMLButtonElement).click();
    f.detectChanges();

    expect(api.teamSummary).toHaveBeenCalledOnceWith(1);
    expect(text(f)).toContain('Équipe en bonne santé.');
    expect(f.nativeElement.querySelector('.ai-table')!.textContent).toContain('Sami');
    expect(f.nativeElement.querySelector('.ai-late')).not.toBeNull();
  });

  it("n'affiche que les fonctions activées", () => {
    setup(caps({ teamSummary: ON, auditAnomalies: OFF, taskAssignment: OFF }));
    const f = create(1);

    expect(f.nativeElement.querySelectorAll('.ai-card').length).toBe(1);
  });
});

void ON;
