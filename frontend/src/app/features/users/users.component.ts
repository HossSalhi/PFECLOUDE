import { Component, OnInit, ChangeDetectionStrategy } from '@angular/core';
import { ApiService, UserData } from '../../core/services/api.service';
import { AuthService } from '../../core/services/auth.service';
import { FormsModule } from '@angular/forms';
import { NgClass } from '@angular/common';

@Component({
    selector: 'app-users',
    templateUrl: './users.component.html',
    changeDetection: ChangeDetectionStrategy.Eager,
    imports: [FormsModule, NgClass]
})
export class UsersComponent implements OnInit {
  users: UserData[] = [];
  filteredUsers: UserData[] = [];
  loading = false;
  error = '';
  success = '';
  modalError = '';

  searchQuery = '';
  filterRole = '';
  filterStatus = '';

  showCreateModal = false;
  showEditModal   = false;
  showBlockModal  = false;
  showResetModal  = false;
  showDeleteConfirm = false;

  selectedUser: UserData | null = null;

  formFirstName = '';
  formLastName  = '';
  formUsername  = '';
  formEmail     = '';
  formPassword  = '';
  formRole      = 'USER';
  blockReason   = '';
  newPassword   = '';
  saving = false;

  constructor(public auth: AuthService, private api: ApiService) {}

  get isSuperAdmin(): boolean { return this.auth.isSuperAdmin(); }
  get isAdmin(): boolean      { return this.auth.isAdmin(); }

  ngOnInit(): void { this.loadUsers(); }

  loadUsers(): void {
    this.loading = true;
    this.error = '';
    this.api.getUsers().subscribe({
      next: users => { this.users = users; this.applyFilters(); this.loading = false; },
      error: () => { this.error = 'Erreur lors du chargement des utilisateurs.'; this.loading = false; }
    });
  }

  applyFilters(): void {
    let r = [...this.users];
    if (this.searchQuery.trim()) {
      const q = this.searchQuery.toLowerCase();
      r = r.filter(u =>
        u.username.toLowerCase().includes(q) ||
        u.email.toLowerCase().includes(q) ||
        (u.firstName || '').toLowerCase().includes(q) ||
        (u.lastName  || '').toLowerCase().includes(q)
      );
    }
    if (this.filterRole)   r = r.filter(u => u.role === this.filterRole);
    if (this.filterStatus === 'active')   r = r.filter(u => u.enabled && !u.blockedAt);
    if (this.filterStatus === 'blocked')  r = r.filter(u => !!u.blockedAt);
    if (this.filterStatus === 'disabled') r = r.filter(u => !u.enabled && !u.blockedAt);
    this.filteredUsers = r;
  }

  // ── Create ──────────────────────────────────────────────────────────────
  openCreate(): void {
    this.formFirstName = this.formLastName = this.formUsername =
    this.formEmail = this.formPassword = '';
    this.formRole = 'USER';
    this.modalError = '';
    this.showCreateModal = true;
  }

  submitCreate(): void {
    this.saving = true; this.modalError = '';
    this.api.createUser({
      firstName: this.formFirstName, lastName: this.formLastName,
      username: this.formUsername, email: this.formEmail,
      password: this.formPassword, role: this.formRole
    }).subscribe({
      next: () => { this.saving = false; this.showCreateModal = false; this.flash('Utilisateur créé avec succès.'); this.loadUsers(); },
      error: err => { this.saving = false; this.modalError = err?.error?.error || 'Erreur lors de la création.'; }
    });
  }

  // ── Edit ────────────────────────────────────────────────────────────────
  openEdit(user: UserData): void {
    this.selectedUser = user;
    this.formFirstName = user.firstName || '';
    this.formLastName  = user.lastName  || '';
    this.formUsername  = user.username;
    this.formEmail     = user.email;
    this.formPassword  = '';
    this.formRole      = user.role;
    this.modalError    = '';
    this.showEditModal = true;
  }

