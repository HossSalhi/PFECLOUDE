import { ChangeDetectionStrategy, Component, Input, OnChanges, OnInit, SimpleChanges, inject } from '@angular/core';
import { AiAssistService, aiErrorMessage } from '../../core/services/ai-assist.service';
import { AiCapabilities, AiTeamSummary } from '../../core/models/ai-assist.models';
import { AiAuditAnomaliesComponent } from './ai-audit-anomalies.component';
import { AiCardComponent } from './ai-card.component';
import { AiTaskAdvisorComponent } from './ai-task-advisor.component';

/**
 * Assistant IA d'UNE équipe (fiche d'équipe) : résumé d'activité, anomalies d'audit de ses membres et suggestion
 * d'assignation. Visible pour un administrateur ou pour le chef ACTIF de cette équipe seulement ; chaque appel est
 * revérifié par le Backend.
 */
@Component({
  selector: 'app-ai-team-panel',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.Eager,
  imports: [AiCardComponent, AiAuditAnomaliesComponent, AiTaskAdvisorComponent],
  template: `
    @if (visible) {
      <div class="ai-panel">
        <div class="section-title"><i class="fas fa-wand-magic-sparkles"></i> Assistant IA de l'équipe</div>
        @if (caps?.teamSummary?.enabled) {
          <app-ai-card title="Résumé d'activité de l'équipe" subtitle="Avancement, retards et répartition de la charge" icon="fa-chart-simple"
                       runLabel="Résumé IA" [loading]="loading" [done]="!!summary" [error]="error"
                       [notice]="caps?.teamSummary?.ready ? '' : (caps?.teamSummary?.message || '')" [disabled]="!caps?.teamSummary?.ready"
                       [facts]="summary?.facts || []" [narrative]="summary?.analysis || null" [generatedAt]="ranAt" (run)="run()">
            @if (summary && summary.members.length > 0) {
              <table class="org-table ai-table">
                <thead><tr><th>Membre</th><th>Total</th><th>Ouvertes</th><th>Terminées</th><th>En retard</th></tr></thead>
                <tbody>
                  @for (m of summary.members; track m.userId) {
                    <tr><td>{{ m.displayName }}</td><td>{{ m.total }}</td><td>{{ m.open }}</td><td>{{ m.done }}</td><td [class.ai-late]="m.overdue > 0">{{ m.overdue }}</td></tr>
                  }
                </tbody>
              </table>
            }
          </app-ai-card>
        }
        <app-ai-audit-anomalies [teamId]="teamId" />
        <app-ai-task-advisor [teamId]="teamId" [manual]="true" />
      </div>
    }`
})
export class AiTeamPanelComponent implements OnInit, OnChanges {
  private api = inject(AiAssistService);

  @Input({ required: true }) teamId!: number;

  caps: AiCapabilities | null = null;
  summary: AiTeamSummary | null = null;
  loading = false;
  error = '';
  ranAt: Date | null = null;

  ngOnInit(): void {
    this.api.capabilities().subscribe(c => this.caps = c);
  }

  ngOnChanges(changes: SimpleChanges): void {
    if (changes['teamId'] && !changes['teamId'].firstChange) { this.summary = null; this.error = ''; this.ranAt = null; }
  }

  /** Administrateur, ou chef actif de CETTE équipe, et au moins une fonction activée. */
  get visible(): boolean {
    const c = this.caps;
    if (!c || !c.allowed) { return false; }
    const mine = c.admin || c.ledTeams.some(t => t.id === this.teamId);
    return mine && (c.teamSummary.enabled || c.auditAnomalies.enabled || c.taskAssignment.enabled);
  }

  run(): void {
    this.loading = true;
    this.error = '';
    this.api.teamSummary(this.teamId).subscribe({
      next: r => { this.summary = r; this.ranAt = new Date(); this.loading = false; },
      error: e => { this.error = aiErrorMessage(e); this.loading = false; }
    });
  }
}
