import { Component, OnInit, ChangeDetectionStrategy } from '@angular/core';
import { MaintenanceService } from '../../core/services/maintenance.service';
import { ApiService, UserData } from '../../core/services/api.service';
import { OrgPermissionsService } from '../../core/services/org-permissions.service';
import {
  Maintenance, MaintenanceHistoryEntry, MaintenanceRequest, MaintenanceResource, MaintenanceResourceType,
  MaintenanceStatus, MAINTENANCE_STATUS_LABELS, RESOURCE_TYPE_LABELS
} from '../../core/models/org.models';
import { buildMonthGrid, CalendarDay, gridRange, MONTH_NAMES, WEEKDAY_NAMES } from '../../shared/calendar.util';
import { errorMessage, formatDateTime, toDatetimeLocalValue, toLocalIso } from '../../shared/date.util';

const FIELD_LABELS: Record<string, string> = {
  title: 'Titre', resource: 'Ressource', startAt: 'Début', endAt: 'Fin', status: 'Statut',
  responsible: 'Responsable', reason: 'Motif', description: 'Description'
};

/**
 * Gestion de la maintenance : liste filtrable, calendrier mensuel, création / modification, démarrage, fin,
 * annulation, suppression et historique. Les droits affichés viennent du Backend ; le Backend les applique aussi.
 * Une maintenance est une information de gestion : elle n'arrête ni la supervision ni les mécanismes de sécurité.
 */
@Component({
    selector: 'app-maintenance',
    templateUrl: './maintenance.component.html',
    changeDetection: ChangeDetectionStrategy.Eager,
    standalone: false
})
export class MaintenanceComponent implements OnInit {

  view: 'list' | 'calendar' = 'list';

  // Liste
  items: Maintenance[] = [];
  total = 0;
  page = 0;
  size = 15;
  loading = false;
  filters = {
    status: '' as MaintenanceStatus | '',
    resourceType: '' as MaintenanceResourceType | '',
    resource: '',
    responsibleId: null as number | null,
    from: '',
    to: '',
    q: ''
  };

  // Calendrier
  calYear = new Date().getFullYear();
  calMonth = new Date().getMonth();
  weeks: CalendarDay[][] = [];
  calLoading = false;
  readonly weekdays = WEEKDAY_NAMES;

  // Référentiels
  users: UserData[] = [];
  resources: MaintenanceResource[] = [];

  // Messages
  error = '';
  success = '';

  // Détail
  selected: Maintenance | null = null;
  history: MaintenanceHistoryEntry[] = [];
  historyLoading = false;

  // Formulaire
  showForm = false;
  editing: Maintenance | null = null;
  saving = false;
  modalError = '';
  form = this.emptyForm();

  // Confirmations
  showCancel = false;
  cancelReason = '';
  showDelete = false;
  confirm: { title: string; message: string; label: string; run: () => void } | null = null;
  busy = false;

  readonly statusLabels = MAINTENANCE_STATUS_LABELS;
  readonly typeLabels = RESOURCE_TYPE_LABELS;
  readonly statuses = Object.keys(MAINTENANCE_STATUS_LABELS) as MaintenanceStatus[];
  readonly resourceTypes = Object.keys(RESOURCE_TYPE_LABELS) as MaintenanceResourceType[];

  constructor(
    private service: MaintenanceService,
    private api: ApiService,
    public perms: OrgPermissionsService
  ) {}

  // ── Droits d'affichage (le Backend applique les mêmes règles) ─────────────

  get canCreate(): boolean { return this.perms.can('MAINTENANCE_CREATE'); }
  get canUpdate(): boolean { return this.perms.can('MAINTENANCE_UPDATE'); }
  get canCancel(): boolean { return this.perms.can('MAINTENANCE_CANCEL'); }
  get canDelete(): boolean { return this.perms.can('MAINTENANCE_DELETE'); }

  isActive(m: Maintenance): boolean { return m.status === 'PLANNED' || m.status === 'IN_PROGRESS'; }

  // ── Cycle de vie du composant ─────────────────────────────────────────────

  ngOnInit(): void {
    this.service.resources().subscribe({ next: r => this.resources = r, error: () => {} });
    if (this.canCreate) {
      this.api.getUsers().subscribe({ next: u => this.users = u.filter(x => x.enabled), error: () => {} });
    }
    this.load();
  }

  // ── Liste ─────────────────────────────────────────────────────────────────