  submitEdit(): void {
    if (!this.selectedUser) return;
    this.saving = true; this.modalError = '';
    const payload: any = {
      firstName: this.formFirstName, lastName: this.formLastName,
      username: this.formUsername, email: this.formEmail, role: this.formRole
    };
    if (this.formPassword) payload['password'] = this.formPassword;
    this.api.updateUser(this.selectedUser.id, payload).subscribe({
      next: () => { this.saving = false; this.showEditModal = false; this.flash('Utilisateur modifié avec succès.'); this.loadUsers(); },
      error: err => { this.saving = false; this.modalError = err?.error?.error || 'Erreur lors de la modification.'; }
    });
  }

  // ── Delete ──────────────────────────────────────────────────────────────
  openDelete(user: UserData): void { this.selectedUser = user; this.showDeleteConfirm = true; }

  confirmDelete(): void {
    if (!this.selectedUser) return;
    this.saving = true;
    this.api.deleteUser(this.selectedUser.id).subscribe({
      next: () => { this.saving = false; this.showDeleteConfirm = false; this.flash('Utilisateur supprimé.'); this.loadUsers(); },
      error: err => { this.saving = false; this.error = err?.error?.error || 'Erreur lors de la suppression.'; this.showDeleteConfirm = false; }
    });
  }

  // ── Enable / Disable ────────────────────────────────────────────────────
  toggleEnabled(user: UserData): void {
    const obs = user.enabled ? this.api.disableUser(user.id) : this.api.enableUser(user.id);
    obs.subscribe({
      next: () => { this.flash(user.enabled ? 'Compte désactivé.' : 'Compte activé.'); this.loadUsers(); },
      error: err => { this.error = err?.error?.error || 'Erreur.'; }
    });
  }

  // ── Block ───────────────────────────────────────────────────────────────
  openBlock(user: UserData): void {
    this.selectedUser = user; this.blockReason = ''; this.modalError = '';
    this.showBlockModal = true;
  }

  submitBlock(): void {
    if (!this.selectedUser || !this.blockReason.trim()) return;
    this.saving = true; this.modalError = '';
    this.api.blockUser(this.selectedUser.id, this.blockReason).subscribe({
      next: () => { this.saving = false; this.showBlockModal = false; this.flash('Utilisateur bloqué.'); this.loadUsers(); },
      error: err => { this.saving = false; this.modalError = err?.error?.error || 'Erreur lors du blocage.'; }
    });
  }

  unblock(user: UserData): void {
    this.api.unblockUser(user.id).subscribe({
      next: () => { this.flash('Utilisateur débloqué.'); this.loadUsers(); },
      error: err => { this.error = err?.error?.error || 'Erreur.'; }
    });
  }

  // ── Reset Password ──────────────────────────────────────────────────────
  openResetPassword(user: UserData): void {
    this.selectedUser = user; this.newPassword = ''; this.modalError = '';
    this.showResetModal = true;
  }

  submitReset(): void {
    if (!this.selectedUser || this.newPassword.length < 8) return;
    this.saving = true; this.modalError = '';
    this.api.resetUserPassword(this.selectedUser.id, this.newPassword).subscribe({
      next: () => { this.saving = false; this.showResetModal = false; this.flash('Mot de passe réinitialisé.'); },
      error: err => { this.saving = false; this.modalError = err?.error?.error || 'Erreur.'; }
    });
  }

  // ── Helpers ─────────────────────────────────────────────────────────────
  getStatus(u: UserData): 'active' | 'blocked' | 'disabled' {
    if (u.blockedAt) return 'blocked';
    if (!u.enabled)  return 'disabled';
    return 'active';
  }

  canBlock(u: UserData): boolean {
    if (this.isSuperAdmin) return !u.blockedAt;
    if (this.isAdmin)      return u.role === 'USER' && !u.blockedAt;
    return false;
  }

  canUnblock(u: UserData): boolean {
    if (this.isSuperAdmin) return !!u.blockedAt;
    if (this.isAdmin)      return u.role === 'USER' && !!u.blockedAt;
    return false;
  }

  closeModals(): void {
    this.showCreateModal = this.showEditModal = this.showBlockModal =
    this.showResetModal  = this.showDeleteConfirm = false;
    this.modalError = '';
  }

  private flash(msg: string): void {
    this.success = msg;
    setTimeout(() => this.success = '', 3500);
  }

  formatDate(d: string | null): string {
    if (!d) return '—';
    return new Date(d).toLocaleDateString('fr-FR', { day: '2-digit', month: 'short', year: 'numeric' });
  }
}
