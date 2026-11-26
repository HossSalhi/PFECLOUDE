import { Component, OnInit } from '@angular/core';
import { ActivatedRoute } from '@angular/router';
import { TeamService } from '../../core/services/team.service';
import { ApiService, UserData } from '../../core/services/api.service';
import { OrgPermissionsService } from '../../core/services/org-permissions.service';
import {
  TaskPriority, TaskRequest, TaskStatus, TaskSummary, Team, TeamMember, TeamTask,
  TASK_PRIORITY_LABELS, TASK_STATUS_LABELS
} from '../../core/models/org.models';
import { errorMessage, formatDate, formatDateTime, toDateValue } from '../../shared/date.util';
import { allowedTransitions } from '../../shared/task-transitions.util';

/**
 * Tâches d'équipe : liste filtrable, avancement, création / modification, affectation et changement de statut.
 * Distinct de la gestion des incidents (les tâches « d'approbation » d'incidents restent dans leur propre module).
 * Un membre simple (USER) voit les tâches de ses équipes et peut faire avancer SES tâches ; le reste est réservé
 * aux gestionnaires — règles appliquées par le Backend, reprises ici pour n'afficher que les actions utiles.
 */
@Component({
    selector: 'app-team-tasks',
    templateUrl: './team-tasks.component.html',
    standalone: false
})
export class TeamTasksComponent implements OnInit {

  items: TeamTask[] = [];
  total = 0;
  page = 0;
  size = 15;
  loading = false;
  error = '';
  success = '';

  summary: TaskSummary | null = null;

  filters = {
    teamId: null as number | null,
    assigneeId: null as number | null,
    status: '' as TaskStatus | '',
    priority: '' as TaskPriority | '',
    overdue: false,
    mine: false,
    q: ''
  };

  // Référentiels
  teams: Team[] = [];
  users: UserData[] = [];
  formMembers: TeamMember[] = [];
  membersLoading = false;

  // Détail
  selected: TeamTask | null = null;

  // Formulaire
  showForm = false;
  editing: TeamTask | null = null;
  saving = false;
  modalError = '';
  form = this.emptyForm();

  // Affectation
  showAssign = false;
  assignTarget: TeamTask | null = null;
  assignMembers: TeamMember[] = [];
  assigneeId: number | null = null;

  busy = false;
  readonly today = toDateValue(new Date());

  readonly statusLabels = TASK_STATUS_LABELS;
  readonly priorityLabels = TASK_PRIORITY_LABELS;
  readonly statuses = Object.keys(TASK_STATUS_LABELS) as TaskStatus[];
  readonly priorities = Object.keys(TASK_PRIORITY_LABELS) as TaskPriority[];

  constructor(
    private service: TeamService,
    private api: ApiService,
    private route: ActivatedRoute,
    public perms: OrgPermissionsService
  ) {}

  get canCreate(): boolean { return this.perms.can('TASKS_CREATE'); }
  get canUpdate(): boolean { return this.perms.can('TASKS_UPDATE'); }
  get canAssign(): boolean { return this.perms.can('TASKS_ASSIGN'); }
  get canOwnStatus(): boolean { return this.perms.can('TASKS_UPDATE_OWN_STATUS'); }

  ngOnInit(): void {
    const teamId = Number(this.route.snapshot.queryParamMap.get('teamId'));
    if (Number.isInteger(teamId) && teamId > 0) { this.filters.teamId = teamId; }
    this.service.listTeams(false).subscribe({ next: t => this.teams = t, error: () => {} });
    if (this.canCreate || this.canAssign) {
      this.api.getUsers().subscribe({ next: u => this.users = u.filter(x => x.enabled), error: () => {} });
    }
    this.load();
  }

  fmt(v: string | null | undefined): string { return formatDateTime(v); }
  fmtDate(v: string | null | undefined): string { return formatDate(v); }

  // ── Liste et avancement ───────────────────────────────────────────────────

  load(resetPage = false): void {
    if (resetPage) { this.page = 0; }
    this.loading = true;
    this.error = '';
    this.service.listTasks({ ...this.filters, page: this.page, size: this.size }).subscribe({
      next: p => { this.items = p.content; this.total = p.totalElements; this.loading = false; },
      error: e => { this.error = errorMessage(e, 'Impossible de charger les tâches.'); this.loading = false; }
    });
    this.service.taskSummary(this.filters.teamId, this.filters.mine).subscribe({
      next: s => this.summary = s,
      error: () => this.summary = null
    });
  }

  resetFilters(): void {
    this.filters = { teamId: null, assigneeId: null, status: '', priority: '', overdue: false, mine: false, q: '' };
    this.load(true);
  }

  get totalPages(): number { return Math.max(1, Math.ceil(this.total / this.size)); }

  goTo(page: number): void {
    this.page = Math.min(Math.max(0, page), this.totalPages - 1);
    this.load();
  }

  // ── Droits d'affichage par tâche ──────────────────────────────────────────

  isOpen(t: TeamTask): boolean { return t.status !== 'DONE' && t.status !== 'CANCELLED'; }