  load(resetPage = false): void {
    if (resetPage) { this.page = 0; }
    this.loading = true;
    this.error = '';
    this.service.list({
      status: this.filters.status,
      resourceType: this.filters.resourceType,
      resource: this.filters.resource,
      responsibleId: this.filters.responsibleId,
      from: this.filters.from ? toLocalIso(new Date(this.filters.from)) : undefined,
      to: this.filters.to ? toLocalIso(new Date(this.filters.to)) : undefined,
      q: this.filters.q,
      page: this.page,
      size: this.size
    }).subscribe({
      next: p => { this.items = p.content; this.total = p.totalElements; this.loading = false; },
      error: e => { this.error = errorMessage(e, 'Impossible de charger les maintenances.'); this.loading = false; }
    });
  }

  resetFilters(): void {
    this.filters = { status: '', resourceType: '', resource: '', responsibleId: null, from: '', to: '', q: '' };
    this.load(true);
  }

  get totalPages(): number { return Math.max(1, Math.ceil(this.total / this.size)); }

  goTo(page: number): void {
    this.page = Math.min(Math.max(0, page), this.totalPages - 1);
    this.load();
  }

  // ── Calendrier ────────────────────────────────────────────────────────────

  setView(view: 'list' | 'calendar'): void {
    this.view = view;
    if (view === 'calendar') { this.loadCalendar(); }
  }

  get calTitle(): string { return `${MONTH_NAMES[this.calMonth]} ${this.calYear}`; }

  loadCalendar(): void {
    const { from, to } = gridRange(this.calYear, this.calMonth);
    this.calLoading = true;
    this.service.calendar(toLocalIso(from), toLocalIso(to)).subscribe({
      next: events => {
        this.weeks = buildMonthGrid(this.calYear, this.calMonth, events.map(m => ({
          id: m.id, title: `${m.title} — ${m.resourceName}`, start: m.startAt, end: m.endAt, status: m.status
        })));
        this.calLoading = false;
      },
      error: e => { this.error = errorMessage(e, 'Impossible de charger le calendrier.'); this.calLoading = false; }
    });
  }

  shiftMonth(delta: number): void {
    const d = new Date(this.calYear, this.calMonth + delta, 1);
    this.calYear = d.getFullYear();
    this.calMonth = d.getMonth();
    this.loadCalendar();
  }

  goToday(): void {
    const now = new Date();
    this.calYear = now.getFullYear();
    this.calMonth = now.getMonth();
    this.loadCalendar();
  }

  openFromCalendar(id: number): void {
    this.service.get(id).subscribe({ next: m => this.openDetail(m), error: e => this.error = errorMessage(e) });
  }

  // ── Détail ────────────────────────────────────────────────────────────────

  openDetail(m: Maintenance): void {
    this.selected = m;
    this.history = [];
    this.historyLoading = true;
    this.service.history(m.id).subscribe({
      next: h => { this.history = h; this.historyLoading = false; },
      error: () => { this.historyLoading = false; }
    });
  }

  closeDetail(): void { this.selected = null; }

  historyLines(h: MaintenanceHistoryEntry): string[] {
    if (!h.details) { return []; }
    try {
      const d = JSON.parse(h.details) as { old?: Record<string, unknown>; new?: Record<string, unknown> };
      const oldV = d.old || {};
      const newV = d.new || {};
      return Object.keys(newV).map(k => {
        const label = FIELD_LABELS[k] || k;
        return k in oldV ? `${label} : ${this.show(oldV[k])} → ${this.show(newV[k])}` : `${label} : ${this.show(newV[k])}`;
      });
    } catch {
      return [h.details];
    }
  }

  private show(v: unknown): string {
    if (v === null || v === undefined || v === '') { return '—'; }
    const s = String(v);
    return /^\d{4}-\d{2}-\d{2}T/.test(s) ? formatDateTime(s) : s;
  }

  fmt(v: string | null | undefined): string { return formatDateTime(v); }

  // ── Formulaire ────────────────────────────────────────────────────────────

  private emptyForm() {
    return {
      title: '', description: '', reason: '',
      resourceType: 'APPLICATION' as MaintenanceResourceType,
      resourceName: '', startAt: '', endAt: '', responsibleId: null as number | null
    };
  }

  openCreate(): void {
    this.editing = null;
    this.form = this.emptyForm();
    this.modalError = '';
    this.showForm = true;
  }

  openEdit(m: Maintenance): void {
    this.editing = m;
    this.form = {
      title: m.title, description: m.description || '', reason: m.reason || '',
      resourceType: m.resourceType, resourceName: m.resourceName,
      startAt: toDatetimeLocalValue(m.startAt), endAt: toDatetimeLocalValue(m.endAt),
      responsibleId: m.responsible ? m.responsible.id : null
    };
    this.modalError = '';
    this.selected = null;
    this.showForm = true;
  }

  closeForm(): void { this.showForm = false; this.editing = null; }

