import { ChartConfiguration, ChartType } from 'chart.js';
import { ReportChartDef } from '../core/models/org.models';

export interface ChartView {
  title: string;
  type: ChartType;
  data: ChartConfiguration['data'];
  options: ChartConfiguration['options'];
}

/** Palette HELIOS : orange Orange en premier, puis des teintes bien distinctes. */
export const CHART_PALETTE = ['#ff7900', '#1565c0', '#2e7d32', '#7b2ff7', '#c62828', '#00838f', '#f9a825', '#6d4c41', '#9e9e9e'];

const SUPPORTED: ReadonlyArray<string> = ['bar', 'line', 'doughnut'];

/** Transforme les graphiques d'un rapport (type, libellés, séries) en configuration chart.js prête à afficher. */
export function buildChartViews(charts: ReportChartDef[] | null | undefined): ChartView[] {
  return (charts || []).map(c => {
    const type = (SUPPORTED.includes(c.type) ? c.type : 'bar') as ChartType;
    const labels = c.labels || [];
    const datasets = (c.datasets || []).map((d, i) => {
      const color = CHART_PALETTE[i % CHART_PALETTE.length];
      if (type === 'doughnut') {
        return { label: d.label, data: d.data, backgroundColor: labels.map((_, j) => CHART_PALETTE[j % CHART_PALETTE.length]) };
      }
      if (type === 'line') {
        return { label: d.label, data: d.data, borderColor: color, backgroundColor: color + '33', borderWidth: 2, tension: 0.3, fill: true };
      }
      return { label: d.label, data: d.data, backgroundColor: color, borderRadius: 4 };
    });
    const options = type === 'doughnut'
      ? { responsive: true, maintainAspectRatio: false, plugins: { legend: { position: 'right' } } }
      : {
        responsive: true, maintainAspectRatio: false,
        plugins: { legend: { position: 'bottom', display: datasets.length > 1 } },
        scales: { y: { beginAtZero: true, ticks: { precision: 0 } } }
      };
    return {
      title: c.title,
      type,
      data: { labels, datasets } as unknown as ChartConfiguration['data'],
      options: options as unknown as ChartConfiguration['options']
    };
  });
}

/** Nombre en notation française (espace fine pour les milliers, virgule décimale), sans dépendre de la locale Angular. */
export function formatNumber(value: number | null | undefined, maxFractionDigits = 1): string {
  if (value === null || value === undefined || isNaN(Number(value))) { return '—'; }
  return new Intl.NumberFormat('fr-FR', { maximumFractionDigits: maxFractionDigits }).format(Number(value));
}
