import { DatePipe, DecimalPipe } from '@angular/common';
import { ChangeDetectionStrategy, Component, OnInit, inject } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { AiAssistService, aiErrorMessage } from '../../core/services/ai-assist.service';
import {
  AiCapabilities, AiFeatureState, TelemetryAnalysis, TelemetryFinding, TelemetryQuery, TelemetryRunSummary, TelemetryScope, TelemetryVerdict
} from '../../core/models/ai-assist.models';
import { AiCardComponent } from './ai-card.component';

/**
 * (e) Analyse IA des logs (Loki) et des métriques (Prometheus), avec HISTORIQUE TRAÇABLE. Les anomalies sont détectées par
 * des règles déterministes côté serveur ; l'IA ne rédige que la synthèse, à la demande. Chaque analyse est enregistrée
 * (qui, quand, période, périmètre, constats, texte IA) et peut être rouverte à l'identique. Un chef d'équipe n'analyse que
 * le périmètre de sa spécialité ; un administrateur choisit le périmètre.
 */
@Component({
  selector: 'app-ai-telemetry',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.Eager,
  imports: [AiCardComponent, FormsModule, DatePipe, DecimalPipe],
  template: `
    @if (feature?.enabled) {
      <app-ai-card title="Analyse IA des logs et métriques" subtitle="Détection d'anomalies sur l'historique, enregistrée et traçable" icon="fa-magnifying-glass-chart"
                   runLabel="Analyser" [loading]="loading" [done]="!!current" [error]="error" [notice]="feature?.ready ? '' : (feature?.message || '')"
                   [disabled]="!feature?.ready || (!caps?.admin && teamId === null)" [facts]="current?.facts || []" [narrative]="current?.analysis || null"
                   [generatedAt]="generatedAt" (run)="analyse()">
        <div class="ai-options">
          @if (caps?.admin) {
            <label>Périmètre
              <select class="form-input" [(ngModel)]="scope" aria-label="Périmètre">
                @for (s of scopes; track s.id) { <option [ngValue]="s.id">{{ s.label }}</option> }
              </select>
            </label>
          } @else if (caps && caps.ledTeams.length > 0) {
            <label>Équipe
              <select class="form-input" [(ngModel)]="teamId" aria-label="Équipe">
                @for (t of caps.ledTeams; track t.id) { <option [ngValue]="t.id">{{ t.name }}</option> }
              </select>
            </label>
          }
          <label>Période analysée
            <select class="form-input" [(ngModel)]="minutes" aria-label="Période analysée">
              @for (p of periods; track p.minutes) { <option [ngValue]="p.minutes">{{ p.label }}</option> }
            </select>
          </label>
        </div>
        <div class="text-muted text-small">Métriques : 7 jours d'historique au plus (rétention Prometheus). Logs : 24 h au plus, comparés aux 12 h précédentes.
          @if (!caps?.admin) { Le périmètre est celui de la spécialité de votre équipe. }</div>

        @if (current; as a) {
          <div class="ai-verdict" [attr.data-verdict]="a.verdict" role="status">
            <i class="fas" [class]="verdictIcon(a.verdict)"></i>
            <div>
              <strong>{{ verdictText(a) }}</strong>
              <div class="text-small">Analyse #{{ a.id }} · {{ a.requestedAt | date:'dd/MM/yyyy HH:mm' }} · par {{ a.requestedBy || '—' }} · période du
                {{ a.windowStart | date:'dd/MM HH:mm' }} au {{ a.windowEnd | date:'dd/MM HH:mm' }} · périmètre {{ scopeLabel(a.scope) }}
                @if (a.teamName) { · équipe {{ a.teamName }} } · calculée en {{ a.durationMs | number }} ms</div>
              @if (a.verdict === 'ANOMALIES' && a.dataQuality !== 'COMPLETE') {
                <div class="ai-warn"><i class="fas fa-triangle-exclamation"></i> Données {{ a.dataQuality === 'PARTIAL' ? 'partielles' : 'insuffisantes' }} : d'autres anomalies ont pu passer inaperçues.</div>
              }
            </div>
          </div>

          @if (failedQueries(a).length > 0) {
            <div class="ai-failed" role="alert"><strong><i class="fas fa-circle-xmark"></i> {{ failedQueries(a).length }} requête(s) en échec</strong>
              <ul>
                @for (q of failedQueries(a); track q.id) {
                  <li><strong>{{ q.source }}</strong> ({{ q.id }}) — {{ q.reason }}<details class="ai-evidence"><summary>Requête</summary><code>{{ q.query }}</code></details></li>
                }
              </ul>
            </div>
          }

          @if (a.findings.length > 0) {
            <div style="overflow-x:auto">
              <table class="org-table ai-table">
                <thead><tr><th>Gravité</th><th>Côté</th><th>Entité</th><th>Signal</th><th>Règle</th><th>Période</th><th>Mesuré</th><th>Référence</th><th>Détail</th></tr></thead>
                <tbody>
                  @for (f of a.findings; track $index) {
                    <tr>
                      <td><span class="ai-sev" [attr.data-sev]="f.severity">{{ sevLabel(f.severity) }}</span></td>
                      <td><span class="ai-side" [attr.data-side]="sideOf(f.scope)">{{ sideOf(f.scope) === 'APPLICATION' ? 'Application' : 'Infrastructure' }}</span></td>
                      <td><strong>{{ f.entity }}</strong><div class="text-muted text-small">{{ scopeLabel(f.scope) }}</div></td>
                      <td>{{ signalLabel(f.signal) }}</td>
                      <td>{{ ruleLabel(f.rule) }}</td>
                      <td class="text-small">{{ f.start | date:'dd/MM HH:mm' }} → {{ f.end | date:'HH:mm' }}</td>
                      <td>{{ valueOf(f, f.observed) }}</td>
                      <td>{{ f.baseline === null ? '—' : valueOf(f, f.baseline) }}</td>
                      <td>{{ f.detail }}<details class="ai-evidence"><summary>Requête utilisée</summary><code>{{ f.evidence }}</code></details></td>
                    </tr>
                  }
                </tbody>
              </table>
            </div>
          }

          <div class="section-title" style="margin-top:14px">Qualité des données</div>
          <ul class="ai-quality">
            @for (q of a.quality; track $index) {
              <li [class.is-off]="!q.available"><i class="fas" [class]="q.available ? 'fa-circle-check' : 'fa-circle-xmark'"></i>
                <strong>{{ q.source }}</strong> — {{ q.available ? ('couverture ' + (q.coverage * 100 | number:'1.0-0') + ' %') : 'indisponible' }}. {{ q.note }}</li>
            }
          </ul>

          <details class="ai-trace">
            <summary>Traçabilité : requêtes, périodes, seuils, couverture</summary>
            <dl>
              <dt>Règles</dt><dd>{{ a.trace.rulesVersion }}</dd>
              <dt>Période analysée</dt><dd>{{ a.trace.windowStart | date:'dd/MM/yyyy HH:mm' }} → {{ a.trace.windowEnd | date:'dd/MM/yyyy HH:mm' }}</dd>
              <dt>Période de référence</dt><dd>{{ a.trace.baselineStart ? ((a.trace.baselineStart | date:'dd/MM/yyyy HH:mm') + ' → ' + (a.trace.baselineEnd | date:'dd/MM/yyyy HH:mm')) : 'aucune (fenêtre trop longue)' }}</dd>
              <dt>Pas des séries</dt><dd>métriques {{ a.trace.metricsStepSeconds ?? '—' }} s · logs {{ a.trace.logsStepSeconds ?? '—' }} s</dd>
            </dl>
            <div class="text-small"><strong>Requêtes exécutées</strong></div>
            <ul>
              @for (q of a.trace.queries; track q.id) {
                <li><strong>{{ q.id }}</strong> — {{ q.status === 'OK' ? 'OK' : 'ÉCHEC : ' + q.reason }} · pas {{ q.stepSeconds ?? '—' }} s · {{ q.series ?? '—' }} série(s), {{ q.points ?? '—' }} point(s)<br><code>{{ q.query }}</code></li>
              }
            </ul>
            <div class="text-small"><strong>Seuils</strong></div>
            <ul>@for (t of thresholdList(a); track t[0]) { <li>{{ t[0] }} : {{ t[1] }}</li> }</ul>
            <div class="text-small"><strong>Couverture par entité</strong></div>
            <ul>
              @for (c of a.trace.coverage; track $index) {
                <li [class.ai-low]="c.coverage < 0.8">{{ c.source }} · {{ c.entity }} · {{ c.signal }} : {{ c.coverage * 100 | number:'1.0-0' }} % — {{ c.note }}</li>
              }
            </ul>
          </details>
        }
      </app-ai-card>

      <div class="ai-card ai-history">
        <div class="ai-head">
          <div><div class="ai-title"><i class="fas fa-clock-rotate-left"></i> Historique des analyses</div>
            <div class="ai-sub">Chaque analyse est conservée : qui l'a lancée, quand, avec quel résultat.</div></div>
          <button type="button" class="btn-secondary" (click)="loadHistory(true)" [disabled]="historyLoading"><i class="fas fa-rotate-right"></i> Actualiser</button>
        </div>
        @if (historyError) { <div class="alert-error" role="alert">{{ historyError }}</div> }
        @if (history.length === 0 && !historyLoading && !historyError) { <p class="text-muted">Aucune analyse enregistrée pour le moment.</p> }
        @if (history.length > 0) {
          <div style="overflow-x:auto">
            <table class="org-table ai-table">
              <thead><tr><th>#</th><th>Date</th><th>Par</th><th>Périmètre</th><th>Période</th><th>Verdict</th><th>Anomalies</th><th></th></tr></thead>
              <tbody>
                @for (h of history; track h.id) {
                  <tr [class.is-open]="current?.id === h.id">
                    <td>{{ h.id }}</td>
                    <td>{{ h.requestedAt | date:'dd/MM/yyyy HH:mm' }}</td>
                    <td>{{ h.requestedBy || '—' }}</td>
                    <td>{{ scopeLabel(h.scope) }}@if (h.teamName) { <div class="text-muted text-small">{{ h.teamName }}</div> }</td>
                    <td>{{ periodLabel(h.minutes) }}</td>
                    <td><span class="ai-verdict-pill" [attr.data-verdict]="h.verdict">{{ verdictShort(h.verdict) }}</span></td>
                    <td>{{ h.anomalyCount }}@if (h.highCount > 0) { <span class="ai-sev" data-sev="HIGH" style="margin-left:6px">{{ h.highCount }} haute(s)</span> }</td>
                    <td><button type="button" class="btn-secondary" (click)="open(h)" [disabled]="opening === h.id">Ouvrir</button></td>
                  </tr>
                }
              </tbody>
            </table>
          </div>
          @if (!lastPage) { <button type="button" class="btn-link" (click)="more()" [disabled]="historyLoading">Afficher les analyses plus anciennes</button> }
        }
      </div>
    }`
})
export class AiTelemetryComponent implements OnInit {
  private api = inject(AiAssistService);

