import { Injectable, OnDestroy } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { AuthService } from './auth.service';
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
  private connected$ = new Subject<boolean>();

  constructor(private http: HttpClient, private auth: AuthService) {
    this.connect();
  }

  connect(): void {
    if (this.eventSource) {
      this.eventSource.close();
    }

    // Le flux SSE n'est plus public : EventSource ne peut pas envoyer d'en-tête Authorization, on obtient donc un TICKET
    // court (30 s), à usage unique, via une requête authentifiée ; le JWT n'est jamais placé dans l'URL.
    if (!this.auth.isLoggedIn()) {
      this.scheduleReconnect();
      return;
    }
    this.http.post<{ ticket: string }>(`${this.baseUrl}/api/events/ticket`, {}).subscribe({
      next: res => this.openStream(res.ticket),
      error: () => {
        this.connected$.next(false);
        this.scheduleReconnect();
      }
    });
  }

  private openStream(ticket: string): void {
    const url = `${this.baseUrl}/api/events/stream?ticket=${encodeURIComponent(ticket)}`;

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
    this.connected$.complete();
  }
}
