import { ChangeDetectionStrategy, Component, OnInit, inject } from '@angular/core';
import { DatePipe, DecimalPipe } from '@angular/common';
import { RouterLink } from '@angular/router';
import { ChartConfiguration } from 'chart.js';
import { NgChartsModule } from 'ng2-charts';
import { AuthService } from '../../core/services/auth.service';
import { SupervisionService } from '../../core/services/supervision.service';
import { CHART_PALETTE } from '../../shared/chart.util';
import {
  CamundaOverview, Correlation, HostsOverview,
  LogStats, MeaningEntry, SeriesLine, SeriesResponse, SupPoint, SupervisionMetric, TopErrors
} from '../../core/models/supervision.models';
import { MonitoringLinksComponent } from '../infrastructure/monitoring-links.component';
import { CamundaPanelComponent } from './camunda-panel.component';
import { GrafanaPanelComponent } from './grafana-panel.component';
import { LogsViewerComponent } from './logs-viewer.component';

export type SupTab = 'overview' | 'metrics' | 'logs' | 'camunda' | 'grafana';

/** Un bloc de données qui se charge indépendamment des autres (une panne de Loki ne bloque pas Prometheus). */
interface Section<T> { loading: boolean; data: T | null; error: string; }
const empty = <T>(): Section<T> => ({ loading: false, data: null, error: '' });

/**
 * Supervision pour tous les utilisateurs connectés, en LECTURE SEULE : machines et métriques (Prometheus), logs et leur
 * signification (Loki), rapprochement avec les incidents HELIOS, vue de Camunda et dashboards Grafana. Chaque onglet ne
 * charge ses données qu'à son ouverture : les requêtes de logs sont lentes (quelques secondes). Les boutons directs
 * vers les outils ne sont proposés qu'aux administrateurs.
 */
@Component({
  selector: 'app-supervision',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.Eager,
  templateUrl: './supervision.component.html',
  imports: [NgChartsModule, DecimalPipe, DatePipe, RouterLink, MonitoringLinksComponent, CamundaPanelComponent, GrafanaPanelComponent, LogsViewerComponent]
})
export class SupervisionComponent implements OnInit {
  private api = inject(SupervisionService);
  readonly auth = inject(AuthService);

  readonly tabs: { id: SupTab; label: string; icon: string }[] = [
    { id: 'overview', label: "Vue d'ensemble", icon: 'fa-gauge-high' },
    { id: 'metrics', label: 'Métriques', icon: 'fa-chart-line' },
    { id: 'logs', label: 'Logs', icon: 'fa-file-lines' },
    { id: 'camunda', label: 'Camunda', icon: 'fa-diagram-project' },
    { id: 'grafana', label: 'Grafana', icon: 'fa-chart-area' }
  ];
  readonly logPeriods = [15, 60, 180];
  readonly metricPeriods = [15, 60, 180, 360, 1440];
  readonly metrics: { id: SupervisionMetric; label: string }[] = [
    { id: 'CPU', label: 'CPU' }, { id: 'MEMORY', label: 'Mémoire' }, { id: 'DISK', label: 'Disque' }
  ];

  tab: SupTab = 'overview';
  logMinutes = 60;
  metricMinutes = 60;
  metric: SupervisionMetric = 'CPU';

  hosts = empty<HostsOverview>();
  correlation = empty<Correlation>();
  series = empty<SeriesResponse>();
  logs = empty<LogStats>();
  topErrors = empty<TopErrors>();
  meanings = empty<MeaningEntry[]>();
  camunda = empty<CamundaOverview>();

  metricChart: ChartConfiguration<'line'>['data'] | null = null;
  correlationChart: ChartConfiguration<'line'>['data'] | null = null;
  readonly lineOptions: ChartConfiguration<'line'>['options'] = {
    responsive: true, maintainAspectRatio: false, animation: false,
    plugins: { legend: { position: 'bottom' } },
    scales: { y: { beginAtZero: true } }
  };
  readonly correlationOptions: ChartConfiguration<'line'>['options'] = {
    responsive: true, maintainAspectRatio: false, animation: false,
    plugins: { legend: { position: 'bottom' } },
    scales: {
      y: { beginAtZero: true, position: 'left', title: { display: true, text: "Logs d'erreur" } },
      y1: { beginAtZero: true, max: 100, position: 'right', grid: { drawOnChartArea: false }, title: { display: true, text: 'CPU max (%)' } }
    }
  };

  ngOnInit(): void { this.open('overview'); }

  get isAdmin(): boolean { return this.auth.isAtLeastAdmin(); }

  // ── Navigation ─────────────────────────────────────────────────────────────

  open(tab: SupTab): void {
    this.tab = tab;
    this.load(tab, false);
  }

