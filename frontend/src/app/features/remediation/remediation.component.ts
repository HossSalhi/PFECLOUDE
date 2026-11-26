import { Component, OnInit, OnDestroy } from '@angular/core';
import { Subscription } from 'rxjs';
import { ApiService, Action, RemediationSummary, PagedResponse } from '../../core/services/api.service';
import { SseService } from '../../core/services/sse.service';

@Component({
    selector: 'app-remediation',
    templateUrl: './remediation.component.html',
    standalone: false
})
export class RemediationComponent implements OnInit, OnDestroy {
  summary: RemediationSummary | null = null;
  actions: Action[] = [];
  loadingSummary = true;
  loadingActions = true;
  hoursWindow = 24;
  currentPage = 0;
  totalPages = 0;
  private subs: Subscription[] = [];

  constructor(private api: ApiService, private sse: SseService) {}

  ngOnInit(): void {
    this.loadAll();
    // Real-time: refresh the action history when incidents change.
    this.subs.push(this.sse.onIncidentCreated().subscribe(() => this.loadAll()));
    this.subs.push(this.sse.onIncidentUpdated().subscribe(() => this.loadAll()));
  }

  loadAll(): void {
    this.loadSummary();
    this.loadActions();
  }

  loadSummary(): void {
    this.loadingSummary = true;
    this.api.getRemediationSummary(this.hoursWindow).subscribe({
      next: data => { this.summary = data; this.loadingSummary = false; },
      error: () => { this.loadingSummary = false; }
    });
  }

  loadActions(): void {
    this.loadingActions = true;
    this.api.getActionsHistory(this.currentPage, 100).subscribe({
      next: (page: PagedResponse<Action>) => {
        this.actions = page.content || [];
        this.totalPages = page.totalPages || 0;
        this.loadingActions = false;
      },
      error: () => { this.loadingActions = false; }
    });
  }

  setHours(h: number): void {
    this.hoursWindow = h;
    this.loadSummary();
  }

  goToPage(p: number): void {
    if (p >= 0 && p < this.totalPages) {
      this.currentPage = p;
      this.loadActions();
    }
  }

  trackByAction(_: number, action: Action): number { return action.id; }

  getStatusClass(s: string): string { return s?.toLowerCase() || ''; }

  getSuccessRatePct(): number {
    return this.summary ? Math.round(this.summary.successRate * 100) : 0;
  }

  getAutoRatePct(): number {
    return this.summary ? Math.round(this.summary.autoActionRate * 100) : 0;
  }

  ngOnDestroy(): void {
    this.subs.forEach(s => s.unsubscribe());
  }
}
