import { ChangeDetectionStrategy, Component, OnDestroy, OnInit, inject } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { SupervisionService } from '../../core/services/supervision.service';
import { LogLevelFilter, LogLines, LogLineView, LogSource } from '../../core/models/supervision.models';

/** Un morceau d'une ligne : texte ordinaire ou occurrence du mot cherché (rendu sans innerHTML). */
export interface Segment { text: string; hit: boolean; }

/**
 * Visualiseur de logs (administrateurs) : les lignes réelles de Loki pour une machine ou une application, filtrées par
 * niveau et par mot, avec rafraîchissement automatique facultatif. Lecture seule ; le serveur borne la période (3 h) et le
 * nombre de lignes (200), échappe le mot cherché et masque les secrets.
 */
@Component({
  selector: 'app-logs-viewer',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.Eager,
  templateUrl: './logs-viewer.component.html',
  imports: [FormsModule]
})
export class LogsViewerComponent implements OnInit, OnDestroy {
  private api = inject(SupervisionService);

  readonly levels: { id: LogLevelFilter; label: string }[] = [
    { id: 'ALL', label: 'Tous' }, { id: 'ERROR', label: 'Erreurs' }, { id: 'WARN', label: 'Avertissements' }
  ];
  readonly periods = [{ m: 15, label: '15 min' }, { m: 30, label: '30 min' }, { m: 60, label: '1 h' }, { m: 180, label: '3 h' }];
  readonly limits = [50, 100, 200];

  sources: LogSource[] = [];
  sourcesError = '';
  source = '';
  level: LogLevelFilter = 'ALL';
  minutes = 30;
  limit = 100;
  query = '';
  auto = false;

  loading = false;
  result: LogLines | null = null;
  error = '';
  private timer: ReturnType<typeof setInterval> | null = null;

  ngOnInit(): void {
    this.api.logSources().subscribe({
      next: s => {
        if (!s.available) { this.sourcesError = s.error || 'Loki est indisponible.'; return; }
        this.sources = s.sources;
        const preferred = s.sources.find(x => x.key === 'app:pfe/backend') || s.sources[0];
        if (preferred) { this.source = preferred.key; this.load(); }
      },
      error: () => this.sourcesError = "Le serveur n'a pas pu répondre."
    });
  }

  ngOnDestroy(): void { this.stopAuto(); }

  get hosts(): LogSource[] { return this.sources.filter(s => s.kind === 'HOST'); }
  get apps(): LogSource[] { return this.sources.filter(s => s.kind === 'APP'); }

  load(): void {
    if (!this.source) { return; }
    this.loading = true;
    this.error = '';
    this.api.logLines(this.source, this.level, this.query, this.minutes, this.limit).subscribe({
      next: r => { this.result = r; this.loading = false; },
      error: e => { this.error = e?.status === 403 ? 'Action non autorisée pour votre rôle.' : "Le serveur n'a pas pu répondre."; this.loading = false; }
    });
  }

  setLevel(l: LogLevelFilter): void { this.level = l; this.load(); }

  toggleAuto(): void {
    this.auto = !this.auto;
    if (this.auto) {
      this.timer = setInterval(() => { if (!this.loading) { this.load(); } }, 10_000);
    } else {
      this.stopAuto();
    }
  }

  private stopAuto(): void {
    if (this.timer) { clearInterval(this.timer); this.timer = null; }
    this.auto = false;
  }

  /** Découpe une ligne autour du mot cherché (insensible à la casse) pour le surligner sans HTML. */
  segments(line: LogLineView): Segment[] {
    const q = this.result?.query || '';
    if (!q) { return [{ text: line.text, hit: false }]; }
    const out: Segment[] = [];
    const lower = line.text.toLowerCase(), needle = q.toLowerCase();
    let from = 0;
    for (let i = lower.indexOf(needle); i >= 0; i = lower.indexOf(needle, from)) {
      if (i > from) { out.push({ text: line.text.slice(from, i), hit: false }); }
      out.push({ text: line.text.slice(i, i + needle.length), hit: true });
      from = i + needle.length;
    }
    if (from < line.text.length) { out.push({ text: line.text.slice(from), hit: false }); }
    return out;
  }

  time(at: string): string {
    const d = new Date(at);
    const p = (n: number) => String(n).padStart(2, '0');
    return `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
  }

  count(level: 'ERROR' | 'WARN'): number { return (this.result?.lines || []).filter(l => l.level === level).length; }
}
