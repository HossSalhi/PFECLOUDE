import { Component, OnInit, OnDestroy } from '@angular/core';
import { ActivatedRoute, Router } from '@angular/router';
import { Subscription } from 'rxjs';
import { ApiService, Incident, Action } from '../../../core/services/api.service';

interface HistoryEntry {
  id: number;
  status: string;
  comment: string;
  createdAt: string;
}

@Component({
  selector: 'app-incident-detail',
  templateUrl: './incident-detail.component.html'
})
export class IncidentDetailComponent implements OnInit, OnDestroy {
  incident: Incident | null = null;
  history: HistoryEntry[] = [];
  actions: Action[] = [];
  loading = true;
  updatingStatus = false;
  incidentId!: number;
  private subs: Subscription[] = [];

  statusOptions = ['OPEN', 'IN_PROGRESS', 'RESOLVED'];

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
  }

  updateStatus(newStatus: string): void {
    if (!this.incident) return;

    // "Démarrer" (IN_PROGRESS) declenche le pipeline de remediation:
    // - Cree un workflow Camunda
    // - Cree une Human Task pour validation
    // - Le pod sera redemarre/scale via kubectl par le worker Camunda apres approbation
    // S'applique a tous les incidents OPEN sauf les MANUAL (qui sont juste des notes)
    const shouldTriggerRemediation =
      newStatus === 'IN_PROGRESS' &&
      this.incident.source !== 'MANUAL' &&
      this.incident.status === 'OPEN';

    if (shouldTriggerRemediation) {
      this.updatingStatus = true;
      this.api.executeIncident(this.incident.id).subscribe({
        next: () => { this.updatingStatus = false; this.loadAll(); },
        error: () => { this.updatingStatus = false; }
      });
      return;
    }

    // Sinon: simple changement de statut en DB (cas MANUAL ou Resolved/Reouvert)
    this.updatingStatus = true;
    this.api.updateIncidentStatus(this.incident.id, newStatus).subscribe({
      next: updated => {
        this.incident = { ...this.incident!, status: updated.status };
        this.updatingStatus = false;
        this.loadAll();
      },
      error: () => { this.updatingStatus = false; }
    });
  }

  /** True si cliquer "Demarrer" va declencher Camunda (pas juste un changement DB). */
  willTriggerRemediation(): boolean {
    return !!this.incident
        && this.incident.status === 'OPEN'
        && this.incident.source !== 'MANUAL';
  }

  goBack(): void {
    this.router.navigate(['/incidents']);
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

  ngOnDestroy(): void {
    this.subs.forEach(s => s.unsubscribe());
  }
}
