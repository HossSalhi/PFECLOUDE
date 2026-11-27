import { ChangeDetectionStrategy, Component, OnInit, inject } from '@angular/core';
import { ActivatedRoute, RouterLink } from '@angular/router';
import { SelfHealingService } from '../../core/services/self-healing.service';
import {
  ActionLogs, CamundaTrace, IncidentAlerts, LogSignals, SelfHealingItem, SelfHealingListing, SelfHealingTrace, ToolAlerts, TraceAction
} from '../../core/models/self-healing.models';
import { errorMessage, formatDateTime, toLocalIso } from '../../shared/date.util';

/** Un bloc qui se charge seul : une panne de Loki n'empêche pas d'afficher la chronologie. */
interface Block<T> { loading: boolean; data: T | null; error: string; }

/**
 * Onglet « Auto-remédiation » de l'Audit-trail (administrateurs : AUDIT_VIEW_ALL). Vue DÉRIVÉE et en lecture seule du
 * cycle de self-healing : transitions de l'incident, actions de remédiation, vérifications, relevés pris sur la VM et,
 * à la demande, les vrais logs de la VM ou du pod autour de l'exécution de chaque action. Rien n'est écrit, ni dans
 * l'audit ni sur les VM.
 */
@Component({
  selector: 'app-self-healing-trace',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.Eager,
  templateUrl: './self-healing-trace.component.html',
  imports: [RouterLink]
})
export class SelfHealingTraceComponent implements OnInit {
  private api = inject(SelfHealingService);
  private route = inject(ActivatedRoute);

  readonly periods = [{ label: '24 h', hours: 24 }, { label: '7 jours', hours: 168 }, { label: '31 jours', hours: 744 }];
  hours = 24;

  list: Block<SelfHealingListing> = { loading: false, data: null, error: '' };
  trace: Block<SelfHealingTrace> = { loading: false, data: null, error: '' };
  selectedId: number | null = null;
  logs = new Map<number, Block<ActionLogs>>();
  openLogs = new Set<number>();

  // Sections chargées à la demande : chacune interroge un outil différent (Prometheus, Loki, Camunda).
  alerts: Block<IncidentAlerts> = { loading: false, data: null, error: '' };
  signals: Block<LogSignals> = { loading: false, data: null, error: '' };
  camundaTrace: Block<CamundaTrace> = { loading: false, data: null, error: '' };

  readonly fmt = formatDateTime;

  ngOnInit(): void {
    const id = Number(this.route.snapshot.queryParamMap?.get('incident'));
    this.loadList();
    if (id > 0) { this.open(id); }
  }

  setPeriod(hours: number): void { this.hours = hours; this.loadList(); }

  loadList(): void {
    const to = new Date();
    const from = new Date(to.getTime() - this.hours * 3600_000);
    this.list = { loading: true, data: null, error: '' };
    this.api.list(toLocalIso(from), toLocalIso(to), 50).subscribe({
      next: d => this.list = { loading: false, data: d, error: '' },
      error: e => this.list = { loading: false, data: null, error: errorMessage(e, 'Impossible de charger les incidents.') }
    });
  }

  open(item: SelfHealingItem | number): void {
    const id = typeof item === 'number' ? item : item.incidentId;
    this.selectedId = id;
    this.logs = new Map();
    this.openLogs = new Set();
    this.alerts = { loading: false, data: null, error: '' };
    this.signals = { loading: false, data: null, error: '' };
    this.camundaTrace = { loading: false, data: null, error: '' };
    this.trace = { loading: true, data: null, error: '' };
    this.api.trace(id).subscribe({
      next: d => this.trace = { loading: false, data: d, error: '' },
      error: e => this.trace = { loading: false, data: null, error: errorMessage(e, 'Impossible de charger la trace.') }
    });
  }

  close(): void { this.selectedId = null; this.trace = { loading: false, data: null, error: '' }; }

  /** Affiche ou masque les logs d'une action ; ils ne sont demandés à Loki qu'à la première ouverture. */
  toggleLogs(action: TraceAction): void {
    if (this.openLogs.has(action.id)) { this.openLogs.delete(action.id); return; }
    this.openLogs.add(action.id);
    if (this.logs.has(action.id) || this.selectedId == null) { return; }
    const block: Block<ActionLogs> = { loading: true, data: null, error: '' };
    this.logs.set(action.id, block);
    this.api.logs(this.selectedId, action.id).subscribe({
      next: d => { block.loading = false; block.data = d; },
      error: e => { block.loading = false; block.error = errorMessage(e, 'Impossible de charger les logs.'); }
    });
  }

  loadAlerts(): void {
    if (this.selectedId == null) { return; }
    this.alerts = { loading: true, data: null, error: '' };
    this.api.alerts(this.selectedId).subscribe({
      next: d => this.alerts = { loading: false, data: d, error: '' },
      error: e => this.alerts = { loading: false, data: null, error: errorMessage(e, 'Impossible de charger les alertes.') }
    });
  }

  loadSignals(): void {
    if (this.selectedId == null) { return; }
    this.signals = { loading: true, data: null, error: '' };
    this.api.logSignals(this.selectedId).subscribe({
      next: d => this.signals = { loading: false, data: d, error: '' },
      error: e => this.signals = { loading: false, data: null, error: errorMessage(e, 'Impossible de charger les signaux de logs.') }
    });
  }

  loadCamunda(): void {
    if (this.selectedId == null) { return; }
    this.camundaTrace = { loading: true, data: null, error: '' };
    this.api.camunda(this.selectedId).subscribe({
      next: d => this.camundaTrace = { loading: false, data: d, error: '' },
      error: e => this.camundaTrace = { loading: false, data: null, error: errorMessage(e, 'Impossible de charger la trace Camunda.') }
    });
  }

  /** Les trois sources d'alertes à afficher, dans l'ordre. */
  sources(a: IncidentAlerts): ToolAlerts[] { return [a.prometheus, a.alertmanager, a.grafana]; }

  statusLabel(status: string): string {
    return status === 'OK' ? 'Disponible' : status === 'NOT_CONFIGURED' ? 'Non configuré' : 'Indisponible';
  }

  duration(ms: number | null): string {
    if (ms == null) { return '—'; }
    if (ms < 1000) { return `${ms} ms`; }
    const s = Math.round(ms / 1000);
    return s < 60 ? `${s} s` : `${Math.floor(s / 60)} min ${s % 60} s`;
  }

  logsOf(action: TraceAction): Block<ActionLogs> | undefined { return this.logs.get(action.id); }
  isOpen(action: TraceAction): boolean { return this.openLogs.has(action.id); }

  kindIcon(kind: string): string {
    return { INCIDENT: 'fa-triangle-exclamation', HISTORY: 'fa-clock-rotate-left', ACTION: 'fa-screwdriver-wrench',
      VERIFICATION: 'fa-clipboard-check', EVIDENCE: 'fa-terminal' }[kind] ?? 'fa-circle';
  }
}