  readonly scopes: { id: TelemetryScope; label: string }[] = [
    { id: 'ALL', label: 'Tout' }, { id: 'VM', label: 'Machines virtuelles' }, { id: 'CLUSTER', label: 'Cluster Kubernetes' },
    { id: 'APPLICATION', label: 'Applications' }, { id: 'SERVICE', label: 'Services de supervision' }
  ];
  readonly periods = [
    { minutes: 60, label: '1 heure' }, { minutes: 360, label: '6 heures' }, { minutes: 1440, label: '24 heures' },
    { minutes: 4320, label: '3 jours' }, { minutes: 10080, label: '7 jours' }
  ];

  caps: AiCapabilities | null = null;
  feature: AiFeatureState | null = null;
  scope: TelemetryScope = 'ALL';
  teamId: number | null = null;
  minutes = 60;

  current: TelemetryAnalysis | null = null;
  generatedAt: Date | null = null;
  loading = false;
  error = '';

  history: TelemetryRunSummary[] = [];
  historyLoading = false;
  historyError = '';
  lastPage = true;
  private page = 0;
  opening: number | null = null;

  ngOnInit(): void {
    this.api.capabilities().subscribe(c => {
      this.caps = c;
      this.feature = c ? c.telemetryAnalysis : null;
      this.teamId = c && !c.admin && c.ledTeams.length > 0 ? c.ledTeams[0].id : null;
      if (this.feature?.enabled) { this.loadHistory(true); }
    });
  }

