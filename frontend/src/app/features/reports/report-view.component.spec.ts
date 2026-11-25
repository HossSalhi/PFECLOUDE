import { ComponentFixture, TestBed } from '@angular/core/testing';
import { NO_ERRORS_SCHEMA } from '@angular/core';
import { ReportContent } from '../../core/models/org.models';
import { ReportViewComponent } from './report-view.component';

describe('ReportViewComponent', () => {
  let fixture: ComponentFixture<ReportViewComponent>;
  let component: ReportViewComponent;

  const content: ReportContent = {
    type: 'PLATFORM_ACTIVITY', typeLabel: 'Activités de la plateforme', title: 'Activités — novembre',
    periodFrom: '2026-11-01T00:00', periodTo: '2026-12-01T00:00', previousFrom: '2026-10-02T00:00', previousTo: '2026-11-01T00:00',
    generatedAt: '2026-12-01T08:00:00', generatedBy: 'admin',
    summary: [
      { label: 'Actions', value: 120, previous: 100, delta: 20, deltaPercent: 20 },
      { label: 'Échecs', value: 3, previous: 0, delta: 3, deltaPercent: null },
      { label: 'Durée moyenne', value: 12.5, unit: 'min' }
    ],
    sections: [{ title: 'Par module', columns: ['Module', 'Actions'], rows: [['AUTH', 80], ['USERS', null]] }],
    charts: [{ type: 'bar', title: 'Par module', labels: ['AUTH', 'USERS'], datasets: [{ label: 'Actions', data: [80, 40] }] }]
  };

  beforeEach(() => {
    TestBed.configureTestingModule({ declarations: [ReportViewComponent], schemas: [NO_ERRORS_SCHEMA] });
    fixture = TestBed.createComponent(ReportViewComponent);
    component = fixture.componentInstance;
  });

  it("n'affiche rien sans contenu", () => {
    fixture.detectChanges();
    expect(fixture.nativeElement.textContent.trim()).toBe('');
  });

  it('affiche indicateurs, graphiques et tableaux du rapport', () => {
    component.content = content;
    component.ngOnChanges();
    fixture.detectChanges();
    const text: string = fixture.nativeElement.textContent;
    expect(text).toContain('Actions');
    expect(text).toContain('Durée moyenne');
    expect(text).toContain('min');
    expect(text).toContain('Par module');
    expect(component.charts.length).toBe(1);
    expect(fixture.nativeElement.querySelectorAll('.stat-tile').length).toBe(3);
    expect(fixture.nativeElement.querySelectorAll('canvas').length).toBe(1);
    expect(fixture.nativeElement.querySelectorAll('tbody tr').length).toBe(2);
    expect(text).toContain('—');                                  // cellule nulle affichée par un tiret
  });

  it('indique la dernière journée incluse de la période (la fin du Backend est exclusive)', () => {
    component.content = content;
    component.ngOnChanges();
    fixture.detectChanges();
    expect(fixture.nativeElement.textContent).toContain('du 01/11/2026 au 30/11/2026');
    expect(component.lastDay('2026-12-01T00:00')).toBe('30/11/2026');
    expect(component.lastDay('2026-03-01T00:00:00')).toBe('28/02/2026');
  });

  it('formate la variation par rapport à la période précédente sans la juger', () => {
    expect(component.deltaText(content.summary[0])).toBe('▲ +20 (+20 %)');
    expect(component.deltaText(content.summary[1])).toBe('▲ +3');                       // pas de pourcentage si l'avant vaut 0
    expect(component.deltaText({ label: 'x', value: 5, previous: 8, delta: -3, deltaPercent: -37.5 })).toBe('▼ -3 (-37,5 %)');
    expect(component.deltaText({ label: 'x', value: 5, previous: 5, delta: 0, deltaPercent: 0 })).toBe('• 0 (0 %)');
    expect(component.deltaText(content.summary[2])).toBe('');
    expect(component.hasComparison(content.summary[0])).toBeTrue();
    expect(component.hasComparison(content.summary[2])).toBeFalse();
  });
});
