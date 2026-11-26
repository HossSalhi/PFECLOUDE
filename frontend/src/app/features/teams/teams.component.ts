import { Component, OnInit } from '@angular/core';
import { Observable } from 'rxjs';
import { TeamService } from '../../core/services/team.service';
import { ApiService, UserData } from '../../core/services/api.service';
import { OrgPermissionsService } from '../../core/services/org-permissions.service';
import { Team, TeamMember, TeamSummary } from '../../core/models/org.models';
import { errorMessage, formatDateTime } from '../../shared/date.util';

/**
 * Gestion des équipes : création / modification, responsable, membres, activation / désactivation / suppression,
 * avancement des tâches de l'équipe. Les responsables et membres sont des utilisateurs HELIOS existants.
 */
@Component({
    selector: 'app-teams',
    templateUrl: './teams.component.html',
    standalone: false
})
export class TeamsComponent implements OnInit {

  teams: Team[] = [];
  includeInactive = false;
  q = '';
  loading = false;
  error = '';
  success = '';

  users: UserData[] = [];

  // Équipe sélectionnée
  selected: Team | null = null;
  members: TeamMember[] = [];
  summary: TeamSummary | null = null;
  detailLoading = false;
  addMemberId: number | null = null;
  newLeadId: number | null = null;

  // Formulaire
  showForm = false;
  editing: Team | null = null;
  saving = false;
  modalError = '';
  form = { name: '', description: '', leadId: null as number | null };

  // Confirmation
  confirm: { title: string; message: string; label: string; danger: boolean; run: () => void } | null = null;
  busy = false;

  constructor(
    private service: TeamService,
    private api: ApiService,
    public perms: OrgPermissionsService
  ) {}

  get canCreate(): boolean { return this.perms.can('TEAMS_CREATE'); }
  get canUpdate(): boolean { return this.perms.can('TEAMS_UPDATE'); }
  get canDeactivate(): boolean { return this.perms.can('TEAMS_DEACTIVATE'); }
  get canDelete(): boolean { return this.perms.can('TEAMS_DELETE'); }
  get canMembers(): boolean { return this.perms.can('TEAMS_MANAGE_MEMBERS'); }

  ngOnInit(): void {
    if (this.canCreate || this.canMembers || this.canUpdate) {
      this.api.getUsers().subscribe({ next: u => this.users = u.filter(x => x.enabled), error: () => {} });
    }
    this.load();
  }

  load(): void {
    this.loading = true;
    this.error = '';
    this.service.listTeams(this.includeInactive, this.q).subscribe({
      next: t => {
        this.teams = t;
        this.loading = false;
        if (this.selected) {
          const again = t.find(x => x.id === this.selected!.id);
          if (again) { this.selected = again; } else { this.selected = null; }
        }
      },
      error: e => { this.error = errorMessage(e, 'Impossible de charger les équipes.'); this.loading = false; }
    });
  }

  fmt(v: string | null | undefined): string { return formatDateTime(v); }

  // ── Détail d'une équipe ───────────────────────────────────────────────────

  select(team: Team): void {
    this.selected = team;
    this.addMemberId = null;
    this.newLeadId = null;
    this.loadDetail();
  }

  private loadDetail(): void {
    if (!this.selected) { return; }
    const id = this.selected.id;
    this.detailLoading = true;
    this.service.members(id).subscribe({
      next: m => { this.members = m; this.detailLoading = false; },
      error: e => { this.error = errorMessage(e); this.detailLoading = false; }
    });
    this.service.teamSummary(id).subscribe({ next: s => this.summary = s, error: () => this.summary = null });
  }

  /** Utilisateurs actifs qui ne sont pas encore membres de l'équipe sélectionnée. */
  get candidates(): UserData[] {
    return this.users.filter(u => !this.members.some(m => m.userId === u.id));
  }

  // ── Formulaire ────────────────────────────────────────────────────────────

  openCreate(): void {
    this.editing = null;
    this.form = { name: '', description: '', leadId: null };
    this.modalError = '';
    this.showForm = true;
  }