  analyse(): void {
    this.loading = true;
    this.error = '';
    this.api.analyseTelemetry({ teamId: this.caps?.admin ? null : this.teamId, scope: this.caps?.admin ? this.scope : null, minutes: this.minutes }).subscribe({
      next: a => { this.current = a; this.generatedAt = new Date(); this.loading = false; this.loadHistory(true); },
      error: e => { this.error = aiErrorMessage(e); this.loading = false; }
    });
  }

  loadHistory(reset: boolean): void {
    if (reset) { this.page = 0; }
    this.historyLoading = true;
    this.historyError = '';
    this.api.telemetryHistory(null, this.page, 10).subscribe({
      next: p => {
        this.history = reset ? p.content : [...this.history, ...p.content];
        this.lastPage = p.last;
        this.historyLoading = false;
      },
      error: e => { this.historyError = aiErrorMessage(e); this.historyLoading = false; }
    });
  }

  more(): void { this.page += 1; this.loadHistory(false); }

  /** Rouvre une analyse enregistrée, à l'identique (les constats ne sont pas recalculés). */
  open(h: TelemetryRunSummary): void {
    this.opening = h.id;
    this.error = '';
    this.api.telemetryAnalysis(h.id).subscribe({
      next: a => { this.current = a; this.generatedAt = new Date(a.requestedAt); this.opening = null; },
      error: e => { this.error = aiErrorMessage(e); this.opening = null; }
    });
  }

