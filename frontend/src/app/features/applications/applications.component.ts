import { Component, OnInit, OnDestroy } from '@angular/core';
import { interval, Subscription } from 'rxjs';
import { switchMap, startWith } from 'rxjs/operators';
import { ApiService } from '../../core/services/api.service';

export interface MonitoredApp {
  namespace: string;
  app: string;
  type: string;
  monitored: boolean;
}

@Component({
    selector: 'app-applications',
    templateUrl: './applications.component.html',
    standalone: false
})
export class ApplicationsComponent implements OnInit, OnDestroy {
  loading = true;
  lastRefresh = '';
  total = 0;
  apps: MonitoredApp[] = [];
  private sub?: Subscription;

  constructor(private api: ApiService) {}

  ngOnInit(): void {
    this.sub = interval(120000).pipe(startWith(0), switchMap(() => this.api.getMonitoredApps()))
      .subscribe({
        next: data => {
          this.apps = data.apps || [];
          this.total = data.total || this.apps.length;
          this.lastRefresh = new Date().toLocaleTimeString('fr-FR');
          this.loading = false;
        },
        error: () => { this.loading = false; }
      });
  }

  refresh(): void {
    this.loading = true;
    this.api.getMonitoredApps().subscribe({
      next: data => {
        this.apps = data.apps || [];
        this.total = data.total || this.apps.length;
        this.lastRefresh = new Date().toLocaleTimeString('fr-FR');
        this.loading = false;
      },
      error: () => { this.loading = false; }
    });
  }

  getK8sApps(): MonitoredApp[] {
    return this.apps.filter(a => a.type === 'K8s Deployment');
  }

  getVmApps(): MonitoredApp[] {
    return this.apps.filter(a => a.type === 'VM Process');
  }

  getTypeIcon(type: string): string {
    if (type === 'K8s Deployment') return '☸️';
    if (type === 'VM Process') return '🐳';
    return '📦';
  }

  ngOnDestroy(): void {
    this.sub?.unsubscribe();
  }
}
