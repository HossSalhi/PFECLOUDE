import { DatePipe } from '@angular/common';
import { ChangeDetectionStrategy, Component, Input, OnInit, inject } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { AiAssistService, aiErrorMessage } from '../../core/services/ai-assist.service';
import { AiAnomalies, AiFeatureState } from '../../core/models/ai-assist.models';
import { AiCardComponent } from './ai-card.component';

/**
 * (c) Anomalies du journal d'audit : détection par règles (sans IA), puis une phrase IA par constat à la demande.
 * Un chef d'équipe n'obtient que les anomalies de SON équipe (le Backend l'impose) ; un administrateur voit tout.
 */
@Component({
  selector: 'app-ai-audit-anomalies',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.Eager,
  imports: [AiCardComponent, FormsModule, DatePipe],
  template: `
    @if (feature?.enabled) {
      <app-ai-card title="Anomalies d'audit" [subtitle]="subtitle" icon="fa-shield-halved"
                   runLabel="Analyser avec l'IA" [loading]="loading" [done]="!!data" [error]="error" [notice]="feature?.ready ? '' : (feature?.message || '')"
                   [disabled]="!feature?.ready" [facts]="data?.facts || []" [narrative]="data?.analysis || null" [generatedAt]="ranAt"
                   (run)="run(true)">
        <div class="ai-options">
          <label>Période
            <select class="form-input" [(ngModel)]="days" aria-label="Période d'analyse">
              <option [ngValue]="7">7 jours</option><option [ngValue]="14">14 jours</option><option [ngValue]="30">30 jours</option>
            </select>
          </label>
          <button type="button" class="btn-secondary" (click)="run(false)" [disabled]="loading"><i class="fas fa-magnifying-glass"></i> Détection seule (sans IA)</button>
        </div>
        @if (data) {
          @if (data.anomalies.length === 0) {
            <div class="alert-success"><i class="fas fa-circle-check"></i> Aucune anomalie détectée sur {{ data.days }} jours.</div>
          } @else {
            <div class="ai-list">
              @for (a of data.anomalies; track a.key) {
                <article class="ai-item" [attr.data-sev]="a.severity">
                  <div class="ai-item-head">
                    <span class="ai-sev" [attr.data-sev]="a.severity">{{ sevLabel(a.severity) }}</span>
                    <strong>{{ a.ruleLabel }}</strong>
                    <span class="text-muted">· {{ a.actor }} · {{ a.count }} événement(s)</span>
                  </div>
                  <p class="ai-item-sentence">@if (a.aiSentence) { <span class="ai-mini"><i class="fas fa-robot"></i> IA</span> } {{ a.sentence }}</p>
                  <div class="text-muted text-small">Du {{ a.firstAt | date:'dd/MM HH:mm' }} au {{ a.lastAt | date:'dd/MM HH:mm' }}</div>
                </article>
              }
            </div>
          }
        }
      </app-ai-card>
    }`
})
export class AiAuditAnomaliesComponent implements OnInit {
  private api = inject(AiAssistService);

  /** {@code null} : toute l'activité (administrateur) ; sinon l'équipe visée. */
  @Input() teamId: number | null = null;

  feature: AiFeatureState | null = null;
  data: AiAnomalies | null = null;
  days = 7;
  loading = false;
  error = '';
  ranAt: Date | null = null;

  ngOnInit(): void {
    this.api.capabilities().subscribe(c => this.feature = c ? c.auditAnomalies : null);
  }

  run(withAi: boolean): void {
    this.loading = true;
    this.error = '';
    const call = withAi ? this.api.explainAnomalies(this.teamId, this.days) : this.api.anomalies(this.teamId, this.days);
    call.subscribe({
      next: r => { this.data = r; this.ranAt = new Date(); this.loading = false; },
      error: e => { this.error = aiErrorMessage(e); this.loading = false; }
    });
  }

  get subtitle(): string {
    return this.teamId === null ? "Toute l'activité (administrateur)" : "Activité des membres de l'équipe";
  }

  sevLabel(s: string): string { return s === 'HIGH' ? 'Haute' : s === 'MEDIUM' ? 'Moyenne' : 'Faible'; }
}
