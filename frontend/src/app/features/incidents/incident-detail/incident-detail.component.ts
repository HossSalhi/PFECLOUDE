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

  ngOnDestroy(): void {
    this.subs.forEach(s => s.unsubscribe());
  }
}
