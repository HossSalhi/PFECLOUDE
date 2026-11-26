import { Component, OnInit, OnDestroy, ChangeDetectionStrategy } from '@angular/core';
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

/** Politique VM_DOWN (infra/helios-vm-policy.yaml) : mode de redémarrage de chaque VM et dernier état d'alimentation. */
export interface VmPolicyEntry {
  name: string;
  ip: string;
  mode: 'AUTO' | 'DETECT_ONLY' | 'DENY_RESTART' | string;
  vmClass: string;
  /** Qui redémarre : chaîne Camunda, chemin direct du backend (dormant) ou personne (.160 détection seule ; .153 est AUTO depuis le 2026-11-17). */
  restartRoute?: 'CAMUNDA' | 'BACKEND_DIRECT' | 'NONE' | string;
  /** Canaux de notification : habituels, ou interface seulement (.160 : ni e-mail ni escalade Camunda). */
  notifications?: 'EMAIL_AND_UI' | 'UI_ONLY' | string;
  powerState?: 'RUNNING' | 'POWERED_OFF' | 'UNKNOWN' | string;
  stateReason?: string;
  offConfirmed?: boolean;
  maintenance?: boolean;
  watchdogReachable?: boolean;
}

@Component({
    selector: 'app-infrastructure',
    templateUrl: './infrastructure.component.html',
    changeDetection: ChangeDetectionStrategy.Eager,
    standalone: false
})
export class InfrastructureComponent implements OnInit, OnDestroy {
  loading = true;
  lastRefresh = '';
  checkedAt = '';
  total = 0;
  upCount = 0;
  downCount = 0;
  components: ComponentHealth[] = [];
  vmPolicy: VmPolicyEntry[] = [];
  /** Message visible si l'API d'infrastructure ne répond pas (les données affichées sont alors périmées). */
  loadError = '';
  private sub?: Subscription;

  // VM metadata (static, enriched with live health)
  vmMeta: Record<string, { ip: string; role: string; services: string[] }> = {
    'prometheus':  { ip: '192.168.148.160', role: 'Monitoring VM',      services: ['Prometheus :9090'] },
    'loki':        { ip: '192.168.148.160', role: 'Monitoring VM',      services: ['Loki :3100'] },
    'grafana':     { ip: '192.168.148.160', role: 'Monitoring VM',      services: ['Grafana :3000'] },
    'camunda':     { ip: '192.168.148.161', role: 'Workflow & services VM', services: ['Camunda :8080'] },
    'k8s-master':  { ip: '192.168.148.153', role: 'K8s Control Plane',  services: ['kube-apiserver', 'etcd'] },
    'k8s-monitoring': { ip: '192.168.148.160', role: 'Monitoring VM (détection seule : rallumage par l\'administrateur)', services: ['Prometheus', 'Grafana', 'Loki', 'HAProxy', 'DNS', 'etcd'] },
    'k8s-lb2':     { ip: '192.168.148.162', role: 'Load balancer HAProxy + DNS actif', services: ['HAProxy :443/:6443', 'DNS :53', 'etcd'] },
    'k8s-worker1': { ip: '192.168.148.158', role: 'K8s Worker 1',       services: ['kubelet', 'kube-proxy'] },
    'k8s-worker2': { ip: '192.168.148.155', role: 'K8s Worker 2',       services: ['kubelet', 'kube-proxy'] },
  };

  constructor(private api: ApiService) {}

  ngOnInit(): void {
    // Auto-refresh every 60s
    this.sub = interval(60000).pipe(startWith(0), switchMap(() => this.api.getInfrastructureHealth()))
      .subscribe({
        next: data => this.applyHealth(data),
        error: () => this.onLoadError()
      });
  }

  refresh(): void {
    this.loading = true;
    this.api.getInfrastructureHealth().subscribe({
      next: data => this.applyHealth(data),
      error: () => this.onLoadError()
    });
  }

  private applyHealth(data: any): void {
    this.components = data.components || [];
    this.vmPolicy  = data.vmPolicy || [];
    this.total    = data.total;
    this.upCount  = data.up;
    this.downCount = data.down;
    this.checkedAt = data.checkedAt ? new Date(data.checkedAt).toLocaleTimeString('fr-FR') : '';
    this.lastRefresh = new Date().toLocaleTimeString('fr-FR');
    this.loadError = '';
    this.loading = false;
  }

  /** Erreur HTTP : on garde les dernières données mais on l'AFFIRME (jamais de données périmées sans alerte). */
  private onLoadError(): void {
    this.loadError = "L'état de l'infrastructure n'a pas pu être chargé : les données affichées peuvent être périmées.";
    this.loading = false;
  }

  /** Mode de redémarrage d'une VM (nom exact) : AUTO, DETECT_ONLY ou DENY_RESTART ; vide si hors politique. */
  modeOf(app: string): string {
    return this.vmPolicy.find(v => v.name === app)?.mode || '';
  }

  isDetectionOnly(app: string): boolean {
    return this.modeOf(app) === 'DETECT_ONLY';
  }

  /**
   * Précision de route : .160 (VM de supervision) est en détection seule — incident et notifications d'interface seulement
   * (ni e-mail, ni escalade Camunda), aucun redémarrage automatique. (Une éventuelle route « chemin direct du backend »
   * reste décrite si la politique la déclarait un jour.)
   */
  routeNote(v: VmPolicyEntry): string {
    if (v.mode === 'AUTO' && v.restartRoute === 'BACKEND_DIRECT') {
      return v.notifications === 'UI_ONLY'
        ? 'chemin direct du backend · interface seulement (ni e-mail, ni escalade Camunda)'
        : 'chemin direct du backend';
    }
    if (v.mode === 'DETECT_ONLY' && v.notifications === 'UI_ONLY') {
      return 'interface seulement (ni e-mail, ni escalade Camunda)';
    }
    return '';
  }

  modeLabel(mode: string): string {
    switch (mode) {
      case 'AUTO': return 'Redémarrage automatique';
      case 'DETECT_ONLY': return 'DÉTECTION SEULE — redémarrage par l\'administrateur';
      case 'DENY_RESTART': return 'Aucun redémarrage automatique';
      default: return '';
    }
  }

  powerLabel(state?: string): string {
    switch (state) {
      case 'RUNNING': return 'Allumée';
      case 'POWERED_OFF': return 'Éteinte';
      default: return 'État inconnu';
    }
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