  /** Charge un onglet ; {@code force} relance les requêtes même si des données sont déjà là (bouton Actualiser). */
  load(tab: SupTab, force: boolean): void {
    switch (tab) {
      case 'overview':
        if (force || !this.hosts.data) { this.fetch(this.hosts, this.api.hosts()); }
        if (force || !this.camunda.data) { this.fetch(this.camunda, this.api.camundaOverview()); }
        if (force || !this.correlation.data) { this.fetch(this.correlation, this.api.correlation(this.logMinutes), c => this.buildCorrelation(c)); }
        break;
      case 'metrics':
        if (force || !this.series.data) { this.loadSeries(); }
        break;
      case 'logs':
        if (force || !this.logs.data) { this.loadLogs(); }
        break;
      case 'camunda':
      case 'grafana':
        break;   // panneaux autonomes : ils chargent leurs données eux-mêmes
    }
  }

  /** Actualise l'onglet courant ; les panneaux Camunda et Grafana ont leur propre bouton. */
  refresh(): void { this.load(this.tab, true); }

  private fetch<T>(section: Section<T>, call: { subscribe: (o: any) => any }, after?: (d: T) => void): void {
    section.loading = true;
    section.error = '';
    call.subscribe({
      next: (d: T) => { section.data = d; section.loading = false; after?.(d); },
      error: () => { section.data = null; section.error = "Le serveur n'a pas pu répondre."; section.loading = false; }
    });
  }

  // ── Métriques ──────────────────────────────────────────────────────────────

  setMetric(m: SupervisionMetric): void { this.metric = m; this.loadSeries(); }
  setMetricMinutes(n: number): void { this.metricMinutes = n; this.loadSeries(); }

  loadSeries(): void {
    this.fetch(this.series, this.api.series(this.metric, this.metricMinutes), s => { this.metricChart = this.buildLines(s.lines); });
  }

  buildLines(lines: SeriesLine[]): ChartConfiguration<'line'>['data'] {
    const first = lines.find(l => l.points.length) || null;
    return {
      labels: (first ? first.points : []).map(p => this.clock(p.t)),
      datasets: lines.map((l, i) => ({
        label: l.name, data: l.points.map(p => p.v), borderColor: CHART_PALETTE[i % CHART_PALETTE.length],
        backgroundColor: CHART_PALETTE[i % CHART_PALETTE.length] + '22', borderWidth: 2, pointRadius: 0, tension: 0.25
      }))
    };
  }

  // ── Logs ───────────────────────────────────────────────────────────────────

  setLogMinutes(n: number): void {
    this.logMinutes = n;
    this.correlation = empty<Correlation>();
    this.loadLogs();
    if (this.tab === 'overview') { this.load('overview', false); }
  }

  loadLogs(): void {
    this.fetch(this.logs, this.api.logStats(this.logMinutes));
    this.fetch(this.topErrors, this.api.topErrors(this.logMinutes, 10));
  }

  loadMeanings(): void {
    if (!this.meanings.data && !this.meanings.loading) { this.fetch(this.meanings, this.api.meanings()); }
  }

  errorRate(g: { total: number; errors: number }): number { return g.total ? (g.errors * 100) / g.total : 0; }

  // ── Rapprochement avec les incidents ───────────────────────────────────────

  buildCorrelation(c: Correlation): void {
    const labels = c.errorSeries.map(p => this.clock(p.t));
    const cpuAt = new Map<string, number>(c.cpuSeries.map((p: SupPoint) => [this.clock(p.t), p.v]));
    this.correlationChart = {
      labels,
      datasets: [
        { label: "Logs d'erreur", data: c.errorSeries.map(p => p.v), borderColor: '#c62828', backgroundColor: '#c6282833', fill: true, borderWidth: 2, pointRadius: 0, tension: 0.25, yAxisID: 'y' },
        { label: 'CPU max (%)', data: labels.map(l => cpuAt.get(l) ?? null) as number[], borderColor: '#1565c0', borderWidth: 2, pointRadius: 0, tension: 0.25, yAxisID: 'y1' }
      ]
    };
  }

  // ── Aides d'affichage ──────────────────────────────────────────────────────

  clock(t: number): string {
    const d = new Date(t);
    return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
  }

  /** Niveau de couleur d'une jauge : vert < 70 %, orange < 90 %, rouge au-delà. */
  level(percent: number | null): string {
    if (percent == null) { return 'na'; }
    return percent >= 90 ? 'crit' : percent >= 70 ? 'warn' : 'ok';
  }

  periodLabel(minutes: number): string {
    return minutes < 60 ? `${minutes} min` : minutes % 60 === 0 ? `${minutes / 60} h` : `${minutes} min`;
  }
}
