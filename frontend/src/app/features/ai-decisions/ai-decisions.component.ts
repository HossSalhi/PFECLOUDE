import { Component, OnInit, OnDestroy } from '@angular/core';
import { Subscription } from 'rxjs';
import { ApiService, Incident, PagedResponse } from '../../core/services/api.service';
import { AuthService } from '../../core/services/auth.service';
import { SseService } from '../../core/services/sse.service';
import { scenarioForIncidentType, validationLabel, neutralizeAiText, ScenarioRule, BusinessScenario } from '../../core/config/remediation-scenarios';

/**
 * Page « Décisions de remédiation » — DÉTERMINISTE.
 *
 * Pour chaque incident, affiche la RÈGLE déterministe réellement appliquée (scénario CPU / VM_DOWN /
 * DISK) et l'ACTION configurée, dérivées du catalogue déterministe (miroir de bridge.py). Aucune
 * notion de « confiance IA » : la décision ne dépend d'aucun modèle. Un type non couvert -> escalade.
 */
@Component({
  selector: 'app-ai-decisions',
  templateUrl: './ai-decisions.component.html'
})
export class AiDecisionsComponent implements OnInit, OnDestroy {
  incidents: Incident[] = [];
  filtered: Incident[] = [];
  loading = true;
  scenarioFilter: 'ALL' | BusinessScenario = 'ALL';
  expandedId: number | null = null;
  private subs: Subscription[] = [];

  // Retour opérateur (endpoint réel /api/feedback)
  feedbackIncidentId: number | null = null;
  feedbackCorrect = true;
  feedbackComment = '';
  feedbackSuccess = '';

  scenarioFilters: { label: string; value: 'ALL' | BusinessScenario }[] = [
    { label: 'Tous', value: 'ALL' },
    { label: 'CPU', value: 'CPU' },
    { label: 'VM_DOWN', value: 'VM_DOWN' },
    { label: 'DISK', value: 'DISK' }
  ];

  constructor(private api: ApiService, private auth: AuthService, private sse: SseService) {}

  ngOnInit(): void {
    this.load();
    // Temps réel : rafraîchir à chaque création/mise à jour d'incident.
    this.subs.push(this.sse.onIncidentCreated().subscribe(() => this.load()));
    this.subs.push(this.sse.onIncidentUpdated().subscribe(() => this.load()));
  }

  load(): void {
    this.loading = true;
    this.fetchPage(0, []);
  }

  // Récupère TOUTES les pages (l'API plafonne size à 100).
  private fetchPage(page: number, acc: Incident[]): void {
    this.api.getIncidents(page, 100).subscribe({
      next: (resp: PagedResponse<Incident>) => {
        const all = acc.concat(resp.content || []);
        const totalPages = resp.totalPages || 1;
        if (page + 1 < totalPages && page < 49) {
          this.fetchPage(page + 1, all);
        } else {
          this.incidents = all;
          this.applyFilter();
          this.loading = false;
        }
      },
      error: () => {
        this.incidents = acc;
        this.applyFilter();
        this.loading = false;
      }
    });
  }

  applyFilter(): void {
    if (this.scenarioFilter === 'ALL') {
      this.filtered = this.incidents;
    } else {
      this.filtered = this.incidents.filter(i => this.scenarioOf(i)?.scenario === this.scenarioFilter);
    }
  }

  setFilter(f: 'ALL' | BusinessScenario): void {
    this.scenarioFilter = f;
    this.applyFilter();
  }

  /** Règle déterministe (scénario) applicable à un incident, ou null (type non couvert -> escalade). */
  scenarioOf(inc: Incident): ScenarioRule | null {
    return scenarioForIncidentType(inc?.incidentType);
  }

  validationText(status: string): string {
    return validationLabel(status as any);
  }

  /** Titre présenté sans terminologie héritée (la donnée persistée n'est jamais modifiée). */
  displayTitle(t: string | null | undefined): string {
    return neutralizeAiText(t);
  }

  toggleExpand(id: number): void {
    this.expandedId = this.expandedId === id ? null : id;
    if (this.feedbackIncidentId !== id) {
      this.feedbackIncidentId = null;
      this.feedbackSuccess = '';
    }
  }

  openFeedback(id: number): void {
    if (this.feedbackIncidentId === id) {
      this.feedbackIncidentId = null;
      this.feedbackSuccess = '';
    } else {
      this.feedbackIncidentId = id;
      this.feedbackCorrect = true;
      this.feedbackComment = '';
      this.feedbackSuccess = '';
    }
  }

  submitFeedback(): void {
    if (this.feedbackIncidentId == null) return;
    this.api.submitFeedback(this.feedbackIncidentId, this.feedbackCorrect, this.feedbackComment, this.auth.getUsername() ?? 'operator')
      .subscribe({
        next: () => {
          this.feedbackSuccess = 'Retour enregistré avec succès.';
          setTimeout(() => {
            this.feedbackIncidentId = null;
            this.feedbackSuccess = '';
          }, 2000);
        },
        error: () => {
          this.feedbackSuccess = 'Erreur lors de l\'envoi du retour.';
        }
      });
  }

  getSeverityClass(s: string): string { return s?.toLowerCase() || ''; }
  getStatusClass(s: string): string { return s?.toLowerCase() || ''; }

  ngOnDestroy(): void {
    this.subs.forEach(s => s.unsubscribe());
  }
}
