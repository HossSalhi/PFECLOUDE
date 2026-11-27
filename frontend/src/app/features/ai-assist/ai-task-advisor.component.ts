import { ChangeDetectionStrategy, Component, EventEmitter, Input, OnInit, Output, inject } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { AiAssistService, aiErrorMessage } from '../../core/services/ai-assist.service';
import { AiAssignment, AiCandidate, AiFeatureState } from '../../core/models/ai-assist.models';
import { AiCardComponent } from './ai-card.component';

/**
 * (a) Suggestion d'assignation d'une tâche : classement DÉTERMINISTE des membres de l'équipe + justification IA. L'humain
 * choisit ou ignore ; rien n'est assigné ici. Le choix est tracé (suggestion suivie ou non) pour mesurer le taux d'acceptation.
 * Deux usages : intégré au formulaire de tâche (le titre vient du formulaire) ou autonome (saisie du titre, mode {@code manual}).
 */
@Component({
  selector: 'app-ai-task-advisor',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.Eager,
  imports: [AiCardComponent, FormsModule],
  template: `
    @if (feature?.enabled && teamId !== null) {
      <app-ai-card title="Qui assigner ?" subtitle="Classement des membres selon la charge, les retards et l'historique" icon="fa-user-check"
                   runLabel="Suggérer un responsable" [loading]="loading" [done]="!!data" [error]="error"
                   [notice]="feature?.ready ? '' : (feature?.message || '')" [disabled]="!feature?.ready || !canRun"
                   [facts]="data?.facts || []" [narrative]="data?.analysis || null" [generatedAt]="ranAt" (run)="run()">
        @if (manual) {
          <div class="ai-options">
            <label style="flex:1">Titre de la tâche
              <input class="form-input" type="text" maxlength="200" [(ngModel)]="manualTitle" placeholder="Ex. Remplacer le switch du rack 4" aria-label="Titre de la tâche">
            </label>
          </div>
        } @else if (!title.trim()) {
          <div class="text-muted text-small">Saisissez d'abord le titre de la tâche pour obtenir une suggestion.</div>
        }
        @if (data && data.candidates.length === 0) {
          <div class="alert-warning">Aucun membre actif dans cette équipe : ajoutez des membres avant d'assigner une tâche.</div>
        }
        @if (data && data.candidates.length > 0 && !decided) {
          <ol class="ai-candidates">
            @for (c of data.candidates; track c.userId; let i = $index) {
              <li class="ai-cand" [class.is-top]="c.userId === data.recommendedUserId">
                <div class="ai-cand-head">
                  <span class="ai-rank">{{ i + 1 }}</span>
                  <strong>{{ c.displayName }}</strong>
                  @if (c.userId === data.recommendedUserId) { <span class="ai-reco"><i class="fas fa-star"></i> Recommandé</span> }
                  <span class="ai-score" [attr.data-level]="level(c)">{{ c.score }}<small>/100</small></span>
                </div>
                <div class="ai-bar"><span [style.width.%]="c.score" [attr.data-level]="level(c)"></span></div>
                <div class="text-muted text-small">{{ c.reasons.join(' · ') }}</div>
                <button type="button" class="btn-secondary" (click)="choose(c)"><i class="fas fa-check"></i> Choisir {{ c.displayName }}</button>
              </li>
            }
          </ol>
          <button type="button" class="btn-link" (click)="ignore()"><i class="fas fa-xmark"></i> Ignorer la suggestion</button>
        }
        @if (decided) {
          <div class="alert-success" role="status"><i class="fas fa-circle-check"></i> {{ decided }}</div>
        }
      </app-ai-card>
    }`
})
export class AiTaskAdvisorComponent implements OnInit {
  private api = inject(AiAssistService);

  @Input() teamId: number | null = null;
  @Input() title = '';
  @Input() description: string | null = null;
  @Input() priority: string | null = null;
  /** Mode autonome : le composant propose sa propre zone de saisie du titre. */
  @Input() manual = false;
  /** Émis quand l'humain choisit un membre : le formulaire parent peut alors le sélectionner. */
  @Output() chosen = new EventEmitter<number>();

  feature: AiFeatureState | null = null;
  data: AiAssignment | null = null;
  manualTitle = '';
  loading = false;
  error = '';
  decided = '';
  ranAt: Date | null = null;

  ngOnInit(): void {
    this.api.capabilities().subscribe(c => this.feature = c ? c.taskAssignment : null);
  }

  private get subject(): string { return (this.manual ? this.manualTitle : this.title).trim(); }

  get canRun(): boolean { return this.subject.length > 0; }

  run(): void {
    if (this.teamId === null || !this.canRun) { return; }
    this.loading = true;
    this.error = '';
    this.decided = '';
    this.api.recommendAssignment({ teamId: this.teamId, title: this.subject, description: this.description, priority: this.priority }).subscribe({
      next: r => { this.data = r; this.ranAt = new Date(); this.loading = false; },
      error: e => { this.error = aiErrorMessage(e); this.loading = false; }
    });
  }

  /** Choix humain : « accepté » seulement si c'est le membre recommandé ; tracé dans le journal d'audit. */
  choose(c: AiCandidate): void {
    if (this.teamId === null || !this.data) { return; }
    const accepted = c.userId === this.data.recommendedUserId;
    this.api.assignmentFeedback(this.teamId, c.userId, accepted).subscribe({ error: () => { /* la trace est facultative */ } });
    this.decided = accepted ? `Suggestion retenue : ${c.displayName}.` : `${c.displayName} choisi à la place du membre recommandé.`;
    this.chosen.emit(c.userId);
  }

  ignore(): void {
    if (this.teamId !== null && this.data && this.data.recommendedUserId !== null) {
      this.api.assignmentFeedback(this.teamId, this.data.recommendedUserId, false).subscribe({ error: () => { /* facultatif */ } });
    }
    this.decided = 'Suggestion ignorée.';
  }

  level(c: AiCandidate): string { return c.score >= 70 ? 'good' : c.score >= 40 ? 'warn' : 'bad'; }
}
