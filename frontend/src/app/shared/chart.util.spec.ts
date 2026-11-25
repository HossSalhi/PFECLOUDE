import { buildChartViews, CHART_PALETTE, formatNumber } from './chart.util';
import { ReportChartDef } from '../core/models/org.models';

describe('chart.util', () => {

  const bar: ReportChartDef = {
    type: 'bar', title: 'Activité par module', labels: ['AUTH', 'USERS'],
    datasets: [{ label: 'Actions', data: [4, 9] }]
  };

  it('convertit un graphique en barres', () => {
    const [view] = buildChartViews([bar]);
    expect(view.type).toBe('bar');
    expect(view.title).toBe('Activité par module');
    expect(view.data.labels).toEqual(['AUTH', 'USERS']);
    expect((view.data.datasets[0] as any).backgroundColor).toBe(CHART_PALETTE[0]);
    expect((view.data.datasets[0] as any).data).toEqual([4, 9]);
  });

  it('colore chaque part d\'un anneau et chaque série d\'une courbe', () => {
    const doughnut: ReportChartDef = { type: 'doughnut', title: 'Statuts', labels: ['A', 'B', 'C'], datasets: [{ label: 'Tâches', data: [1, 2, 3] }] };
    const line: ReportChartDef = {
      type: 'line', title: 'Par jour', labels: ['1', '2'],
      datasets: [{ label: 'Succès', data: [1, 2] }, { label: 'Échecs', data: [0, 1] }]
    };
    const views = buildChartViews([doughnut, line]);
    expect(views.map(v => v.type)).toEqual(['doughnut', 'line']);
    expect((views[0].data.datasets[0] as any).backgroundColor).toEqual(CHART_PALETTE.slice(0, 3));
    expect((views[1].data.datasets[0] as any).borderColor).toBe(CHART_PALETTE[0]);
    expect((views[1].data.datasets[1] as any).borderColor).toBe(CHART_PALETTE[1]);
  });

  it('retombe sur des barres pour un type inconnu et tolère l\'absence de données', () => {
    const unknown = { ...bar, type: 'radar' } as unknown as ReportChartDef;
    expect(buildChartViews([unknown])[0].type).toBe('bar');
    expect(buildChartViews(null)).toEqual([]);
    expect(buildChartViews(undefined)).toEqual([]);
    expect(buildChartViews([{ type: 'bar', title: 'Vide', labels: [], datasets: [] }])[0].data.datasets).toEqual([]);
  });

  it('formate les nombres à la française', () => {
    expect(formatNumber(12)).toBe('12');
    expect(formatNumber(12.34)).toBe('12,3');
    expect(formatNumber(null)).toBe('—');
    expect(formatNumber(undefined)).toBe('—');
    expect(formatNumber(NaN)).toBe('—');
  });
});
