import { NgClass } from '@angular/common';
import { ChangeDetectionStrategy, Component, EventEmitter, Input, Output } from '@angular/core';
import { AiFact, AiNarrative } from '../../core/models/ai-assist.models';

/**
 * Carte commune des 4 fonctions d'assistance IA : indicateurs calculés par le Backend en haut, contenu factuel au milieu
 * (projeté), puis le bloc « Analyse IA » clairement distinct (badge violet), avec fraîcheur et bouton Générer/Régénérer.
 * Même rendu, mêmes états de chargement et d'indisponibilité partout.
 */
@Component({
  selector: 'app-ai-card',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.Eager,
  imports: [NgClass],
  template: `
    <section class="ai-card" [attr.aria-busy]="loading">
      <header class="ai-head">
        <div>
          <div class="ai-title"><i class="fas" [ngClass]="icon"></i> {{ title }}</div>
          @if (subtitle) { <div class="ai-sub">{{ subtitle }}</div> }
        </div>
        <div class="ai-actions">
          @if (generatedAt) { <span class="ai-fresh" title="Date de la dernière génération"><i class="far fa-clock"></i> Généré à {{ time }}</span> }
          <button type="button" class="btn-primary ai-run" (click)="run.emit()" [disabled]="loading || disabled">
            @if (loading) { <span class="ai-spin" aria-hidden="true"></span> Analyse en cours… }
            @else { <i class="fas fa-wand-magic-sparkles"></i> {{ done ? 'Régénérer' : runLabel }} }
          </button>
        </div>
      </header>

      @if (notice) { <div class="alert-warning" role="status"><i class="fas fa-circle-info"></i> {{ notice }}</div> }
      @if (error) { <div class="alert-error" role="alert"><i class="fas fa-exclamation-circle"></i> {{ error }}</div> }

      @if (facts.length > 0) {
        <div class="ai-facts">
          @for (f of facts; track $index) {
            <div class="ai-fact" [attr.data-tone]="f.tone"><span class="ai-fact-v">{{ f.value }}</span><span class="ai-fact-l">{{ f.label }}</span></div>
          }
        </div>
      }

      <ng-content></ng-content>

      @if (narrative; as n) {
        @if (n.status === 'OK') {
          <div class="ai-analysis">
            <div class="ai-badge"><i class="fas fa-robot"></i> Analyse IA</div>
            <p class="ai-text">{{ n.text }}</p>
            <div class="ai-foot"><i class="fas fa-triangle-exclamation"></i> Suggestion générée automatiquement à partir des chiffres ci-dessus
              @if (n.model) { · modèle {{ n.model }} } · à valider par un humain : l'IA ne décide ni n'exécute rien.</div>
          </div>
        } @else if (n.status === 'NOT_CONFIGURED' || n.status === 'UNAVAILABLE') {
          <div class="ai-analysis is-off" role="status">
            <div class="ai-badge is-off"><i class="fas fa-robot"></i> Analyse IA indisponible</div>
            <p class="ai-text">{{ n.message }} Les chiffres ci-dessus restent valables.</p>
          </div>
        } @else if (n.status === 'DISABLED') {
          <div class="ai-analysis is-off" role="status"><div class="ai-badge is-off"><i class="fas fa-robot"></i> Assistance IA désactivée</div></div>
        }
      }
    </section>
  `
})
export class AiCardComponent {
  @Input() title = 'Assistant IA';
  @Input() subtitle = '';
  @Input() icon = 'fa-wand-magic-sparkles';
  @Input() runLabel = 'Générer';
  @Input() loading = false;
  @Input() done = false;
  @Input() disabled = false;
  @Input() error = '';
  @Input() notice = '';
  @Input() facts: AiFact[] = [];
  @Input() narrative: AiNarrative | null = null;
  @Input() generatedAt: Date | null = null;
  @Output() run = new EventEmitter<void>();

  get time(): string {
    const d = this.generatedAt;
    if (!d) { return ''; }
    const p = (n: number) => String(n).padStart(2, '0');
    return `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
  }
}
