import { ChangeDetectionStrategy, Component, OnInit, inject } from '@angular/core';
import { DomSanitizer, SafeResourceUrl } from '@angular/platform-browser';
import { SupervisionService } from '../../core/services/supervision.service';
import { GrafanaConfig, GrafanaDashboard } from '../../core/models/supervision.models';

/**
 * Dashboards Grafana intégrés dans HELIOS (lecture seule). Le serveur fournit les adresses validées ; l'intégration n'est
 * affichée que si elle est activée. La période et l'actualisation sont passées à Grafana par l'adresse (from, refresh).
 * Si HELIOS est ouvert en HTTPS et Grafana en HTTP, le navigateur bloque l'intégration : un avertissement et un lien
 * « Ouvrir dans Grafana » sont alors proposés.
 */
@Component({
  selector: 'app-grafana-panel',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.Eager,
  templateUrl: './grafana-panel.component.html'
})
export class GrafanaPanelComponent implements OnInit {
  private api = inject(SupervisionService);
  private sanitizer = inject(DomSanitizer);

  readonly ranges = [{ id: '1h', label: '1 h' }, { id: '6h', label: '6 h' }, { id: '24h', label: '24 h' }, { id: '7d', label: '7 jours' }];

  config: GrafanaConfig | null = null;
  loading = true;
  error = '';
  selected: GrafanaDashboard | null = null;
  range = '6h';
  refresh = true;
  frameUrl: SafeResourceUrl | null = null;
  openUrl: string | null = null;
  mixedContent = false;

  ngOnInit(): void {
    this.api.grafana().subscribe({
      next: c => {
        this.config = c;
        this.loading = false;
        if (c.embedEnabled && c.dashboards.length) { this.choose(c.dashboards[0]); }
      },
      error: () => { this.error = "Le serveur n'a pas pu répondre."; this.loading = false; }
    });
  }

  choose(d: GrafanaDashboard): void {
    this.selected = d;
    this.build();
  }

  setRange(r: string): void { this.range = r; this.build(); }

  toggleRefresh(): void { this.refresh = !this.refresh; this.build(); }

  reload(): void { this.build(); }

  /** Compose l'adresse du dashboard (période, thème, actualisation) ; seule une adresse http(s) du serveur est intégrée. */
  private build(): void {
    const base = this.selected?.embedUrl;
    if (!base || !/^https?:\/\//i.test(base)) { this.frameUrl = null; this.openUrl = null; this.mixedContent = false; return; }
    let url = `${base}&from=now-${this.range}&to=now&theme=light`;
    if (this.refresh) { url += '&refresh=30s'; }
    this.openUrl = url.replace('&kiosk', '');
    this.mixedContent = typeof location !== 'undefined' && location.protocol === 'https:' && url.startsWith('http://');
    this.frameUrl = this.mixedContent ? null : this.sanitizer.bypassSecurityTrustResourceUrl(url);
  }
}
