import { Component, OnInit, OnDestroy, ChangeDetectionStrategy } from '@angular/core';
import { ActivatedRoute, Router } from '@angular/router';
import { Subscription } from 'rxjs';
import { ApiService, Incident, Action, WorkflowLogEntry, TerminalEvidence, IncidentVerification } from '../../../core/services/api.service';
import { scenarioForIncidentType, validationLabel, neutralizeAiText, ScenarioRule, REMEDIATION_CONFIG_VERSION } from '../../../core/config/remediation-scenarios';

interface HistoryEntry {
  id: number;
  status: string;
  comment: string;
  createdAt: string;
}

@Component({
    selector: 'app-incident-detail',
    templateUrl: './incident-detail.component.html',
    changeDetection: ChangeDetectionStrategy.Eager,
    standalone: false
})
export class IncidentDetailComponent implements OnInit, OnDestroy {
  incident: Incident | null = null;
  history: HistoryEntry[] = [];
  actions: Action[] = [];
  workflowLogs: WorkflowLogEntry[] = [];
  // LOT HELIOS-INCIDENT-IDENTITY-CARD-01 — preuves terminal AVANT/APRÈS + vérification finale
  evidence: TerminalEvidence[] = [];
  verification: IncidentVerification | null = null;
  loading = true;
  incidentId!: number;
  // Version de la configuration déterministe affichée (mirroir du mapping backend).
  configVersion = REMEDIATION_CONFIG_VERSION;
  private subs: Subscription[] = [];

  constructor(
    private route: ActivatedRoute,
    private router: Router,
    private api: ApiService
  ) {}

  ngOnInit(): void {
    this.incidentId = Number(this.route.snapshot.paramMap.get('id'));
    this.loadAll();
  }

  loadAll(): void {
    this.loading = true;
    this.api.getIncident(this.incidentId).subscribe({
      next: inc => {
        this.incident = inc;
        this.loading = false;
      },
      error: () => { this.loading = false; this.router.navigate(['/incidents']); }
    });
    this.api.getActionsForIncident(this.incidentId).subscribe({
      next: acts => { this.actions = acts || []; },
      error: () => {}
    });
    this.api.getIncidentHistory(this.incidentId).subscribe({
      next: hist => { this.history = hist || []; },
      error: () => {}
    });
    this.api.getWorkflowLogs(this.incidentId).subscribe({
      next: logs => { this.workflowLogs = logs || []; },
      error: () => {}
    });
    // Preuves terminal (BEFORE/AFTER) — vides tant qu'un mécanisme d'exécution ne les a pas fournies.
    this.api.getIncidentEvidence(this.incidentId).subscribe({
      next: ev => { this.evidence = ev || []; },
      error: () => { this.evidence = []; }
    });
    // Vérification finale — null tant que non collectée (HTTP 204).
    this.api.getIncidentVerification(this.incidentId).subscribe({
      next: v => { this.verification = v || null; },
      error: () => { this.verification = null; }
    });
  }

  /** Preuves d'une phase donnée (BEFORE/AFTER). */
  evidenceByPhase(phase: string): TerminalEvidence[] {
    return this.evidence.filter(e => e.phase === phase);
  }

  /** true si la preuve est une donnée de TEST explicite (source = FIXTURE). */
  isFixture(e: TerminalEvidence): boolean {
    return (e.source || '').toUpperCase() === 'FIXTURE';
  }

  /** Paires clé/valeur des mesures extraites (JSON), ou [] si aucune. */
  measurePairs(json: string | null): { k: string; v: any }[] {
    if (!json) { return []; }
    try {
      const o = JSON.parse(json);
      return Object.keys(o).map(k => ({ k, v: o[k] }));
    } catch {
      return [];
    }
  }

  goBack(): void {
    this.router.navigate(['/incidents']);
  }

  /** Scénario métier déterministe (CPU / VM_DOWN / DISK) dérivé du type d'incident, ou null. */
  scenario(): ScenarioRule | null {
    return scenarioForIncidentType(this.incident?.incidentType);
  }

  /**
   * VMDOWN-POLICY (consigne finale 2026-11-06) : .160 (k8s-monitoring, VM de supervision) est en DÉTECTION SEULE — elle
   * n'est JAMAIS rallumée automatiquement ; incident et notifications d'interface seulement (aucun e-mail, aucune
   * escalade Camunda). Noms exacts : aucun alias.
   */
  isSupervisionDetectionOnly(): boolean {
    return this.incident?.namespace === 'infra' && this.incident?.app === 'k8s-monitoring';
  }

  /** Libellé lisible du niveau de validation d'un scénario. */
  validationText(status: string): string {
    return validationLabel(status as any);
  }

