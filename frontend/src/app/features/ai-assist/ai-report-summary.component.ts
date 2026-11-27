import { ChangeDetectionStrategy, Component, Input, OnChanges, OnInit, SimpleChanges, inject } from '@angular/core';
import { AiAssistService, aiErrorMessage } from '../../core/services/ai-assist.service';
import { AiFeatureState, AiReportSummary } from '../../core/models/ai-assist.models';
import { AiCardComponent } from './ai-card.component';

/** (b) Bouton « Résumé IA » d'un rapport généré : visible seulement si la fonction est activée et permise (administrateurs). */
@Component({
  selector: 'app-ai-report-summary',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.Eager,
  imports: [AiCardComponent],
  template: `
    @if (feature?.enabled) {
      <app-ai-card title="Résumé exécutif IA" subtitle="Synthèse en langage naturel des indicateurs de ce rapport" icon="fa-file-lines"
                   runLabel="Résumé IA" [loading]="loading" [done]="!!data" [error]="error" [notice]="feature?.ready ? '' : (feature?.message || '')"
                   [disabled]="!feature?.ready" [facts]="data?.facts || []" [narrative]="data?.analysis || null" [generatedAt]="ranAt"
                   (run)="run()" />
    }`
})
export class AiReportSummaryComponent implements OnInit, OnChanges {
  private api = inject(AiAssistService);

  @Input({ required: true }) reportId!: number;

  feature: AiFeatureState | null = null;
  data: AiReportSummary | null = null;
  loading = false;
  error = '';
  ranAt: Date | null = null;

  ngOnInit(): void {
    this.api.capabilities().subscribe(c => this.feature = c ? c.reportsSummary : null);
  }

  ngOnChanges(changes: SimpleChanges): void {
    if (changes['reportId'] && !changes['reportId'].firstChange) { this.data = null; this.error = ''; this.ranAt = null; }
  }

  run(): void {
    this.loading = true;
    this.error = '';
    this.api.reportSummary(this.reportId).subscribe({
      next: r => { this.data = r; this.ranAt = new Date(); this.loading = false; },
      error: e => { this.error = aiErrorMessage(e); this.loading = false; }
    });
  }
}
