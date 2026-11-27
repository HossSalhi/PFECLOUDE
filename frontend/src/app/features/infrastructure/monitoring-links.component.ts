import { ChangeDetectionStrategy, Component, OnInit, inject } from '@angular/core';
import { ApiService } from '../../core/services/api.service';

export interface MonitoringLinks {
  prometheus: string;
  grafana: string;
  loki: string;
  camunda: string;
}

interface SupervisionLink { key: keyof MonitoringLinks; label: string; icon: string; hint: string; url: string | null; }

/**
 * Section « Supervision » (ADMIN et SUPER_ADMIN seulement : le composant parent ne l'affiche pas aux autres rôles, et
 * l'API la refuse de toute façon). Chaque outil s'ouvre dans un NOUVEL onglet ; rien n'est intégré dans la page
 * (pas d'iframe) : Camunda Operate en particulier n'est pas intégrable.
 */
@Component({
  selector: 'app-monitoring-links',
  changeDetection: ChangeDetectionStrategy.Eager,
  template: `
    <section class="supervision-links mb-24" aria-label="Supervision">
      <div class="supervision-head"><i class="fas fa-binoculars"></i> Supervision</div>
      @if (error) {
        <div class="supervision-error" role="alert">{{ error }}</div>
      } @else {
        <div class="supervision-actions">
          @for (l of links; track l.key) {
            @if (l.url) {
              <a class="btn btn-ghost btn-sm" [href]="l.url" target="_blank" rel="noopener noreferrer" [title]="l.hint">
                <i class="fas" [class]="l.icon"></i> {{ l.label }} <i class="fas fa-arrow-up-right-from-square"></i>
              </a>
            }
          }
        </div>
      }
    </section>
  `
})
export class MonitoringLinksComponent implements OnInit {
  private api = inject(ApiService);

  links: SupervisionLink[] = [];
  error = '';

  ngOnInit(): void {
    this.api.getMonitoringLinks().subscribe({
      next: (l: MonitoringLinks) => this.links = [
        { key: 'prometheus', label: 'Prometheus', icon: 'fa-fire', hint: 'Métriques et requêtes', url: safeUrl(l.prometheus) },
        { key: 'grafana', label: 'Grafana', icon: 'fa-chart-line', hint: 'Tableaux de bord', url: safeUrl(l.grafana) },
        { key: 'loki', label: 'Loki', icon: 'fa-file-lines', hint: 'Journaux', url: safeUrl(l.loki) },
        { key: 'camunda', label: 'Camunda', icon: 'fa-diagram-project', hint: 'Orchestration des workflows', url: safeUrl(l.camunda) }
      ],
      error: () => this.error = 'Les liens de supervision ne sont pas disponibles.'
    });
  }
}

/** N'accepte que http(s) : une valeur de configuration inattendue (javascript:, data:…) n'est jamais rendue en lien. */
export function safeUrl(url: string | null | undefined): string | null {
  return typeof url === 'string' && /^https?:\/\//i.test(url.trim()) ? url.trim() : null;
}
