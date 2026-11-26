import { Component, Input, OnChanges } from '@angular/core';
import { ReportContent, ReportMetric } from '../../core/models/org.models';
import { buildChartViews, ChartView, formatNumber } from '../../shared/chart.util';
import { formatDate, toLocalIso } from '../../shared/date.util';

/**
 * Affichage d'un rapport (indicateurs, comparaison avec la période précédente, graphiques, tableaux).
 * Réutilisé par la consultation d'un rapport enregistré et par les statistiques calculées à la demande.
 */
@Component({
    selector: 'app-report-view',
    templateUrl: './report-view.component.html',
    standalone: false
})
export class ReportViewComponent implements OnChanges {

  @Input() content: ReportContent | null = null;

  charts: ChartView[] = [];

  ngOnChanges(): void {
    this.charts = buildChartViews(this.content ? this.content.charts : []);
  }

  num(value: number | null | undefined): string { return formatNumber(value); }

  day(iso: string | null | undefined): string { return formatDate(iso); }

  /** Dernier jour inclus de la période (la borne de fin du Backend est exclusive). */
  lastDay(isoEnd: string): string {
    const d = new Date(isoEnd);
    if (isNaN(d.getTime())) { return isoEnd; }
    d.setMilliseconds(d.getMilliseconds() - 1);
    return formatDate(toLocalIso(d));
  }

  hasComparison(m: ReportMetric): boolean { return m.previous !== undefined && m.previous !== null; }

  /** « ▲ +12 (+8,3 %) » — variation par rapport à la période précédente (neutre : aucune valeur n'est jugée bonne ou mauvaise). */
  deltaText(m: ReportMetric): string {
    if (m.delta === undefined || m.delta === null) { return ''; }
    const arrow = m.delta > 0 ? '▲' : m.delta < 0 ? '▼' : '•';
    const sign = m.delta > 0 ? '+' : '';
    const pct = m.deltaPercent === undefined || m.deltaPercent === null ? '' : ` (${m.deltaPercent > 0 ? '+' : ''}${formatNumber(m.deltaPercent)} %)`;
    return `${arrow} ${sign}${formatNumber(m.delta)}${pct}`;
  }

  trackByIndex(index: number): number { return index; }
}
