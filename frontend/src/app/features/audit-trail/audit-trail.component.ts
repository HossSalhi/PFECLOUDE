import { Component, OnInit, ChangeDetectionStrategy } from '@angular/core';
import { AuditTrailService } from '../../core/services/audit-trail.service';
import { OrgPermissionsService } from '../../core/services/org-permissions.service';
import { AuditEntry, AuditMeta, AuditQuery, AUDIT_ACTION_LABELS, AUDIT_MODULE_LABELS } from '../../core/models/org.models';
import { errorMessage, formatDateTime, toLocalIso } from '../../shared/date.util';
import { saveBlob } from '../../shared/download.util';
import { FormsModule } from '@angular/forms';
import { NgClass } from '@angular/common';

export interface AuditDiffRow {
  field: string;
  before: string;
  after: string;
  changed: boolean;
}

const RESULT_LABELS: Record<string, string> = { SUCCESS: 'Succès', FAILURE: 'Échec', DENIED: 'Refusé' };

/**
 * Journal d'audit des activités : consultation filtrable, détail avec anciennes et nouvelles valeurs, export CSV.
 * Lecture seule : aucune action de modification ni de suppression n'existe, côté interface comme côté API.
 * Un utilisateur sans droit global ne voit que sa propre activité (règle appliquée par le Backend).
 */
@Component({
    selector: 'app-audit-trail',
    templateUrl: './audit-trail.component.html',
    changeDetection: ChangeDetectionStrategy.Eager,
    imports: [FormsModule, NgClass]
})
export class AuditTrailComponent implements OnInit {

  items: AuditEntry[] = [];
  total = 0;
  page = 0;
  size = 20;
  loading = false;
  exporting = false;
  error = '';
  success = '';

  meta: AuditMeta = { modules: [], actions: [], results: [] };
  filters = { username: '', module: '', action: '', result: '', from: '', to: '', q: '' };

  // Détail
  selected: AuditEntry | null = null;
  detailLoading = false;
  diff: AuditDiffRow[] = [];

  readonly moduleLabels = AUDIT_MODULE_LABELS;
  readonly actionLabels = AUDIT_ACTION_LABELS;
  readonly resultLabels = RESULT_LABELS;

  constructor(private service: AuditTrailService, public perms: OrgPermissionsService) {}

  get canViewAll(): boolean { return this.perms.can('AUDIT_VIEW_ALL'); }
  get canExport(): boolean { return this.perms.can('AUDIT_EXPORT'); }

  ngOnInit(): void {
    this.service.meta().subscribe({ next: m => this.meta = m, error: () => {} });
    this.load();
  }

  fmt(v: string | null | undefined): string { return formatDateTime(v); }

  private query(): AuditQuery {
    return {
      username: this.canViewAll ? this.filters.username.trim() : '',
      module: this.filters.module,
      action: this.filters.action,
      result: this.filters.result,
      from: this.filters.from ? toLocalIso(new Date(this.filters.from)) : undefined,
      to: this.filters.to ? toLocalIso(new Date(this.filters.to)) : undefined,
      q: this.filters.q.trim()
    };
  }

  load(resetPage = false): void {
    if (resetPage) { this.page = 0; }
    this.loading = true;
    this.error = '';
    this.service.search({ ...this.query(), page: this.page, size: this.size }).subscribe({
      next: p => { this.items = p.content; this.total = p.totalElements; this.loading = false; },
      error: e => { this.error = errorMessage(e, "Impossible de charger le journal d'audit."); this.loading = false; }
    });
  }

  resetFilters(): void {
    this.filters = { username: '', module: '', action: '', result: '', from: '', to: '', q: '' };
    this.load(true);
  }

  get totalPages(): number { return Math.max(1, Math.ceil(this.total / this.size)); }

  goTo(page: number): void {
    this.page = Math.min(Math.max(0, page), this.totalPages - 1);
    this.load();
  }

  // ── Export ────────────────────────────────────────────────────────────────

  exportCsv(): void {
    this.exporting = true;
    this.error = '';
    this.service.exportCsv(this.query()).subscribe({
      next: resp => {
        this.exporting = false;
        saveBlob(resp, 'audit.csv');
        this.success = 'Export CSV téléchargé (les filtres actuels sont appliqués).';
        window.setTimeout(() => this.success = '', 5000);
      },
      error: e => { this.exporting = false; this.error = errorMessage(e, "Export impossible."); }
    });
  }

  // ── Détail ────────────────────────────────────────────────────────────────

  openDetail(entry: AuditEntry): void {
    this.selected = entry;
    this.diff = [];
    this.detailLoading = true;
    this.service.get(entry.id).subscribe({
      next: full => { this.selected = full; this.diff = this.buildDiff(full); this.detailLoading = false; },
      error: e => { this.detailLoading = false; this.error = errorMessage(e); }
    });
  }

  closeDetail(): void { this.selected = null; }

  /** Compare anciennes et nouvelles valeurs champ par champ (les valeurs sensibles sont déjà masquées par le Backend). */
  buildDiff(entry: AuditEntry): AuditDiffRow[] {
    const before = this.parse(entry.oldValues);
    const after = this.parse(entry.newValues);
    const fields = Array.from(new Set([...Object.keys(before), ...Object.keys(after)]));
    return fields.map(field => {
      const b = this.show(before[field]);
      const a = this.show(after[field]);
      return { field, before: b, after: a, changed: b !== a };
    });
  }

  private parse(json: string | null): Record<string, unknown> {
    if (!json) { return {}; }
    try {
      const v = JSON.parse(json);
      return v && typeof v === 'object' && !Array.isArray(v) ? v as Record<string, unknown> : { valeur: v };
    } catch {
      return { valeur: json };
    }
  }

  private show(v: unknown): string {
    if (v === undefined || v === null || v === '') { return '—'; }
    return typeof v === 'object' ? JSON.stringify(v) : String(v);
  }

  moduleLabel(code: string | null): string { return code ? (AUDIT_MODULE_LABELS[code] || code) : '—'; }
  actionLabel(code: string | null): string { return code ? (AUDIT_ACTION_LABELS[code] || code) : '—'; }
  resultLabel(code: string | null): string { return code ? (RESULT_LABELS[code] || code) : '—'; }
}
