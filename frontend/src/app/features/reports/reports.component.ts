import { Component, OnInit } from '@angular/core';
import { ReportService } from '../../core/services/report.service';
import { OrgPermissionsService } from '../../core/services/org-permissions.service';
import {
  FREQUENCY_LABELS, ReportContent, ReportDetail, ReportFrequency, ReportItem, ReportSchedule, ReportType, ReportTypeInfo
} from '../../core/models/org.models';
import { errorMessage, formatDateTime } from '../../shared/date.util';
import { saveBlob } from '../../shared/download.util';
import { PERIOD_PRESET_LABELS, PeriodPreset, presetRange, toBackendPeriod, validatePeriod } from '../../shared/period.util';

type Tab = 'reports' | 'stats' | 'schedules';

/**
 * Gestion des rapports : génération manuelle sur une période (avec comparaison à la période précédente),
 * liste des rapports déjà générés (manuels et périodiques), consultation, export PDF / CSV, statistiques
 * d'activité à la demande et plannings de génération périodique. Tout est calculé par le Backend sur les données
 * réelles ; cette page ne fabrique aucune donnée.
 */
@Component({
  selector: 'app-reports',
  templateUrl: './reports.component.html'
})
export class ReportsComponent implements OnInit {

  tab: Tab = 'reports';
  error = '';
  success = '';

  types: ReportTypeInfo[] = [];

  // Liste
  items: ReportItem[] = [];
  total = 0;
  page = 0;
  size = 10;
  typeFilter: ReportType | '' = '';
  loading = false;
  exportingId: number | null = null;

  // Génération
  showGenerate = false;
  genType: ReportType = 'USER_ACTIVITY';
  genFrom = '';
  genTo = '';
  genCompare = true;
  generating = false;
  modalError = '';

  // Consultation
  viewing: ReportDetail | null = null;
  viewContent: ReportContent | null = null;
  viewLoading = false;

  // Statistiques à la demande
  statsFrom = '';
  statsTo = '';
  statsCompare = true;
  statsContent: ReportContent | null = null;
  statsLoading = false;
  statsError = '';

  // Plannings
  schedules: ReportSchedule[] = [];
  schedulesLoading = false;
  newScheduleType: ReportType = 'USER_ACTIVITY';
  newScheduleFrequency: ReportFrequency = 'WEEKLY';
  scheduleBusy = false;

  readonly frequencyLabels = FREQUENCY_LABELS;
  readonly frequencies = Object.keys(FREQUENCY_LABELS) as ReportFrequency[];
  readonly presets = Object.keys(PERIOD_PRESET_LABELS) as PeriodPreset[];
  readonly presetLabels = PERIOD_PRESET_LABELS;

  constructor(private service: ReportService, public perms: OrgPermissionsService) {}

  get canGenerate(): boolean { return this.perms.can('REPORTS_GENERATE'); }
  get canExport(): boolean { return this.perms.can('REPORTS_EXPORT'); }
  get canSchedule(): boolean { return this.perms.can('REPORTS_SCHEDULE'); }

  ngOnInit(): void {
    const range = presetRange('last30');
    this.genFrom = this.statsFrom = range.from;
    this.genTo = this.statsTo = range.to;
    this.service.types().subscribe({
      next: t => { this.types = t; if (t.length > 0) { this.genType = t[0].type; this.newScheduleType = t[0].type; } },
      error: e => this.error = errorMessage(e)
    });
    this.load();
  }

  fmt(v: string | null | undefined): string { return formatDateTime(v); }

  setTab(tab: Tab): void {
    this.tab = tab;
    this.error = '';
    if (tab === 'schedules') { this.loadSchedules(); }
  }

  // ── Liste ─────────────────────────────────────────────────────────────────

  load(resetPage = false): void {
    if (resetPage) { this.page = 0; }
    this.loading = true;
    this.error = '';
    this.service.list(this.typeFilter, this.page, this.size).subscribe({
      next: p => { this.items = p.content; this.total = p.totalElements; this.loading = false; },
      error: e => { this.error = errorMessage(e, 'Impossible de charger les rapports.'); this.loading = false; }
    });
  }

  get totalPages(): number { return Math.max(1, Math.ceil(this.total / this.size)); }

  goTo(page: number): void {
    this.page = Math.min(Math.max(0, page), this.totalPages - 1);
    this.load();
  }

  // ── Génération ────────────────────────────────────────────────────────────

  openGenerate(): void {
    this.modalError = '';
    this.showGenerate = true;
  }

