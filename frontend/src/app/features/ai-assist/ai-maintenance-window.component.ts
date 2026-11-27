import { DatePipe } from '@angular/common';
import { ChangeDetectionStrategy, Component, OnInit, inject } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { AiAssistService, aiErrorMessage } from '../../core/services/ai-assist.service';
import { AiCapabilities, AiFeatureState, AiMaintenanceWindows } from '../../core/models/ai-assist.models';
import { AiCardComponent } from './ai-card.component';

/**
 * (d) Fenêtre de maintenance recommandée : charge réelle par heure (Prometheus) croisée avec les maintenances déjà
 * planifiées sur la ressource ; l'IA formule la recommandation. Aucune maintenance n'est créée : l'humain planifie lui-même.
 * Un chef d'équipe choisit parmi SES équipes et ne peut viser que les ressources de leur périmètre (imposé par le Backend).
 */
@Component({
  selector: 'app-ai-maintenance-window',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.Eager,
  imports: [AiCardComponent, FormsModule, DatePipe],
  template: `
    @if (feature?.enabled) {
      <app-ai-card title="Quand planifier ?" subtitle="Créneaux les moins chargés, sans conflit avec les maintenances existantes" icon="fa-calendar-check"
                   runLabel="Recommander une fenêtre" [loading]="loading" [done]="!!data" [error]="error"
                   [notice]="feature?.ready ? '' : (feature?.message || '')" [disabled]="!feature?.ready || !resourceName.trim()"
                   [facts]="data?.facts || []" [narrative]="data?.analysis || null" [generatedAt]="ranAt" (run)="run()">
        <div class="ai-options">
          @if (caps && !caps.admin && caps.ledTeams.length > 0) {
            <label>Équipe
              <select class="form-input" [(ngModel)]="teamId" aria-label="Équipe">
                @for (t of caps.ledTeams; track t.id) { <option [ngValue]="t.id">{{ t.name }}</option> }
              </select>
            </label>
          }
          <label>Type
            <select class="form-input" [(ngModel)]="resourceType" aria-label="Type de ressource">
              <option value="VM">Machine virtuelle</option><option value="NODE">Nœud</option>
              <option value="APPLICATION">Application</option><option value="SERVICE">Service</option>
            </select>
          </label>
          <label style="flex:1;min-width:160px">Ressource
            <input class="form-input" type="text" maxlength="200" [(ngModel)]="resourceName" placeholder="Ex. k8s-worker1" aria-label="Ressource">
          </label>
          <label>Durée
            <select class="form-input" [(ngModel)]="duration" aria-label="Durée">
              <option [ngValue]="60">1 h</option><option [ngValue]="120">2 h</option><option [ngValue]="240">4 h</option><option [ngValue]="480">8 h</option>
            </select>
          </label>
          <label>Horizon
            <select class="form-input" [(ngModel)]="horizon" aria-label="Horizon">
              <option [ngValue]="7">7 jours</option><option [ngValue]="14">14 jours</option><option [ngValue]="30">30 jours</option>
            </select>
          </label>
        </div>
        @if (data && data.windows.length === 0 && data.analysis.status !== 'DISABLED') {
          <div class="alert-warning">Aucun créneau libre sur l'horizon demandé : élargissez-le ou réduisez la durée.</div>
        }
        @if (data && data.windows.length > 0) {
          <ol class="ai-candidates">
            @for (w of data.windows; track w.start; let i = $index) {
              <li class="ai-cand" [class.is-top]="i === 0">
                <div class="ai-cand-head">
                  <span class="ai-rank">{{ i + 1 }}</span>
                  <strong>{{ w.start | date:'EEE dd/MM HH:mm' }} → {{ w.end | date:'HH:mm' }}</strong>
                  @if (i === 0) { <span class="ai-reco"><i class="fas fa-star"></i> Meilleur créneau</span> }
                  <span class="ai-score" [attr.data-level]="level(w.averageLoad)">{{ w.averageLoad }}<small> % charge</small></span>
                </div>
                <div class="ai-bar"><span [style.width.%]="w.averageLoad" [attr.data-level]="level(w.averageLoad)"></span></div>
                <div class="text-muted text-small">Pic {{ w.peakLoad }} %@if (w.notes.length) { · {{ w.notes.join(' · ') }} }</div>
              </li>
            }
          </ol>
          @if (!data.metricsAvailable) {
            <div class="text-muted text-small"><i class="fas fa-circle-info"></i> Prometheus étant indisponible, les créneaux reposent sur les horaires creux habituels.</div>
          }
        }
      </app-ai-card>
    }`
})
export class AiMaintenanceWindowComponent implements OnInit {
  private api = inject(AiAssistService);

  caps: AiCapabilities | null = null;
  feature: AiFeatureState | null = null;
  data: AiMaintenanceWindows | null = null;

  teamId: number | null = null;
  resourceType = 'VM';
  resourceName = '';
  duration = 120;
  horizon = 14;

  loading = false;
  error = '';
  ranAt: Date | null = null;

  ngOnInit(): void {
    this.api.capabilities().subscribe(c => {
      this.caps = c;
      this.feature = c ? c.maintenanceWindow : null;
      // un chef d'équipe vise toujours une de SES équipes ; un administrateur n'en cible aucune (toutes les ressources)
      this.teamId = c && !c.admin && c.ledTeams.length > 0 ? c.ledTeams[0].id : null;
    });
  }

  run(): void {
    this.loading = true;
    this.error = '';
    this.api.maintenanceWindows({
      teamId: this.teamId, resourceType: this.resourceType, resourceName: this.resourceName.trim(),
      durationMinutes: this.duration, horizonDays: this.horizon
    }).subscribe({
      next: r => { this.data = r; this.ranAt = new Date(); this.loading = false; },
      error: e => { this.error = aiErrorMessage(e); this.loading = false; }
    });
  }

  level(load: number): string { return load < 40 ? 'good' : load < 70 ? 'warn' : 'bad'; }
}