  /** Statuts proposés pour cette tâche (même règle que le Backend : un membre simple ne touche qu'à ses tâches). */
  statusChoices(t: TeamTask): TaskStatus[] {
    if (this.canUpdate) { return allowedTransitions(t.status, true); }
    if (this.canOwnStatus && t.assignee && t.assignee.username === this.perms.currentUsername()) {
      return allowedTransitions(t.status, false);
    }
    return [];
  }

  // ── Changement de statut ──────────────────────────────────────────────────

  changeStatus(t: TeamTask, status: TaskStatus | ''): void {
    if (!status) { return; }
    this.busy = true;
    this.error = '';
    this.service.changeTaskStatus(t.id, status).subscribe({
      next: updated => {
        this.busy = false;
        if (this.selected && this.selected.id === updated.id) { this.selected = updated; }
        this.flash(`Statut de « ${updated.title} » : ${this.statusLabels[updated.status]}.`);
        this.load();
      },
      // Resynchronise d'abord la liste (load() efface l'erreur), puis affiche le refus du Backend.
      error: e => { this.busy = false; this.load(); this.error = errorMessage(e); }
    });
  }

  // ── Formulaire ────────────────────────────────────────────────────────────

  private emptyForm() {
    return {
      title: '', description: '', teamId: null as number | null, assigneeId: null as number | null,
      priority: 'MEDIUM' as TaskPriority, dueDate: ''
    };
  }

  openCreate(): void {
    this.editing = null;
    this.form = this.emptyForm();
    if (this.filters.teamId) { this.form.teamId = this.filters.teamId; this.loadFormMembers(); } else { this.formMembers = []; }
    this.modalError = '';
    this.showForm = true;
  }

  openEdit(t: TeamTask): void {
    this.editing = t;
    this.form = {
      title: t.title, description: t.description || '', teamId: t.team ? t.team.id : null,
      assigneeId: t.assignee ? t.assignee.id : null, priority: t.priority, dueDate: t.dueDate || ''
    };
    this.selected = null;
    this.modalError = '';
    this.loadFormMembers();
    this.showForm = true;
  }

  closeForm(): void { this.showForm = false; this.editing = null; }

  /** Membres de l'équipe choisie : seuls eux peuvent se voir confier la tâche (règle du Backend). */
  onFormTeamChange(): void {
    this.form.assigneeId = null;
    this.loadFormMembers();
  }

  private loadFormMembers(): void {
    if (this.form.teamId === null) { this.formMembers = []; return; }
    this.membersLoading = true;
    this.service.members(this.form.teamId).subscribe({
      next: m => { this.formMembers = m.filter(x => x.enabled); this.membersLoading = false; },
      error: () => { this.formMembers = []; this.membersLoading = false; }
    });
  }

  /** Personnes proposées : les membres de l'équipe si elle est choisie, sinon tous les utilisateurs actifs. */
  get assigneeChoices(): Array<{ id: number; label: string }> {
    if (this.form.teamId !== null) {
      return this.formMembers.map(m => ({ id: m.userId, label: `${m.displayName} (@${m.username})` }));
    }
    return this.users.map(u => ({ id: u.id, label: `${u.firstName} ${u.lastName} (@${u.username})` }));
  }

  submit(): void {
    if (!this.form.title.trim()) { this.modalError = 'Le titre est obligatoire.'; return; }
    if (!this.editing && this.form.dueDate && this.form.dueDate < this.today) {
      this.modalError = "L'échéance ne peut pas être dans le passé."; return;
    }
    const body: TaskRequest = {
      title: this.form.title.trim(),
      description: this.form.description.trim() || null,
      teamId: this.form.teamId,
      assigneeId: this.form.assigneeId,
      priority: this.form.priority,
      dueDate: this.form.dueDate || null
    };
    this.saving = true;
    this.modalError = '';
    const call = this.editing ? this.service.updateTask(this.editing.id, body) : this.service.createTask(body);
    call.subscribe({
      next: t => {
        this.saving = false;
        this.showForm = false;
        this.flash(this.editing ? `Tâche « ${t.title} » modifiée.` : `Tâche « ${t.title} » créée.`);
        this.editing = null;
        this.load();
      },
      error: e => { this.saving = false; this.modalError = errorMessage(e); }
    });
  }

  // ── Affectation ───────────────────────────────────────────────────────────

  openAssign(t: TeamTask): void {
    this.assignTarget = t;
    this.assigneeId = t.assignee ? t.assignee.id : null;
    this.assignMembers = [];
    this.modalError = '';
    this.selected = null;
    this.showAssign = true;
    if (t.team) {
      this.service.members(t.team.id).subscribe({
        next: m => this.assignMembers = m.filter(x => x.enabled),
        error: e => this.modalError = errorMessage(e)
      });
    }
  }

  submitAssign(): void {
    if (!this.assignTarget) { return; }
    this.busy = true;
    this.service.assignTask(this.assignTarget.id, this.assigneeId).subscribe({
      next: t => {
        this.busy = false;
        this.showAssign = false;
        this.flash(t.assignee ? `Tâche affectée à ${t.assignee.displayName}.` : 'Affectation retirée.');
        this.load();
      },
      error: e => { this.busy = false; this.modalError = errorMessage(e); }
    });
  }

  private flash(message: string): void {
    this.success = message;
    window.setTimeout(() => { if (this.success === message) { this.success = ''; } }, 5000);
  }
}
