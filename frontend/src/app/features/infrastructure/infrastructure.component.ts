import { Component, OnInit, OnDestroy } from '@angular/core';
import { interval, Subscription } from 'rxjs';
import { switchMap, startWith } from 'rxjs/operators';
import { ApiService } from '../../core/services/api.service';

export interface ComponentHealth {
  namespace: string;
  app: string;
  up: boolean;
  status: string;
  detail: string;
}

@Component({
  selector: 'app-infrastructure',
  templateUrl: './infrastructure.component.html'
})
export class InfrastructureComponent implements OnInit, OnDestroy {
  loading = true;
  lastRefresh = '';
  checkedAt = '';
  total = 0;
  upCount = 0;
  downCount = 0;
  components: ComponentHealth[] = [];
  private sub?: Subscription;

  // VM metadata (static, enriched with live health)
  vmMeta: Record<string, { ip: string; role: string; services: string[] }> = {
    'prometheus':  { ip: '192.168.148.160', role: 'Monitoring VM',      services: ['Prometheus :9090'] },
    'loki':        { ip: '192.168.148.160', role: 'Monitoring VM',      services: ['Loki :3100'] },
    'grafana':     { ip: '192.168.148.160', role: 'Monitoring VM',      services: ['Grafana :3000'] },
    'ai-service':  { ip: '192.168.148.161', role: 'AI + Workflow VM',   services: ['FastAPI :8000'] },
    'camunda':     { ip: '192.168.148.161', role: 'AI + Workflow VM',   services: ['Camunda :8080'] },
    'ollama':      { ip: '192.168.148.161', role: 'AI + Workflow VM',   services: ['Ollama :11434'] },
    'k8s-master':  { ip: '192.168.148.153', role: 'K8s Control Plane',  services: ['kube-apiserver', 'etcd'] },
    'k8s-worker1': { ip: '192.168.148.158', role: 'K8s Worker 1',       services: ['kubelet', 'kube-proxy'] },
    'k8s-worker2': { ip: '192.168.148.155', role: 'K8s Worker 2',       services: ['kubelet', 'kube-proxy'] },
  };

  constructor(private api: ApiService) {}

  ngOnInit(): void {
    // Auto-refresh every 60s
    this.sub = interval(60000).pipe(startWith(0), switchMap(() => this.api.getInfrastructureHealth()))
      .subscribe({
        next: data => {
          this.components = data.components || [];
          this.total    = data.total;
          this.upCount  = data.up;
          this.downCount = data.down;
          this.checkedAt = data.checkedAt ? new Date(data.checkedAt).toLocaleTimeString('fr-FR') : '';
          this.lastRefresh = new Date().toLocaleTimeString('fr-FR');
          this.loading = false;
        },
        error: () => { this.loading = false; }
      });
  }

  refresh(): void {
    this.loading = true;
    this.api.getInfrastructureHealth().subscribe({
      next: data => {
        this.components = data.components || [];
        this.total    = data.total;
        this.upCount  = data.up;
        this.downCount = data.down;
        this.checkedAt = data.checkedAt ? new Date(data.checkedAt).toLocaleTimeString('fr-FR') : '';
        this.lastRefresh = new Date().toLocaleTimeString('fr-FR');
        this.loading = false;
      },
      error: () => { this.loading = false; }
    });
  }

  getMeta(app: string) {
    return this.vmMeta[app] || { ip: 'N/A', role: 'Service', services: [] };
  }

  getVmComponents(): ComponentHealth[] {
    return this.components.filter(c => c.namespace === 'infra');
  }

  getK8sNodes(): ComponentHealth[] {
    return this.components.filter(c => c.namespace === 'k8s-nodes');
  }

  ngOnDestroy(): void {
    this.sub?.unsubscribe();
  }
}
