import { Injectable, OnDestroy } from '@angular/core';
import { Subject, Observable } from 'rxjs';
import { environment } from '../../../environments/environment';

export interface SseEvent {
  type: string;
  data: any;
}

@Injectable({
  providedIn: 'root'
})
export class SseService implements OnDestroy {
  private baseUrl = environment.apiBaseUrl;
  private eventSource: EventSource | null = null;
  private reconnectTimer: any = null;

  private incidentCreated$ = new Subject<any>();
  private incidentUpdated$ = new Subject<any>();
  private finetuningLog$ = new Subject<string>();
  private finetuningDone$ = new Subject<any>();
  private finetuningStarted$ = new Subject<any>();
  private connected$ = new Subject<boolean>();

  constructor() {
    this.connect();
  }

  connect(): void {
    if (this.eventSource) {
      this.eventSource.close();
    }

    const url = `${this.baseUrl}/api/events/stream`;

    try {
      this.eventSource = new EventSource(url);

      this.eventSource.onopen = () => {
        this.connected$.next(true);
        if (this.reconnectTimer) {
          clearTimeout(this.reconnectTimer);
          this.reconnectTimer = null;
        }
      };

      this.eventSource.onerror = () => {
        this.connected$.next(false);
        this.eventSource?.close();
        this.eventSource = null;
        this.scheduleReconnect();
      };

      this.eventSource.addEventListener('incident_created', (event: MessageEvent) => {
        try {
          const data = JSON.parse(event.data);
          this.incidentCreated$.next(data);
        } catch {
          this.incidentCreated$.next(event.data);
        }
      });

      this.eventSource.addEventListener('incident_updated', (event: MessageEvent) => {
        try {
          const data = JSON.parse(event.data);
          this.incidentUpdated$.next(data);
        } catch {
          this.incidentUpdated$.next(event.data);
        }
      });

      this.eventSource.addEventListener('finetuning_log', (event: MessageEvent) => {
        this.finetuningLog$.next(event.data);
      });

      this.eventSource.addEventListener('finetuning_done', (event: MessageEvent) => {
        try {
          const data = JSON.parse(event.data);
          this.finetuningDone$.next(data);
        } catch {
          this.finetuningDone$.next(event.data);
        }
      });

      this.eventSource.addEventListener('finetuning_started', (event: MessageEvent) => {
        try {
          const data = JSON.parse(event.data);
          this.finetuningStarted$.next(data);
        } catch {
          this.finetuningStarted$.next(event.data);
        }
      });

      this.eventSource.onmessage = (event: MessageEvent) => {
        // generic message handler
      };

    } catch (error) {
      console.error('SSE connection error:', error);
      this.scheduleReconnect();
    }
  }

  private scheduleReconnect(): void {
    if (!this.reconnectTimer) {
      this.reconnectTimer = setTimeout(() => {
        this.reconnectTimer = null;
        this.connect();
      }, 5000);
    }
  }

  onIncidentCreated(): Observable<any> {
    return this.incidentCreated$.asObservable();
  }

  onIncidentUpdated(): Observable<any> {
    return this.incidentUpdated$.asObservable();
  }

  onFinetuningLog(): Observable<string> {
    return this.finetuningLog$.asObservable();
  }

  onFinetuningDone(): Observable<any> {
    return this.finetuningDone$.asObservable();
  }

  onFinetuningStarted(): Observable<any> {
    return this.finetuningStarted$.asObservable();
  }

  onConnected(): Observable<boolean> {
    return this.connected$.asObservable();
  }

  ngOnDestroy(): void {
    if (this.eventSource) {
      this.eventSource.close();
    }
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
    }
    this.incidentCreated$.complete();
    this.incidentUpdated$.complete();
    this.finetuningLog$.complete();
    this.finetuningDone$.complete();
    this.finetuningStarted$.complete();
    this.connected$.complete();
  }
}