  /** Le début et la ressource ne sont plus modifiables une fois la maintenance démarrée (règle du Backend). */
  get lockedFields(): boolean { return !!this.editing && this.editing.status === 'IN_PROGRESS'; }

  resourcesOfType(): MaintenanceResource[] {
    return this.resources.filter(r => r.type === this.form.resourceType);
  }

  private validateForm(): string {
    if (!this.form.title.trim()) { return 'Le titre est obligatoire.'; }
    if (!this.form.resourceName.trim()) { return 'La ressource est obligatoire.'; }
    if (!this.form.startAt || !this.form.endAt) { return 'Les dates de début et de fin sont obligatoires.'; }
    const start = new Date(this.form.startAt).getTime();
    const end = new Date(this.form.endAt).getTime();
    if (isNaN(start) || isNaN(end)) { return 'Dates invalides.'; }
    if (end <= start) { return 'La date de fin doit être postérieure à la date de début.'; }
    if (end - start > 31 * 24 * 3600 * 1000) { return 'Une maintenance ne peut pas dépasser 31 jours.'; }
    return '';
  }

  submit(): void {
    const problem = this.validateForm();
    if (problem) { this.modalError = problem; return; }
    const body: MaintenanceRequest = {
      title: this.form.title.trim(),
      description: this.form.description.trim() || null,
      reason: this.form.reason.trim() || null,
      resourceType: this.form.resourceType,
      resourceName: this.form.resourceName.trim(),
      startAt: toLocalIso(new Date(this.form.startAt)),
      endAt: toLocalIso(new Date(this.form.endAt)),
      responsibleId: this.form.responsibleId
    };
    this.saving = true;
    this.modalError = '';
    const call = this.editing ? this.service.update(this.editing.id, body) : this.service.create(body);
    call.subscribe({
      next: m => {
        this.saving = false;
        this.showForm = false;
        this.flash(this.editing ? `Maintenance « ${m.title} » modifiée.` : `Maintenance « ${m.title} » planifiée.`);
        this.editing = null;
        this.refreshAll();
      },
      error: e => { this.saving = false; this.modalError = errorMessage(e); }
    });
  }

  // ── Actions ───────────────────────────────────────────────────────────────

  askStart(m: Maintenance): void {
    this.confirm = {
      title: 'Déclarer la ressource en maintenance',
      message: `Démarrer la maintenance « ${m.title} » sur ${m.resourceName} ? C'est une information de gestion : la supervision et les mécanismes de sécurité restent actifs.`,
      label: 'Démarrer',
      run: () => this.act(this.service.start(m.id), 'Maintenance démarrée.')
    };
  }

  askComplete(m: Maintenance): void {
    this.confirm = {
      title: 'Terminer la maintenance',
      message: `Marquer la maintenance « ${m.title} » comme terminée ?`,
      label: 'Terminer',
      run: () => this.act(this.service.complete(m.id), 'Maintenance terminée.')
    };
  }

  askCancel(m: Maintenance): void {
    this.selected = m;
    this.cancelReason = '';
    this.modalError = '';
    this.showCancel = true;
  }

  submitCancel(): void {
    if (!this.selected) { return; }
    const id = this.selected.id;
    this.busy = true;
    this.service.cancel(id, this.cancelReason.trim() || null).subscribe({
      next: () => { this.busy = false; this.showCancel = false; this.selected = null; this.flash('Maintenance annulée.'); this.refreshAll(); },
      error: e => { this.busy = false; this.modalError = errorMessage(e); }
    });
  }

  askDelete(m: Maintenance): void {
    this.selected = m;
    this.modalError = '';
    this.showDelete = true;
  }

  submitDelete(): void {
    if (!this.selected) { return; }
    this.busy = true;
    this.service.remove(this.selected.id).subscribe({
      next: () => { this.busy = false; this.showDelete = false; this.selected = null; this.flash('Maintenance supprimée (la trace d\'audit est conservée).'); this.refreshAll(); },
      error: e => { this.busy = false; this.modalError = errorMessage(e); }
    });
  }

  runConfirm(): void {
    const c = this.confirm;
    this.confirm = null;
    if (c) { c.run(); }
  }

  private act(call: ReturnType<MaintenanceService['start']>, message: string): void {
    this.busy = true;
    call.subscribe({
      next: m => { this.busy = false; this.selected = m; this.openDetail(m); this.flash(message); this.refreshAll(); },
      error: e => { this.busy = false; this.error = errorMessage(e); }
    });
  }

  private refreshAll(): void {
    this.load();
    if (this.view === 'calendar') { this.loadCalendar(); }
  }

  private flash(message: string): void {
    this.success = message;
    window.setTimeout(() => { if (this.success === message) { this.success = ''; } }, 5000);
  }
}
