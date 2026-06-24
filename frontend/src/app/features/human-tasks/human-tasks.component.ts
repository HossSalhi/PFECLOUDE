import { Component, OnInit, OnDestroy } from '@angular/core';
import { Subscription } from 'rxjs';
import { ApiService, Task } from '../../core/services/api.service';
import { SseService } from '../../core/services/sse.service';

@Component({
  selector: 'app-human-tasks',
  templateUrl: './human-tasks.component.html'
})
export class HumanTasksComponent implements OnInit, OnDestroy {
  pendingTasks: Task[] = [];
  allTasks: Task[] = [];
  loading = true;
  processingId: string | null = null;
  activeTab: 'pending' | 'all' = 'pending';
  successMsg = '';
  errorMsg = '';
  private subs: Subscription[] = [];

  constructor(private api: ApiService, private sse: SseService) {}

  ngOnInit(): void {
    this.loadTasks();
    // Real-time: a new human task appears when an incident is remediated.
    this.subs.push(this.sse.onIncidentCreated().subscribe(() => this.loadTasks()));
    this.subs.push(this.sse.onIncidentUpdated().subscribe(() => this.loadTasks()));
  }

  loadTasks(): void {
    this.loading = true;
    this.subs.push(
      this.api.getPendingTasks().subscribe({
        next: tasks => {
          this.pendingTasks = (tasks || []).filter(t =>
            t.incidentStatus !== 'RESOLVED' && t.incidentStatus !== 'CLOSED'
          );
          this.loading = false;
        },
        error: () => { this.loading = false; }
      }),
      this.api.getTasks().subscribe({
        next: tasks => { this.allTasks = tasks || []; },
        error: () => {}
      })
    );
  }

  approve(task: Task): void {
    this.processingId = task.id;
    this.clearMessages();
    this.api.approveTask(task.id).subscribe({
      next: () => {
        this.processingId = null;
        this.successMsg = `Task "${task.name}" approved successfully.`;
        this.loadTasks();
        setTimeout(() => this.successMsg = '', 4000);
      },
      error: (err) => {
        this.processingId = null;
        this.errorMsg = `Failed to approve task: ${err?.message || 'Unknown error'}`;
        setTimeout(() => this.errorMsg = '', 4000);
      }
    });
  }

  reject(task: Task): void {
    this.processingId = task.id;
    this.clearMessages();
    this.api.rejectTask(task.id).subscribe({
      next: () => {
        this.processingId = null;
        this.successMsg = `Task "${task.name}" rejected.`;
        this.loadTasks();
        setTimeout(() => this.successMsg = '', 4000);
      },
      error: (err) => {
        this.processingId = null;
        this.errorMsg = `Failed to reject task: ${err?.message || 'Unknown error'}`;
        setTimeout(() => this.errorMsg = '', 4000);
      }
    });
  }

  clearMessages(): void {
    this.successMsg = '';
    this.errorMsg = '';
  }

  trackByTask(_: number, task: Task): string { return task.id; }

  getStatusClass(s: string): string { return s?.toLowerCase() || ''; }

  ngOnDestroy(): void {
    this.subs.forEach(s => s.unsubscribe());
  }
}