  openEdit(team: Team): void {
    this.editing = team;
    this.form = { name: team.name, description: team.description || '', leadId: team.lead ? team.lead.id : null };
    this.modalError = '';
    this.showForm = true;
  }

  closeForm(): void { this.showForm = false; this.editing = null; }

  submit(): void {
    if (!this.form.name.trim()) { this.modalError = "Le nom de l'équipe est obligatoire."; return; }
    const body = { name: this.form.name.trim(), description: this.form.description.trim() || null, leadId: this.form.leadId };
    this.saving = true;
    this.modalError = '';
    const call = this.editing ? this.service.updateTeam(this.editing.id, body) : this.service.createTeam(body);
    call.subscribe({
      next: t => {
        this.saving = false;
        this.showForm = false;
        this.flash(this.editing ? `Équipe « ${t.name} » modifiée.` : `Équipe « ${t.name} » créée.`);
        this.editing = null;
        this.load();
        if (this.selected && this.selected.id === t.id) { this.selected = t; this.loadDetail(); }
      },
      error: e => { this.saving = false; this.modalError = errorMessage(e); }
    });
  }

  // ── Membres et responsable ────────────────────────────────────────────────

  addMember(): void {
    if (!this.selected || this.addMemberId === null) { return; }
    this.run(this.service.addMember(this.selected.id, this.addMemberId), 'Membre ajouté.', true);
    this.addMemberId = null;
  }

  askRemoveMember(m: TeamMember): void {
    if (!this.selected) { return; }
    const team = this.selected;
    this.confirm = {
      title: 'Retirer un membre', danger: true, label: 'Retirer',
      message: `Retirer ${m.displayName} (@${m.username}) de l'équipe « ${team.name} » ?`,
      run: () => this.run(this.service.removeMember(team.id, m.userId), 'Membre retiré.', true)
    };
  }

  changeLead(): void {
    if (!this.selected || this.newLeadId === null) { return; }
    this.run(this.service.changeLead(this.selected.id, this.newLeadId), 'Responsable modifié.', true);
    this.newLeadId = null;
  }

  // ── Activation, désactivation, suppression ────────────────────────────────

  askToggle(team: Team): void {
    const deactivate = team.active;
    this.confirm = {
      title: deactivate ? "Désactiver l'équipe" : "Réactiver l'équipe", danger: deactivate,
      label: deactivate ? 'Désactiver' : 'Réactiver',
      message: deactivate
        ? `Désactiver « ${team.name} » ? Elle ne pourra plus recevoir de membres ni de nouvelles tâches ; l'historique est conservé.`
        : `Réactiver l'équipe « ${team.name} » ?`,
      run: () => this.run(deactivate ? this.service.deactivate(team.id) : this.service.activate(team.id),
        deactivate ? 'Équipe désactivée.' : 'Équipe réactivée.', true)
    };
  }

  askDelete(team: Team): void {
    this.confirm = {
      title: "Supprimer l'équipe", danger: true, label: 'Supprimer',
      message: `Supprimer définitivement « ${team.name} » ? Possible seulement si aucune tâche n'y est rattachée (sinon, désactivez-la).`,
      run: () => {
        this.busy = true;
        this.service.deleteTeam(team.id).subscribe({
          next: () => { this.busy = false; this.selected = null; this.flash('Équipe supprimée.'); this.load(); },
          error: e => { this.busy = false; this.error = errorMessage(e); }
        });
      }
    };
  }

  runConfirm(): void {
    const c = this.confirm;
    this.confirm = null;
    if (c) { c.run(); }
  }

  private run(call: Observable<unknown>, message: string, reloadDetail: boolean): void {
    this.busy = true;
    this.error = '';
    call.subscribe({
      next: () => { this.busy = false; this.flash(message); this.load(); if (reloadDetail) { this.loadDetail(); } },
      error: (e: unknown) => { this.busy = false; this.error = errorMessage(e); }
    });
  }

  private flash(message: string): void {
    this.success = message;
    window.setTimeout(() => { if (this.success === message) { this.success = ''; } }, 5000);
  }
}