  verdictText(a: TelemetryAnalysis): string {
    return a.verdict === 'NO_ANOMALY' ? 'Aucune anomalie détectée'
      : a.verdict === 'ANOMALIES' ? `${a.anomalyCount} anomalie(s) détectée(s)${a.highCount > 0 ? ' dont ' + a.highCount + ' de gravité haute' : ''}`
      : a.verdict === 'PARTIAL_COVERAGE' ? 'Couverture partielle : aucune anomalie vue, mais des données manquent'
      : 'Données insuffisantes pour conclure';
  }

  failedQueries(a: TelemetryAnalysis): TelemetryQuery[] { return (a.trace?.queries ?? []).filter(q => q.status === 'FAILED'); }

  thresholdList(a: TelemetryAnalysis): [string, string][] { return Object.entries(a.trace?.thresholds ?? {}); }

  verdictShort(v: TelemetryVerdict): string {
    return v === 'NO_ANOMALY' ? 'Aucune anomalie' : v === 'ANOMALIES' ? 'Anomalies' : v === 'PARTIAL_COVERAGE' ? 'Couverture partielle' : 'Données insuffisantes';
  }

  verdictIcon(v: TelemetryVerdict): string {
    return v === 'NO_ANOMALY' ? 'fa-circle-check' : v === 'ANOMALIES' || v === 'PARTIAL_COVERAGE' ? 'fa-triangle-exclamation' : 'fa-circle-question';
  }

  sideOf(scope: string): 'APPLICATION' | 'INFRASTRUCTURE' { return scope === 'APPLICATION' ? 'APPLICATION' : 'INFRASTRUCTURE'; }

  sevLabel(s: string): string { return s === 'HIGH' ? 'Haute' : s === 'MEDIUM' ? 'Moyenne' : 'Faible'; }

  signalLabel(s: string): string {
    return ({ CPU: 'CPU', MEMORY: 'Mémoire', DISK: 'Disque', LOG_ERRORS: 'Erreurs des logs', TARGET: 'Cible de supervision', ALERT: 'Alerte Prometheus', WORKLOAD: 'Charge de travail', MONITORING: 'Outil de supervision' } as Record<string, string>)[s] ?? s;
  }

  ruleLabel(r: string): string {
    return ({
      SUSTAINED_HIGH: 'Dépassement soutenu', JUMP_VS_BASELINE: 'Hausse nette', FILLING_UP: 'Saturation prévue',
      NEW_ERRORS: 'Nouvelles erreurs', ERROR_SPIKE: "Pic d'erreurs", TARGET_DOWN: 'Cible injoignable', TARGET_UNSTABLE: 'Cible instable',
      ALERT_FIRING: 'Alerte en cours', DEPLOYMENT_UNAVAILABLE: 'Déploiement incomplet', POD_RESTARTS: 'Redémarrages', POD_NOT_RUNNING: 'Pods arrêtés',
      POD_STUCK: 'Conteneur bloqué', MONITORING_NOT_READY: 'Outil non prêt'
    } as Record<string, string>)[r] ?? r;
  }

  scopeLabel(s: string): string { return this.scopes.find(x => x.id === s)?.label ?? s; }

  periodLabel(minutes: number): string {
    return this.periods.find(p => p.minutes === minutes)?.label ?? `${minutes} min`;
  }

  valueOf(f: TelemetryFinding, v: number | null): string {
    if (v === null) { return '—'; }
    return f.unit === '%' ? `${v} %` : `${v} ${f.unit ?? ''}`.trim();
  }
}