  /** Titre présenté sans terminologie héritée (la donnée persistée n'est jamais modifiée). */
  displayTitle(t: string | null | undefined): string {
    return neutralizeAiText(t);
  }

  /** Libellé NEUTRE de la source : les anciennes données de détection sont présentées sans jargon. */
  sourceLabel(): string {
    const s = (this.incident?.source || '').toUpperCase();
    if (s === 'AI') { return 'DÉTECTION'; }
    return this.incident?.source || '—';
  }

  getSeverityClass(s: string): string { return s?.toLowerCase() || ''; }
  getStatusClass(s: string): string { return s?.toLowerCase() || ''; }

  formatDate(d: string): string {
    if (!d) return '';
    return new Date(d).toLocaleString('fr-FR');
  }

  /** Parse the structured description "Label: Value | Label: Value" into chips.
   *  Returns [] for the legacy verbose format so the template falls back to raw text. */
  descriptionChips(): { label: string; value: string }[] {
    const d = (this.incident?.description || '').trim();
    if (!d || d.includes('explanation=') || d.includes('executionMode=')) return [];
    return d.split('|')
      .map(p => p.trim())
      .filter(p => p.includes(':'))
      .map(p => {
        const i = p.indexOf(':');
        return { label: p.slice(0, i).trim(), value: p.slice(i + 1).trim() };
      })
      // Drop the AI-proposed "Mode": the REAL execution mode (Auto/Manuel) + status is shown
      // authoritatively in the "Actions de Remédiation" table, so showing the AI's proposal here
      // (which can differ from the backend's actual decision) only causes confusion.
      .filter(c => c.label.length > 0 && c.value.length > 0 && c.label.length < 30
                   && c.label.toLowerCase() !== 'mode');
  }

  /** Parse the AI explanation prose into labeled sections (Evidence / Root cause / Action / Why). */
  explanationSections(): { label: string; icon: string; text: string; highlight?: boolean }[] {
    const e = (this.incident?.explanation || '').trim();
    if (!e) return [];
    const grab = (re: RegExp): string => {
      const m = e.match(re);
      return m && m[1] ? m[1].trim().replace(/[.;]\s*$/, '') : '';
    };
    const evidence = grab(/Measured evidence:\s*(.*?)(?:;\s*K8s root cause:|\.\s*Recommended action:|$)/i);
    const cause    = grab(/K8s root cause:\s*(.*?)(?:\.\s*Recommended action:|$)/i);
    const action   = grab(/Recommended action:\s*(.*?)(?:\.\s*Why:|$)/i);
    const why      = grab(/Why:\s*(.*)$/i);
    const out: { label: string; icon: string; text: string; highlight?: boolean }[] = [];
    if (evidence) out.push({ label: 'Preuves mesurées', icon: 'fa-magnifying-glass-chart', text: evidence });
    if (cause)    out.push({ label: 'Cause racine',      icon: 'fa-triangle-exclamation',   text: cause });
    if (action)   out.push({ label: 'Action recommandée', icon: 'fa-screwdriver-wrench',     text: action });
    if (why)      out.push({ label: 'Pourquoi',          icon: 'fa-lightbulb',              text: why, highlight: true });
    return out;
  }

  hasStructuredExplanation(): boolean { return this.explanationSections().length > 0; }

  /** Action RECOMMANDÉE par la détection/diagnostic, extraite de l'explication ("Recommended action: X"),
   *  ou null. C'est une recommandation, PAS une preuve d'exécution. */
  recommendedAction(): string | null {
    const m = (this.incident?.explanation || '').match(/Recommended action:\s*([A-Za-z0-9_]+)/i);
    return m ? m[1].trim() : null;
  }

  /** Cause racine issue des données ("root cause: …"), ou null → l'UI affiche « Cause racine non déterminée ». */
  rootCause(): string | null {
    const m = (this.incident?.explanation || '').match(/(?:K8s\s+)?root cause:\s*(.*?)(?:\.\s*Recommended action:|$)/i);
    const v = m && m[1] ? m[1].trim().replace(/[.;]\s*$/, '') : '';
    return v || null;
  }

  /** Actions RÉELLEMENT enregistrées = source d'autorité de ce qui a été exécuté / escaladé / échoué. */
  hasExecutedActions(): boolean { return this.actions.length > 0; }

  /** true si une action d'escalade a été enregistrée (aucune action technique exécutée sur la cible). */
  isEscalationOnly(): boolean {
    return this.actions.length > 0 && this.actions.every(a => (a.actionType || a.type || '').toUpperCase() === 'ESCALATION');
  }

  ngOnDestroy(): void {
    this.subs.forEach(s => s.unsubscribe());
  }
}
