import { Component, OnInit, OnDestroy } from '@angular/core';
import { Subscription } from 'rxjs';
import { ApiService, Incident, PagedResponse } from '../../core/services/api.service';
import { AuthService } from '../../core/services/auth.service';
import { SseService } from '../../core/services/sse.service';

@Component({
  selector: 'app-ai-decisions',
  templateUrl: './ai-decisions.component.html'
})
export class AiDecisionsComponent implements OnInit, OnDestroy {
  incidents: Incident[] = [];
  filtered: Incident[] = [];
  loading = true;
  confidenceFilter = 'ALL';
  expandedId: number | null = null;
  private subs: Subscription[] = [];

  // Feedback state
  feedbackIncidentId: number | null = null;
  feedbackCorrect: boolean = true;
  feedbackComment: string = '';
  feedbackSuccess: string = '';

  confidenceFilters = [
    { label: 'All', value: 'ALL' },
    { label: 'High (>0.7)', value: 'HIGH' },
    { label: 'Medium (0.4-0.7)', value: 'MEDIUM' },
    { label: 'Low (<0.4)', value: 'LOW' }
  ];

  constructor(private api: ApiService, private auth: AuthService, private sse: SseService) {}

  ngOnInit(): void {
    this.load();
    // Real-time: refresh when any incident is created or updated.
    this.subs.push(this.sse.onIncidentCreated().subscribe(() => this.load()));
    this.subs.push(this.sse.onIncidentUpdated().subscribe(() => this.load()));
  }

  load(): void {
    this.loading = true;
    this.api.getIncidents(0, 1000).subscribe({
      next: (page: PagedResponse<Incident>) => {
        this.incidents = (page.content || []).filter(i => i.source === 'AI' || i.confidence != null);
        this.applyFilter();
        this.loading = false;
      },
      error: () => { this.loading = false; }
    });
  }

  applyFilter(): void {
    if (this.confidenceFilter === 'ALL') {
      this.filtered = this.incidents;
    } else if (this.confidenceFilter === 'HIGH') {
      this.filtered = this.incidents.filter(i => (i.confidence || 0) > 0.7);
    } else if (this.confidenceFilter === 'MEDIUM') {
      this.filtered = this.incidents.filter(i => {
        const c = i.confidence || 0;
        return c >= 0.4 && c <= 0.7;
      });
    } else if (this.confidenceFilter === 'LOW') {
      this.filtered = this.incidents.filter(i => (i.confidence || 0) < 0.4);
    }
  }

  setFilter(f: string): void {
    this.confidenceFilter = f;
    this.applyFilter();
  }

  toggleExpand(id: number): void {
    this.expandedId = this.expandedId === id ? null : id;
    // Close feedback if expanding a different row
    if (this.feedbackIncidentId !== id) {
      this.feedbackIncidentId = null;
      this.feedbackSuccess = '';
    }
  }

  openFeedback(id: number): void {
    if (this.feedbackIncidentId === id) {
      this.feedbackIncidentId = null;
      this.feedbackSuccess = '';
    } else {
      this.feedbackIncidentId = id;
      this.feedbackCorrect = true;
      this.feedbackComment = '';
      this.feedbackSuccess = '';
    }
  }

  submitFeedback(): void {
    if (this.feedbackIncidentId == null) return;
    this.api.submitFeedback(this.feedbackIncidentId, this.feedbackCorrect, this.feedbackComment, this.auth.getUsername() ?? 'operator')
      .subscribe({
        next: () => {
          this.feedbackSuccess = 'Feedback envoyé avec succès !';
          setTimeout(() => {
            this.feedbackIncidentId = null;
            this.feedbackSuccess = '';
          }, 2000);
        },
        error: () => {
          this.feedbackSuccess = 'Erreur lors de l\'envoi du feedback.';
        }
      });
  }

  getConfidenceClass(c: number | undefined): string {
    if (c === undefined || c === null) return 'medium';
    if (c < 0.4) return 'low';
    if (c <= 0.7) return 'medium';
    return 'high';
  }

  getConfidencePct(c: number | undefined): number {
    return Math.round((c || 0) * 100);
  }

  getSeverityClass(s: string): string { return s?.toLowerCase() || ''; }
  getStatusClass(s: string): string { return s?.toLowerCase() || ''; }

  ngOnDestroy(): void {
    this.subs.forEach(s => s.unsubscribe());
  }
}
