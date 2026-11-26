import { Component, OnInit, OnDestroy, ChangeDetectionStrategy } from '@angular/core';
import { Subscription, interval } from 'rxjs';
import { ChartConfiguration, ChartData } from 'chart.js';
import { ApiService, DashboardSummary, Incident } from '../../core/services/api.service';
import { SseService } from '../../core/services/sse.service';
import { NgChartsModule } from 'ng2-charts';
import { NgClass, DecimalPipe, DatePipe } from '@angular/common';
import { RouterLink } from '@angular/router';

@Component({
    selector: 'app-dashboard',
    templateUrl: './dashboard.component.html',
    changeDetection: ChangeDetectionStrategy.Eager,
    imports: [NgChartsModule, NgClass, RouterLink, DecimalPipe, DatePipe]
})
export class DashboardComponent implements OnInit, OnDestroy {
  summary: DashboardSummary | null = null;
  recentIncidents: Incident[] = [];
  loading = true;
  incidentsLoading = true;
  lastRefresh = '';
  private subs: Subscription[] = [];

  // ── Bar chart: incidents by status ──────────────────────────────────
  barChartData: ChartData<'bar'> = {
    labels: ['Open', 'In Progress', 'Resolved'],
    datasets: [
      {
        label: 'Incidents',
        data: [0, 0, 0],
        backgroundColor: ['#FFC107', '#17A2B8', '#28A745'],
        borderRadius: 4,
        borderSkipped: false
      }
    ]
  };

  barChartOptions: ChartConfiguration<'bar'>['options'] = {
    responsive: true,
    maintainAspectRatio: false,
    plugins: {
      legend: { display: false }
    },
    scales: {
      y: {
        beginAtZero: true,
        ticks: { stepSize: 1 }
      }
    }
  };

  // ── Doughnut chart: incidents by severity ────────────────────────────
  doughnutData: ChartData<'doughnut'> = {
    labels: ['CRITICAL', 'HIGH', 'MEDIUM', 'LOW'],
    datasets: [
      {
        data: [0, 0, 0, 0],
        backgroundColor: ['#DC3545', '#FF7900', '#FFC107', '#28A745'],
        hoverOffset: 4
      }
    ]
  };

  doughnutOptions: ChartConfiguration<'doughnut'>['options'] = {
    responsive: true,
    maintainAspectRatio: false,
    plugins: {
      legend: {
        position: 'right',
        labels: { font: { size: 12 }, boxWidth: 14 }
      }
    }
  };

  constructor(
    private api: ApiService,
    private sse: SseService
  ) {}

  ngOnInit(): void {
    this.loadData();

    // Auto-refresh every 30 seconds
    const refreshSub = interval(30000).subscribe(() => this.loadData());
    this.subs.push(refreshSub);

    // Real-time via SSE
    const createdSub = this.sse.onIncidentCreated().subscribe(() => this.loadData());
    const updatedSub = this.sse.onIncidentUpdated().subscribe(() => this.loadData());
    this.subs.push(createdSub, updatedSub);
  }

  loadData(): void {
    this.api.getDashboardSummary().subscribe({
      next: data => {
        this.summary = data;
        this.loading = false;
        this.lastRefresh = new Date().toLocaleTimeString('fr-FR');
        this.updateCharts(data);
      },
      error: () => { this.loading = false; }
    });

    this.api.getIncidents(0, 5).subscribe({
      next: page => {
        this.recentIncidents = page.content || [];
        this.incidentsLoading = false;
        this.updateDoughnutFromIncidents(this.recentIncidents);
      },
      error: () => { this.incidentsLoading = false; }
    });
  }

  private updateCharts(data: DashboardSummary): void {
    // Update bar chart with status counts
    this.barChartData = {
      ...this.barChartData,
      datasets: [
        {
          ...this.barChartData.datasets[0],
          data: [data.openIncidents, data.inProgressIncidents, data.resolvedIncidents]
        }
      ]
    };
  }

  private updateDoughnutFromIncidents(incidents: Incident[]): void {
    const counts = { CRITICAL: 0, HIGH: 0, MEDIUM: 0, LOW: 0 };
    incidents.forEach(inc => {
      const sev = (inc.severity || '').toUpperCase() as keyof typeof counts;
      if (sev in counts) counts[sev]++;
    });
    this.doughnutData = {
      ...this.doughnutData,
      datasets: [
        {
          ...this.doughnutData.datasets[0],
          data: [counts.CRITICAL, counts.HIGH, counts.MEDIUM, counts.LOW]
        }
      ]
    };
  }

  trackByIncident(_: number, incident: Incident): number { return incident.id; }

  getSeverityClass(severity: string): string {
    return severity?.toLowerCase() || 'low';
  }

  getStatusClass(status: string): string {
    return status?.toLowerCase().replace('_', '_') || '';
  }

  ngOnDestroy(): void {
    this.subs.forEach(s => s.unsubscribe());
  }
}
