import { Component, OnInit, OnDestroy, ViewChild, ElementRef } from '@angular/core';
import { Subscription } from 'rxjs';
import { ApiService, FineTuningStatus } from '../../core/services/api.service';
import { SseService } from '../../core/services/sse.service';

export interface LogLine {
  text: string;
  type: 'info' | 'success' | 'error' | 'warn';
  timestamp: string;
}

export interface RunHistory {
  startedAt: string;
  status: string;
  duration?: string;
  logs: number;
}

@Component({
  selector: 'app-fine-tuning',
  templateUrl: './fine-tuning.component.html'
})
export class FineTuningComponent implements OnInit, OnDestroy {
  @ViewChild('terminal') terminalRef!: ElementRef;

  status: FineTuningStatus | null = null;
  loading = true;
  triggering = false;
  forceRun = false;
  minIncidents = 20;
  logs: LogLine[] = [];
  runHistory: RunHistory[] = [];
  triggerSuccess = '';
  triggerError = '';

  stages = [
    { label: 'Building dataset', done: false, active: false },
    { label: 'LoRA fine-tuning', done: false, active: false },
    { label: 'Dequantizing', done: false, active: false },
    { label: 'Merging model', done: false, active: false },
    { label: 'Deploying', done: false, active: false }
  ];

  private subs: Subscription[] = [];
  private pollInterval: any;

  constructor(
    private api: ApiService,
    private sse: SseService
  ) {}

  ngOnInit(): void {
    this.loadStatus();

    // SSE listeners
    const logSub = this.sse.onFinetuningLog().subscribe(line => {
      this.appendLog(line);
      this.updateStagesFromLog(line);
    });

    const startedSub = this.sse.onFinetuningStarted().subscribe(data => {
      this.appendLog('Fine-tuning started!', 'success');
      this.loadStatus();
      this.resetStages();
    });

    const doneSub = this.sse.onFinetuningDone().subscribe(data => {
      this.appendLog('Fine-tuning completed!', 'success');
      this.markAllStagesDone();
      this.loadStatus();
      if (this.pollInterval) {
        clearInterval(this.pollInterval);
        this.pollInterval = null;
      }
    });

    this.subs.push(logSub, startedSub, doneSub);
  }

  loadStatus(): void {
    this.api.getFineTuningStatus().subscribe({
      next: s => {
        this.status = s;
        this.loading = false;
        if (s.running && !this.pollInterval) {
          this.startPolling();
        }
        if (s.lastLog) {
          this.appendLog(s.lastLog, 'info');
        }
      },
      error: () => { this.loading = false; }
    });
  }

  private startPolling(): void {
    this.pollInterval = setInterval(() => {
      this.api.getFineTuningStatus().subscribe({
        next: s => {
          this.status = s;
          if (!s.running && this.pollInterval) {
            clearInterval(this.pollInterval);
            this.pollInterval = null;
          }
        },
        error: () => {}
      });
    }, 5000);
  }

  trigger(): void {
    if (this.triggering || this.status?.running) return;
    if (!this.forceRun && (this.minIncidents < 1 || this.minIncidents > 1000)) {
      this.triggerError = 'Minimum incidents must be between 1 and 1000.';
      setTimeout(() => this.triggerError = '', 4000);
      return;
    }
    this.triggering = true;
    this.triggerSuccess = '';
    this.triggerError = '';
    this.resetStages();
    this.logs = [];
    this.appendLog('Sending trigger request to AI service...', 'info');

    this.api.triggerFineTuning(this.forceRun, this.minIncidents).subscribe({
      next: res => {
        this.triggering = false;
        this.triggerSuccess = 'Fine-tuning job submitted successfully. Waiting for events...';
        this.appendLog('Job submitted. Waiting for finetuning_started event...', 'success');
        this.loadStatus();
        this.startPolling();
        setTimeout(() => this.triggerSuccess = '', 6000);
      },
      error: err => {
        this.triggering = false;
        const msg = err?.error?.message || err?.message || 'Failed to trigger fine-tuning';
        this.triggerError = msg;
        this.appendLog(`Error: ${msg}`, 'error');
        setTimeout(() => this.triggerError = '', 6000);
      }
    });
  }

  appendLog(text: string, type: LogLine['type'] = 'info'): void {
    const timestamp = new Date().toLocaleTimeString('fr-FR', {
      hour: '2-digit', minute: '2-digit', second: '2-digit'
    });
    this.logs.push({ text, type, timestamp });

    // Auto-scroll terminal
    setTimeout(() => {
      if (this.terminalRef?.nativeElement) {
        const el = this.terminalRef.nativeElement;
        el.scrollTop = el.scrollHeight;
      }
    }, 50);
  }

  private updateStagesFromLog(line: string): void {
    const l = line.toLowerCase();
    if (l.includes('building dataset') || l.includes('dataset')) {
      this.activateStage(0);
    } else if (l.includes('lora') || l.includes('fine-tun') || l.includes('training')) {
      this.completeStage(0);
      this.activateStage(1);
    } else if (l.includes('dequantiz')) {
      this.completeStage(1);
      this.activateStage(2);
    } else if (l.includes('merg')) {
      this.completeStage(2);
      this.activateStage(3);
    } else if (l.includes('deploy') || l.includes('loading model')) {
      this.completeStage(3);
      this.activateStage(4);
    } else if (l.includes('done') || l.includes('complete') || l.includes('success')) {
      this.markAllStagesDone();
    }
  }

  private activateStage(index: number): void {
    if (index < this.stages.length) {
      this.stages.forEach((s, i) => {
        if (i < index) { s.done = true; s.active = false; }
        if (i === index) { s.active = true; s.done = false; }
        if (i > index) { s.active = false; }
      });
    }
  }

  private completeStage(index: number): void {
    if (index < this.stages.length) {
      this.stages[index].done = true;
      this.stages[index].active = false;
    }
  }

  private resetStages(): void {
    this.stages.forEach(s => { s.done = false; s.active = false; });
  }

  private markAllStagesDone(): void {
    this.stages.forEach(s => { s.done = true; s.active = false; });
  }

  getStatusClass(s: string | undefined): string {
    if (!s) return 'idle';
    const lower = s.toLowerCase();
    if (lower === 'running') return 'running';
    if (lower === 'success' || lower === 'idle') return lower;
    if (lower === 'failed' || lower === 'error') return 'failed';
    return 'idle';
  }

  clearLogs(): void {
    this.logs = [];
  }

  ngOnDestroy(): void {
    this.subs.forEach(s => s.unsubscribe());
    if (this.pollInterval) clearInterval(this.pollInterval);
  }
}