  applyPreset(target: 'gen' | 'stats', preset: PeriodPreset): void {
    const range = presetRange(preset);
    if (target === 'gen') { this.genFrom = range.from; this.genTo = range.to; } else { this.statsFrom = range.from; this.statsTo = range.to; }
  }

  generate(): void {
    const problem = validatePeriod(this.genFrom, this.genTo);
    if (problem) { this.modalError = problem; return; }
    const period = toBackendPeriod(this.genFrom, this.genTo);
    this.generating = true;
    this.modalError = '';
    this.service.generate({ type: this.genType, from: period.from, to: period.to, compare: this.genCompare }).subscribe({
      next: report => {
        this.generating = false;
        this.showGenerate = false;
        this.flash(`Rapport « ${report.title} » généré.`);
        this.typeFilter = '';
        this.load(true);
        this.open(report);
      },
      error: e => { this.generating = false; this.modalError = errorMessage(e); }
    });
  }

  // ── Consultation et export ────────────────────────────────────────────────

  open(report: ReportItem): void {
    this.viewing = { report, errorMessage: null, content: null };
    this.viewContent = null;
    this.viewLoading = true;
    this.service.get(report.id).subscribe({
      next: detail => {
        this.viewing = detail;
        this.viewContent = this.parse(detail.content);
        this.viewLoading = false;
      },
      error: e => { this.viewLoading = false; this.viewing = null; this.error = errorMessage(e); }
    });
  }

  closeViewer(): void { this.viewing = null; this.viewContent = null; }

  private parse(json: string | null): ReportContent | null {
    if (!json) { return null; }
    try {
      return JSON.parse(json) as ReportContent;
    } catch {
      return null;
    }
  }

  exportReport(report: ReportItem, format: 'pdf' | 'csv'): void {
    this.exportingId = report.id;
    this.error = '';
    this.service.export(report.id, format).subscribe({
      next: resp => {
        this.exportingId = null;
        saveBlob(resp, `rapport-${report.id}.${format}`);
        this.flash(`Export ${format.toUpperCase()} téléchargé.`);
      },
      error: e => { this.exportingId = null; this.error = errorMessage(e, 'Export impossible.'); }
    });
  }

  // ── Statistiques à la demande ─────────────────────────────────────────────

  computeStats(): void {
    const problem = validatePeriod(this.statsFrom, this.statsTo);
    if (problem) { this.statsError = problem; return; }
    const period = toBackendPeriod(this.statsFrom, this.statsTo);
    this.statsLoading = true;
    this.statsError = '';
    this.service.statistics(period.from, period.to, this.statsCompare).subscribe({
      next: c => { this.statsContent = c; this.statsLoading = false; },
      error: e => { this.statsLoading = false; this.statsError = errorMessage(e, 'Statistiques indisponibles.'); }
    });
  }

  // ── Plannings ─────────────────────────────────────────────────────────────

  loadSchedules(): void {
    this.schedulesLoading = true;
    this.service.schedules().subscribe({
      next: s => { this.schedules = s; this.schedulesLoading = false; },
      error: e => { this.schedulesLoading = false; this.error = errorMessage(e, 'Impossible de charger les plannings.'); }
    });
  }

  addSchedule(): void {
    this.scheduleBusy = true;
    this.error = '';
    this.service.createSchedule(this.newScheduleType, this.newScheduleFrequency).subscribe({
      next: () => { this.scheduleBusy = false; this.flash('Planning créé.'); this.loadSchedules(); },
      error: e => { this.scheduleBusy = false; this.error = errorMessage(e); }
    });
  }

  toggleSchedule(s: ReportSchedule): void {
    this.scheduleBusy = true;
    this.error = '';
    this.service.toggleSchedule(s.id, !s.enabled).subscribe({
      next: () => { this.scheduleBusy = false; this.flash(s.enabled ? 'Planning désactivé.' : 'Planning activé.'); this.loadSchedules(); },
      error: e => { this.scheduleBusy = false; this.error = errorMessage(e); }
    });
  }

  removeSchedule(s: ReportSchedule): void {
    if (!window.confirm(`Supprimer le planning « ${s.typeLabel} » (${this.frequencyLabels[s.frequency]}) ? Les rapports déjà générés sont conservés.`)) { return; }
    this.scheduleBusy = true;
    this.error = '';
    this.service.deleteSchedule(s.id).subscribe({
      next: () => { this.scheduleBusy = false; this.flash('Planning supprimé.'); this.loadSchedules(); this.load(); },
      error: e => { this.scheduleBusy = false; this.error = errorMessage(e); }
    });
  }

  private flash(message: string): void {
    this.success = message;
    window.setTimeout(() => { if (this.success === message) { this.success = ''; } }, 5000);
  }
}
