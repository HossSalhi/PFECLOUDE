import { Component, OnInit, OnDestroy } from '@angular/core';
import { Subscription } from 'rxjs';
import { Router } from '@angular/router';
import { ApiService, Incident, PagedResponse } from '../../core/services/api.service';
import { SseService } from '../../core/services/sse.service';

@Component({
  selector: 'app-incidents',
  templateUrl: './incidents.component.html'
})
export class IncidentsComponent implements OnInit, OnDestroy {
  incidents: Incident[] = [];
  loading = true;
  currentFilter = 'ALL';
  currentPage = 0;
  pageSize = 20;
  totalPages = 0;
  totalElements = 0;
  private subs: Subscription[] = [];

  filters = ['ALL', 'OPEN', 'IN_PROGRESS', 'RESOLVED', 'RESOLUTION_FAILED'];

  constructor(
    private api: ApiService,
    private sse: SseService,
    private router: Router
  ) {}

  ngOnInit(): void {
    this.loadIncidents();

    const createdSub = this.sse.onIncidentCreated().subscribe(() => this.loadIncidents());
    const updatedSub = this.sse.onIncidentUpdated().subscribe(() => this.loadIncidents());
    this.subs.push(createdSub, updatedSub);
  }

  loadIncidents(): void {
    this.loading = true;
    this.api.getIncidents(this.currentPage, this.pageSize, this.currentFilter).subscribe({
      next: (page: PagedResponse<Incident>) => {
        this.incidents = page.content || [];
        this.totalPages = page.totalPages || 0;
        this.totalElements = page.totalElements || 0;
        this.loading = false;
      },
      error: () => { this.loading = false; }
    });
  }

  setFilter(filter: string): void {
    this.currentFilter = filter;
    this.currentPage = 0;
    this.loadIncidents();
  }

  goToPage(page: number): void {
    if (page >= 0 && page < this.totalPages) {
      this.currentPage = page;
      this.loadIncidents();
    }
  }

  getPages(): number[] {
    const pages: number[] = [];
    const start = Math.max(0, this.currentPage - 2);
    const end = Math.min(this.totalPages - 1, this.currentPage + 2);
    for (let i = start; i <= end; i++) {
      pages.push(i);
    }
    return pages;
  }

  getSeverityClass(s: string): string { return s?.toLowerCase() || ''; }
  getStatusClass(s: string): string { return s?.toLowerCase().replace('_', '_') || ''; }

  viewDetail(id: number): void { this.router.navigate(['/incidents', id]); }

  exportCsv(): void {
    const headers = ['ID', 'Title', 'Severity', 'Status', 'Source', 'Created'];
    const rows = this.incidents.map(i => [
      i.id,
      `"${(i.title || '').replace(/"/g, '""')}"`,
      i.severity,
      i.status,
      i.source,
      i.createdAt
    ]);
    const csv = [headers.join(','), ...rows.map(r => r.join(','))].join('\n');
    const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url; a.download = `incidents-${new Date().toISOString().slice(0,10)}.csv`;
    a.click(); URL.revokeObjectURL(url);
  }

  trackByIncident(_: number, incident: Incident): number { return incident.id; }

  ngOnDestroy(): void {
    this.subs.forEach(s => s.unsubscribe());
  }
}
